from __future__ import annotations

import os
import random
from datetime import datetime

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
            inspector = inspect(conn.sync_connection())
            columns = {c["name"] for c in inspector.get_columns("racers")}
            new_columns = {
                "speed": "INTEGER",
                "cornering": "INTEGER",
                "stamina": "INTEGER",
                "temperament": "INTEGER",
                "mood": "INTEGER",
                "injuries": "VARCHAR",
            }
            for name, col_type in new_columns.items():
                if name not in columns:
                    await conn.execute(
                        text(
                            f"ALTER TABLE racers ADD COLUMN {name} {col_type} DEFAULT 0"
                            if col_type == "INTEGER"
                            else f"ALTER TABLE racers ADD COLUMN {name} {col_type} DEFAULT ''"
                        )
                    )
        self._initialized = True

    async def _run(self) -> None:
        await self.tick()

    async def tick(self) -> None:
        await self._init_db()
        async with self.sessionmaker() as session:
            await self._create_daily_races(session)
            await self._start_ready_races(session)

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
                await repo.create_race(session, guild_id=guild.id)

    async def _start_ready_races(self, session: AsyncSession) -> None:
        result = await session.execute(
            select(models.Race).where(models.Race.finished.is_(False))
        )
        races = result.scalars().all()
        if not races:
            return

        racers_result = await session.execute(
            select(models.Racer).where(models.Racer.retired.is_(False))
        )
        racers = racers_result.scalars().all()
        if not racers:
            return

        for race in races:
            participants = random.sample(racers, min(3, len(racers)))
            bet_rows = await session.execute(
                select(models.Bet).where(models.Bet.race_id == race.id)
            )
            bets = bet_rows.scalars().all()
            winning_racer = min((b.racer_id for b in bets), default=None)

            placements, _log = logic.simulate_race({"racers": participants}, race.id)
            await repo.update_race(session, race.id, finished=True)
            await logic.resolve_payouts(session, race.id)
            await self._apply_retirements(session, participants)
            await session.commit()
            await self._post_results(race.guild_id, placements)
            if bets and winning_racer is not None:
                await self._dm_payouts(bets, winning_racer, race.id)

    async def _post_results(self, guild_id: int, placements: list[int]) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.system_channel or (
            guild.text_channels[0] if guild.text_channels else None
        )
        if channel is None:
            return
        embed = discord.Embed(title="Race Results")
        for i, rid in enumerate(placements, start=1):
            embed.add_field(name=f"#{i}", value=f"Racer {rid}", inline=False)
        await channel.send(embed=embed)

    async def _dm_payouts(
        self, bets: list[models.Bet], winning_racer: int, race_id: int
    ) -> None:
        for bet in bets:
            user = self.bot.get_user(bet.user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(bet.user_id)
                except Exception:
                    continue
            if user is None:
                continue
            if bet.racer_id == winning_racer:
                msg = f"You won {bet.amount * 2} coins on race {race_id}!"
            else:
                msg = f"You lost {bet.amount} coins on race {race_id}."
            try:
                await user.send(msg)
            except Exception:
                continue

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
                )
