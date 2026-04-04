import types
from pathlib import Path
from unittest.mock import patch

import discord
import pytest
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot import DiscordBot
from cogs import derby as derby_cog
from cogs import economy as economy_cog
from config import Settings
from db_base import Base
from derby import models
from derby import repositories as repo
from economy import repositories as wallet_repo
import economy.models  # noqa: F401


GUILD_ID = 1


class DummyChannel:
    """Minimal channel stub that records sent messages."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.messages.append({"content": content, **kwargs})


class DummyGuild:
    def __init__(self, id: int = GUILD_ID) -> None:
        self.id = id


class DummyContext:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sent: list[dict[str, object]] = []
        self.interaction = None
        self.channel = DummyChannel()
        self.guild = DummyGuild()
        self.author = types.SimpleNamespace(id=42, display_name="TestUser")

    async def defer(self, **kwargs) -> None:
        pass

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.sent.append({"content": content, **kwargs})


@pytest.mark.asyncio
async def test_setup_adds_cog():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await derby_cog.setup(bot)
    assert "derby" in bot.cogs
    assert "stable" in bot.cogs


@pytest.mark.asyncio
async def test_race_upcoming(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=GUILD_ID)
        r1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        r2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID
        )
        await repo.create_race_entries(session, race.id, [r1.id, r2.id])

    await cog.race_upcoming(ctx)

    assert ctx.sent and ctx.sent[0]["embed"].title == "Upcoming Race"
    fields = ctx.sent[0]["embed"].fields
    assert fields[0].name == "Race ID" and fields[0].value == str(race.id)


@pytest.mark.asyncio
async def test_race_bet(tmp_path: Path) -> None:

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=5)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=GUILD_ID)
        racer1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        racer2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID
        )
        await repo.create_race_entries(session, race.id, [racer1.id, racer2.id])

    await cog.race_bet(ctx, racer=racer1.id, amount=20)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 80
    assert bet.racer_id == racer1.id and bet.amount == 20

    await cog.race_bet(ctx, racer=racer2.id, amount=30)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 70
    assert bet.racer_id == racer2.id and bet.amount == 30


@pytest.mark.asyncio
async def test_admin_check_requires_role() -> None:
    import checks as checks_module

    check_fn = checks_module.has_role("Race Admin")
    ctx = DummyContext(None)  # type: ignore[arg-type]
    ctx.author = types.SimpleNamespace(roles=[types.SimpleNamespace(name="Not Admin")])
    result = await check_fn.predicate(ctx)
    assert not result

    ctx.author = types.SimpleNamespace(roles=[types.SimpleNamespace(name="Race Admin")])
    result = await check_fn.predicate(ctx)
    assert result


@pytest.mark.asyncio
async def test_wallet_command_creates_and_returns_balance(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = economy_cog.Economy(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=10)

    await cog.wallet(ctx)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)

    assert wallet.balance == bot.settings.default_wallet
    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None and "Welcome" in embed.title


@pytest.mark.asyncio
async def test_racer_delete(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )

    await cog.racer_delete(ctx, racer=racer.id)

    async with sessionmaker() as session:
        assert await repo.get_racer(session, racer.id) is None

    assert ctx.sent and f"#{racer.id}" in ctx.sent[-1]["content"]


@pytest.mark.asyncio
async def test_race_force_start(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
    )
    streamed: list[list[str]] = []
    posted: list[list[int]] = []

    async def fake_stream(race_id, guild_id, log, **kwargs):
        streamed.append(log)

    async def fake_post(guild_id, placements, names=None):
        posted.append(placements)

    async def noop(*args, **kwargs):
        pass

    bot.scheduler = types.SimpleNamespace(
        sessionmaker=sessionmaker,
        _stream_commentary=fake_stream,
        _post_results=fake_post,
        _announce_injuries=noop,
        active_races=set(),
    )
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=GUILD_ID)
        racer1 = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID,
            speed=31, cornering=31, stamina=31,
        )
        racer2 = await repo.create_racer(
            session, name="B", owner_id=2, guild_id=GUILD_ID,
            speed=0, cornering=0, stamina=0,
        )
        await repo.create_race_entries(session, race.id, [racer1.id, racer2.id])
        await wallet_repo.create_wallet(
            session, user_id=5, guild_id=GUILD_ID, balance=50
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=5, racer_id=racer1.id, amount=10,
            payout_multiplier=3.5,
        )

    await cog.race_force_start(ctx, race_id=race.id)

    async with sessionmaker() as session:
        finished = await repo.get_race(session, race.id)
        wallet = await wallet_repo.get_wallet(session, 5, GUILD_ID)

    assert finished.finished
    assert finished.winner_id is not None
    # force-start sends a "getting ready" embed then streams commentary
    assert ctx.sent  # at least the "getting ready" embed
    assert any(
        hasattr(msg.get("embed"), "title") and "Getting Ready" in msg["embed"].title
        for msg in ctx.sent
        if msg.get("embed")
    )
    assert streamed  # commentary was streamed
    assert posted  # results were posted

    # Verify payout: racer1 (speed=31) should beat racer2 (speed=0)
    # Wallet started at 50, bet was 10 at 3.5x = 35 payout
    # Expected: 50 + 35 = 85 (bet was placed directly, not via race_bet, so no deduction)
    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, 5, GUILD_ID)
        bets = (
            await session.execute(
                select(models.Bet).where(models.Bet.race_id == race.id)
            )
        ).scalars().all()
    assert wallet.balance == 85, f"Expected 85, got {wallet.balance}"
    assert bets == [], "Bets should be deleted after resolve_payouts"


@pytest.mark.asyncio
async def test_debug_race(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=GUILD_ID)
        racer = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=5, racer_id=racer.id, amount=10
        )

    await cog.debug_race(ctx, race_id=race.id)

    assert ctx.sent and ctx.sent[-1].get("ephemeral") is True
    assert "race" in ctx.sent[-1]["content"]


@pytest.mark.asyncio
async def test_race_history(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.guild = types.SimpleNamespace(id=GUILD_ID)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD_ID
        )
        race = await repo.create_race(
            session, guild_id=GUILD_ID, finished=True, winner_id=racer.id
        )
        await repo.create_bet(
            session, race_id=race.id, user_id=5, racer_id=racer.id, amount=15
        )

    await cog.race_history(ctx, count=1)

    assert ctx.sent
    embed = ctx.sent[0]["embed"]
    assert embed.title == "Recent Races"
    field = embed.fields[0]
    assert f"Race {race.id}" == field.name
    assert "A" in field.value and "30" in field.value


@pytest.mark.asyncio
async def test_on_command_error_check_failure() -> None:
    bot = DiscordBot()
    ctx = DummyContext(bot)

    await bot.on_command_error(ctx, commands.CheckFailure())

    assert ctx.sent
    assert "permission" in ctx.sent[0]["embed"].description


@pytest.mark.asyncio
async def test_add_racer_with_stats(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=42)

    await cog.add_racer(
        ctx,
        name="Speedy",
        owner=owner,
        speed=20,
        cornering=21,
        stamina=22,
        temperament="Agile",
    )

    async with sessionmaker() as session:
        racer = (await session.execute(select(models.Racer))).scalars().first()

    assert racer.speed == 20 and racer.cornering == 21
    assert racer.stamina == 22 and racer.temperament == "Agile"
    assert racer.guild_id == GUILD_ID


@pytest.mark.asyncio
async def test_add_racer_random_stats(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=99)

    # side_effect: career_length(25,40), speed(0,31), cornering(0,31), stamina(0,31)
    with patch("random.randint", side_effect=[30, 1, 2, 3]), patch(
        "random.choice", return_value="Burly"
    ):
        await cog.add_racer(ctx, name="Lucky", owner=owner, random_stats=True)

    async with sessionmaker() as session:
        racer = (await session.execute(select(models.Racer))).scalars().first()

    assert [racer.speed, racer.cornering, racer.stamina, racer.temperament] == [
        1,
        2,
        3,
        "Burly",
    ]
    assert racer.career_length == 30
    assert racer.peak_end == 18  # int(30 * 0.6)
    assert racer.guild_id == GUILD_ID


@pytest.mark.asyncio
async def test_add_racer_default_name(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=42)

    await cog.add_racer(ctx, owner=owner, random_stats=True)

    async with sessionmaker() as session:
        racer = (await session.execute(select(models.Racer))).scalars().first()

    assert racer is not None
    assert racer.name  # got a name from the pool
    from derby.logic import _load_names
    assert racer.name in _load_names()


@pytest.mark.asyncio
async def test_add_racer_default_name_avoids_taken(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=42)

    # Create a racer with no name (gets a default)
    await cog.add_racer(ctx, owner=owner, random_stats=True)
    # Create another — should get a different name
    ctx2 = DummyContext(bot)
    await cog.add_racer(ctx2, owner=owner, random_stats=True)

    async with sessionmaker() as session:
        racers = (await session.execute(select(models.Racer))).scalars().all()

    assert len(racers) == 2
    assert racers[0].name != racers[1].name


@pytest.mark.asyncio
async def test_edit_racer_stats(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Edit", owner_id=1, guild_id=GUILD_ID
        )

    await cog.edit_racer(ctx, racer=racer.id, speed=15)

    async with sessionmaker() as session:
        updated = await repo.get_racer(session, racer.id)

    assert updated.speed == 15


@pytest.mark.asyncio
async def test_race_info_bands(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session,
            name="Info",
            owner_id=1,
            guild_id=GUILD_ID,
            speed=31,
            cornering=30,
            stamina=27,
            temperament="Reckless",
        )

    await cog.race_info(ctx, racer=racer.id)

    embed = ctx.sent[-1]["embed"]
    values = [f.value for f in embed.fields[:4]]
    assert values == ["Perfect", "Fantastic", "Very Good", "Reckless"]


@pytest.mark.asyncio
async def test_race_info_mood_label(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Moody", owner_id=1, guild_id=GUILD_ID, mood=5
        )

    await cog.race_info(ctx, racer=racer.id)

    embed = ctx.sent[-1]["embed"]
    assert embed.fields[4].value == "Great"


@pytest.mark.asyncio
async def test_guild_settings_override_default_wallet(tmp_path: Path) -> None:
    """Per-guild default_wallet override should apply when creating wallets."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = economy_cog.Economy(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=42)

    # Set a per-guild override for default_wallet
    async with sessionmaker() as session:
        await repo.create_guild_settings(
            session, guild_id=GUILD_ID, default_wallet=500
        )

    await cog.wallet(ctx)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, 42, GUILD_ID)

    assert wallet.balance == 500  # guild override, not global 100


@pytest.mark.asyncio
async def test_settings_show(tmp_path: Path) -> None:
    """The settings group should display current values."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    await cog._show_settings(ctx)

    assert ctx.sent
    embed = ctx.sent[0]["embed"]
    assert embed.title == "Guild Settings"
    field_names = [f.name for f in embed.fields]
    assert "default_wallet" in field_names
    assert "channel_name" in field_names


# ---------------------------------------------------------------------------
# Stable command tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_buy(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=200,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        max_racers_per_owner=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    # Create an unowned racer and give user a wallet
    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Flash", owner_id=0, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    await cog.stable_buy.callback(cog, ctx, racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Purchased" in embed.title

    # Verify racer is now owned and wallet deducted
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.owner_id == ctx.author.id
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        expected_price = 20 + 30 * 2  # base + stats_total * mult
        assert w.balance == 200 - expected_price


@pytest.mark.asyncio
async def test_stable_buy_insufficient_funds(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=5,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        max_racers_per_owner=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Pricey", owner_id=0, guild_id=GUILD_ID,
            speed=20, cornering=20, stamina=20,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=5,
        )

    await cog.stable_buy.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "Not enough coins" in str(ctx.sent[0].get("content", ""))

    # Racer should still be unowned
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.owner_id == 0


@pytest.mark.asyncio
async def test_stable_sell(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        racer_sell_fraction=0.5,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Bolt", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=50,
        )

    await cog.stable_sell.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "Sold" in str(ctx.sent[0].get("content", ""))

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.owner_id == 0
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        sell_price = int((20 + 30 * 2) * 0.5)
        assert w.balance == 50 + sell_price


@pytest.mark.asyncio
async def test_stable_sell_not_owner(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="NotYours", owner_id=999, guild_id=GUILD_ID,
        )

    await cog.stable_sell.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "don't own" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_stable_rename(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="OldName", owner_id=ctx.author.id, guild_id=GUILD_ID,
        )

    await cog.stable_rename.callback(cog, ctx, racer.id, "NewName")

    assert ctx.sent
    assert "Renamed" in str(ctx.sent[0].get("content", ""))

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.name == "NewName"


@pytest.mark.asyncio
async def test_stable_rename_taken(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await repo.create_racer(
            session, name="Taken", owner_id=0, guild_id=GUILD_ID,
        )
        racer = await repo.create_racer(
            session, name="Mine", owner_id=ctx.author.id, guild_id=GUILD_ID,
        )

    await cog.stable_rename.callback(cog, ctx, racer.id, "Taken")

    assert ctx.sent
    assert "already exists" in str(ctx.sent[0].get("content", ""))


# ---------------------------------------------------------------------------
# /stable train tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_train_success(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=200,
        training_base=10,
        training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Flash", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=4,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    # Mood 4 (Good) → 0% failure chance
    await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Training Complete" in embed.title

    # Cost = 10 + 10 * 2 = 30
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.speed == 11
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 30
        assert r.mood == 3  # dropped from 4


@pytest.mark.asyncio
async def test_stable_train_not_owner(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="NotMine", owner_id=999, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10,
        )

    await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    assert "don't own" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_stable_train_insufficient_funds(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=5,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Expensive", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=15, cornering=10, stamina=10, mood=5,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=5,
        )

    await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    assert "Training costs" in str(ctx.sent[0].get("content", ""))

    # Stat should be unchanged
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.speed == 15


@pytest.mark.asyncio
async def test_stable_train_max_stat(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Maxed", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=31, cornering=10, stamina=10,
        )

    await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    assert "already at maximum" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_stable_train_retired(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="OldTimer", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, retired=True,
        )

    await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    assert "retired" in str(ctx.sent[0].get("content", "")).lower()


@pytest.mark.asyncio
async def test_stable_train_failure(tmp_path: Path) -> None:
    """When training fails, coins are spent and mood drops but stat is unchanged."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Unlucky", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=3,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    # Force failure by mocking random.random to return 0.0 (always < any positive fail_chance)
    # But mood 3 gives 0% fail, so we need to set mood to 1 (50%)
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        r.mood = 1
        await session.commit()

    with patch("cogs.derby.random.random", return_value=0.0):
        await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Failed" in embed.title

    # Coins spent, mood stays at 1 (min), stat unchanged
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.speed == 10  # unchanged
        assert r.mood == 1  # was 1, min is 1
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 30  # cost = 10 + 10*2 = 30


@pytest.mark.asyncio
async def test_stable_train_mood_floor(tmp_path: Path) -> None:
    """Mood at 1 stays at 1 after training (doesn't go below)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Grumpy", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=5, cornering=10, stamina=10, mood=1,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    # Force success despite mood (mock random to return 0.99 > 0.50 fail chance)
    with patch("cogs.derby.random.random", return_value=0.99):
        await cog.stable_train.callback(cog, ctx, racer.id, "speed")

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.speed == 6  # trained successfully
        assert r.mood == 1  # floor, didn't go to 0


# ---------------------------------------------------------------------------
# /stable rest tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_rest_success(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Tired", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=3,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    await cog.stable_rest.callback(cog, ctx, racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Rest" in embed.title

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.mood == 4  # 3 → 4
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 15


@pytest.mark.asyncio
async def test_stable_rest_not_owner(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="NotMine", owner_id=999, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=2,
        )

    await cog.stable_rest.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "don't own" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_stable_rest_already_max(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Happy", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=5,
        )

    await cog.stable_rest.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "great spirits" in str(ctx.sent[0].get("content", "")).lower()


@pytest.mark.asyncio
async def test_stable_rest_insufficient_funds(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=5, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Broke", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=2,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=5,
        )

    await cog.stable_rest.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "Resting costs" in str(ctx.sent[0].get("content", ""))


# ---------------------------------------------------------------------------
# /stable feed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_feed_success(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Hungry", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=2,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    await cog.stable_feed.callback(cog, ctx, racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Feast" in embed.title

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.mood == 4  # 2 → 4
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 30


@pytest.mark.asyncio
async def test_stable_feed_caps_at_5(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Almost", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=4,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    await cog.stable_feed.callback(cog, ctx, racer.id)

    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
        assert r.mood == 5  # 4 → 5 (capped, not 6)
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 30


@pytest.mark.asyncio
async def test_stable_feed_retired(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set())
    cog = derby_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="OldTimer", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=2, retired=True,
        )

    await cog.stable_feed.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "retired" in str(ctx.sent[0].get("content", "")).lower()
