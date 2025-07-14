import asyncio
from pathlib import Path

import discord
import pytest
from sqlalchemy import select

from config import Settings
from derby import repositories as repo
from derby.models import Race, Racer
from derby.scheduler import DerbyScheduler


class DummyChannel:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(
        self, content: str | None = None, *, embed: discord.Embed | None = None
    ) -> None:
        if embed is not None and embed.description is not None:
            self.messages.append(embed.description)
        elif content is not None:
            self.messages.append(content)


class DummyGuild:
    def __init__(self, gid: int) -> None:
        self.id = gid
        self.system_channel = DummyChannel()
        self.text_channels = [self.system_channel]


class DummyBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guilds: list[DummyGuild] = []
        self.users: dict[int, DummyUser] = {}
        self.loop = asyncio.get_event_loop()

    def get_guild(self, gid: int) -> DummyGuild | None:
        for g in self.guilds:
            if g.id == gid:
                return g
        return None

    def get_user(self, uid: int) -> "DummyUser | None":
        return self.users.get(uid)


class DummyUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.dms: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.dms.append({"content": content, **kwargs})


@pytest.mark.asyncio
async def test_scheduler_creates_and_runs_race(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=101)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        await repo.create_racer(session, name="A", owner_id=1)
        await repo.create_racer(session, name="B", owner_id=2)
        await scheduler.tick()
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 1 and races[0].finished


@pytest.mark.asyncio
async def test_retirement(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=0)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    async with scheduler.sessionmaker() as session:
        await repo.create_racer(session, name="A", owner_id=1)
        await repo.create_racer(session, name="B", owner_id=2)
    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        racers = (await session.execute(select(Racer))).scalars().all()
        retired = [r for r in racers if r.retired]
        assert retired and len(racers) == 4


@pytest.mark.asyncio
async def test_stream_commentary(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=101)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        race = await repo.create_race(session, guild_id=guild.id)

    events = ["E1", "E2", "E3"]
    await scheduler._stream_commentary(race.id, guild.id, events, delay=0)
    assert guild.system_channel.messages == events


@pytest.mark.asyncio
async def test_commentary_stops_when_cancelled(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=101)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        race = await repo.create_race(session, guild_id=guild.id)

    events = ["A", "B", "C"]

    async def cancel() -> None:
        await asyncio.sleep(0.01)
        async with scheduler.sessionmaker() as session:
            await repo.delete_race(session, race.id)

    cancel_task = asyncio.create_task(cancel())
    await scheduler._stream_commentary(race.id, guild.id, events, delay=0.05)
    await cancel_task

    assert len(guild.system_channel.messages) == 1


@pytest.mark.asyncio
async def test_payout_dm_sent(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=101)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    user1 = DummyUser(10)
    user2 = DummyUser(11)
    bot.users[user1.id] = user1
    bot.users[user2.id] = user2

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        race = await repo.create_race(session, guild_id=guild.id)
        r1 = await repo.create_racer(session, name="A", owner_id=1)
        r2 = await repo.create_racer(session, name="B", owner_id=2)
        await repo.create_wallet(session, user_id=user1.id, balance=100)
        await repo.create_wallet(session, user_id=user2.id, balance=100)
        await repo.create_bet(
            session, race_id=race.id, user_id=user1.id, racer_id=r1.id, amount=10
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=user2.id, racer_id=r2.id, amount=20
        )

    await scheduler.tick()

    assert len(user1.dms) == 1
    assert len(user2.dms) == 1
