from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime
from typing import Any

import discord
from discord.ext import tasks
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from . import commentary, logic, models
from . import repositories as repo


class DerbyScheduler:
    """Background task handling daily races."""

    def __init__(self, bot: discord.Client, db_path: str | None = None) -> None:
        self.bot = bot
        root = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
        self.db_path = db_path or os.path.join(root, "database", "database.db")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)
        self._initialized = False
        self.task = tasks.loop(hours=24)(self._run)
        self.commentaries: dict[int, tasks.Loop] = {}

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
        self.task.start()

    async def close(self) -> None:
        if self.task.is_running():
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
        self._initialized = True

    async def _run(self) -> None:
        await self.tick()

    async def tick(self) -> None:
        await self._init_db()
        async with self.sessionmaker() as session:
            await self._create_daily_races(session)
        race_tasks = await self._start_ready_races()
        if race_tasks:
            await asyncio.gather(*race_tasks, return_exceptions=True)

    async def _create_daily_races(self, session: AsyncSession) -> None:
        now = datetime.utcnow()
        start_of_day = datetime(now.year, now.month, now.day)
        for guild in self.bot.guilds:
            result = await session.execute(
                select(func.count(models.Race.id)).where(
                    models.Race.guild_id == guild.id,
                    models.Race.started_at >= start_of_day,
                )
            )
            count = result.scalar_one()
            needed = self.bot.settings.race_frequency - count
            for _ in range(max(0, needed)):
                race = await repo.create_race(session, guild_id=guild.id)
                self.bot.logger.info(
                    "Race scheduled",
                    extra={"guild_id": guild.id, "race_id": race.id},
                )

    async def _start_ready_races(self) -> list[asyncio.Task]:
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(models.Race).where(models.Race.finished.is_(False))
            )
            races = result.scalars().all()
            if not races:
                return []

            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
            if not racers:
                return []

        race_tasks = []
        for race in races:
            participants = random.sample(racers, min(8, len(racers)))
            t = asyncio.create_task(
                self._run_race(race.id, race.guild_id, participants),
                name=f"race-{race.id}",
            )
            race_tasks.append(t)
        return race_tasks

    async def _run_race(
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
        await self._stream_commentary(race_id, guild_id, log)
        await self._post_results(guild_id, result.placements, names)
        await self._dm_payouts(bets, race_id, winner_id, names)
        if retirements:
            await self._announce_retirements(guild_id, retirements)
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
                msg = (
                    f"You won {bet.amount * 2} coins betting on "
                    f"**{racer_name}** in race {race_id}!"
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
        self, race_id: int, guild_id: int, log: list[str], delay: float = 3.0
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return

        events = iter(log)
        done = asyncio.Event()
        loop_task: tasks.Loop | None = None
        counter = {"i": 0}

        async def send_next() -> None:
            nonlocal loop_task
            async with self.sessionmaker() as session:
                if await repo.get_race(session, race_id) is None:
                    if loop_task:
                        loop_task.cancel()
                    done.set()
                    return
            try:
                event = next(events)
            except StopIteration:
                if loop_task:
                    loop_task.cancel()
                done.set()
                return

            embed = discord.Embed(
                description=event,
                color=0x2ECC71 if counter["i"] < len(log) - 1 else 0xF1C40F,
            )
            embed.set_footer(text=f"\U0001f3c7 Race {race_id}")
            counter["i"] += 1

            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                if loop_task:
                    loop_task.cancel()
                done.set()
                return

        loop_task = tasks.loop(seconds=delay)(send_next)
        await send_next()
        if not done.is_set():
            self.commentaries[race_id] = loop_task
            loop_task.start()
            await done.wait()
            loop_task.cancel()
            self.commentaries.pop(race_id, None)

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
