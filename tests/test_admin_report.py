"""Tests for the admin reporting suite (command usage analytics)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from config import Settings
from derby import repositories as repo
from derby.models import CommandLog
from derby.scheduler import DerbyScheduler

# Admin reporting is cross-cutting (touches every game's tables) but lives
# at the top level rather than under tests/derby/. Mark explicitly so
# `pytest -m admin` keeps working without filename-keyword scanning.
pytestmark = pytest.mark.admin


GUILD_ID = 1
USER_ID = 100
USER_ID_2 = 200


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


class DummyBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.guilds: list[DummyGuild] = []
        self.loop = asyncio.get_event_loop()
        self.logger = logging.getLogger("test")

    def get_guild(self, gid: int):
        for g in self.guilds:
            if g.id == gid:
                return g
        return None


class DummyContext:
    def __init__(self, bot, user_id: int = USER_ID) -> None:
        self.bot = bot
        self.author = SimpleNamespace(id=user_id, display_name="TestUser")
        self.guild = SimpleNamespace(id=GUILD_ID)
        self.sent: list = []
        self.interaction = None
        self.channel = DummyChannel()

    async def defer(self, **kwargs) -> None:
        pass

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append({"content": content, **kwargs})


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


def _get_embed(ctx: DummyContext) -> discord.Embed:
    """Extract the embed from the last sent message."""
    assert ctx.sent, "No messages sent"
    return ctx.sent[-1].get("embed")


# ---------------------------------------------------------------------------
# Repository-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_command_creates_entry(tmp_path: Path) -> None:
    """log_command writes a row to command_logs."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        entry = await repo.log_command(
            session,
            guild_id=GUILD_ID,
            user_id=USER_ID,
            command="stable report",
            cog="Derby",
        )
        assert entry.id is not None
        assert entry.command == "stable report"
        assert entry.cog == "Derby"
        assert entry.guild_id == GUILD_ID
        assert entry.user_id == USER_ID


@pytest.mark.asyncio
async def test_command_usage_counts(tmp_path: Path) -> None:
    """get_command_usage returns correct counts and unique users."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        for _ in range(3):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID_2,
            command="bet", cog="Derby",
        )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID,
            command="daily", cog="Economy",
        )

        since = datetime.utcnow() - timedelta(hours=1)
        rows = await repo.get_command_usage(session, GUILD_ID, since)

    # bet should be first (4 uses, 2 users), daily second (1 use, 1 user)
    assert len(rows) == 2
    assert rows[0][0] == "bet"
    assert rows[0][1] == 4  # count
    assert rows[0][2] == 2  # unique users
    assert rows[1][0] == "daily"
    assert rows[1][1] == 1


@pytest.mark.asyncio
async def test_command_usage_respects_date_filter(tmp_path: Path) -> None:
    """Old entries are excluded by the since parameter."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        # Recent command
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID,
            command="bet", cog="Derby",
        )
        # Old command — insert directly with old timestamp
        old_entry = CommandLog(
            guild_id=GUILD_ID, user_id=USER_ID, command="daily", cog="Economy",
            created_at=datetime.utcnow() - timedelta(days=30),
        )
        session.add(old_entry)
        await session.commit()

        since = datetime.utcnow() - timedelta(days=7)
        rows = await repo.get_command_usage(session, GUILD_ID, since)

    assert len(rows) == 1
    assert rows[0][0] == "bet"


@pytest.mark.asyncio
async def test_player_activity_counts(tmp_path: Path) -> None:
    """get_player_activity returns correct per-user counts."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        for _ in range(5):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        for _ in range(2):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID_2,
                command="daily", cog="Economy",
            )

        since = datetime.utcnow() - timedelta(hours=1)
        rows = await repo.get_player_activity(session, GUILD_ID, since)

    assert len(rows) == 2
    assert rows[0][0] == USER_ID  # most active
    assert rows[0][1] == 5
    assert rows[1][0] == USER_ID_2
    assert rows[1][1] == 2


@pytest.mark.asyncio
async def test_player_top_command(tmp_path: Path) -> None:
    """get_player_top_command returns the most-used command for a player."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        for _ in range(3):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID,
            command="daily", cog="Economy",
        )

        since = datetime.utcnow() - timedelta(hours=1)
        top = await repo.get_player_top_command(session, GUILD_ID, USER_ID, since)

    assert top == "bet"


@pytest.mark.asyncio
async def test_weekly_totals(tmp_path: Path) -> None:
    """get_weekly_totals returns correct aggregates."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)

    async with sched.sessionmaker() as session:
        for _ in range(3):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID_2,
            command="daily", cog="Economy",
        )

        now = datetime.utcnow()
        start = now - timedelta(hours=1)
        total_cmds, unique_users = await repo.get_weekly_totals(
            session, GUILD_ID, start, now + timedelta(minutes=1),
        )

    assert total_cmds == 4
    assert unique_users == 2


# ---------------------------------------------------------------------------
# End-to-end command tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_usage_command(tmp_path: Path) -> None:
    """End-to-end: logs commands, runs /reports usage, checks embed."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.reports import Reports

    async with sched.sessionmaker() as session:
        for _ in range(3):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID_2,
            command="stable report", cog="Derby",
        )

    cog = Reports(bot)
    ctx = DummyContext(bot)
    await cog.report_usage.callback(cog, ctx, days=7)

    embed = _get_embed(ctx)
    assert embed is not None
    assert "Command Usage" in embed.title
    assert "/bet" in embed.description
    assert "3" in embed.description
    assert "/stable report" in embed.description


@pytest.mark.asyncio
async def test_admin_activity_command(tmp_path: Path) -> None:
    """End-to-end: logs commands, runs /reports activity, checks embed."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.reports import Reports

    async with sched.sessionmaker() as session:
        for _ in range(5):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        await repo.log_command(
            session, guild_id=GUILD_ID, user_id=USER_ID_2,
            command="daily", cog="Economy",
        )

    cog = Reports(bot)
    ctx = DummyContext(bot)
    await cog.report_activity.callback(cog, ctx, days=7)

    embed = _get_embed(ctx)
    assert embed is not None
    assert "Player Activity" in embed.title
    assert f"<@{USER_ID}>" in embed.description
    assert "5" in embed.description
    assert "2 active players" in embed.footer.text


@pytest.mark.asyncio
async def test_admin_trends_command(tmp_path: Path) -> None:
    """End-to-end: two periods of data, checks trend comparison."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.reports import Reports

    now = datetime.utcnow()
    async with sched.sessionmaker() as session:
        # Current period commands (last 7 days)
        for _ in range(5):
            await repo.log_command(
                session, guild_id=GUILD_ID, user_id=USER_ID,
                command="bet", cog="Derby",
            )
        # Previous period commands (8-14 days ago)
        for _ in range(3):
            old_entry = CommandLog(
                guild_id=GUILD_ID, user_id=USER_ID_2,
                command="daily", cog="Economy",
                created_at=now - timedelta(days=10),
            )
            session.add(old_entry)
        await session.commit()

    cog = Reports(bot)
    ctx = DummyContext(bot)
    await cog.report_trends.callback(cog, ctx, days=14)

    embed = _get_embed(ctx)
    assert embed is not None
    assert "Usage Trends" in embed.title
    assert "Current" in embed.description
    assert "Previous" in embed.description
    assert "Change" in embed.description


@pytest.mark.asyncio
async def test_report_usage_empty(tmp_path: Path) -> None:
    """Usage report handles no data gracefully."""
    bot = _make_bot()
    await _make_scheduler(bot, tmp_path)
    from cogs.reports import Reports

    cog = Reports(bot)
    ctx = DummyContext(bot)
    await cog.report_usage.callback(cog, ctx, days=7)

    assert ctx.sent
    assert "No command usage" in ctx.sent[0]["content"]
