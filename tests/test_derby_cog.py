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
from config import Settings
from derby import models
from derby import repositories as repo


class DummyContext:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sent: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs) -> None:
        self.sent.append({"content": content, **kwargs})


@pytest.mark.asyncio
async def test_setup_adds_cog():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await derby_cog.setup(bot)
    assert "derby" in bot.cogs


@pytest.mark.asyncio
async def test_race_upcoming(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=1)
        await repo.create_racer(session, name="A", owner_id=1)
        await repo.create_racer(session, name="B", owner_id=2)

    await cog.race_upcoming(ctx)

    assert ctx.sent and ctx.sent[0]["embed"].title == "Upcoming Race"
    fields = ctx.sent[0]["embed"].fields
    assert fields[0].name == "Race ID" and fields[0].value == str(race.id)


@pytest.mark.asyncio
async def test_race_bet(tmp_path: Path) -> None:

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=5)

    async with sessionmaker() as session:
        await repo.create_race(session, guild_id=1)
        racer1 = await repo.create_racer(session, name="A", owner_id=1)
        racer2 = await repo.create_racer(session, name="B", owner_id=2)

    await cog.race_bet(ctx, racer_id=racer1.id, amount=20)

    async with sessionmaker() as session:
        wallet = await repo.get_wallet(session, ctx.author.id)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 80
    assert bet.racer_id == racer1.id and bet.amount == 20

    await cog.race_bet(ctx, racer_id=racer2.id, amount=30)

    async with sessionmaker() as session:
        wallet = await repo.get_wallet(session, ctx.author.id)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 70
    assert bet.racer_id == racer2.id and bet.amount == 30


@pytest.mark.asyncio
async def test_admin_check_requires_role(tmp_path: Path) -> None:

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    ctx.guild = types.SimpleNamespace(id=1)
    ctx.author = types.SimpleNamespace(
        roles=[types.SimpleNamespace(name="Not Admin")],
        guild_permissions=discord.Permissions(manage_guild=True),
    )

    with pytest.raises(commands.CheckFailure):
        await cog.add_racer.can_run(ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wallet_command_creates_and_returns_balance(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=10)

    await cog.wallet(ctx)

    async with sessionmaker() as session:
        wallet = await repo.get_wallet(session, ctx.author.id)

    assert wallet.balance == bot.settings.default_wallet
    assert ctx.sent and str(wallet.balance) in ctx.sent[0]["content"]


@pytest.mark.asyncio
async def test_racer_delete(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(session, name="A", owner_id=1)

    await cog.racer_delete(ctx, racer_id=racer.id)

    async with sessionmaker() as session:
        assert await repo.get_racer(session, racer.id) is None

    assert ctx.sent and ctx.sent[-1]["content"] == f"Racer {racer.id} deleted"


@pytest.mark.asyncio
async def test_race_force_start(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=1)
        racer1 = await repo.create_racer(session, name="A", owner_id=1)
        await repo.create_racer(session, name="B", owner_id=2)
        await repo.create_wallet(session, user_id=5, balance=50)
        await repo.create_bet(
            session, race_id=race.id, user_id=5, racer_id=racer1.id, amount=10
        )

    await cog.race_force_start(ctx, race_id=race.id)

    async with sessionmaker() as session:
        finished = await repo.get_race(session, race.id)
        wallet = await repo.get_wallet(session, 5)

    assert finished.finished
    assert wallet.balance == 70
    assert ctx.sent and f"Race {race.id} finished" in ctx.sent[-1]["content"]


@pytest.mark.asyncio
async def test_debug_race(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=1)
        racer = await repo.create_racer(session, name="A", owner_id=1)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    ctx.guild = types.SimpleNamespace(id=1)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=1, finished=True)
        racer = await repo.create_racer(session, name="A", owner_id=1)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
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


@pytest.mark.asyncio
async def test_add_racer_random_stats(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=99)

    with patch("random.randint", side_effect=[1, 2, 3]), patch(
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


@pytest.mark.asyncio
async def test_edit_racer_stats(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(session, name="Edit", owner_id=1)

    await cog.edit_racer(ctx, racer_id=racer.id, speed=15)

    async with sessionmaker() as session:
        updated = await repo.get_racer(session, racer.id)

    assert updated.speed == 15


@pytest.mark.asyncio
async def test_race_info_bands(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session,
            name="Info",
            owner_id=1,
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1,
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(session, name="Moody", owner_id=1, mood=5)

    await cog.race_info(ctx, racer=racer.id)

    embed = ctx.sent[-1]["embed"]
    assert embed.fields[4].value == "Great"
