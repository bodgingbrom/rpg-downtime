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

from . import Base, logic, models
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
        await self._announce_race_start(guild_id, race_id, participants)
        await asyncio.sleep(self.bot.settings.bet_window)
        await self._countdown(guild_id)
        self.bot.logger.info(
            "Race starting",
            extra={"guild_id": guild_id, "race_id": race_id},
        )
        placements, log = logic.simulate_race({"racers": participants}, race_id)
        winner_id = placements[0] if placements else None
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
            await self._apply_retirements(session, participants)
            await session.commit()
        await self._stream_commentary(race_id, guild_id, log)
        await self._post_results(guild_id, placements)
        await self._dm_payouts(bets, race_id, winner_id)
        self.bot.logger.info(
            "Race finished",
            extra={"guild_id": guild_id, "race_id": race_id},
        )

    async def _post_results(self, guild_id: int, placements: list[int]) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        async with self.sessionmaker() as session:
            racers = (
                (
                    await session.execute(
                        select(models.Racer).where(models.Racer.id.in_(placements))
                    )
                )
                .scalars()
                .all()
            )
        names = {r.id: r.name for r in racers}
        embed = discord.Embed(title="Race Results")
        for i, rid in enumerate(placements, start=1):
            embed.add_field(
                name=f"{i}.", value=names.get(rid, f"Racer {rid}"), inline=False
            )
        await channel.send(embed=embed)

    async def _dm_payouts(
        self, bets: list[models.Bet], race_id: int, winner_id: int | None
    ) -> None:
        if not bets or winner_id is None:
            return
        winning = winner_id
        for bet in bets:
            user = self.bot.get_user(bet.user_id)
            if user is None:
                continue
            if bet.racer_id == winning:
                msg = f"You won {bet.amount * 2} coins on race {race_id}!"
            else:
                msg = f"You lost your bet of {bet.amount} coins on race {race_id}."
            try:
                await user.send(msg)
            except (discord.Forbidden, discord.HTTPException):
                continue

    async def _stream_commentary(
        self, race_id: int, guild_id: int, log: list[str], delay: float = 2.0
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return

        events = iter(log)
        done = asyncio.Event()
        commentary: tasks.Loop | None = None

        async def send_next() -> None:
            nonlocal commentary
            async with self.sessionmaker() as session:
                if await repo.get_race(session, race_id) is None:
                    if commentary:
                        commentary.cancel()
                    done.set()
                    return
            try:
                event = next(events)
            except StopIteration:
                if commentary:
                    commentary.cancel()
                done.set()
                return
            embed = discord.Embed(description=event)
            try:
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                if commentary:
                    commentary.cancel()
                done.set()
                return

        commentary = tasks.loop(seconds=delay)(send_next)
        await send_next()
        if not done.is_set():
            self.commentaries[race_id] = commentary
            commentary.start()
            await done.wait()
            commentary.cancel()
            self.commentaries.pop(race_id, None)

    async def _apply_retirements(
        self, session: AsyncSession, racers: list[models.Racer]
    ) -> None:
        threshold = self.bot.settings.retirement_threshold
        for racer in racers:
            if random.randint(1, 100) >= threshold:
                await repo.update_racer(session, racer.id, retired=True)
                await repo.create_racer(
                    session,
                    name=f"{racer.name} II",
                    owner_id=racer.owner_id,
                    speed=int(racer.speed * random.uniform(0.5, 0.75)),
                    cornering=int(racer.cornering * random.uniform(0.5, 0.75)),
                    stamina=int(racer.stamina * random.uniform(0.5, 0.75)),
                    temperament=racer.temperament,
                )

    async def _announce_race_start(
        self, guild_id: int, race_id: int, racers: list[models.Racer]
    ) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self._get_channel(guild)
        if channel is None:
            return
        odds = logic.calculate_odds(racers, [], 0.1)
        minutes = self.bot.settings.bet_window // 60
        embed = discord.Embed(
            title="Race Starting Soon",
            description=f"Race {race_id} begins in {minutes} minutes. Place your bets!",
        )
        for r in racers:
            embed.add_field(
                name=r.name, value=f"{odds.get(r.id, 0):.1f}x", inline=False
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
