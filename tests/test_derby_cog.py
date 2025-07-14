import types
from pathlib import Path

import discord
import pytest
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cogs import derby as derby_cog
from config import Settings
from derby import repositories as repo


class DummyContext:
    def __init__(self, bot: commands.Bot, author_id: int = 1) -> None:
        self.bot = bot
        self.author = types.SimpleNamespace(id=author_id)
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
async def test_wallet_creates_and_shows_balance(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.settings = Settings(
        race_frequency=1, default_wallet=50, retirement_threshold=65
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker)
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot, author_id=42)

    await cog.wallet(ctx)

    assert ctx.sent and ctx.sent[0]["content"] == "Your balance is 50 coins"
    async with sessionmaker() as session:
        wallet = await repo.get_wallet(session, 42)
        assert wallet is not None and wallet.balance == 50
