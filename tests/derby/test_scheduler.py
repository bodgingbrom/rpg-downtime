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


GUILD_ID = 1


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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
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
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID
        )
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        # Both racers are at the end of their career — one more race retires them
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID,
            career_length=5, peak_end=3,
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID,
            career_length=5, peak_end=3,
        )
        await repo.update_racer(session, r1.id, races_completed=4)
        await repo.update_racer(session, r2.id, races_completed=4)
        race = await repo.create_race(session, guild_id=guild.id)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        racers = (
            await session.execute(
                select(Racer).where(Racer.guild_id == GUILD_ID)
            )
        ).scalars().all()
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    user1 = DummyUser(10)
    user2 = DummyUser(11)
    bot.users[user1.id] = user1
    bot.users[user2.id] = user2

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        race = await repo.create_race(session, guild_id=guild.id)
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID
        )
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])
        await wallet_repo.create_wallet(
            session, user_id=user1.id, guild_id=GUILD_ID, balance=100
        )
        await wallet_repo.create_wallet(
            session, user_id=user2.id, guild_id=GUILD_ID, balance=100
        )
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        racer_ids = []
        for i in range(10):
            r = await repo.create_racer(
                session, name=f"R{i}", owner_id=i, guild_id=GUILD_ID
            )
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
    guild = DummyGuild(GUILD_ID)
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
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID
        )

    await scheduler._ensure_pending_races()

    async with scheduler.sessionmaker() as session:
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 1
        assert not races[0].finished
        entries = await repo.get_race_entries(session, races[0].id)
        assert len(entries) == 2


@pytest.mark.asyncio
async def test_guild_isolation(tmp_path: Path) -> None:
    """Racers from one guild should not appear in another guild's races."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild_a = DummyGuild(100)
    guild_b = DummyGuild(200)
    bot.guilds.extend([guild_a, guild_b])

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        # Create racers in guild A only
        await repo.create_racer(
            session, name="A1", owner_id=1, guild_id=100
        )
        await repo.create_racer(
            session, name="A2", owner_id=2, guild_id=100
        )
        # Create racers in guild B only
        await repo.create_racer(
            session, name="B1", owner_id=3, guild_id=200
        )
        await repo.create_racer(
            session, name="B2", owner_id=4, guild_id=200
        )

    await scheduler._ensure_pending_races()

    async with scheduler.sessionmaker() as session:
        # Each guild should have its own pending race
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 2

        for race in races:
            participants = await repo.get_race_participants(session, race.id)
            participant_guilds = set()
            for p in participants:
                racer = await repo.get_racer(session, p.id)
                participant_guilds.add(racer.guild_id)
            # All participants belong to the race's guild
            assert participant_guilds == {race.guild_id}


@pytest.mark.asyncio
async def test_guild_settings_channel_override(tmp_path: Path) -> None:
    """Guild-specific channel_name should be used when set."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        channel_name=None,  # global default: use system_channel
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    arena = DummyChannel("arena")
    guild.text_channels.append(arena)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    # Without override: should use system_channel
    channel = scheduler._get_channel(guild)
    assert channel is guild.system_channel

    # Set per-guild override
    async with scheduler.sessionmaker() as session:
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, channel_name="arena"
        )
    gs = await scheduler._load_guild_settings(GUILD_ID)
    channel = scheduler._get_channel(guild, gs)
    assert channel is arena


@pytest.mark.asyncio
async def test_guild_settings_max_racers_override(tmp_path: Path) -> None:
    """Per-guild max_racers_per_race should limit participants."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        max_racers_per_race=6,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()
    async with scheduler.sessionmaker() as session:
        for i in range(10):
            await repo.create_racer(
                session, name=f"R{i}", owner_id=i, guild_id=GUILD_ID
            )
        # Set guild override: max 3 racers per race
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, max_racers_per_race=3
        )

    await scheduler._ensure_pending_races()

    async with scheduler.sessionmaker() as session:
        races = (await session.execute(select(Race))).scalars().all()
        assert len(races) == 1
        entries = await repo.get_race_entries(session, races[0].id)
        assert len(entries) == 3  # guild override, not global 6


@pytest.mark.asyncio
async def test_replenish_pool(tmp_path: Path) -> None:
    """Pool replenishment creates racers up to min_pool_size, capped at 5 per call."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=8,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    # Start with 2 unowned racers — need 6 more to reach min_pool_size=8
    async with scheduler.sessionmaker() as session:
        await repo.create_racer(
            session, name="A", owner_id=0, guild_id=GUILD_ID, speed=10
        )
        await repo.create_racer(
            session, name="B", owner_id=0, guild_id=GUILD_ID, speed=10
        )

    # First call: creates 5 (capped)
    created = await scheduler._replenish_pool(GUILD_ID)
    assert created == 5

    # Second call: creates 1 more (only 1 needed now)
    created = await scheduler._replenish_pool(GUILD_ID)
    assert created == 1

    # Third call: pool is full, no more created
    created = await scheduler._replenish_pool(GUILD_ID)
    assert created == 0

    async with scheduler.sessionmaker() as session:
        count = await repo.count_unowned_eligible_racers(session, GUILD_ID)
        assert count == 8


@pytest.mark.asyncio
async def test_replenish_pool_disabled(tmp_path: Path) -> None:
    """When min_pool_size=0, no racers are created."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    created = await scheduler._replenish_pool(GUILD_ID)
    assert created == 0


@pytest.mark.asyncio
async def test_placement_prizes_credited(tmp_path: Path) -> None:
    """After a race, owned racers' owners receive placement prizes."""
    from economy import repositories as wallet_repo

    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
        placement_prizes="50,30,20",
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    async with scheduler.sessionmaker() as session:
        r1 = await repo.create_racer(
            session, name="Owned1", owner_id=100, guild_id=GUILD_ID,
            speed=30, cornering=30, stamina=30,
        )
        r2 = await repo.create_racer(
            session, name="Owned2", owner_id=200, guild_id=GUILD_ID,
            speed=1, cornering=1, stamina=1,
        )
        await wallet_repo.create_wallet(
            session, user_id=100, guild_id=GUILD_ID, balance=0,
        )
        await wallet_repo.create_wallet(
            session, user_id=200, guild_id=GUILD_ID, balance=0,
        )
        race = await repo.create_race(session, guild_id=GUILD_ID)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        w1 = await wallet_repo.get_wallet(session, 100, GUILD_ID)
        w2 = await wallet_repo.get_wallet(session, 200, GUILD_ID)
        # One got 1st (50), other got 2nd (30) — total 80
        assert w1.balance + w2.balance == 80


@pytest.mark.asyncio
async def test_breed_cooldown_ticks_down(tmp_path: Path) -> None:
    """After a race, breed_cooldown decrements for all guild racers."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    async with scheduler.sessionmaker() as session:
        # Two race participants
        r1 = await repo.create_racer(
            session, name="Runner1", owner_id=1, guild_id=GUILD_ID,
        )
        r2 = await repo.create_racer(
            session, name="Runner2", owner_id=2, guild_id=GUILD_ID,
        )
        # A non-participant with cooldown (should still tick)
        r3 = await repo.create_racer(
            session, name="Breeder", owner_id=3, guild_id=GUILD_ID,
            breed_cooldown=4,
        )
        # A racer already at 0 cooldown (should stay at 0)
        r4 = await repo.create_racer(
            session, name="NoCooldown", owner_id=4, guild_id=GUILD_ID,
            breed_cooldown=0,
        )
        race = await repo.create_race(session, guild_id=guild.id)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        breeder = await repo.get_racer(session, r3.id)
        assert breeder.breed_cooldown == 3  # 4 → 3
        no_cd = await repo.get_racer(session, r4.id)
        assert no_cd.breed_cooldown == 0  # stays at 0


@pytest.mark.asyncio
async def test_breed_cooldown_stops_at_zero(tmp_path: Path) -> None:
    """Breed cooldown doesn't go negative."""
    db_path = tmp_path / "db.sqlite"
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot = DummyBot(settings)
    guild = DummyGuild(GUILD_ID)
    bot.guilds.append(guild)

    scheduler = DerbyScheduler(bot, db_path=str(db_path))
    await scheduler._init_db()

    async with scheduler.sessionmaker() as session:
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID,
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID,
        )
        r3 = await repo.create_racer(
            session, name="LastRace", owner_id=3, guild_id=GUILD_ID,
            breed_cooldown=1,
        )
        race = await repo.create_race(session, guild_id=guild.id)
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await scheduler.tick()

    async with scheduler.sessionmaker() as session:
        racer = await repo.get_racer(session, r3.id)
        assert racer.breed_cooldown == 0  # 1 → 0, not negative
