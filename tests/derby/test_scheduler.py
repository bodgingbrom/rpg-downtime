import asyncio
import logging
from pathlib import Path

import discord
import pytest
from sqlalchemy import select

from config import Settings
from derby import repositories as repo
from derby.models import Race, RaceEntry, Racer
from derby.scheduler import DerbyScheduler
from economy import repositories as wallet_repo


class DummyChannel:
    def __init__(self, name: str = "general") -> None:
        self.name = name
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
        self.logger = logging.getLogger("test")

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
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    # No racers — tick should not run anything
    await scheduler.tick()
    async with scheduler.sessionmaker() as session:
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 0

    # Add racers and create a pending race with entries
    async with scheduler.sessionmaker() as session:
        r1 = await repo.create_racer(session, name="A", owner_id=1)
        r2 = await repo.create_racer(session, name="B", owner_id=2)
        race = await repo.create_race(session, guild_id=guild.id)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    # tick() should run the pending race and create the next one
    await scheduler.tick()
    async with scheduler.sessionmaker() as session:
        races = (await session.execute(select(Race))).scalars().all()
        finished = [r for r in races if r.finished]
        pending = [r for r in races if not r.finished]
        assert len(finished) == 1
        # A new pending race should have been created
        assert len(pending) == 1
        # The new pending race should have entries
        entries = await repo.get_race_entries(session, pending[0].id)
        assert len(entries) >= 2


@pytest.mark.asyncio
async def test_retirement(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        # Both racers are at the end of their career — one more race retires them
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, career_length=5, peak_end=3
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, career_length=5, peak_end=3
        )
        await repo.update_racer(session, r1.id, races_completed=4)
        await repo.update_racer(session, r2.id, races_completed=4)
        race = await repo.create_race(session, guild_id=guild.id)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        racers = (await session.execute(select(Racer))).scalars().all()
        retired = [r for r in racers if r.retired]
        active = [r for r in racers if not r.retired]
        assert len(retired) == 2  # both original racers retired
        assert len(active) == 2  # two successors created


@pytest.mark.asyncio
async def test_stream_commentary(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
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
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
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
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
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
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])
        await wallet_repo.create_wallet(session, user_id=user1.id, balance=100)
        await wallet_repo.create_wallet(session, user_id=user2.id, balance=100)
        await repo.create_bet(
            session, race_id=race.id, user_id=user1.id, racer_id=r1.id, amount=10
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=user2.id, racer_id=r2.id, amount=20
        )

    # Run the pre-created race via tick
    await scheduler.tick()

    assert len(user1.dms) == 1
    assert len(user2.dms) == 1


@pytest.mark.asyncio
async def test_race_uses_max_six_racers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        racer_ids = []
        for i in range(10):
            r = await repo.create_racer(session, name=f"R{i}", owner_id=i)
            racer_ids.append(r.id)
        race = await repo.create_race(session, guild_id=guild.id)
        # Assign all 10 to the race so we can check the cap on _create_next_race
        await repo.create_race_entries(session, race.id, racer_ids[:6])

    counts: list[int] = []

    async def fake_announce(gid: int, rid: int, racers: list[Racer], **kwargs) -> None:
        counts.append(len(racers))

    async def noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(scheduler, "_announce_race_start", fake_announce)
    monkeypatch.setattr(scheduler, "_countdown", noop)
    monkeypatch.setattr(scheduler, "_stream_commentary", noop)
    monkeypatch.setattr(scheduler, "_post_results", noop)
    monkeypatch.setattr(scheduler, "_dm_payouts", noop)

    await scheduler.tick()

    assert counts == [6]

    # The next auto-created race should also have at most 6 entries
    async with scheduler.sessionmaker() as session:
        result = await session.execute(
            select(Race).where(Race.finished.is_(False))
        )
        next_race = result.scalars().first()
        assert next_race is not None
        entries = await repo.get_race_entries(session, next_race.id)
        assert len(entries) == 6


@pytest.mark.asyncio
async def test_config_channel_used(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        channel_name="special",
    )
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    special = DummyChannel("special")
    guild.text_channels.append(special)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        await repo.create_race(session, guild_id=guild.id)

    await scheduler._announce_race_start(guild.id, 1, [])

    assert special.messages
    assert not guild.system_channel.messages


@pytest.mark.asyncio
async def test_ensure_pending_races_creates_race(tmp_path: Path) -> None:
    """On startup, _ensure_pending_races should create a race if none exists."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(1)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        await repo.create_racer(session, name="A", owner_id=1)
        await repo.create_racer(session, name="B", owner_id=2)

    await scheduler._ensure_pending_races()

    async with scheduler.sessionmaker() as session:
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 1
        assert not races[0].finished
        entries = await repo.get_race_entries(session, races[0].id)
        assert len(entries) == 2
