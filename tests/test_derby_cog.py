import types
from pathlib import Path

import discord
import pytest
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        race_frequency=1, default_wallet=100, retirement_threshold=65
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
        race_frequency=1, default_wallet=100, retirement_threshold=65
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=5)

    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=1)
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
        race_frequency=1, default_wallet=100, retirement_threshold=65
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

async def test_wallet_command_creates_and_returns_balance(tmp_path: Path) -> None:
    ctx.author = types.SimpleNamespace(id=10)

    await cog.wallet(ctx)

    async with sessionmaker() as session:
        wallet = await repo.get_wallet(session, ctx.author.id)

    assert wallet.balance == bot.settings.default_wallet
    assert ctx.sent and str(wallet.balance) in ctx.sent[0]["content"]

