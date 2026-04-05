from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, time as dt_time
from typing import Any

import discord
from discord.ext import tasks
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import resolve_guild_setting
from db_base import Base
from . import commentary, logic, models
from . import repositories as repo


def _parse_race_times(time_strings: list[str]) -> list[dt_time]:
    """Parse 'HH:MM' strings into datetime.time objects (UTC)."""
    times = []
    for ts in time_strings:
        h, m = ts.strip().split(":")
        times.append(dt_time(hour=int(h), minute=int(m)))
    return times


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
        self.commentaries: dict[int, tasks.Loop] = {}
        self.active_races: set[int] = set()  # race IDs currently in progress

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
    ) -> discord.abc.Messageable | None:
        """Return the configured channel for the guild or a sensible default."""
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
            }
            for name, (col_type, default) in racer_migrations.items():
                if name not in racer_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE racers ADD COLUMN {name} {col_type} DEFAULT {default}"
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
            }
            for col_name, (col_type, default) in gs_migrations.items():
                if col_name not in gs_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE guild_settings ADD COLUMN "
                            f"{col_name} {col_type} DEFAULT {default}"
                        )
                    )
        self._initialized = True

    async def _run(self) -> None:
        await self.tick()

    async def tick(self) -> None:
        """Run the pending race for each guild, then create the next one."""
        await self._init_db()

        for guild in self.bot.guilds:
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
        async with self.sessionmaker() as session:
            race = await repo.create_race(session, guild_id=guild_id)
            participants = random.sample(
                racers, min(max_racers, len(racers))
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

    async def _ensure_pending_races(self) -> None:
        """Ensure each guild has a pending race with participants.

        Called on startup so there's always something for /race upcoming.
        """
        for guild in self.bot.guilds:
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
        async with self.sessionmaker() as session:
            racers = await repo.get_guild_racers(
                session, race.guild_id, min_training=min_train,
            )
            if len(racers) < 2:
                return
            participants = random.sample(
                racers, min(max_racers, len(racers))
            )
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
        race_map = logic.pick_map()
        await self._announce_race_start(
            guild_id, race_id, participants, race_map=race_map,
            guild_settings=gs,
        )
        await asyncio.sleep(self._resolve("bet_window", gs))
        await self._countdown(guild_id, guild_settings=gs)
        self.bot.logger.info(
            "Race starting",
            extra={"guild_id": guild_id, "race_id": race_id},
        )
        result = logic.simulate_race(
            {"racers": participants}, race_id, race_map=race_map
        )
        winner_id = result.placements[0] if result.placements else None
        async with self.sessionmaker() as session:
            await repo.update_race(
                session, race_id, finished=True, winner_id=winner_id
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
            if winner_id is not None:
                await logic.resolve_payouts(
                    session, race_id, winner_id, guild_id=guild_id
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
            await session.commit()
        names = result.racer_names

        # Show a "getting ready" message while LLM generates commentary
        guild = self.bot.get_guild(guild_id)
        if guild:
            channel = self._get_channel(guild, gs)
            if channel:
                lineup = ", ".join(
                    f"**{names.get(rid, f'Racer {rid}')}**"
                    for rid in result.placements
                )
                track_info = f" on **{result.map_name}**" if result.map_name else ""
                ready_embed = discord.Embed(
                    title="\U0001f3c7 Racers Getting Ready!",
                    description=(
                        f"The racers line up{track_info}!\n\n"
                        f"Lineup: {lineup}\n\n"
                        f"*The race is about to begin...*"
                    ),
                    color=0xFFAA00,
                )
                try:
                    await channel.send(embed=ready_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        log = await commentary.generate_commentary(result)
        if log is None:
            log = commentary.build_template_commentary(result)
        await self._stream_commentary(
            race_id, guild_id, log,
            delay=self._resolve("commentary_delay", gs),
        )
        await self._post_results(guild_id, result.placements, names)
        await self._dm_payouts(bets, race_id, winner_id, names)
        if new_injuries:
            await self._announce_injuries(guild_id, new_injuries, names)
        if retirements:
            await self._announce_retirements(guild_id, retirements)
        if healed:
            await self._announce_healed(guild_id, healed)
        if placement_awards:
            await self._announce_placement_prizes(
                guild_id, placement_awards, names
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
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _dm_payouts(
        self,
        bets: list[models.Bet],
        race_id: int,
        winner_id: int | None,
        names: dict[int, str] | None = None,
    ) -> None:
        if not bets or winner_id is None:
            return
        names = names or {}
        for bet in bets:
            user = self.bot.get_user(bet.user_id)
            if user is None:
                continue
            racer_name = names.get(bet.racer_id, f"Racer {bet.racer_id}")
            if bet.racer_id == winner_id:
                payout = int(bet.amount * bet.payout_multiplier)
                msg = (
                    f"You won {payout} coins betting on "
                    f"**{racer_name}** in race {race_id}! "
                    f"({bet.payout_multiplier:.1f}x odds)"
                )
            else:
                msg = (
                    f"You lost your bet of {bet.amount} coins on "
                    f"**{racer_name}** in race {race_id}."
                )
            try:
                await user.send(msg)
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def _stream_commentary(
        self, race_id: int, guild_id: int, log: list[str], delay: float = 6.0
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return

        for i, event in enumerate(log):
            # Check if race was cancelled
            async with self.sessionmaker() as session:
                if await repo.get_race(session, race_id) is None:
                    return

            embed = discord.Embed(
                description=event,
                color=0x2ECC71 if i < len(log) - 1 else 0xF1C40F,
            )
            embed.set_footer(text=f"\U0001f3c7 Race {race_id}")

            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                return

            if i < len(log) - 1:
                await asyncio.sleep(delay)

    async def _increment_careers(
        self, session: AsyncSession, racers: list[models.Racer]
    ) -> None:
        """Increment races_completed for all participants."""
        for racer in racers:
            racer.races_completed += 1

    async def _apply_retirements(
        self,
        session: AsyncSession,
        racers: list[models.Racer],
        guild_id: int = 0,
    ) -> list[tuple[models.Racer, models.Racer]]:
        """Retire racers that have reached their career_length.

        For each retired racer, create a new house racer with random
        stats to keep the roster populated.
        """
        retirements: list[tuple[models.Racer, models.Racer]] = []
        for racer in racers:
            if racer.races_completed >= racer.career_length:
                await repo.update_racer(session, racer.id, retired=True)
                career_length = random.randint(25, 40)
                successor = await repo.create_racer(
                    session,
                    name=f"{racer.name} II",
                    owner_id=racer.owner_id,
                    guild_id=guild_id,
                    speed=random.randint(0, 31),
                    cornering=random.randint(0, 31),
                    stamina=random.randint(0, 31),
                    temperament=random.choice(list(logic.TEMPERAMENTS.keys())),
                    career_length=career_length,
                    peak_end=int(career_length * 0.6),
                    gender=random.choice(["M", "F"]),
                )
                retirements.append((racer, successor))
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
        for r in racers:
            mult = odds.get(r.id, 0)
            embed.add_field(
                name=f"{r.name} (#{r.id})",
                value=f"{mult:.1f}x \u2014 bet 100, win {int(100 * mult)}",
                inline=False,
            )
        embed.set_footer(text="Use /race bet to place your bet!")
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _announce_retirements(
        self,
        guild_id: int,
        retirements: list[tuple[models.Racer, models.Racer]],
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        for old, new in retirements:
            embed = discord.Embed(
                title=f"Retirement: {old.name}",
                description=(
                    f"**{old.name}** retires after {old.races_completed} races! "
                    f"A new racer, **{new.name}**, joins the roster."
                ),
            )
            embed.add_field(
                name="Speed", value=logic.stat_band(new.speed), inline=True
            )
            embed.add_field(
                name="Cornering", value=logic.stat_band(new.cornering), inline=True
            )
            embed.add_field(
                name="Stamina", value=logic.stat_band(new.stamina), inline=True
            )
            embed.add_field(
                name="Temperament", value=new.temperament, inline=True
            )
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                continue

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
            lines.append(
                f"**{racer_name}** earned **{prize} coins** for <@{owner_id}>!"
            )
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
