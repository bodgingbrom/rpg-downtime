import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from config import Settings
from derby import repositories as repo
from derby.models import Race, Racer
from derby.scheduler import DerbyScheduler


class DummyChannel:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.messages.append({"content": content, **kwargs})


class DummyUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
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
        self.loop = asyncio.get_event_loop()
        self.users: dict[int, DummyUser] = {}

    def add_user(self, user: "DummyUser") -> None:
        self.users[user.id] = user

    def get_guild(self, gid: int) -> DummyGuild | None:
        for g in self.guilds:
            if g.id == gid:
                return g
        return None

    def get_user(self, uid: int):
        return self.users.get(uid)

    async def fetch_user(self, uid: int):
        return self.users.get(uid)


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
async def test_payout_dms(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(race_frequency=1, default_wallet=100, retirement_threshold=101)
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    user1 = DummyUser(1)
    user2 = DummyUser(2)
    bot.add_user(user1)
    bot.add_user(user2)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        race = (await session.execute(select(Race))).scalars().first()
        r1 = await repo.create_racer(session, name="A", owner_id=1)
        r2 = await repo.create_racer(session, name="B", owner_id=2)
        await repo.create_bet(
            session, race_id=race.id, user_id=user1.id, racer_id=r1.id, amount=10
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=user2.id, racer_id=r2.id, amount=20
        )
        await scheduler.tick()

    assert user1.messages and user2.messages
