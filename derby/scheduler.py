from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import datetime, time as dt_time, timezone
from typing import Any

import discord
from discord.ext import tasks
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import resolve_guild_setting
from db_base import Base
import brewing.models  # noqa: F401 — register brewing tables on Base
import dungeon.models  # noqa: F401 — register dungeon tables on Base
import fishing.models  # noqa: F401 — register fishing tables on Base

from . import commentary, flavor_names, logic, models, npc_generation, npc_quips
from . import repositories as repo


def _parse_race_times(time_strings: list[str]) -> list[dt_time]:
    """Parse 'HH:MM' strings into datetime.time objects (UTC)."""
    times = []
    for ts in time_strings:
        h, m = ts.strip().split(":")
        times.append(dt_time(hour=int(h), minute=int(m)))
    return times


# (weekday 0=Mon, hour, minute, rank)
# Sat 00:00 D, Sat 00:10 C, Sun 00:00 B, Sun 00:10 A, Mon 00:00 S
TOURNAMENT_SCHEDULE: list[tuple[int, int, int, str]] = [
    (5, 0, 0, "D"),
    (5, 0, 10, "C"),
    (6, 0, 0, "B"),
    (6, 0, 10, "A"),
    (0, 0, 0, "S"),
]

TOURNAMENT_FIELD_SIZE = 8


class DerbyScheduler:
    """Background task that runs races at configured times."""

    def __init__(self, bot: discord.Client, db_path: str | None = None) -> None:
        self.bot = bot
        root = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
        self.db_path = db_path or os.path.join(root, "database", "database.db")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        self._initialized = False
        self.task: tasks.Loop | None = None
        self.tournament_task: tasks.Loop | None = None
        self.commentaries: dict[int, tasks.Loop] = {}
        self.active_races: set[int] = set()  # race IDs currently in progress
        self._last_tournament_tick: str | None = None  # "weekday-hour-minute" debounce

    def _resolve(
        self,
        key: str,
        guild_settings: models.GuildSettings | None = None,
    ) -> Any:
        """Return a guild override for *key* if set, else the global default."""
        return resolve_guild_setting(guild_settings, self.bot.settings, key)

    async def _load_guild_settings(
        self, guild_id: int
    ) -> models.GuildSettings | None:
        async with self.sessionmaker() as session:
            return await repo.get_guild_settings(session, guild_id)

    def _get_channel(
        self,
        guild: discord.Guild,
        guild_settings: models.GuildSettings | None = None,
        channel_key: str = "derby_channel",
    ) -> discord.abc.Messageable | None:
        """Return the configured channel for the guild or a sensible default."""
        # Try per-game channel first, then legacy channel_name fallback
        name = self._resolve(channel_key, guild_settings)
        if not name:
            name = self._resolve("channel_name", guild_settings)
        if name:
            for channel in guild.text_channels:
                if getattr(channel, "name", None) == name:
                    return channel
        return guild.system_channel or (
            guild.text_channels[0] if guild.text_channels else None
        )

    async def start(self) -> None:
        await self._init_db()
        race_times = _parse_race_times(
            getattr(self.bot, "settings", None)
            and self.bot.settings.race_times
            or ["09:00", "15:00", "21:00"]
        )
        self.task = tasks.loop(time=race_times)(self._run)
        self.task.start()

        # Tournament background tick — every 60 seconds
        self.tournament_task = tasks.loop(seconds=60)(self._tournament_tick)
        self.tournament_task.start()

        # Daily reward generation — midnight UTC
        self.daily_task = tasks.loop(time=[dt_time(0, 0)])(self._daily_tick)
        self.daily_task.start()

        # Daily digest — 00:05 UTC (after dailies are generated)
        self.digest_task = tasks.loop(time=[dt_time(0, 5)])(self._digest_tick)
        self.digest_task.start()

        # Fishing session tick — every 60 seconds
        self.fishing_task = tasks.loop(seconds=60)(self._fishing_tick)
        self.fishing_task.start()

        # Ensure pending races exist once the guild cache is ready.
        # This runs in the background so it doesn't block setup_hook.
        if hasattr(self.bot, "wait_until_ready"):
            asyncio.create_task(self._deferred_ensure_pending_races())
        else:
            # Tests don't use wait_until_ready — run immediately
            await self._ensure_pending_races()

    async def _deferred_ensure_pending_races(self) -> None:
        """Wait for the bot to be fully ready, then create pending races."""
        await self.bot.wait_until_ready()
        await self._ensure_pending_races()

    async def close(self) -> None:
        if self.task and self.task.is_running():
            self.task.cancel()
        if self.tournament_task and self.tournament_task.is_running():
            self.tournament_task.cancel()
        if hasattr(self, "daily_task") and self.daily_task and self.daily_task.is_running():
            self.daily_task.cancel()
        if hasattr(self, "digest_task") and self.digest_task and self.digest_task.is_running():
            self.digest_task.cancel()
        if hasattr(self, "fishing_task") and self.fishing_task and self.fishing_task.is_running():
            self.fishing_task.cancel()
        await self.engine.dispose()

    async def _init_db(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            # Pre-migration: check if wallets table needs rebuild for
            # composite PK (user_id, guild_id).  Drop it so create_all
            # recreates it with the correct schema.
            def _get_tables(sync_conn: Any) -> set[str]:
                insp = inspect(sync_conn)
                return set(insp.get_table_names())

            tables = await conn.run_sync(_get_tables)

            if "wallets" in tables:

                def _wallet_has_guild_id(sync_conn: Any) -> bool:
                    insp = inspect(sync_conn)
                    cols = {c["name"] for c in insp.get_columns("wallets")}
                    return "guild_id" in cols

                has_guild = await conn.run_sync(_wallet_has_guild_id)
                if not has_guild:
                    await conn.execute(text("DROP TABLE wallets"))

            # Rebuild guild_settings if it has the old schema (race_frequency
            # column from the unused initial model).
            if "guild_settings" in tables:

                def _gs_has_channel_name(sync_conn: Any) -> bool:
                    insp = inspect(sync_conn)
                    cols = {c["name"] for c in insp.get_columns("guild_settings")}
                    return "channel_name" in cols

                if not await conn.run_sync(_gs_has_channel_name):
                    await conn.execute(text("DROP TABLE guild_settings"))

            await conn.run_sync(Base.metadata.create_all)

            def get_table_columns(sync_conn: Any, table: str) -> set[str]:
                insp = inspect(sync_conn)
                return {c["name"] for c in insp.get_columns(table)}

            racer_columns = await conn.run_sync(
                lambda c: get_table_columns(c, "racers")
            )
            racer_migrations = {
                "speed": ("INTEGER", "0"),
                "cornering": ("INTEGER", "0"),
                "stamina": ("INTEGER", "0"),
                "temperament": ("VARCHAR", "'Quirky'"),
                "mood": ("INTEGER", "3"),
                "injuries": ("VARCHAR", "''"),
                "injury_races_remaining": ("INTEGER", "0"),
                "races_completed": ("INTEGER", "0"),
                "career_length": ("INTEGER", "30"),
                "peak_end": ("INTEGER", "18"),
                "guild_id": ("INTEGER", "0"),
                "gender": ("VARCHAR", "'M'"),
                "sire_id": ("INTEGER", "NULL"),
                "dam_id": ("INTEGER", "NULL"),
                "foal_count": ("INTEGER", "0"),
                "breed_cooldown": ("INTEGER", "0"),
                "training_count": ("INTEGER", "5"),
                "trains_since_race": ("INTEGER", "0"),
                "rank": ("VARCHAR", "NULL"),
                "tournament_wins": ("INTEGER", "0"),
                "tournament_placements": ("INTEGER", "0"),
                "description": ("TEXT", "NULL"),
                "pool_expires_at": ("DATETIME", "NULL"),
                "npc_id": ("INTEGER", "NULL"),
            }
            for name, (col_type, default) in racer_migrations.items():
                if name not in racer_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE racers ADD COLUMN {name} {col_type} DEFAULT {default}"
                        )
                    )

            # Backfill rank for existing racers that don't have one yet.
            await conn.execute(
                text(
                    "UPDATE racers SET rank = CASE "
                    "WHEN (speed + cornering + stamina) >= 81 THEN 'S' "
                    "WHEN (speed + cornering + stamina) >= 66 THEN 'A' "
                    "WHEN (speed + cornering + stamina) >= 47 THEN 'B' "
                    "WHEN (speed + cornering + stamina) >= 24 THEN 'C' "
                    "ELSE 'D' END "
                    "WHERE rank IS NULL"
                )
            )

            # One-time fix: randomly assign gender to pool racers that all
            # defaulted to 'M' from the gender migration.  Only runs if
            # zero females exist (the telltale sign of the default).
            female_count = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM racers WHERE gender = 'F'")
                )
            ).scalar()
            if female_count == 0:
                await conn.execute(
                    text(
                        "UPDATE racers SET gender = 'F' "
                        "WHERE ABS(RANDOM()) % 2 = 0"
                    )
                )

            # Backfill pool_expires_at for existing pool racers so they
            # don't all expire at once — stagger across the next 24-48h.
            await conn.execute(
                text(
                    "UPDATE racers SET pool_expires_at = "
                    "datetime('now', '+' || (ABS(RANDOM()) % 1440 + 1440) || ' minutes') "
                    "WHERE owner_id = 0 AND pool_expires_at IS NULL AND retired = 0"
                )
            )

            # Migrate guild_id=0 racers: duplicate per guild and fix references
            if "guild_id" not in racer_columns:
                guild_rows = await conn.execute(
                    text("SELECT DISTINCT guild_id FROM races")
                )
                guild_ids = [row[0] for row in guild_rows.fetchall()]

                if guild_ids:
                    old_racers = (
                        await conn.execute(
                            text("SELECT id, guild_id, name, owner_id, retired, "
                                 "speed, cornering, stamina, temperament, mood, "
                                 "injuries, injury_races_remaining, "
                                 "races_completed, career_length, peak_end "
                                 "FROM racers WHERE guild_id = 0")
                        )
                    ).fetchall()

                    for gid in guild_ids:
                        for row in old_racers:
                            old_id = row[0]
                            result = await conn.execute(
                                text(
                                    "INSERT INTO racers "
                                    "(guild_id, name, owner_id, retired, "
                                    "speed, cornering, stamina, temperament, "
                                    "mood, injuries, injury_races_remaining, "
                                    "races_completed, career_length, peak_end) "
                                    "VALUES (:gid, :name, :owner, :retired, "
                                    ":spd, :cor, :sta, :temp, "
                                    ":mood, :inj, :irr, "
                                    ":rc, :cl, :pe)"
                                ),
                                {
                                    "gid": gid, "name": row[2],
                                    "owner": row[3], "retired": row[4],
                                    "spd": row[5], "cor": row[6],
                                    "sta": row[7], "temp": row[8],
                                    "mood": row[9], "inj": row[10],
                                    "irr": row[11], "rc": row[12],
                                    "cl": row[13], "pe": row[14],
                                },
                            )
                            new_id = result.lastrowid
                            # Fix race_entries for this guild's races
                            await conn.execute(
                                text(
                                    "UPDATE race_entries SET racer_id = :new "
                                    "WHERE racer_id = :old AND race_id IN "
                                    "(SELECT id FROM races WHERE guild_id = :gid)"
                                ),
                                {"new": new_id, "old": old_id, "gid": gid},
                            )
                            # Fix bets for this guild's races
                            await conn.execute(
                                text(
                                    "UPDATE bets SET racer_id = :new "
                                    "WHERE racer_id = :old AND race_id IN "
                                    "(SELECT id FROM races WHERE guild_id = :gid)"
                                ),
                                {"new": new_id, "old": old_id, "gid": gid},
                            )
                    # Remove the original guild_id=0 racers
                    await conn.execute(
                        text("DELETE FROM racers WHERE guild_id = 0")
                    )

            race_columns = await conn.run_sync(
                lambda c: get_table_columns(c, "races")
            )
            if "winner_id" not in race_columns:
                await conn.execute(
                    text("ALTER TABLE races ADD COLUMN winner_id INTEGER DEFAULT NULL")
                )

            bet_columns = await conn.run_sync(
                lambda c: get_table_columns(c, "bets")
            )
            if "payout_multiplier" not in bet_columns:
                await conn.execute(
                    text(
                        "ALTER TABLE bets ADD COLUMN payout_multiplier FLOAT DEFAULT 2.0"
                    )
                )
            bet_migrations = {
                "bet_type": ("VARCHAR", "'win'"),
                "racer_ids": ("VARCHAR", "'[]'"),
                "is_free": ("BOOLEAN", "0"),
            }
            for name, (col_type, default) in bet_migrations.items():
                if name not in bet_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE bets ADD COLUMN {name} {col_type} DEFAULT {default}"
                        )
                    )

            race_migrations = {
                "placements": ("VARCHAR", "NULL"),
                "map_name": ("VARCHAR", "NULL"),
                "biggest_payout": ("INTEGER", "NULL"),
                "biggest_payout_user_id": ("INTEGER", "NULL"),
                "biggest_payout_racer_id": ("INTEGER", "NULL"),
            }
            for name, (col_type, default) in race_migrations.items():
                if name not in race_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE races ADD COLUMN {name} {col_type} DEFAULT {default}"
                        )
                    )

            # Add ownership/pool columns to guild_settings
            gs_columns = await conn.run_sync(
                lambda c: get_table_columns(c, "guild_settings")
            )
            gs_migrations = {
                "racer_buy_base": ("INTEGER", "NULL"),
                "racer_buy_multiplier": ("INTEGER", "NULL"),
                "racer_sell_fraction": ("FLOAT", "NULL"),
                "max_racers_per_owner": ("INTEGER", "NULL"),
                "min_pool_size": ("INTEGER", "NULL"),
                "placement_prizes": ("VARCHAR", "NULL"),
                "training_base": ("INTEGER", "NULL"),
                "training_multiplier": ("INTEGER", "NULL"),
                "rest_cost": ("INTEGER", "NULL"),
                "feed_cost": ("INTEGER", "NULL"),
                "stable_upgrade_costs": ("VARCHAR", "NULL"),
                "female_buy_multiplier": ("FLOAT", "NULL"),
                "retired_sell_penalty": ("FLOAT", "NULL"),
                "foal_sell_penalty": ("FLOAT", "NULL"),
                "min_training_to_race": ("INTEGER", "NULL"),
                "breeding_fee": ("INTEGER", "NULL"),
                "breeding_cooldown": ("INTEGER", "NULL"),
                "min_races_to_breed": ("INTEGER", "NULL"),
                "max_foals_per_female": ("INTEGER", "NULL"),
                "racer_flavor": ("TEXT", "NULL"),
                "race_stat_window": ("INTEGER", "NULL"),
                "daily_min": ("INTEGER", "NULL"),
                "daily_max": ("INTEGER", "NULL"),
                "racer_emoji": ("TEXT", "NULL"),
                "max_trains_per_race": ("INTEGER", "NULL"),
                # Per-game channel restrictions
                "derby_channel": ("TEXT", "NULL"),
                "brewing_channel": ("TEXT", "NULL"),
                "fishing_channel": ("TEXT", "NULL"),
                "dungeon_channel": ("TEXT", "NULL"),
                # Fishing (Lazy Lures)
                "fishing_bait_costs": ("TEXT", "NULL"),
                "fishing_cast_multiplier": ("REAL", "NULL"),
            }
            for col_name, (col_type, default) in gs_migrations.items():
                if col_name not in gs_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE guild_settings ADD COLUMN "
                            f"{col_name} {col_type} DEFAULT {default}"
                        )
                    )
            # Add fishing_xp column to fishing_players if missing
            tables = await conn.run_sync(_get_tables)
            if "fishing_players" in tables:
                fp_cols = await conn.run_sync(
                    lambda c: get_table_columns(c, "fishing_players")
                )
                if "fishing_xp" not in fp_cols:
                    await conn.execute(
                        text(
                            "ALTER TABLE fishing_players "
                            "ADD COLUMN fishing_xp INTEGER DEFAULT 0"
                        )
                    )

            # Add thread_id column to dungeon_runs if missing
            if "dungeon_runs" in tables:
                dr_cols = await conn.run_sync(
                    lambda c: get_table_columns(c, "dungeon_runs")
                )
                if "thread_id" not in dr_cols:
                    await conn.execute(
                        text(
                            "ALTER TABLE dungeon_runs "
                            "ADD COLUMN thread_id INTEGER DEFAULT NULL"
                        )
                    )

        # Seed brewing reference data (ingredients + dangerous triples)
        async with self.sessionmaker() as session:
            from brewing.seed_data import seed_if_empty

            await seed_if_empty(session)

        self._initialized = True

    async def _run(self) -> None:
        await self.tick()

    async def tick(self) -> None:
        """Run the pending race for each guild, then create the next one."""
        await self._init_db()

        for guild in self.bot.guilds:
            await self._expire_pool_racers(guild.id)
            await self._replenish_pool(guild.id)
            # Find the pending race (created after the last race finished)
            async with self.sessionmaker() as session:
                result = await session.execute(
                    select(models.Race).where(
                        models.Race.guild_id == guild.id,
                        models.Race.finished.is_(False),
                    ).order_by(models.Race.id)
                )
                race = result.scalars().first()

            if race is None or race.id in self.active_races:
                continue

            # Load stored participants
            async with self.sessionmaker() as session:
                participants = await repo.get_race_participants(session, race.id)

            if len(participants) < 2:
                continue

            await self._run_race(race.id, guild.id, participants)

            # Create the next race with pre-picked participants
            await self._create_next_race(guild.id)

    @staticmethod
    def _pick_competitive_field(
        racers: list[models.Racer],
        max_racers: int,
        window_size: int,
    ) -> list[models.Racer] | None:
        """Pick a competitive field of racers within a stat-total window.

        Owned racers in the window are auto-included (max 1 per owner).
        Remaining slots are filled with unowned pool racers.

        Tries three times with decreasing minimums (max_racers, 4, 2).
        Returns ``None`` if even 2 racers can't be found.
        """
        totals = {r.id: r.speed + r.cornering + r.stamina for r in racers}
        min_total = min(totals.values())
        max_total = max(totals.values())

        # Clamp window so it doesn't exceed the stat spread
        effective_window = min(window_size, max_total - min_total)

        thresholds = [max_racers, 4, 2]
        for minimum in thresholds:
            # Pick a random window start
            if max_total - min_total <= effective_window:
                window_start = min_total
            else:
                window_start = random.randint(
                    min_total, max_total - effective_window
                )
            window_end = window_start + effective_window

            in_window = [
                r for r in racers
                if window_start <= totals[r.id] <= window_end
            ]

            # Separate owned vs unowned
            owned = [r for r in in_window if r.owner_id != 0]
            unowned = [r for r in in_window if r.owner_id == 0]

            # Deduplicate owners: pick 1 racer per owner
            by_owner: dict[int, list[models.Racer]] = {}
            for r in owned:
                by_owner.setdefault(r.owner_id, []).append(r)
            owner_picks = [random.choice(rs) for rs in by_owner.values()]

            # If owned alone exceed max, randomly trim (still 1 per owner)
            if len(owner_picks) > max_racers:
                owner_picks = random.sample(owner_picks, max_racers)

            remaining_slots = max_racers - len(owner_picks)
            pool_picks = random.sample(
                unowned, min(remaining_slots, len(unowned))
            )

            field = owner_picks + pool_picks
            if len(field) >= minimum:
                return field

        return None

    async def _create_next_race(self, guild_id: int) -> models.Race | None:
        """Create a pending race for a guild and pre-pick its participants."""
        gs = await self._load_guild_settings(guild_id)
        min_train = self._resolve("min_training_to_race", gs)
        async with self.sessionmaker() as session:
            racers = await repo.get_guild_racers(
                session, guild_id, min_training=min_train,
            )

        if len(racers) < 2:
            return None

        max_racers = self._resolve("max_racers_per_race", gs)
        window_size = self._resolve("race_stat_window", gs)
        participants = self._pick_competitive_field(
            racers, max_racers, window_size
        )

        if participants is None:
            self.bot.logger.warning(
                "Not enough racers for a competitive race",
                extra={"guild_id": guild_id},
            )
            return None

        race_map = logic.pick_map()
        async with self.sessionmaker() as session:
            race = await repo.create_race(
                session, guild_id=guild_id,
                map_name=race_map.name if race_map else None,
            )
            await repo.create_race_entries(
                session, race.id, [r.id for r in participants]
            )

        self.bot.logger.info(
            "Next race created with %d participants",
            len(participants),
            extra={"guild_id": guild_id, "race_id": race.id},
        )
        return race

    async def _ensure_flavor_names(self, guild_id: int) -> None:
        """Generate flavor-specific racer names if needed.

        If the guild has a ``racer_flavor`` set and no flavor names file
        exists yet, call the LLM to generate themed names.
        """
        gs = await self._load_guild_settings(guild_id)
        racer_flavor = self._resolve("racer_flavor", gs)
        if not racer_flavor:
            return

        existing = flavor_names.load_flavor_names(guild_id)
        if existing:
            return  # already generated

        self.bot.logger.info(
            "Generating flavor names for theme: %s",
            racer_flavor,
            extra={"guild_id": guild_id},
        )
        names = await flavor_names.generate_flavor_names(racer_flavor)
        if names:
            flavor_names.save_flavor_names(guild_id, names)
            self.bot.logger.info(
                "Saved %d flavor names",
                len(names),
                extra={"guild_id": guild_id},
            )
        else:
            self.bot.logger.warning(
                "Failed to generate flavor names — using base names only",
                extra={"guild_id": guild_id},
            )

    async def _ensure_guild_npcs(self, guild_id: int) -> None:
        """Generate NPC trainers for a guild if it has a racer_flavor and no NPCs.

        Each NPC gets 2 racers (one per rank in their band) and a pool of quips.
        """
        gs = await self._load_guild_settings(guild_id)
        racer_flavor = self._resolve("racer_flavor", gs)
        if not racer_flavor:
            return

        async with self.sessionmaker() as session:
            existing = await repo.get_guild_npcs(session, guild_id)
            if existing:
                return  # NPCs already generated

        self.bot.logger.info(
            "Generating NPC trainers for theme: %s",
            racer_flavor,
            extra={"guild_id": guild_id},
        )

        npcs_data = await npc_generation.generate_guild_npcs(racer_flavor)
        if not npcs_data:
            self.bot.logger.warning(
                "Failed to generate NPCs — skipping",
                extra={"guild_id": guild_id},
            )
            return

        from . import descriptions

        async with self.sessionmaker() as session:
            for npc_data in npcs_data:
                # Generate quips
                win_quips = await npc_generation.generate_npc_quips(
                    npc_data["name"], npc_data["personality_desc"],
                    racer_flavor, "win", count=20,
                )
                loss_quips = await npc_generation.generate_npc_quips(
                    npc_data["name"], npc_data["personality_desc"],
                    racer_flavor, "loss", count=15,
                )

                npc = await repo.create_npc(
                    session,
                    guild_id=guild_id,
                    name=npc_data["name"],
                    personality=npc_data["personality"],
                    personality_desc=npc_data["personality_desc"],
                    rank_min=npc_data["rank_min"],
                    rank_max=npc_data["rank_max"],
                    win_quips=json.dumps(win_quips or []),
                    loss_quips=json.dumps(loss_quips or []),
                    emoji=npc_data.get("emoji", ""),
                    catchphrase=npc_data.get("catchphrase", ""),
                )

                # Create 2 racers for this NPC
                for rank_key, name_key in [
                    ("rank_min", "racer1_name"),
                    ("rank_max", "racer2_name"),
                ]:
                    rank = npc_data[rank_key]
                    racer_name = npc_data[name_key]
                    stats = npc_generation.generate_racer_stats_for_rank(rank)
                    temperament = random.choice(npc_generation.TEMPERAMENTS)
                    gender = random.choice(["M", "F"])

                    racer = await repo.create_racer(
                        session,
                        name=racer_name,
                        owner_id=0,
                        guild_id=guild_id,
                        speed=stats["speed"],
                        cornering=stats["cornering"],
                        stamina=stats["stamina"],
                        temperament=temperament,
                        gender=gender,
                        rank=rank,
                        npc_id=npc.id,
                    )

                    # Generate description if flavor is set
                    try:
                        desc = await descriptions.generate_description(
                            racer, racer_flavor
                        )
                        if desc:
                            await repo.update_racer(
                                session, racer.id, description=desc
                            )
                    except Exception:
                        pass  # Description is optional

                self.bot.logger.info(
                    "Created NPC: %s (%s, %s-%s rank)",
                    npc_data["name"],
                    npc_data["personality"],
                    npc_data["rank_min"],
                    npc_data["rank_max"],
                    extra={"guild_id": guild_id},
                )

    async def _ensure_pending_races(self) -> None:
        """Ensure each guild has a pending race with participants.

        Called on startup so there's always something for /race upcoming.
        """
        for guild in self.bot.guilds:
            await self._ensure_flavor_names(guild.id)
            await self._ensure_guild_npcs(guild.id)
            await self._replenish_pool(guild.id)

            async with self.sessionmaker() as session:
                result = await session.execute(
                    select(models.Race).where(
                        models.Race.guild_id == guild.id,
                        models.Race.finished.is_(False),
                    )
                )
                pending = result.scalars().first()

            if pending is not None:
                # Check if it has participants; backfill if not (legacy race)
                async with self.sessionmaker() as session:
                    entries = await repo.get_race_entries(session, pending.id)
                if not entries:
                    await self._backfill_race_entries(pending)
                continue

            await self._create_next_race(guild.id)

        # Generate daily rewards for today if not already done (startup catch-up)
        await self._generate_dailies()

    async def _daily_tick(self) -> None:
        """Called at midnight UTC.  Generate daily rewards for all players."""
        await self._init_db()
        await self._generate_dailies()

    async def _generate_dailies(self) -> None:
        """Pre-generate today's daily rewards for all players in all guilds."""
        from . import descriptions

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for guild in self.bot.guilds:
            gs = await self._load_guild_settings(guild.id)
            daily_min = self._resolve("daily_min", gs)
            daily_max = self._resolve("daily_max", gs)
            racer_flavor = self._resolve("racer_flavor", gs)

            async with self.sessionmaker() as session:
                # Get all players who own non-retired racers
                owner_ids = await repo.get_racer_owner_ids(session, guild.id)

                for owner_id in owner_ids:
                    # Skip if already generated for today
                    existing = await repo.get_daily_reward(
                        session, owner_id, guild.id, today
                    )
                    if existing is not None:
                        continue

                    # Find their best racer
                    racers = await repo.get_owned_racers(
                        session, owner_id, guild.id
                    )
                    if not racers:
                        continue

                    best = max(racers, key=lambda r: logic._racer_power(r))
                    rank = best.rank or "D"
                    multiplier = logic.daily_rank_multiplier(rank)
                    base = random.randint(daily_min, daily_max)
                    amount = base * multiplier

                    # Generate flavor text
                    flavor_text = None
                    if racer_flavor:
                        try:
                            flavor_text = await descriptions.generate_daily_flavor(
                                best.name, rank, amount, racer_flavor,
                            )
                        except Exception:
                            pass  # Fall through to generic

                    if not flavor_text:
                        flavor_text = (
                            f"{best.name} found something worth **{amount} coins** "
                            f"while out exploring!"
                        )

                    await repo.create_daily_reward(
                        session,
                        user_id=owner_id,
                        guild_id=guild.id,
                        date=today,
                        racer_id=best.id,
                        racer_name=best.name,
                        amount=amount,
                        flavor_text=flavor_text,
                    )

                # Also generate for players with wallets but no racers
                from economy.models import Wallet
                wallet_result = await session.execute(
                    select(Wallet.user_id).where(
                        Wallet.guild_id == guild.id,
                    )
                )
                wallet_user_ids = {row[0] for row in wallet_result.all()}
                no_racer_ids = wallet_user_ids - set(owner_ids)

                for user_id in no_racer_ids:
                    existing = await repo.get_daily_reward(
                        session, user_id, guild.id, today
                    )
                    if existing is not None:
                        continue

                    amount = random.randint(daily_min, daily_max)
                    flavor_text = (
                        f"You scavenged **{amount} coins** from around the track."
                    )
                    await repo.create_daily_reward(
                        session,
                        user_id=user_id,
                        guild_id=guild.id,
                        date=today,
                        amount=amount,
                        flavor_text=flavor_text,
                    )

    async def _digest_tick(self) -> None:
        """Called at 00:05 UTC.  Post daily digest to all guild channels."""
        await self._init_db()
        for guild in self.bot.guilds:
            try:
                gs = await self._load_guild_settings(guild.id)
                embed = await self._build_digest_embed(guild.id, guild_settings=gs)
                if embed is None:
                    continue
                channel = self._get_channel(guild, gs)
                if channel is None:
                    continue
                await channel.send(embed=embed)
            except Exception:
                self.bot.logger.exception(
                    "Failed to post daily digest",
                    extra={"guild_id": guild.id},
                )

    async def _build_digest_embed(self, guild_id: int, guild_settings: models.GuildSettings | None = None) -> discord.Embed | None:
        """Build the daily digest embed for a guild.

        Returns ``None`` if there's nothing to show (no players in guild).
        """
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        weekday = now.strftime("%A")
        date_str = now.strftime("%B %d, %Y")

        embed = discord.Embed(
            title=f"\U0001f4dc Daily Digest — {weekday}, {date_str}",
            color=0x5865F2,
        )

        # 1. Daily reward reminder (always present)
        embed.add_field(
            name="\U0001f381 Daily Reward",
            value="Your daily reward is ready! Use `/daily` to claim it.",
            inline=False,
        )

        # 2 & 3. Yesterday's races — biggest payout & longshot winner
        yesterday_start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_end = now.replace(hour=0, minute=0, second=0, microsecond=0)

        async with self.sessionmaker() as session:
            yesterday_races = await repo.get_races_finished_between(
                session, guild_id, yesterday_start, yesterday_end
            )

            if yesterday_races:
                # Biggest payout
                best_race = None
                for race in yesterday_races:
                    bp = getattr(race, "biggest_payout", None)
                    if bp and bp > 0:
                        if best_race is None or bp > best_race.biggest_payout:
                            best_race = race

                if best_race is not None:
                    racer = await repo.get_racer(
                        session, best_race.biggest_payout_racer_id
                    )
                    racer_name = racer.name if racer else "Unknown"
                    embed.add_field(
                        name="\U0001f4b0 Yesterday's Best Payout",
                        value=(
                            f"<@{best_race.biggest_payout_user_id}> won "
                            f"**{best_race.biggest_payout} coins** betting on "
                            f"**{racer_name}**!"
                        ),
                        inline=False,
                    )

                # Longshot winner — highest odds (multiplier) winner across yesterday's races
                best_longshot_name = None
                best_longshot_mult = 0.0

                for race in yesterday_races:
                    if race.winner_id is None:
                        continue
                    participants = await repo.get_race_participants(
                        session, race.id
                    )
                    if len(participants) < 2:
                        continue
                    odds = logic.calculate_odds(participants, [], 0.1)
                    winner_mult = odds.get(race.winner_id, 0.0)
                    if winner_mult > best_longshot_mult:
                        best_longshot_mult = winner_mult
                        winner_racer = next(
                            (r for r in participants if r.id == race.winner_id),
                            None,
                        )
                        best_longshot_name = (
                            winner_racer.name if winner_racer else None
                        )

                if best_longshot_name:
                    embed.add_field(
                        name=f"{self._resolve('racer_emoji', guild_settings)} Yesterday's Longshot Winner",
                        value=f"**{best_longshot_name}** defied the odds and took the win!",
                        inline=False,
                    )

        # 4. Tournament section (day-of-week dependent)
        day_of_week = now.weekday()  # 0=Mon ... 6=Sun
        tournament_text = await self._build_tournament_digest(
            guild_id, day_of_week
        )
        if tournament_text:
            embed.add_field(
                name="\U0001f3c6 Tournaments",
                value=tournament_text,
                inline=False,
            )

        fishing_text = await self._build_fishing_digest(guild_id)
        if fishing_text:
            embed.add_field(
                name="\U0001f3a3 Fishing",
                value=fishing_text,
                inline=False,
            )

        return embed

    async def _build_tournament_digest(
        self, guild_id: int, day_of_week: int
    ) -> str | None:
        """Return tournament-related text for the digest, or None if not relevant."""
        # Friday=4: preview weekend tournaments
        # Saturday=5: D/C counts + B/A reminder
        # Sunday=6: B/A counts + S reminder
        if day_of_week == 4:  # Friday
            return (
                "Weekend tournaments start tomorrow! "
                "Register your racers with `/tournament register`\n"
                "📅 **Saturday:** D & C rank\n"
                "📅 **Sunday:** B & A rank\n"
                "📅 **Monday:** S rank"
            )

        if day_of_week == 5:  # Saturday
            lines = ["Today's tournaments: **D & C rank**"]
            async with self.sessionmaker() as session:
                for rank in ("D", "C"):
                    t = await repo.get_pending_tournament(session, guild_id, rank)
                    if t:
                        entries = await repo.get_tournament_entries(session, t.id)
                        player_count = sum(
                            1 for e in entries if not e.is_pool_filler
                        )
                        lines.append(
                            f"  {rank}-Rank: **{player_count}** registered"
                        )
            lines.append("\n📅 **Tomorrow:** B & A rank tournaments")
            return "\n".join(lines)

        if day_of_week == 6:  # Sunday
            lines = ["Today's tournaments: **B & A rank**"]
            async with self.sessionmaker() as session:
                for rank in ("B", "A"):
                    t = await repo.get_pending_tournament(session, guild_id, rank)
                    if t:
                        entries = await repo.get_tournament_entries(session, t.id)
                        player_count = sum(
                            1 for e in entries if not e.is_pool_filler
                        )
                        lines.append(
                            f"  {rank}-Rank: **{player_count}** registered"
                        )
            lines.append("\n📅 **Tomorrow:** S rank tournament")
            return "\n".join(lines)

        return None

    async def _build_fishing_digest(self, guild_id: int) -> str | None:
        """Return fishing stats for yesterday, or None if no activity."""
        from datetime import timedelta as _td

        from fishing import repositories as fish_repo

        yesterday = (datetime.now(timezone.utc) - _td(days=1)).strftime("%Y-%m-%d")
        async with self.sessionmaker() as session:
            summaries = await fish_repo.get_guild_daily_summaries(
                session, guild_id, yesterday
            )

        if not summaries:
            return None

        total_fish = sum(s.total_fish for s in summaries)
        total_coins = sum(s.total_coins for s in summaries)
        if total_fish == 0:
            return None

        lines = [f"**{total_fish}** fish caught for **{total_coins}** coins"]

        # Most active angler
        top_angler = max(summaries, key=lambda s: s.total_fish)
        lines.append(f"Top angler: <@{top_angler.user_id}> ({top_angler.total_fish} fish)")

        # Biggest catch (by length)
        with_length = [s for s in summaries if s.biggest_catch_length]
        if with_length:
            biggest = max(with_length, key=lambda s: s.biggest_catch_length)
            lines.append(
                f"Biggest catch: **{biggest.biggest_catch_name}** "
                f"({biggest.biggest_catch_length}in) by <@{biggest.user_id}>"
            )

        return "\n".join(lines)

    async def _expire_pool_racers(self, guild_id: int) -> int:
        """Delete unowned pool racers whose expiry time has passed."""
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(models.Racer).where(
                    models.Racer.guild_id == guild_id,
                    models.Racer.owner_id == 0,
                    models.Racer.pool_expires_at <= func.now(),
                )
            )
            expired = result.scalars().all()
            for racer in expired:
                await session.delete(racer)
            if expired:
                await session.commit()
                self.bot.logger.info(
                    "Expired %d pool racers",
                    len(expired),
                    extra={"guild_id": guild_id},
                )
        return len(expired)

    async def _replenish_pool(self, guild_id: int) -> int:
        """Ensure the guild has at least ``min_pool_size`` unowned eligible racers.

        Creates up to 5 new racers per call to avoid flooding.
        Returns the number of racers created.
        """
        gs = await self._load_guild_settings(guild_id)
        min_size = self._resolve("min_pool_size", gs)

        async with self.sessionmaker() as session:
            current = await repo.count_unowned_eligible_racers(session, guild_id)

        gap = min_size - current
        if gap <= 0:
            return 0

        to_create = min(gap, 5)  # cap per call

        # Gather taken names for uniqueness
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(models.Racer.name).where(
                    models.Racer.guild_id == guild_id,
                    models.Racer.retired.is_(False),
                )
            )
            taken = {row[0] for row in result.all()}

        created = 0
        for _ in range(to_create):
            kwargs = logic.generate_pool_racer(guild_id, taken)
            taken.add(kwargs["name"])
            async with self.sessionmaker() as session:
                await repo.create_racer(session, **kwargs)
            created += 1

        if created:
            self.bot.logger.info(
                "Replenished pool with %d racers (had %d, target %d)",
                created, current, min_size,
                extra={"guild_id": guild_id},
            )
        return created

    async def _backfill_race_entries(self, race: models.Race) -> None:
        """Add participants to a legacy pending race that has none."""
        gs = await self._load_guild_settings(race.guild_id)
        max_racers = self._resolve("max_racers_per_race", gs)
        min_train = self._resolve("min_training_to_race", gs)
        window_size = self._resolve("race_stat_window", gs)
        async with self.sessionmaker() as session:
            racers = await repo.get_guild_racers(
                session, race.guild_id, min_training=min_train,
            )
            if len(racers) < 2:
                return
            participants = self._pick_competitive_field(
                racers, max_racers, window_size
            )
            if participants is None:
                return
            await repo.create_race_entries(
                session, race.id, [r.id for r in participants]
            )

    async def _run_race(
        self, race_id: int, guild_id: int, participants: list[models.Racer]
    ) -> None:
        if race_id in self.active_races:
            return  # another coroutine is already handling this race
        self.active_races.add(race_id)
        try:
            await self._run_race_inner(race_id, guild_id, participants)
        finally:
            self.active_races.discard(race_id)

    async def _run_race_inner(
        self, race_id: int, guild_id: int, participants: list[models.Racer]
    ) -> None:
        gs = await self._load_guild_settings(guild_id)
        # Use the pre-picked map stored on the race, fall back to a random pick
        async with self.sessionmaker() as session:
            race_obj = await repo.get_race(session, race_id)
        race_map = None
        if race_obj and race_obj.map_name:
            race_map = logic.get_map_by_name(race_obj.map_name)
        if race_map is None:
            race_map = logic.pick_map()
        await self._announce_race_start(
            guild_id, race_id, participants, race_map=race_map,
            guild_settings=gs,
        )
        await asyncio.sleep(self._resolve("bet_window", gs))
        self.bot.logger.info(
            "Race starting",
            extra={"guild_id": guild_id, "race_id": race_id},
        )
        # Load potion buffs for race participants
        racer_ids = [r.id for r in participants]
        async with self.sessionmaker() as session:
            raw_buffs = await repo.get_race_buffs_for_racers(session, racer_ids)
        stat_buffs, mood_buffs = logic.convert_buffs(raw_buffs)

        result = logic.simulate_race(
            {"racers": participants}, race_id, race_map=race_map,
            stat_buffs=stat_buffs, mood_buffs=mood_buffs,
        )
        winner_id = result.placements[0] if result.placements else None
        placements_json = json.dumps(result.placements)
        async with self.sessionmaker() as session:
            await repo.update_race(
                session, race_id, finished=True, winner_id=winner_id,
                placements=placements_json,
            )
            bets = (
                (
                    await session.execute(
                        select(models.Bet).where(models.Bet.race_id == race_id)
                    )
                )
                .scalars()
                .all()
            )
            bet_results = await logic.resolve_payouts(
                session, race_id, result.placements, guild_id=guild_id
            )
            prize_list = logic.parse_placement_prizes(
                self._resolve("placement_prizes", gs)
            )
            placement_awards = await logic.resolve_placement_prizes(
                session, result.placements, participants,
                guild_id=guild_id, prize_list=prize_list,
            )
            mood_changes = await logic.apply_mood_drift(
                session, result.placements, participants
            )
            new_injuries = logic.check_injury_risk(result)
            await logic.apply_injuries(session, new_injuries, participants)
            healed = await self._tick_injury_recovery(session, guild_id)
            await self._increment_careers(session, participants)
            await self._tick_breed_cooldowns(session, guild_id)
            retirements = await self._apply_retirements(
                session, participants, guild_id=guild_id
            )
            stat_gains = await logic.apply_placement_stat_gains(
                session, result.placements, participants, race_map, prize_list,
            )
            # Consume potion buffs after race
            await repo.consume_racer_buffs(session, racer_ids)
            await session.commit()

        # Reset training counters for all guild racers
        async with self.sessionmaker() as session:
            await session.execute(
                text(
                    "UPDATE racers SET trains_since_race = 0 "
                    "WHERE guild_id = :gid AND trains_since_race > 0"
                ),
                {"gid": guild_id},
            )
            await session.commit()

        names = result.racer_names

        # Show a "getting ready" message while LLM generates commentary
        guild = self.bot.get_guild(guild_id)
        if guild:
            channel = self._get_channel(guild, gs)
            if channel:
                # Build lineup with owner names, sorted alphabetically
                npc_names: dict[int, str] = {}
                npc_ids = {r.npc_id for r in participants if r.npc_id}
                if npc_ids:
                    async with self.sessionmaker() as session:
                        for npc_id in npc_ids:
                            npc = await repo.get_npc(session, npc_id)
                            if npc:
                                prefix = f"{npc.emoji} " if npc.emoji else ""
                                npc_names[npc_id] = f"{prefix}{npc.name}"

                owner_names: dict[int, str] = {}
                player_ids = {
                    r.owner_id for r in participants
                    if r.owner_id and r.owner_id != 0 and not r.npc_id
                }
                for pid in player_ids:
                    try:
                        member = guild.get_member(pid) or await guild.fetch_member(pid)
                        owner_names[pid] = member.display_name
                    except (discord.NotFound, discord.HTTPException):
                        owner_names[pid] = f"Player #{pid}"

                sorted_participants = sorted(participants, key=lambda r: r.name.lower())
                lineup_lines = []
                for r in sorted_participants:
                    name = r.name
                    if r.npc_id and r.npc_id in npc_names:
                        owner_tag = npc_names[r.npc_id]
                    elif r.owner_id and r.owner_id != 0:
                        owner_tag = owner_names.get(r.owner_id, f"Player #{r.owner_id}")
                    else:
                        owner_tag = "Unowned"
                    lineup_lines.append(f"**{name}** ({owner_tag})")

                lineup = "\n".join(lineup_lines)
                track_info = f" on **{result.map_name}**" if result.map_name else ""
                ready_embed = discord.Embed(
                    title=f"{self._resolve('racer_emoji', gs)} Racers Getting Ready!",
                    description=(
                        f"The racers line up{track_info}!\n\n"
                        f"{lineup}\n\n"
                        f"*The race is about to begin...*"
                    ),
                    color=0xFFAA00,
                )
                try:
                    await channel.send(embed=ready_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

                await asyncio.sleep(3)

        log = await commentary.generate_commentary(result)
        if log is None:
            log = commentary.build_template_commentary(result)
        await self._stream_commentary(
            race_id, guild_id, log,
            delay=self._resolve("commentary_delay", gs),
            guild_settings=gs,
        )
        await self._post_results(guild_id, result.placements, names)
        await self._announce_bet_results(
            guild_id, bet_results, names
        )
        await self._dm_payouts(bet_results, race_id, names)
        if new_injuries:
            await self._announce_injuries(guild_id, new_injuries, names)
        if retirements:
            await self._announce_retirements(guild_id, retirements)
        if healed:
            await self._announce_healed(guild_id, healed)
        if placement_awards:
            await self._announce_placement_prizes(
                guild_id, placement_awards, names,
                stat_gains=stat_gains,
            )
        self.bot.logger.info(
            "Race finished",
            extra={"guild_id": guild_id, "race_id": race_id},
        )

    MEDAL_EMOJI = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}

    async def _post_results(
        self,
        guild_id: int,
        placements: list[int],
        names: dict[int, str] | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        if names is None:
            async with self.sessionmaker() as session:
                racers = (
                    (
                        await session.execute(
                            select(models.Racer).where(
                                models.Racer.id.in_(placements)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            names = {r.id: r.name for r in racers}

        results_lines: list[str] = []
        for i, rid in enumerate(placements, start=1):
            medal = self.MEDAL_EMOJI.get(i, f"**{i}.**")
            racer_name = names.get(rid, f"Racer {rid}")
            results_lines.append(f"{medal} {racer_name}")

        winner_name = names.get(placements[0], "Unknown") if placements else "Unknown"
        embed = discord.Embed(
            title="\U0001f3c1 Race Complete!",
            description="\n".join(results_lines),
            color=0xF1C40F,
        )
        embed.add_field(
            name="\U0001f3c6 Winner",
            value=f"**{winner_name}**",
            inline=False,
        )

        # NPC trainer reactions
        npc_reactions = await self._get_npc_reactions(placements)
        if npc_reactions:
            embed.add_field(
                name="\U0001f4ac Trainer Reactions",
                value="\n".join(npc_reactions),
                inline=False,
            )

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _get_npc_reactions(
        self, placements: list[int]
    ) -> list[str]:
        """Check if any NPC racers won or finished last, return quip lines."""
        if not placements:
            return []

        reactions: list[str] = []
        async with self.sessionmaker() as session:
            # Check winner (1st place)
            winner_racer = await repo.get_racer(session, placements[0])
            if winner_racer and winner_racer.npc_id:
                npc = await repo.get_npc(session, winner_racer.npc_id)
                if npc:
                    quips = npc_quips.parse_quips(npc.win_quips)
                    used = npc_quips.parse_used(npc.win_quips_used)
                    if quips:
                        quip, used = npc_quips.pick_quip(quips, used)
                        await repo.update_npc(
                            session, npc.id,
                            win_quips_used=json.dumps(used),
                        )
                        emoji = f"{npc.emoji} " if npc.emoji else ""
                        reactions.append(f"{emoji}**{npc.name}:** \"{quip}\"")
                        # Fire-and-forget quip regeneration check
                        if npc_quips.should_regenerate(quips, used):
                            gs = await self._load_guild_settings(
                                winner_racer.guild_id
                            )
                            flavor = self._resolve("racer_flavor", gs) or ""
                            asyncio.create_task(
                                self._regenerate_npc_quips(
                                    npc, flavor, "win"
                                )
                            )

            # Check last place (40% chance)
            if len(placements) >= 2:
                last_racer = await repo.get_racer(session, placements[-1])
                if last_racer and last_racer.npc_id and random.random() < 0.4:
                    npc = await repo.get_npc(session, last_racer.npc_id)
                    if npc:
                        quips = npc_quips.parse_quips(npc.loss_quips)
                        used = npc_quips.parse_used(npc.loss_quips_used)
                        if quips:
                            quip, used = npc_quips.pick_quip(quips, used)
                            await repo.update_npc(
                                session, npc.id,
                                loss_quips_used=json.dumps(used),
                            )
                            emoji = f"{npc.emoji} " if npc.emoji else ""
                            reactions.append(
                                f"{emoji}**{npc.name}:** \"{quip}\""
                            )
                            if npc_quips.should_regenerate(quips, used):
                                gs = await self._load_guild_settings(
                                    last_racer.guild_id
                                )
                                flavor = self._resolve("racer_flavor", gs) or ""
                                asyncio.create_task(
                                    self._regenerate_npc_quips(
                                        npc, flavor, "loss"
                                    )
                                )

        return reactions

    async def _regenerate_npc_quips(
        self, npc: models.NPC, racer_flavor: str, quip_type: str
    ) -> None:
        """Background task to regenerate quips for an NPC when pool is exhausted."""
        try:
            if quip_type == "win":
                existing = npc_quips.parse_quips(npc.win_quips)
            else:
                existing = npc_quips.parse_quips(npc.loss_quips)

            new_quips = await npc_generation.generate_npc_quips(
                npc.name, npc.personality_desc, racer_flavor,
                quip_type, count=20 if quip_type == "win" else 15,
                existing_quips=existing,
            )
            if new_quips:
                async with self.sessionmaker() as session:
                    if quip_type == "win":
                        await repo.update_npc(
                            session, npc.id,
                            win_quips=json.dumps(new_quips),
                            win_quips_used="[]",
                        )
                    else:
                        await repo.update_npc(
                            session, npc.id,
                            loss_quips=json.dumps(new_quips),
                            loss_quips_used="[]",
                        )
                self.bot.logger.info(
                    "Regenerated %s quips for NPC %s",
                    quip_type, npc.name,
                )
        except Exception:
            self.bot.logger.exception(
                "Failed to regenerate quips for NPC %s", npc.name
            )

    BET_TYPE_LABELS = {
        "win": "Win",
        "place": "Place",
        "exacta": "Exacta",
        "trifecta": "Trifecta",
        "superfecta": "Superfecta",
    }

    async def _announce_bet_results(
        self,
        guild_id: int,
        bet_results: list[dict],
        names: dict[int, str] | None = None,
    ) -> None:
        """Announce bet outcomes to the race channel."""
        if not bet_results:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        gs = await self._load_guild_settings(guild_id)
        channel = self._get_channel(guild, gs)
        if channel is None:
            return

        names = names or {}
        winners: list[str] = []
        losers: list[str] = []
        for br in bet_results:
            label = self.BET_TYPE_LABELS.get(br["bet_type"], br["bet_type"])
            free_tag = " \U0001f193" if br.get("is_free") else ""
            racer_name = names.get(br["racer_id"], f"Racer {br['racer_id']}")
            if br["won"]:
                winners.append(
                    f"<@{br['user_id']}> won **{br['payout']} coins** "
                    f"({label}{free_tag} on **{racer_name}**)"
                )
            elif br.get("is_free"):
                losers.append(
                    f"<@{br['user_id']}> \u2014 free bet on "
                    f"**{racer_name}** (no coins lost)"
                )
            else:
                losers.append(
                    f"<@{br['user_id']}> lost **{br['amount']} coins** "
                    f"({label} on **{racer_name}**)"
                )

        if not winners and not losers:
            return

        lines: list[str] = []
        if winners:
            lines.append("**Winners:**")
            lines.extend(winners)
        if losers:
            if winners:
                lines.append("")
            lines.append("**Losers:**")
            lines.extend(losers)

        embed = discord.Embed(
            title="\U0001f3b0 Betting Results",
            description="\n".join(lines),
            color=0x2ECC71 if winners else 0xE74C3C,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _dm_payouts(
        self,
        bet_results: list[dict],
        race_id: int,
        names: dict[int, str] | None = None,
    ) -> None:
        if not bet_results:
            return
        names = names or {}
        for br in bet_results:
            user = self.bot.get_user(br["user_id"])
            if user is None:
                continue
            label = self.BET_TYPE_LABELS.get(br["bet_type"], br["bet_type"])
            racer_name = names.get(br["racer_id"], f"Racer {br['racer_id']}")
            free_tag = " (Free)" if br.get("is_free") else ""
            if br["won"] and br.get("is_free"):
                msg = (
                    f"\U0001f3b0 **{label} Bet**{free_tag} \u2014 Race #{race_id}\n"
                    f"\u2705 The house backed you and you won! "
                    f"**{br['payout']} coins** earned"
                )
            elif br["won"]:
                msg = (
                    f"\U0001f3b0 **{label} Bet** \u2014 Race #{race_id}\n"
                    f"\u2705 Won! {br['amount']} \u00d7 "
                    f"{br['payout'] / br['amount']:.1f}x = "
                    f"**{br['payout']} coins**"
                )
            elif br.get("is_free"):
                msg = (
                    f"\U0001f3b0 **{label} Bet**{free_tag} \u2014 Race #{race_id}\n"
                    f"No luck this time \u2014 but no coins lost. "
                    f"The house covered you."
                )
            else:
                msg = (
                    f"\U0001f3b0 **{label} Bet** \u2014 Race #{race_id}\n"
                    f"\u274c Lost **{br['amount']} coins** "
                    f"on **{racer_name}**"
                )
            try:
                await user.send(msg)
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def _stream_commentary(
        self, race_id: int, guild_id: int, log: list[str], delay: float = 6.0,
        guild_settings: models.GuildSettings | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return

        message: discord.Message | None = None
        lines: list[str] = []

        for i, event in enumerate(log):
            # Check if race was cancelled
            async with self.sessionmaker() as session:
                if await repo.get_race(session, race_id) is None:
                    return

            lines.append(event)
            embed = discord.Embed(
                description="\n\n".join(lines),
                color=0x2ECC71 if i < len(log) - 1 else 0xF1C40F,
            )

            try:
                if message is None:
                    message = await channel.send(embed=embed)
                else:
                    await message.edit(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return

            if i < len(log) - 1:
                await asyncio.sleep(delay)

    async def _increment_careers(
        self, session: AsyncSession, racers: list[models.Racer]
    ) -> None:
        """Increment races_completed for all participants.

        Re-fetches each racer from the current session to ensure the
        change is tracked (participants may be detached from a prior session).
        """
        for racer in racers:
            db_racer = await session.get(models.Racer, racer.id)
            if db_racer is not None:
                db_racer.races_completed += 1
                # Keep the in-memory object in sync for downstream code
                racer.races_completed = db_racer.races_completed

    async def _apply_retirements(
        self,
        session: AsyncSession,
        racers: list[models.Racer],
        guild_id: int = 0,
    ) -> list[models.Racer]:
        """Retire racers that have reached their career_length."""
        retirements: list[models.Racer] = []
        for racer in racers:
            if racer.races_completed >= racer.career_length:
                await repo.update_racer(session, racer.id, retired=True)
                retirements.append(racer)
        return retirements

    async def _announce_race_start(
        self,
        guild_id: int,
        race_id: int,
        racers: list[models.Racer],
        race_map: logic.RaceMap | None = None,
        guild_settings: models.GuildSettings | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild, guild_settings)
        if channel is None:
            return
        odds = logic.calculate_odds(racers, [], 0.1, race_map=race_map)
        minutes = self._resolve("bet_window", guild_settings) // 60
        desc = f"Race {race_id} begins in {minutes} minutes. Place your bets!"
        if race_map:
            layout = " \u2192 ".join(
                f"[{s.type.capitalize()}]" for s in race_map.segments
            )
            desc = (
                f"**Track: {race_map.name}** ({race_map.theme})\n"
                f"{layout}\n\n{desc}"
            )
        embed = discord.Embed(
            title="Race Starting Soon",
            description=desc,
        )
        # Pre-load NPC names for display
        npc_names: dict[int, str] = {}
        npc_ids = {r.npc_id for r in racers if r.npc_id}
        if npc_ids:
            async with self.sessionmaker() as session:
                for npc_id in npc_ids:
                    npc = await repo.get_npc(session, npc_id)
                    if npc:
                        prefix = f"{npc.emoji} " if npc.emoji else ""
                        npc_names[npc_id] = f"{prefix}{npc.name}"

        # Resolve player owner names
        owner_names: dict[int, str] = {}
        player_ids = {
            r.owner_id for r in racers
            if r.owner_id and r.owner_id != 0 and not r.npc_id
        }
        if guild and player_ids:
            for pid in player_ids:
                try:
                    member = guild.get_member(pid) or await guild.fetch_member(pid)
                    owner_names[pid] = member.display_name
                except (discord.NotFound, discord.HTTPException):
                    owner_names[pid] = f"Player #{pid}"

        for r in sorted(racers, key=lambda r: r.name.lower()):
            if r.npc_id and r.npc_id in npc_names:
                owner_tag = npc_names[r.npc_id]
            elif r.owner_id and r.owner_id != 0:
                owner_tag = owner_names.get(r.owner_id, f"Player #{r.owner_id}")
            else:
                owner_tag = "Unowned"
            mult = odds.get(r.id, 0)
            embed.add_field(
                name=f"{r.name} (#{r.id})",
                value=f"{mult:.1f}x \u2014 bet 100, win {int(100 * mult)}\nOwner: {owner_tag}",
                inline=False,
            )
        embed.add_field(
            name="\U0001f3b0 Bet Types",
            value=(
                "**/race bet-win** \u2014 pick the winner\n"
                "**/race bet-place** \u2014 pick 1st or 2nd\n"
                "**/race bet-exacta** \u2014 exact 1st & 2nd\n"
                "**/race bet-trifecta** \u2014 exact 1st, 2nd & 3rd\n"
                "**/race bet-superfecta** \u2014 all 6 in exact order"
            ),
            inline=False,
        )
        embed.set_footer(text="One bet per type \u2014 up to 5 bets per race!")
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _announce_retirements(
        self,
        guild_id: int,
        retirements: list[models.Racer],
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        for racer in retirements:
            if racer.npc_id:
                # NPC racer retirement — create replacement and announce
                await self._handle_npc_retirement(
                    guild_id, racer, channel
                )
            else:
                embed = discord.Embed(
                    title=f"\U0001f3c6 Retirement: {racer.name}",
                    description=(
                        f"**{racer.name}** retires after {racer.races_completed} races!"
                    ),
                )
                try:
                    await channel.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    continue

    async def _handle_npc_retirement(
        self,
        guild_id: int,
        racer: models.Racer,
        channel: discord.abc.Messageable,
    ) -> None:
        """Replace a retired NPC racer and announce the change."""
        async with self.sessionmaker() as session:
            npc = await repo.get_npc(session, racer.npc_id)
            if npc is None:
                return

            gs = await repo.get_guild_settings(session, guild_id)
            racer_flavor = self._resolve("racer_flavor", gs) or ""

            # Get taken names for uniqueness
            result = await session.execute(
                select(models.Racer.name).where(
                    models.Racer.guild_id == guild_id,
                    models.Racer.retired.is_(False),
                )
            )
            taken_names = {row[0] for row in result.all()}

            # Generate a new name via LLM, fall back to "{old name} II"
            new_name = None
            if racer_flavor:
                new_name = await npc_generation.generate_npc_racer_name(
                    npc.name, npc.personality_desc,
                    racer_flavor, taken_names,
                )
            if not new_name:
                new_name = f"{racer.name} II"
                if new_name in taken_names:
                    new_name = f"{racer.name} III"

            # Generate stats for the same rank
            rank = racer.rank or "D"
            stats = npc_generation.generate_racer_stats_for_rank(rank)
            temperament = random.choice(npc_generation.TEMPERAMENTS)
            gender = random.choice(["M", "F"])

            new_racer = await repo.create_racer(
                session,
                name=new_name,
                owner_id=0,
                guild_id=guild_id,
                speed=stats["speed"],
                cornering=stats["cornering"],
                stamina=stats["stamina"],
                temperament=temperament,
                gender=gender,
                rank=rank,
                npc_id=npc.id,
            )

            # Generate description
            if racer_flavor:
                from . import descriptions
                try:
                    desc = await descriptions.generate_description(
                        new_racer, racer_flavor
                    )
                    if desc:
                        await repo.update_racer(
                            session, new_racer.id, description=desc
                        )
                except Exception:
                    pass

        # Announce
        emoji = f"{npc.emoji} " if npc.emoji else ""
        embed = discord.Embed(
            title=f"\U0001f3c6 Retirement & New Prospect",
            description=(
                f"{emoji}**{npc.name}** announces the retirement of "
                f"**{racer.name}** after {racer.races_completed} races.\n\n"
                f"Their new prospect **{new_name}** joins the stable!"
            ),
            color=0xE67E22,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _announce_injuries(
        self,
        guild_id: int,
        injuries: list[tuple[int, str, int]],
        names: dict[int, str],
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        lines = []
        for rid, description, recovery in injuries:
            rname = names.get(rid, f"Racer {rid}")
            lines.append(f"**{rname}** — {description} (out {recovery} races)")
        embed = discord.Embed(
            title="\U0001f915 Race Injuries!",
            description="\n".join(lines),
            color=0xE02B2B,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _tick_injury_recovery(
        self, session: AsyncSession, guild_id: int
    ) -> list[models.Racer]:
        """Decrement injury counters for all injured racers and auto-heal at 0."""
        result = await session.execute(
            select(models.Racer).where(
                models.Racer.guild_id == guild_id,
                models.Racer.retired.is_(False),
                models.Racer.injury_races_remaining > 0,
            )
        )
        injured = result.scalars().all()
        healed: list[models.Racer] = []
        for racer in injured:
            racer.injury_races_remaining -= 1
            if racer.injury_races_remaining <= 0:
                racer.injuries = ""
                racer.injury_races_remaining = 0
                healed.append(racer)
        return healed

    async def _tick_breed_cooldowns(
        self, session: AsyncSession, guild_id: int
    ) -> None:
        """Decrement breed_cooldown for all guild racers with cooldown > 0."""
        result = await session.execute(
            select(models.Racer).where(
                models.Racer.guild_id == guild_id,
                models.Racer.breed_cooldown > 0,
            )
        )
        for racer in result.scalars().all():
            racer.breed_cooldown = max(0, racer.breed_cooldown - 1)

    async def _announce_healed(
        self, guild_id: int, healed: list[models.Racer]
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        names = ", ".join(f"**{r.name}**" for r in healed)
        embed = discord.Embed(
            title="\U0001f489 Racers Recovered!",
            description=f"{names} {'has' if len(healed) == 1 else 'have'} recovered from injuries and rejoined the roster!",
            color=0x2ECC71,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _announce_placement_prizes(
        self,
        guild_id: int,
        awards: list[tuple[int, int, int]],
        names: dict[int, str] | None = None,
        stat_gains: dict[int, tuple[str, int]] | None = None,
    ) -> None:
        """Announce placement prize earnings to the race channel."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        lines: list[str] = []
        for owner_id, racer_id, prize in awards:
            racer_name = (names or {}).get(racer_id, f"Racer {racer_id}")
            line = f"**{racer_name}** earned **{prize} coins** for <@{owner_id}>!"
            if stat_gains and racer_id in stat_gains:
                stat_name, new_val = stat_gains[racer_id]
                line += f" (+1 {stat_name.capitalize()} \u2192 {new_val})"
            lines.append(line)
        if not lines:
            return
        embed = discord.Embed(
            title="\U0001f4b0 Placement Prizes!",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    # ------------------------------------------------------------------
    # Tournament scheduling & execution
    # ------------------------------------------------------------------

    async def _tournament_tick(self) -> None:
        """Called every 60s.  Fire any scheduled tournaments whose time has come."""
        settings = getattr(self.bot, "settings", None)
        if settings and not getattr(settings, "tournament_enabled", True):
            return

        now = datetime.now(timezone.utc)
        tick_key = f"{now.weekday()}-{now.hour}-{now.minute}"
        if tick_key == self._last_tournament_tick:
            return
        self._last_tournament_tick = tick_key

        for weekday, hour, minute, rank in TOURNAMENT_SCHEDULE:
            if now.weekday() == weekday and now.hour == hour and now.minute == minute:
                for guild in self.bot.guilds:
                    try:
                        await self._execute_tournament(guild.id, rank)
                    except Exception:
                        self.bot.logger.exception(
                            "Tournament execution error",
                            extra={"guild_id": guild.id, "rank": rank},
                        )

    async def _execute_tournament(self, guild_id: int, rank: str) -> bool:
        """Run a tournament for a guild+rank.  Returns True if it fired."""
        async with self.sessionmaker() as session:
            tournament = await repo.get_pending_tournament(session, guild_id, rank)
            if tournament is None:
                return False

            entries = await repo.get_tournament_entries(session, tournament.id)
            player_entries = [e for e in entries if not e.is_pool_filler]
            if not player_entries:
                return False  # no players registered — skip

            # Gather racer IDs already registered
            registered_ids = {e.racer_id for e in entries}
            registered_racers: list[models.Racer] = []
            for entry in entries:
                racer = await session.get(models.Racer, entry.racer_id)
                if racer:
                    registered_racers.append(racer)

            # Fill to 8 with pool racers
            all_racers = await self._fill_tournament_field(
                session, tournament.id, guild_id, rank,
                registered_racers, registered_ids,
            )

            if len(all_racers) < TOURNAMENT_FIELD_SIZE:
                self.bot.logger.warning(
                    "Could not fill tournament field to %d (got %d)",
                    TOURNAMENT_FIELD_SIZE, len(all_racers),
                    extra={"guild_id": guild_id, "rank": rank},
                )
                return False

            # Mark tournament as running
            await repo.update_tournament(
                session, tournament.id,
                status="running",
                started_at=datetime.now(timezone.utc),
            )

        # Load potion buffs for tournament participants
        tournament_racer_ids = [r.id for r in all_racers]
        async with self.sessionmaker() as session:
            raw_buffs = await repo.get_race_buffs_for_racers(
                session, tournament_racer_ids
            )
        stat_buffs, mood_buffs = logic.convert_buffs(raw_buffs)

        # Run the tournament engine
        seed = int(datetime.now(timezone.utc).timestamp() * 1000) + guild_id
        result = logic.run_tournament(
            all_racers, seed,
            stat_buffs=stat_buffs, mood_buffs=mood_buffs,
        )

        # Store placements, eliminated rounds, and award prizes
        async with self.sessionmaker() as session:
            entries = await repo.get_tournament_entries(session, tournament.id)
            entry_by_racer = {e.racer_id: e for e in entries}
            entry_by_racer_owner = {e.racer_id: e.owner_id for e in entries}

            for place_idx, racer_id in enumerate(result.final_placements):
                entry = entry_by_racer.get(racer_id)
                if entry:
                    placement = place_idx + 1
                    # Determine which round they were eliminated
                    elim_round = None
                    for rnd in result.rounds:
                        if racer_id in rnd.eliminated:
                            elim_round = rnd.round_number
                            break
                    await repo.update_tournament_entry(
                        session, entry.id,
                        placement=placement,
                        eliminated_round=elim_round,
                    )

            await repo.update_tournament(
                session, tournament.id,
                status="finished",
                finished_at=datetime.now(timezone.utc),
            )

            # Award prizes and rewards
            prize_awards = await logic.resolve_tournament_prizes(
                session, rank, result.final_placements,
                entry_by_racer_owner, guild_id,
            )
            await logic.apply_tournament_rewards(
                session, rank, result.final_placements,
                entry_by_racer_owner,
            )
            # Consume potion buffs after tournament (1 tournament = 1 use)
            await repo.consume_racer_buffs(session, tournament_racer_ids)
            await session.commit()

        # Announce results
        await self._announce_tournament_results(guild_id, rank, result, all_racers)

        self.bot.logger.info(
            "Tournament completed: %s-Rank",
            rank,
            extra={"guild_id": guild_id, "tournament_id": tournament.id},
        )
        return True

    async def _fill_tournament_field(
        self,
        session: AsyncSession,
        tournament_id: int,
        guild_id: int,
        rank: str,
        registered: list[models.Racer],
        registered_ids: set[int],
    ) -> list[models.Racer]:
        """Fill the tournament to 8 racers with pool fillers."""
        all_racers = list(registered)
        needed = TOURNAMENT_FIELD_SIZE - len(all_racers)

        if needed <= 0:
            return all_racers[:TOURNAMENT_FIELD_SIZE]

        # Try existing unowned pool racers of this rank first
        pool = await repo.get_racers_by_rank(
            session, guild_id, rank, unowned_only=True
        )
        available = [r for r in pool if r.id not in registered_ids]
        random.shuffle(available)

        # Gather taken names for generation
        result = await session.execute(
            select(models.Racer.name).where(
                models.Racer.guild_id == guild_id,
                models.Racer.retired.is_(False),
            )
        )
        taken_names = {row[0] for row in result.all()}

        for r in available:
            if needed <= 0:
                break
            entry = await repo.create_tournament_entry(
                session,
                tournament_id=tournament_id,
                racer_id=r.id,
                owner_id=0,
                is_pool_filler=True,
            )
            all_racers.append(r)
            registered_ids.add(r.id)
            needed -= 1

        # Generate new pool racers if we still need more
        while needed > 0:
            kwargs = logic.generate_pool_racer_for_rank(rank, guild_id, taken_names)
            taken_names.add(kwargs["name"])
            new_racer = await repo.create_racer(session, **kwargs)
            await repo.create_tournament_entry(
                session,
                tournament_id=tournament_id,
                racer_id=new_racer.id,
                owner_id=0,
                is_pool_filler=True,
            )
            all_racers.append(new_racer)
            needed -= 1

        return all_racers

    async def _announce_tournament_results(
        self,
        guild_id: int,
        rank: str,
        result: logic.TournamentResult,
        racers: list[models.Racer],
    ) -> None:
        """Post tournament results to the guild channel."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        gs = await self._load_guild_settings(guild_id)
        channel = self._get_channel(guild, gs)
        if channel is None:
            return

        name_map = {r.id: r.name for r in racers}
        owner_map = {r.id: r.owner_id for r in racers}

        # Round-by-round results
        for rnd in result.rounds:
            round_lines = []
            for i, rid in enumerate(rnd.race_result.placements):
                name = name_map.get(rid, f"Racer {rid}")
                owner = owner_map.get(rid, 0)
                owner_tag = f" (<@{owner}>)" if owner else " (pool)"
                medal = self.MEDAL_EMOJI.get(i + 1, f"#{i+1}")
                status = " ✅" if rid in rnd.advancing else " ❌"
                round_lines.append(f"{medal} **{name}**{owner_tag}{status}")

            map_name = rnd.race_result.map_name
            track_info = f" — *{map_name}*" if map_name else ""
            embed = discord.Embed(
                title=f"🏟️ {rank}-Rank Tournament — Round {rnd.round_number}{track_info}",
                description="\n".join(round_lines),
                color=0x9B59B6,
            )
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return
            await asyncio.sleep(3)

        # Final standings
        prizes = logic.TOURNAMENT_PRIZES.get(rank, [0, 0, 0, 0])
        final_lines = []
        place_labels = {1: "🥇", 2: "🥈", 3: "🥉", 4: "4th"}
        for i, rid in enumerate(result.final_placements[:4]):
            name = name_map.get(rid, f"Racer {rid}")
            owner = owner_map.get(rid, 0)
            owner_tag = f" (<@{owner}>)" if owner else " (pool)"
            prize = prizes[i] if i < len(prizes) else 0
            label = place_labels.get(i + 1, f"#{i+1}")
            final_lines.append(f"{label} **{name}**{owner_tag} — **{prize}** coins")

        embed = discord.Embed(
            title=f"🏆 {rank}-Rank Tournament — Final Results!",
            description="\n".join(final_lines),
            color=0xF1C40F,
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _countdown(
        self,
        guild_id: int,
        guild_settings: models.GuildSettings | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild, guild_settings)
        if channel is None:
            return
        delay = self._resolve("countdown_total", guild_settings) / 3
        for num in ("3", "2", "1"):
            try:
                await channel.send(num)
            except (discord.Forbidden, discord.HTTPException):
                return
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Fishing
    # ------------------------------------------------------------------

    async def _fishing_tick(self) -> None:
        """Process all fishing sessions whose catch timer has elapsed."""
        from datetime import timedelta

        from economy import repositories as wallet_repo
        from fishing import logic as fish_logic
        from fishing import repositories as fish_repo

        await self._init_db()
        now = datetime.now(timezone.utc)

        async with self.sessionmaker() as session:
            due_sessions = await fish_repo.get_all_due_sessions(session, now)
            if not due_sessions:
                return

            locations = fish_logic.load_locations()

            for fs in due_sessions:
                try:
                    location_data = locations.get(fs.location_name)
                    if location_data is None:
                        # Location YAML removed — end gracefully; refund leftover bait
                        if fs.bait_remaining > 0:
                            await fish_repo.add_bait(
                                session, fs.user_id, fs.guild_id,
                                fs.bait_type, fs.bait_remaining,
                            )
                        await fish_repo.end_session(session, fs.id)
                        continue

                    rod_data = fish_logic.get_rod(fs.rod_id)

                    # Resolve the catch
                    catch = fish_logic.select_catch(
                        location_data, rod_data, fs.bait_type
                    )

                    # Credit coins to wallet
                    if catch["value"] > 0:
                        wallet = await wallet_repo.get_wallet(
                            session, fs.user_id, fs.guild_id
                        )
                        if wallet is None:
                            wallet = await wallet_repo.create_wallet(
                                session,
                                user_id=fs.user_id,
                                guild_id=fs.guild_id,
                            )
                        wallet.balance += catch["value"]

                    # Award XP
                    xp_gained = fish_logic.calculate_catch_xp(catch, location_data)
                    player, old_level, new_level = await fish_repo.add_xp(
                        session, fs.user_id, fs.guild_id, xp_gained
                    )

                    is_fish = not catch["is_trash"]

                    # Log the catch and check trophy
                    trophy_just_earned = False
                    if is_fish:
                        fish_catch = await fish_repo.upsert_fish_catch(
                            session, fs.user_id, fs.guild_id,
                            catch["name"], fs.location_name,
                            catch.get("rarity", "common"),
                            catch.get("length") or 0,
                            catch["value"], now,
                        )
                        # Only check trophy if this was a NEW species discovery
                        first_discovery = fish_catch.catch_count == 1
                        if first_discovery:
                            caught_species = await fish_repo.get_caught_species_at_location(
                                session, fs.user_id, fs.guild_id, fs.location_name
                            )
                            trophy_just_earned = fish_logic.has_location_trophy(
                                caught_species, location_data
                            )
                        else:
                            caught_species = await fish_repo.get_caught_species_at_location(
                                session, fs.user_id, fs.guild_id, fs.location_name
                            )
                    else:
                        caught_species = set()

                    # Update daily summary for digest
                    date_str = now.strftime("%Y-%m-%d")
                    await fish_repo.upsert_daily_summary(
                        session, fs.user_id, fs.guild_id, date_str, catch
                    )

                    # Calculate next cast with skill + trophy bonuses
                    has_trophy = fish_logic.has_location_trophy(
                        caught_species, location_data
                    )
                    new_remaining = fs.bait_remaining - 1
                    skill_reduction = fish_logic.get_skill_cast_reduction(
                        new_level, location_data.get("skill_level", 1)
                    )
                    trophy_reduction = (
                        fish_logic.TROPHY_CAST_REDUCTION if has_trophy else 0.0
                    )
                    next_catch = now + timedelta(
                        seconds=fish_logic.calculate_cast_time(
                            location_data["base_cast_time"],
                            rod_data,
                            fs.bait_type,
                            skill_reduction=skill_reduction,
                            trophy_reduction=trophy_reduction,
                        )
                    )

                    update_kwargs: dict[str, Any] = {
                        "total_fish": fs.total_fish + (1 if is_fish else 0),
                        "total_coins": fs.total_coins + catch["value"],
                        "last_catch_name": catch["name"],
                        "last_catch_value": catch["value"],
                        "last_catch_length": catch.get("length"),
                        "bait_remaining": new_remaining,
                        "next_catch_at": next_catch,
                    }

                    session_ended = new_remaining <= 0
                    if session_ended:
                        update_kwargs["active"] = False

                    await fish_repo.update_session(session, fs.id, **update_kwargs)

                    # Announcements (level-up and trophy)
                    try:
                        channel = self.bot.get_channel(fs.channel_id)
                        if channel and new_level > old_level:
                            await channel.send(
                                f"\u2B50 <@{fs.user_id}> reached "
                                f"**Fishing Level {new_level}**!"
                            )
                        if channel and trophy_just_earned:
                            loc_name = location_data.get("name", fs.location_name)
                            await channel.send(
                                f"\U0001f3c6 <@{fs.user_id}> completed the "
                                f"**{loc_name}** collection! Trophy earned!"
                            )
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                except Exception:
                    self.bot.logger.error(
                        "Fishing tick error for user %s guild %s",
                        fs.user_id,
                        fs.guild_id,
                        exc_info=True,
                    )

