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

    def _get_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        """Return the configured channel for the guild or a sensible default."""
        name = self.bot.settings.channel_name
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
        # Ensure each guild has a pending race with pre-picked participants
        await self._ensure_pending_races()

    async def close(self) -> None:
        if self.task and self.task.is_running():
            self.task.cancel()
        await self.engine.dispose()

    async def _init_db(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as conn:
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
            }
            for name, (col_type, default) in racer_migrations.items():
                if name not in racer_columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE racers ADD COLUMN {name} {col_type} DEFAULT {default}"
                        )
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
        self._initialized = True

    async def _run(self) -> None:
        await self.tick()

    async def tick(self) -> None:
        """Run the pending race for each guild, then create the next one."""
        await self._init_db()

        for guild in self.bot.guilds:
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
        async with self.sessionmaker() as session:
            racers_result = await session.execute(
                select(models.Racer).where(
                    models.Racer.retired.is_(False),
                    models.Racer.injury_races_remaining == 0,
                )
            )
            racers = racers_result.scalars().all()

        if len(racers) < 2:
            return None

        async with self.sessionmaker() as session:
            race = await repo.create_race(session, guild_id=guild_id)
            participants = random.sample(
                racers, min(self.bot.settings.max_racers_per_race, len(racers))
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

    async def _backfill_race_entries(self, race: models.Race) -> None:
        """Add participants to a legacy pending race that has none."""
        async with self.sessionmaker() as session:
            racers_result = await session.execute(
                select(models.Racer).where(
                    models.Racer.retired.is_(False),
                    models.Racer.injury_races_remaining == 0,
                )
            )
            racers = racers_result.scalars().all()
            if len(racers) < 2:
                return
            participants = random.sample(
                racers, min(self.bot.settings.max_racers_per_race, len(racers))
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
        race_map = logic.pick_map()
        await self._announce_race_start(
            guild_id, race_id, participants, race_map=race_map
        )
        await asyncio.sleep(self.bot.settings.bet_window)
        await self._countdown(guild_id)
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
                await logic.resolve_payouts(session, race_id, winner_id)
            mood_changes = await logic.apply_mood_drift(
                session, result.placements, participants
            )
            new_injuries = logic.check_injury_risk(result)
            await logic.apply_injuries(session, new_injuries, participants)
            healed = await self._tick_injury_recovery(session, guild_id)
            retirements = await self._apply_retirements(session, participants)
            await session.commit()
        names = result.racer_names

        # Show a "getting ready" message while LLM generates commentary
        guild = self.bot.get_guild(guild_id)
        if guild:
            channel = self._get_channel(guild)
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
            race_id, guild_id, log, delay=self.bot.settings.commentary_delay
        )
        await self._post_results(guild_id, result.placements, names)
        await self._dm_payouts(bets, race_id, winner_id, names)
        if new_injuries:
            await self._announce_injuries(guild_id, new_injuries, names)
        if retirements:
            await self._announce_retirements(guild_id, retirements)
        if healed:
            await self._announce_healed(guild_id, healed)
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

    async def _apply_retirements(
        self, session: AsyncSession, racers: list[models.Racer]
    ) -> list[tuple[models.Racer, models.Racer]]:
        threshold = self.bot.settings.retirement_threshold
        retirements: list[tuple[models.Racer, models.Racer]] = []
        for racer in racers:
            if random.randint(1, 100) >= threshold:
                await repo.update_racer(session, racer.id, retired=True)
                successor = await repo.create_racer(
                    session,
                    name=f"{racer.name} II",
                    owner_id=racer.owner_id,
                    speed=int(racer.speed * random.uniform(0.5, 0.75)),
                    cornering=int(racer.cornering * random.uniform(0.5, 0.75)),
                    stamina=int(racer.stamina * random.uniform(0.5, 0.75)),
                    temperament=racer.temperament,
                )
                retirements.append((racer, successor))
        return retirements

    async def _announce_race_start(
        self,
        guild_id: int,
        race_id: int,
        racers: list[models.Racer],
        race_map: logic.RaceMap | None = None,
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        odds = logic.calculate_odds(racers, [], 0.1, race_map=race_map)
        minutes = self.bot.settings.bet_window // 60
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
                    f"**{old.name}** has retired! "
                    f"Their successor **{new.name}** joins the roster."
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

    async def _countdown(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        delay = self.bot.settings.countdown_total / 3
        for num in ("3", "2", "1"):
            try:
                await channel.send(num)
            except (discord.Forbidden, discord.HTTPException):
                return
            await asyncio.sleep(delay)
