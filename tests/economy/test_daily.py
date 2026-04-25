"""Tests for the /daily command and daily reward generation."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
import pytest

from config import Settings
from derby import logic, repositories as repo
from derby.models import DailyReward, GuildSettings, Racer
from derby.scheduler import DerbyScheduler
from economy import repositories as wallet_repo


GUILD_ID = 1
USER_ID = 100


class DummyChannel:
    def __init__(self, name: str = "general") -> None:
        self.name = name
        self.messages: list = []

    async def send(self, content=None, *, embed=None) -> None:
        if embed is not None:
            self.messages.append(embed)
        elif content is not None:
            self.messages.append(content)


class DummyGuild:
    def __init__(self, gid: int) -> None:
        self.id = gid
        self.system_channel = DummyChannel()
        self.text_channels = [self.system_channel]


class DummyUser:
    def __init__(self, uid: int) -> None:
        self.id = uid

    async def send(self, *a, **kw):
        pass


class DummyBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guilds: list[DummyGuild] = []
        self.users: dict[int, DummyUser] = {}
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("test")

    def get_guild(self, gid: int):
        for g in self.guilds:
            if g.id == gid:
                return g
        return None

    def get_user(self, uid: int):
        return self.users.get(uid)


class DummyContext:
    """Minimal mock for discord.ext.commands.Context."""

    def __init__(self, user_id: int, guild_id: int) -> None:
        self.author = SimpleNamespace(id=user_id)
        self.guild = SimpleNamespace(id=guild_id)
        self.responses: list = []

    async def defer(self, *, ephemeral=False):
        pass

    async def send(self, content=None, *, embed=None, ephemeral=False):
        self.responses.append(embed if embed else content)


def _make_bot() -> DummyBot:
    settings = Settings(
        race_times=["09:00"],
        default_wallet=100,
        retirement_threshold=101,
        bet_window=0,
        countdown_total=0,
        commentary_delay=0,
        min_pool_size=0,
        daily_min=15,
        daily_max=30,
    )
    bot = DummyBot(settings)
    bot.guilds.append(DummyGuild(GUILD_ID))
    return bot


async def _make_scheduler(bot: DummyBot, tmp_path: Path) -> DerbyScheduler:
    sched = DerbyScheduler(bot, db_path=str(tmp_path / "db.sqlite"))
    await sched._init_db()
    bot.scheduler = sched
    return sched


# ---------------------------------------------------------------------------
# Rank multiplier unit tests
# ---------------------------------------------------------------------------


def test_daily_rank_multiplier_d():
    assert logic.daily_rank_multiplier("D") == 1


def test_daily_rank_multiplier_c():
    assert logic.daily_rank_multiplier("C") == 2


def test_daily_rank_multiplier_b():
    assert logic.daily_rank_multiplier("B") == 3


def test_daily_rank_multiplier_a():
    assert logic.daily_rank_multiplier("A") == 4


def test_daily_rank_multiplier_s():
    assert logic.daily_rank_multiplier("S") == 5


def test_daily_rank_multiplier_none():
    """None rank defaults to D (1x)."""
    assert logic.daily_rank_multiplier(None) == 1


# ---------------------------------------------------------------------------
# Daily reward generation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_dailies_creates_reward(tmp_path: Path) -> None:
    """Midnight generation creates a reward for a player who owns a racer."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Thunderhooves", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )

    with patch("random.randint", return_value=20):
        await sched._generate_dailies()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward is not None
        assert reward.amount == 20  # D-rank = 1x * 20
        assert reward.racer_name == "Thunderhooves"
        assert reward.claimed is False


@pytest.mark.asyncio
async def test_generate_dailies_rank_multiplier(tmp_path: Path) -> None:
    """S-rank racer gets 5x the base reward."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Champion", owner_id=USER_ID,
            speed=28, cornering=27, stamina=27, rank="S",
        )
        session.add(racer)
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )

    with patch("random.randint", return_value=25):
        await sched._generate_dailies()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward is not None
        assert reward.amount == 125  # S-rank = 5x * 25


@pytest.mark.asyncio
async def test_generate_dailies_picks_best_racer(tmp_path: Path) -> None:
    """Should pick the highest power racer when player owns multiple."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        weak = Racer(
            guild_id=GUILD_ID, name="Slowpoke", owner_id=USER_ID,
            speed=5, cornering=5, stamina=5, rank="D",
        )
        strong = Racer(
            guild_id=GUILD_ID, name="Champion", owner_id=USER_ID,
            speed=25, cornering=20, stamina=20, rank="B",
        )
        session.add_all([weak, strong])
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )

    with patch("random.randint", return_value=20):
        await sched._generate_dailies()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward.racer_name == "Champion"
        assert reward.amount == 60  # B-rank = 3x * 20


@pytest.mark.asyncio
async def test_generate_dailies_skips_existing(tmp_path: Path) -> None:
    """Won't overwrite an already-generated reward for today."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Thunderhooves", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )
        await repo.create_daily_reward(
            session,
            user_id=USER_ID, guild_id=GUILD_ID, date=today,
            racer_id=racer.id, racer_name="Thunderhooves",
            amount=999, flavor_text="Already generated",
        )

    await sched._generate_dailies()

    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward.amount == 999  # Unchanged


@pytest.mark.asyncio
async def test_generate_dailies_no_racer_player(tmp_path: Path) -> None:
    """Players with wallets but no racers get base reward."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=50,
        )

    with patch("random.randint", return_value=22):
        await sched._generate_dailies()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward is not None
        assert reward.amount == 22  # No multiplier
        assert reward.racer_id is None
        assert reward.racer_name is None


@pytest.mark.asyncio
async def test_generate_dailies_with_flavor(tmp_path: Path) -> None:
    """When racer_flavor is set, LLM generates flavor text."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        gs = GuildSettings(guild_id=GUILD_ID, racer_flavor="cyberpunk lizards")
        session.add(gs)
        racer = Racer(
            guild_id=GUILD_ID, name="NeonScale", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )

    with patch("random.randint", return_value=20), \
         patch("derby.descriptions.generate_daily_flavor", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "NeonScale dug up a corroded circuit board behind a dumpster."
        await sched._generate_dailies()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert "corroded circuit board" in reward.flavor_text
    mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_generate_dailies_no_flavor(tmp_path: Path) -> None:
    """Without racer_flavor, generic text is used (no LLM call)."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        racer = Racer(
            guild_id=GUILD_ID, name="Thunderhooves", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )

    with patch("random.randint", return_value=20), \
         patch("derby.descriptions.generate_daily_flavor", new_callable=AsyncMock) as mock_llm:
        await sched._generate_dailies()

    mock_llm.assert_not_called()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert "Thunderhooves" in reward.flavor_text
        assert "exploring" in reward.flavor_text


# ---------------------------------------------------------------------------
# /daily command tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_claim_success(tmp_path: Path) -> None:
    """Claiming a pre-generated reward adds coins and marks it claimed."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )
        racer = Racer(
            guild_id=GUILD_ID, name="Thunderhooves", owner_id=USER_ID,
            speed=10, cornering=8, stamina=5, rank="D",
        )
        session.add(racer)
        await session.commit()
        await repo.create_daily_reward(
            session,
            user_id=USER_ID, guild_id=GUILD_ID, date=today,
            racer_id=racer.id, racer_name="Thunderhooves",
            amount=45, flavor_text="Thunderhooves found a shiny gem!",
        )

    from cogs.economy import Economy
    cog = Economy(bot)
    ctx = DummyContext(USER_ID, GUILD_ID)
    await cog.daily.callback(cog, ctx)

    # Verify coins were added
    async with sched.sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, USER_ID, GUILD_ID)
        assert wallet.balance == 145  # 100 + 45

        reward = await repo.get_daily_reward(session, USER_ID, GUILD_ID, today)
        assert reward.claimed is True

    # Verify embed was sent
    assert len(ctx.responses) == 1
    assert isinstance(ctx.responses[0], discord.Embed)


@pytest.mark.asyncio
async def test_daily_already_claimed(tmp_path: Path) -> None:
    """Second claim on same day is rejected."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with sched.sessionmaker() as session:
        await wallet_repo.create_wallet(
            session, user_id=USER_ID, guild_id=GUILD_ID, balance=100,
        )
        await repo.create_daily_reward(
            session,
            user_id=USER_ID, guild_id=GUILD_ID, date=today,
            amount=45, flavor_text="Already claimed!",
            claimed=True,
        )

    from cogs.economy import Economy
    cog = Economy(bot)
    ctx = DummyContext(USER_ID, GUILD_ID)
    await cog.daily.callback(cog, ctx)

    assert "already claimed" in ctx.responses[0].lower()

    # Balance unchanged
    async with sched.sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, USER_ID, GUILD_ID)
        assert wallet.balance == 100


@pytest.mark.asyncio
async def test_daily_fallback_new_player(tmp_path: Path) -> None:
    """Player with no pre-generated reward gets one generated on the spot."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    from cogs.economy import Economy
    cog = Economy(bot)
    ctx = DummyContext(USER_ID, GUILD_ID)

    with patch("random.randint", return_value=20):
        await cog.daily.callback(cog, ctx)

    # Wallet auto-created with default + daily reward
    async with sched.sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, USER_ID, GUILD_ID)
        assert wallet.balance == 120  # 100 default + 20 daily

    assert len(ctx.responses) == 1
    assert isinstance(ctx.responses[0], discord.Embed)
