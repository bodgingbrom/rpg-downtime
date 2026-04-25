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
from cogs import help as help_cog
from cogs import stable as stable_cog
from cogs import tournament as tournament_cog
from config import Settings
from core import repositories as core_repo
from db_base import Base
from derby import models
from derby import repositories as repo
from derby.settings_cache import GuildSettingsResolver
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

    def get_member(self, member_id: int):
        return types.SimpleNamespace(display_name=f"User#{member_id}")


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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    await derby_cog.setup(bot)
    await stable_cog.setup(bot)
    await tournament_cog.setup(bot)
    assert "derby" in bot.cogs
    assert "stable" in bot.cogs
    assert "tournament_cog" in bot.cogs


async def _make_help_bot():
    """Create a bot with an in-memory DB scheduler for help command tests."""
    engine = create_async_engine("sqlite+aiosqlite://")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings()
    bot.scheduler = types.SimpleNamespace(
        sessionmaker=sm,
        guild_settings=GuildSettingsResolver(sm, bot.settings),
    )
    return bot


@pytest.mark.asyncio
async def test_help_command():
    bot = await _make_help_bot()
    cog = help_cog.Help(bot)
    ctx = DummyContext(bot)
    ctx.invoked_subcommand = None

    await cog.help_command.callback(cog, ctx)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "derby" in embed.description.lower()
    assert "brewing" in embed.description.lower()


@pytest.mark.asyncio
async def test_help_derby():
    bot = await _make_help_bot()
    cog = help_cog.Help(bot)
    ctx = DummyContext(bot)

    await cog.help_derby.callback(cog, ctx)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Downtime Derby" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "Getting Started" in field_names
    assert "Racing & Betting" in field_names
    assert "Your Stable" in field_names
    assert "Breeding" in field_names
    assert "Tournaments" in field_names


@pytest.mark.asyncio
async def test_help_brewing():
    bot = await _make_help_bot()
    cog = help_cog.Help(bot)
    ctx = DummyContext(bot)

    await cog.help_brewing.callback(cog, ctx)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Potion Panic" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "The Basics" in field_names
    assert "Ingredients" in field_names
    assert "Brewing Tips" in field_names


@pytest.mark.asyncio
async def test_race_upcoming(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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


async def _make_bet_env(tmp_path, num_racers=2):
    """Helper: create a bot, cog, context, race, and racers for bet tests."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=5)

    racers = []
    async with sessionmaker() as session:
        race = await repo.create_race(session, guild_id=GUILD_ID)
        for i in range(num_racers):
            r = await repo.create_racer(
                session, name=chr(65 + i), owner_id=i + 1, guild_id=GUILD_ID,
                speed=20 - i * 3, cornering=15, stamina=15,
            )
            racers.append(r)
        await repo.create_race_entries(session, race.id, [r.id for r in racers])

    return cog, ctx, sessionmaker, race, racers


@pytest.mark.asyncio
async def test_race_bet_win(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=20)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 80
    assert bet.racer_id == racers[0].id and bet.amount == 20
    assert bet.bet_type == "win"

    # Replacing a win bet refunds the old one
    await cog.race_bet_win(ctx, racer=racers[1].id, amount=30)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert wallet.balance == 70
    assert bet.racer_id == racers[1].id and bet.amount == 30


@pytest.mark.asyncio
async def test_race_bet_place(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_place(ctx, racer=racers[0].id, amount=25)

    async with sessionmaker() as session:
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert bet.bet_type == "place"
    assert bet.amount == 25


@pytest.mark.asyncio
async def test_race_bet_exacta(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_exacta(ctx, first=racers[0].id, second=racers[1].id, amount=15)

    async with sessionmaker() as session:
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert bet.bet_type == "exacta"
    import json
    picks = json.loads(bet.racer_ids)
    assert picks == [racers[0].id, racers[1].id]


@pytest.mark.asyncio
async def test_race_bet_trifecta(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path, num_racers=4)

    await cog.race_bet_trifecta(
        ctx, first=racers[0].id, second=racers[1].id,
        third=racers[2].id, amount=10,
    )

    async with sessionmaker() as session:
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert bet.bet_type == "trifecta"
    import json
    picks = json.loads(bet.racer_ids)
    assert picks == [racers[0].id, racers[1].id, racers[2].id]


@pytest.mark.asyncio
async def test_race_bet_superfecta(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path, num_racers=6)

    await cog.race_bet_superfecta(
        ctx,
        first=racers[0].id, second=racers[1].id, third=racers[2].id,
        fourth=racers[3].id, fifth=racers[4].id, sixth=racers[5].id,
        amount=5,
    )

    async with sessionmaker() as session:
        bet = (await session.execute(select(models.Bet))).scalars().first()

    assert bet.bet_type == "superfecta"


@pytest.mark.asyncio
async def test_race_bet_superfecta_rejected_small_field(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path, num_racers=4)

    await cog.race_bet_superfecta(
        ctx,
        first=racers[0].id, second=racers[1].id, third=racers[2].id,
        fourth=racers[3].id, fifth=999, sixth=998,
        amount=5,
    )

    # Field size check fires before pick validation
    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()
    assert len(bets) == 0
    assert any("6 racers" in str(m.get("content", "")) for m in ctx.sent)


@pytest.mark.asyncio
async def test_race_bet_duplicate_picks_rejected(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_exacta(
        ctx, first=racers[0].id, second=racers[0].id, amount=10,
    )

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()
    assert len(bets) == 0
    assert any("once" in str(m.get("content", "")).lower() for m in ctx.sent)


@pytest.mark.asyncio
async def test_race_bet_one_per_type_allowed(tmp_path: Path) -> None:
    """Players can have a win AND a place bet simultaneously."""
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=10)
    await cog.race_bet_place(ctx, racer=racers[0].id, amount=10)

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()

    assert len(bets) == 2
    bet_types = {b.bet_type for b in bets}
    assert bet_types == {"win", "place"}


@pytest.mark.asyncio
async def test_race_bet_same_type_replaces(tmp_path: Path) -> None:
    """Placing a second win bet refunds the first and replaces it."""
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=20)
    await cog.race_bet_win(ctx, racer=racers[1].id, amount=30)

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)

    assert len(bets) == 1
    assert bets[0].racer_id == racers[1].id
    assert bets[0].amount == 30
    # Started with 100, first bet took 20, refund gave back 20, second took 30
    assert wallet.balance == 70


@pytest.mark.asyncio
async def test_free_bet_when_broke(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)
    # Create wallet with 0 balance
    async with sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=0
        )

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=0)

    async with sessionmaker() as session:
        bet = (await session.execute(select(models.Bet))).scalars().first()
        wallet = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)

    assert bet is not None
    assert bet.is_free is True
    assert bet.amount == 10
    assert bet.bet_type == "win"
    assert wallet.balance == 0  # Nothing deducted


@pytest.mark.asyncio
async def test_free_bet_rejected_with_coins(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)
    # Default wallet has 100 coins

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=0)

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()

    assert len(bets) == 0
    assert any("balance is 0" in str(m.get("content", "")) for m in ctx.sent)


@pytest.mark.asyncio
async def test_free_bet_one_per_race(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)
    async with sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=0
        )

    # First free bet succeeds
    await cog.race_bet_win(ctx, racer=racers[0].id, amount=0)
    # Second free bet (different type) should be rejected
    ctx.sent.clear()
    await cog.race_bet_place(ctx, racer=racers[0].id, amount=0)

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()

    assert len(bets) == 1  # Only the first free bet
    assert any("already have a free bet" in str(m.get("content", "")) for m in ctx.sent)


@pytest.mark.asyncio
async def test_negative_amount_rejected(tmp_path: Path) -> None:
    cog, ctx, sessionmaker, race, racers = await _make_bet_env(tmp_path)

    await cog.race_bet_win(ctx, racer=racers[0].id, amount=-5)

    async with sessionmaker() as session:
        bets = (await session.execute(select(models.Bet))).scalars().all()

    assert len(bets) == 0
    assert any("positive" in str(m.get("content", "")).lower() for m in ctx.sent)


@pytest.mark.asyncio
async def test_give_coins_positive(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=100,
        retirement_threshold=65, bet_window=0, countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    target = types.SimpleNamespace(id=99, mention="@TestTarget")

    await cog.give_coins(ctx, user=target, amount=50)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, 99, GUILD_ID)
    # Default 100 + 50 given = 150
    assert wallet.balance == 150
    assert any("50" in str(m.get("content", "")) for m in ctx.sent)


@pytest.mark.asyncio
async def test_give_coins_negative(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=100,
        retirement_threshold=65, bet_window=0, countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    target = types.SimpleNamespace(id=99, mention="@TestTarget")

    await cog.give_coins(ctx, user=target, amount=-30)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, 99, GUILD_ID)
    assert wallet.balance == 70


@pytest.mark.asyncio
async def test_give_coins_overdraft_rejected(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=100,
        retirement_threshold=65, bet_window=0, countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    target = types.SimpleNamespace(id=99, mention="@TestTarget")

    await cog.give_coins(ctx, user=target, amount=-200)

    async with sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, 99, GUILD_ID)
    # Wallet was created with default 100, removal rejected, balance unchanged
    assert wallet.balance == 100
    assert any("Cannot remove" in str(m.get("content", "")) for m in ctx.sent)


@pytest.mark.asyncio
async def test_give_coins_zero_rejected(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=100,
        retirement_threshold=65, bet_window=0, countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    target = types.SimpleNamespace(id=99, mention="@TestTarget")

    await cog.give_coins(ctx, user=target, amount=0)

    assert any("must not be zero" in str(m.get("content", "")).lower() for m in ctx.sent)


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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
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

    async def fake_post(guild_id, placements, names=None, **kwargs):
        posted.append(placements)

    async def noop(*args, **kwargs):
        pass

    bot.scheduler = types.SimpleNamespace(
        sessionmaker=sessionmaker,
        _stream_commentary=fake_stream,
        _post_results=fake_post,
        _announce_injuries=noop,
        _announce_bet_results=noop,
        _dm_payouts=noop,
        _announce_placement_prizes=noop,
        _create_next_race=noop,
        active_races=set(),
        betting_races=set(),
        guild_settings=GuildSettingsResolver(sessionmaker, bot.settings),
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
async def test_edit_racer_owner(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="BotOwned", owner_id=999, guild_id=GUILD_ID
        )

    # Simulate a Member-like object for the owner parameter
    new_owner = types.SimpleNamespace(id=0)
    await cog.edit_racer(ctx, racer=racer.id, owner=new_owner)

    async with sessionmaker() as session:
        updated = await repo.get_racer(session, racer.id)

    assert updated.owner_id == 0


@pytest.mark.asyncio
async def test_race_info_bands(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = economy_cog.Economy(bot)
    await bot.add_cog(cog)
    ctx = DummyContext(bot)
    ctx.author = types.SimpleNamespace(id=42)

    # Set a per-guild override for default_wallet
    async with sessionmaker() as session:
        await core_repo.create_guild_settings(
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=200,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        max_racers_per_owner=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=5,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        max_racers_per_owner=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        racer_buy_base=20,
        racer_buy_multiplier=2,
        racer_sell_fraction=0.5,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=100)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=200,
        training_base=10,
        training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
        assert r.training_count == 1  # incremented on success


@pytest.mark.asyncio
async def test_stable_train_not_owner(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=5,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, training_base=10, training_multiplier=2)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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

    with patch("cogs.stable.random.random", return_value=0.0):
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
        assert r.training_count == 0  # NOT incremented on failure
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 200 - 30  # cost = 10 + 10*2 = 30


@pytest.mark.asyncio
async def test_stable_train_mood_floor(tmp_path: Path) -> None:
    """Mood at 1 stays at 1 after training (doesn't go below)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        training_base=10, training_multiplier=2,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    with patch("cogs.stable.random.random", return_value=0.99):
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=5, rest_cost=15)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
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
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(race_times=["12:00"], default_wallet=200, feed_cost=30)
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="OldTimer", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, mood=2, retired=True,
        )

    await cog.stable_feed.callback(cog, ctx, racer.id)

    assert ctx.sent
    assert "retired" in str(ctx.sent[0].get("content", "")).lower()


# ---------------------------------------------------------------------------
# /stable upgrade tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_upgrade_success(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        max_racers_per_owner=3, stable_upgrade_costs="500,1000,2000",
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=600,
        )

    await cog.stable_upgrade.callback(cog, ctx)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Upgraded" in embed.title

    async with sessionmaker() as session:
        pd = await repo.get_player_data(session, ctx.author.id, GUILD_ID)
        assert pd is not None
        assert pd.extra_slots == 1
        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 600 - 500


@pytest.mark.asyncio
async def test_stable_upgrade_at_max(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        max_racers_per_owner=3, stable_upgrade_costs="500,1000,2000",
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    # Already fully upgraded
    async with sessionmaker() as session:
        await repo.create_player_data(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, extra_slots=3,
        )

    await cog.stable_upgrade.callback(cog, ctx)

    assert ctx.sent
    assert "fully upgraded" in str(ctx.sent[0].get("content", "")).lower()


@pytest.mark.asyncio
async def test_stable_upgrade_insufficient_funds(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=50,
        max_racers_per_owner=3, stable_upgrade_costs="500,1000,2000",
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=50,
        )

    await cog.stable_upgrade.callback(cog, ctx)

    assert ctx.sent
    assert "Upgrading costs" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_buy_respects_upgraded_slots(tmp_path: Path) -> None:
    """After upgrading, a player can buy a 4th racer."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=2000,
        racer_buy_base=20, racer_buy_multiplier=2,
        max_racers_per_owner=3, stable_upgrade_costs="500,1000,2000",
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        # Player owns 3 racers (at base limit)
        for i in range(3):
            await repo.create_racer(
                session, name=f"R{i}", owner_id=ctx.author.id, guild_id=GUILD_ID,
                speed=5, cornering=5, stamina=5,
            )
        # An unowned racer to buy
        new_racer = await repo.create_racer(
            session, name="NewOne", owner_id=0, guild_id=GUILD_ID,
            speed=5, cornering=5, stamina=5,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=2000,
        )
        # Upgrade: 1 extra slot
        await repo.create_player_data(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, extra_slots=1,
        )

    await cog.stable_buy.callback(cog, ctx, new_racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Purchased" in embed.title


@pytest.mark.asyncio
async def test_stable_counts_retired_toward_limit(tmp_path: Path) -> None:
    """Retired racers count toward the stable slot limit."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=2000,
        racer_buy_base=20, racer_buy_multiplier=2,
        max_racers_per_owner=3, stable_upgrade_costs="500,1000,2000",
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        # 2 active + 1 retired = 3 total (at limit)
        await repo.create_racer(
            session, name="Active1", owner_id=ctx.author.id, guild_id=GUILD_ID,
        )
        await repo.create_racer(
            session, name="Active2", owner_id=ctx.author.id, guild_id=GUILD_ID,
        )
        await repo.create_racer(
            session, name="Retired1", owner_id=ctx.author.id, guild_id=GUILD_ID,
            retired=True,
        )
        target = await repo.create_racer(
            session, name="WantThis", owner_id=0, guild_id=GUILD_ID,
            speed=5, cornering=5, stamina=5,
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=2000,
        )

    await cog.stable_buy.callback(cog, ctx, target.id)

    assert ctx.sent
    # Should be rejected — stable is full
    assert "full" in str(ctx.sent[0].get("content", "")).lower()

    # Racer should still be unowned
    async with sessionmaker() as session:
        r = await repo.get_racer(session, target.id)
        assert r.owner_id == 0


# ---------------------------------------------------------------------------
# /stable breed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stable_breed_success(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        max_racers_per_owner=6, breeding_fee=25,
        breeding_cooldown=6, min_races_to_breed=5,
        max_foals_per_female=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        sire = await repo.create_racer(
            session, name="Dad", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=20, cornering=20, stamina=20, gender="M",
            career_length=30, peak_end=18, rank="B",
        )
        await repo.update_racer(session, sire.id, races_completed=10)
        dam = await repo.create_racer(
            session, name="Mom", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=25, cornering=25, stamina=25, gender="F",
            career_length=30, peak_end=18, rank="A",
        )
        await repo.update_racer(session, dam.id, races_completed=10)
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=500,
        )

    # Tiered breeding fee: B(100) + A(200) = 300
    await cog.stable_breed.callback(cog, ctx, sire.id, dam.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Foal" in embed.title

    # Verify state changes
    async with sessionmaker() as session:
        s = await repo.get_racer(session, sire.id)
        d = await repo.get_racer(session, dam.id)
        assert s.breed_cooldown == 6
        assert d.breed_cooldown == 6
        assert d.foal_count == 1

        w = await wallet_repo.get_wallet(session, ctx.author.id, GUILD_ID)
        assert w.balance == 500 - 300  # B(100) + A(200)

        # Foal should exist
        all_racers = await repo.get_stable_racers(
            session, ctx.author.id, GUILD_ID
        )
        foals = [r for r in all_racers if r.sire_id == sire.id]
        assert len(foals) == 1
        foal = foals[0]
        assert foal.dam_id == dam.id
        assert foal.name == "Mom's Foal"
        assert foal.training_count == 0


@pytest.mark.asyncio
async def test_stable_breed_insufficient_funds(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=10,
        max_racers_per_owner=6, breeding_fee=25,
        min_races_to_breed=5, max_foals_per_female=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        sire = await repo.create_racer(
            session, name="Dad", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M",
        )
        await repo.update_racer(session, sire.id, races_completed=10)
        dam = await repo.create_racer(
            session, name="Mom", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="F",
        )
        await repo.update_racer(session, dam.id, races_completed=10)
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=10,
        )

    await cog.stable_breed.callback(cog, ctx, sire.id, dam.id)

    assert ctx.sent
    assert "Breeding costs" in str(ctx.sent[0].get("content", ""))


@pytest.mark.asyncio
async def test_stable_breed_same_racer_rejected(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        max_racers_per_owner=6, breeding_fee=25,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Self", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M",
        )
        await repo.update_racer(session, racer.id, races_completed=10)

    await cog.stable_breed.callback(cog, ctx, racer.id, racer.id)

    assert ctx.sent
    assert "two different" in str(ctx.sent[0].get("content", "")).lower()


@pytest.mark.asyncio
async def test_stable_breed_validation_error(tmp_path: Path) -> None:
    """Breeding two males should be rejected by validation."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"], default_wallet=200,
        max_racers_per_owner=6, breeding_fee=25,
        min_races_to_breed=5, max_foals_per_female=3,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        r1 = await repo.create_racer(
            session, name="Boy1", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M",
        )
        await repo.update_racer(session, r1.id, races_completed=10)
        r2 = await repo.create_racer(
            session, name="Boy2", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M",
        )
        await repo.update_racer(session, r2.id, races_completed=10)
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    await cog.stable_breed.callback(cog, ctx, r1.id, r2.id)

    assert ctx.sent
    assert "not female" in str(ctx.sent[0].get("content", "")).lower()


# ---------------------------------------------------------------------------
# /stable view
# ---------------------------------------------------------------------------


async def _make_view_env(tmp_path, **racer_kwargs):
    """Set up bot, cog, session, racer for view tests."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    defaults = dict(
        name="Thunderhoof", owner_id=ctx.author.id, guild_id=GUILD_ID,
        speed=20, cornering=15, stamina=10,
    )
    defaults.update(racer_kwargs)
    async with sessionmaker() as session:
        racer = await repo.create_racer(session, **defaults)
    return cog, ctx, racer, sessionmaker


@pytest.mark.asyncio
async def test_stable_view_own_racer(tmp_path):
    cog, ctx, racer, _ = await _make_view_env(tmp_path)

    await cog.stable_view.callback(cog, ctx, racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Thunderhoof" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "Stats" in field_names
    assert "Temperament" in field_names
    assert "Mood" in field_names
    assert "Career" in field_names
    assert "Rank" in field_names
    assert "Description" in field_names
    # Training costs should be shown for non-retired racer
    assert "Training" in field_names
    training_field = next(f for f in embed.fields if f.name == "Training")
    assert "coins" in training_field.value
    assert "Speed 20\u219221" in training_field.value
    assert "Cornering 15\u219216" in training_field.value
    assert "Stamina 10\u219211" in training_field.value


@pytest.mark.asyncio
async def test_stable_view_retired_no_training_costs(tmp_path):
    """Retired racer should not show training cost lines."""
    cog, ctx, racer, sessionmaker = await _make_view_env(tmp_path)

    async with sessionmaker() as session:
        await repo.update_racer(
            session, racer.id, retired=True, races_completed=30
        )

    await cog.stable_view.callback(cog, ctx, racer.id)

    embed = ctx.sent[0].get("embed")
    training_field = next(f for f in embed.fields if f.name == "Training")
    assert "coins" not in training_field.value


@pytest.mark.asyncio
async def test_stable_view_other_racer(tmp_path):
    """Non-owner should be able to view any guild racer."""
    cog, ctx, racer, _ = await _make_view_env(tmp_path, owner_id=999)

    await cog.stable_view.callback(cog, ctx, racer.id)

    assert ctx.sent
    embed = ctx.sent[0].get("embed")
    assert embed is not None
    assert "Thunderhoof" in embed.title


@pytest.mark.asyncio
async def test_stable_view_not_found(tmp_path):
    cog, ctx, _, _ = await _make_view_env(tmp_path)

    await cog.stable_view.callback(cog, ctx, 99999)

    assert ctx.sent
    msg = str(ctx.sent[0].get("content", ""))
    assert "not found" in msg.lower()


@pytest.mark.asyncio
async def test_stable_view_injured_racer(tmp_path):
    cog, ctx, racer, sessionmaker = await _make_view_env(tmp_path)

    async with sessionmaker() as session:
        await repo.update_racer(
            session, racer.id, injuries="Twisted ankle", injury_races_remaining=3
        )

    await cog.stable_view.callback(cog, ctx, racer.id)

    embed = ctx.sent[0].get("embed")
    assert embed is not None
    # Red color for injured
    assert embed.color.value == 0xE74C3C
    field_names = [f.name for f in embed.fields]
    assert "Injury" in field_names
    injury_field = next(f for f in embed.fields if f.name == "Injury")
    assert "Twisted ankle" in injury_field.value
    assert "3" in injury_field.value


@pytest.mark.asyncio
async def test_stable_view_retired_racer(tmp_path):
    cog, ctx, racer, sessionmaker = await _make_view_env(tmp_path)

    async with sessionmaker() as session:
        await repo.update_racer(
            session, racer.id, retired=True, races_completed=30
        )

    await cog.stable_view.callback(cog, ctx, racer.id)

    embed = ctx.sent[0].get("embed")
    assert embed is not None
    # Gold color for retired
    assert embed.color.value == 0xF1C40F
    career_field = next(f for f in embed.fields if f.name == "Career")
    assert "Retired" in career_field.value


@pytest.mark.asyncio
async def test_stable_view_with_lineage(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        sire = await repo.create_racer(
            session, name="Papa", owner_id=1, guild_id=GUILD_ID
        )
        dam = await repo.create_racer(
            session, name="Mama", owner_id=2, guild_id=GUILD_ID, gender="F"
        )
        foal = await repo.create_racer(
            session, name="Baby", owner_id=ctx.author.id, guild_id=GUILD_ID,
            sire_id=sire.id, dam_id=dam.id,
        )

    await cog.stable_view.callback(cog, ctx, foal.id)

    embed = ctx.sent[0].get("embed")
    field_names = [f.name for f in embed.fields]
    assert "Lineage" in field_names
    lineage_field = next(f for f in embed.fields if f.name == "Lineage")
    assert "Papa" in lineage_field.value
    assert "Mama" in lineage_field.value


# ---------------------------------------------------------------------------
# /derby set-flavor (via settings set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_flavor(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    await cog.settings_set.callback(cog, ctx, "racer_flavor", "cyberpunk racing lizards")

    assert ctx.sent
    msg = str(ctx.sent[0].get("content", ""))
    assert "racer_flavor" in msg
    assert "cyberpunk racing lizards" in msg

    # Verify it was persisted
    async with sessionmaker() as session:
        gs = await core_repo.get_guild_settings(session, GUILD_ID)
    assert gs is not None
    assert gs.racer_flavor == "cyberpunk racing lizards"


@pytest.mark.asyncio
async def test_flavor_shows_in_settings(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    # Set the flavor first
    await cog.settings_set.callback(cog, ctx, "racer_flavor", "enchanted warhorses")
    ctx.sent.clear()

    # View settings
    await cog._show_settings(ctx)

    embed = ctx.sent[0].get("embed")
    assert embed is not None
    field_names = [f.name for f in embed.fields]
    assert "racer_flavor" in field_names
    flavor_field = next(f for f in embed.fields if f.name == "racer_flavor")
    assert "enchanted warhorses" in flavor_field.value


# ---------------------------------------------------------------------------
# LLM description integration tests
# ---------------------------------------------------------------------------


async def _make_view_env_with_flavor(tmp_path, flavor="cyberpunk lizards", **racer_kwargs):
    """Set up env with flavor set for description tests."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    stable_cog_inst = stable_cog.Stable(bot)
    derby_cog_inst = derby_cog.Derby(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        # Set flavor
        if flavor:
            await core_repo.create_guild_settings(session, guild_id=GUILD_ID, racer_flavor=flavor)
        defaults = dict(
            name="Thunderhoof", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=20, cornering=15, stamina=10,
        )
        defaults.update(racer_kwargs)
        racer = await repo.create_racer(session, **defaults)
    return stable_cog_inst, derby_cog_inst, ctx, racer, sessionmaker


@pytest.mark.asyncio
async def test_stable_view_triggers_description_gen(tmp_path):
    """View racer with no desc + flavor → generates description."""
    cog, _, ctx, racer, sessionmaker = await _make_view_env_with_flavor(tmp_path)

    with patch("cogs.stable.descriptions.generate_description", return_value="A sleek blue lizard.") as mock_gen:
        await cog.stable_view.callback(cog, ctx, racer.id)

    mock_gen.assert_called_once()
    embed = ctx.sent[0].get("embed")
    desc_field = next(f for f in embed.fields if f.name == "Description")
    assert "sleek blue lizard" in desc_field.value

    # Verify saved to DB
    async with sessionmaker() as session:
        r = await repo.get_racer(session, racer.id)
    assert r.description == "A sleek blue lizard."


@pytest.mark.asyncio
async def test_stable_view_no_flavor_no_gen(tmp_path):
    """No flavor set → no LLM call, shows hint."""
    cog, _, ctx, racer, _ = await _make_view_env_with_flavor(tmp_path, flavor=None)

    with patch("cogs.stable.descriptions.generate_description") as mock_gen:
        await cog.stable_view.callback(cog, ctx, racer.id)

    mock_gen.assert_not_called()
    embed = ctx.sent[0].get("embed")
    desc_field = next(f for f in embed.fields if f.name == "Description")
    assert "set a racer flavor" in desc_field.value.lower()


@pytest.mark.asyncio
async def test_stable_view_existing_description_no_regen(tmp_path):
    """Racer already has description → no LLM call."""
    cog, _, ctx, racer, _ = await _make_view_env_with_flavor(
        tmp_path, description="Already described."
    )

    with patch("cogs.stable.descriptions.generate_description") as mock_gen:
        await cog.stable_view.callback(cog, ctx, racer.id)

    mock_gen.assert_not_called()
    embed = ctx.sent[0].get("embed")
    desc_field = next(f for f in embed.fields if f.name == "Description")
    assert "Already described." in desc_field.value


@pytest.mark.asyncio
async def test_stable_view_gen_failure_graceful(tmp_path):
    """LLM fails → racer still shown, desc says 'No description yet.'"""
    cog, _, ctx, racer, _ = await _make_view_env_with_flavor(tmp_path)

    with patch("cogs.stable.descriptions.generate_description", return_value=None):
        await cog.stable_view.callback(cog, ctx, racer.id)

    embed = ctx.sent[0].get("embed")
    assert embed is not None
    desc_field = next(f for f in embed.fields if f.name == "Description")
    assert "No description yet." in desc_field.value


@pytest.mark.asyncio
async def test_add_racer_generates_description(tmp_path):
    """Admin add_racer with flavor set → description generated."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=42, mention="@TestUser")

    async with sessionmaker() as session:
        await core_repo.create_guild_settings(session, guild_id=GUILD_ID, racer_flavor="enchanted warhorses")

    with patch("cogs.derby.descriptions.generate_description", return_value="A golden stallion.") as mock_gen:
        await cog.add_racer.callback(cog, ctx, owner, "Goldie", False, 20, 15, 10, "Bold")

    mock_gen.assert_called_once()

    # Verify saved
    async with sessionmaker() as session:
        result = await session.execute(
            select(models.Racer).where(models.Racer.name == "Goldie")
        )
        racer = result.scalars().first()
    assert racer is not None
    assert racer.description == "A golden stallion."


@pytest.mark.asyncio
async def test_add_racer_no_flavor_no_description(tmp_path):
    """Admin add_racer without flavor → no description generated."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = derby_cog.Derby(bot)
    ctx = DummyContext(bot)
    owner = types.SimpleNamespace(id=42, mention="@TestUser")

    with patch("cogs.derby.descriptions.generate_description") as mock_gen:
        await cog.add_racer.callback(cog, ctx, owner, "Shadowmere", False, 20, 15, 10, "Bold")

    mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_breed_generates_foal_description(tmp_path):
    """Breeding with parent descriptions + flavor → foal gets blended description."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await core_repo.create_guild_settings(session, guild_id=GUILD_ID, racer_flavor="racing lizards")
        sire = await repo.create_racer(
            session, name="Papa", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M", speed=20, cornering=15, stamina=10,
            description="A cobalt-scaled lizard with chrome implants.",
        )
        dam = await repo.create_racer(
            session, name="Mama", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="F", speed=15, cornering=20, stamina=15,
            description="A lithe amber-eyed lizard with bioluminescent markings.",
        )
        await repo.update_racer(session, sire.id, races_completed=10)
        await repo.update_racer(session, dam.id, races_completed=10)
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    with patch("cogs.stable.descriptions.generate_description", return_value="A small lizard blending both parents.") as mock_gen:
        await cog.stable_breed.callback(cog, ctx, sire.id, dam.id)

    mock_gen.assert_called_once()
    call_kwargs = mock_gen.call_args
    assert call_kwargs.kwargs.get("sire_desc") is not None
    assert call_kwargs.kwargs.get("dam_desc") is not None
    assert "cobalt" in call_kwargs.kwargs["sire_desc"]
    assert "amber" in call_kwargs.kwargs["dam_desc"]


@pytest.mark.asyncio
async def test_breed_no_parent_desc_no_foal_desc(tmp_path):
    """Parents lack descriptions → no foal description generated."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await core_repo.create_guild_settings(session, guild_id=GUILD_ID, racer_flavor="racing lizards")
        sire = await repo.create_racer(
            session, name="Papa", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="M", speed=20, cornering=15, stamina=10,
        )
        dam = await repo.create_racer(
            session, name="Mama", owner_id=ctx.author.id, guild_id=GUILD_ID,
            gender="F", speed=15, cornering=20, stamina=15,
        )
        await repo.update_racer(session, sire.id, races_completed=10)
        await repo.update_racer(session, dam.id, races_completed=10)
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=200,
        )

    with patch("cogs.stable.descriptions.generate_description") as mock_gen:
        await cog.stable_breed.callback(cog, ctx, sire.id, dam.id)

    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# Rank recalculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_training_recalculates_rank(tmp_path):
    """Training a stat past a rank threshold should promote the racer."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    # Create a racer right at the C/B boundary: total=46 (C-Rank), training +1 → 47 (B-Rank)
    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Climber", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=16, cornering=15, stamina=15, rank="C",
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=500,
        )

    stat_choice = discord.app_commands.Choice(name="Speed", value="speed")
    # Patch random to prevent training failure
    with patch("cogs.stable.random.random", return_value=1.0):
        await cog.stable_train.callback(cog, ctx, racer.id, stat_choice)

    # Check rank was updated
    async with sessionmaker() as session:
        updated = await repo.get_racer(session, racer.id)
    assert updated.speed == 17
    assert updated.rank == "B"

    # Check embed shows rank up
    embed = ctx.sent[0].get("embed")
    field_names = [f.name for f in embed.fields]
    assert any("Rank" in name for name in field_names)


@pytest.mark.asyncio
async def test_training_no_rank_change(tmp_path):
    """Training that doesn't cross a threshold shouldn't show rank change."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, active_races=set(), betting_races=set(), guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    # Total=30 (C-Rank), training +1 → 31 still C-Rank
    async with sessionmaker() as session:
        racer = await repo.create_racer(
            session, name="Steady", owner_id=ctx.author.id, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, rank="C",
        )
        await wallet_repo.create_wallet(
            session, user_id=ctx.author.id, guild_id=GUILD_ID, balance=500,
        )

    stat_choice = discord.app_commands.Choice(name="Speed", value="speed")
    with patch("cogs.stable.random.random", return_value=1.0):
        await cog.stable_train.callback(cog, ctx, racer.id, stat_choice)

    embed = ctx.sent[0].get("embed")
    field_names = [f.name for f in embed.fields]
    assert not any("Rank" in name for name in field_names)


@pytest.mark.asyncio
async def test_stable_browse_rank_filter(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await repo.create_racer(
            session, name="Slow", owner_id=0, guild_id=GUILD_ID,
            speed=5, cornering=5, stamina=5, rank="D",
        )
        await repo.create_racer(
            session, name="Fast", owner_id=0, guild_id=GUILD_ID,
            speed=20, cornering=20, stamina=20, rank="B",
        )

    await cog.stable_browse.callback(cog, ctx, rank="D")
    embed = ctx.sent[0].get("embed")
    assert "D-Rank" in embed.title
    assert len(embed.fields) == 1
    assert "Slow" in embed.fields[0].name


@pytest.mark.asyncio
async def test_stable_browse_gender_filter(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        await repo.create_racer(
            session, name="Stallion", owner_id=0, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, gender="M",
        )
        await repo.create_racer(
            session, name="Mare", owner_id=0, guild_id=GUILD_ID,
            speed=10, cornering=10, stamina=10, gender="F",
        )

    await cog.stable_browse.callback(cog, ctx, gender="F")
    embed = ctx.sent[0].get("embed")
    assert "Female" in embed.title
    assert len(embed.fields) == 1
    assert "Mare" in embed.fields[0].name


@pytest.mark.asyncio
async def test_stable_browse_no_filters(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None)
    bot.settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        retirement_threshold=65,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
    )
    bot.scheduler = types.SimpleNamespace(sessionmaker=sessionmaker, guild_settings=GuildSettingsResolver(sessionmaker, bot.settings))
    cog = stable_cog.Stable(bot)
    ctx = DummyContext(bot)

    async with sessionmaker() as session:
        for i in range(3):
            await repo.create_racer(
                session, name=f"Racer{i}", owner_id=0, guild_id=GUILD_ID,
                speed=10, cornering=10, stamina=10,
            )

    await cog.stable_browse.callback(cog, ctx)
    embed = ctx.sent[0].get("embed")
    assert embed.title == "Racers For Sale"
    assert len(embed.fields) == 3
