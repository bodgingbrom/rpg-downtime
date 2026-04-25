"""Tests for the /stable report command."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

from config import Settings
from derby import logic, repositories as repo
from derby.models import Racer, Tournament, TournamentEntry
from derby.scheduler import DerbyScheduler


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


def _field_value(embed: discord.Embed, name_contains: str) -> str | None:
    """Find a field by partial name match and return its value."""
    for f in embed.fields:
        if name_contains in f.name:
            return f.value
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_empty_stable(tmp_path: Path) -> None:
    """Handles player with no racers."""
    bot = _make_bot()
    await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    assert ctx.sent
    assert "don't own" in ctx.sent[0]["content"]


@pytest.mark.asyncio
async def test_report_healthy_racer(tmp_path: Path) -> None:
    """Shows green ready status for healthy racer."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        await repo.create_racer(
            session, name="Thunderhooves", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=15, cornering=12, stamina=10,
            rank="B", career_length=30,
        )

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert status is not None
    assert "\U0001f7e2" in status  # green circle
    assert "Thunderhooves" in status
    assert "Ready" in status


@pytest.mark.asyncio
async def test_report_injured_racer(tmp_path: Path) -> None:
    """Shows red injured status with race count."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        r = await repo.create_racer(
            session, name="Moonshine", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=10, cornering=10, stamina=10,
            rank="C", career_length=30,
        )
        await repo.update_racer(
            session, r.id, injuries="pulled muscle",
            injury_races_remaining=2,
        )

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "\U0001f534" in status  # red circle
    assert "pulled muscle" in status
    assert "2 races" in status


@pytest.mark.asyncio
async def test_report_retiring_soon(tmp_path: Path) -> None:
    """Shows warning when 3 or fewer races left."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        r = await repo.create_racer(
            session, name="Quicksilver", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=20, cornering=18, stamina=15,
            rank="A", career_length=30,
        )
        await repo.update_racer(session, r.id, races_completed=28)

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "\u26a0\ufe0f" in status  # warning
    assert "Retiring Soon" in status
    assert "2 races left" in status


@pytest.mark.asyncio
async def test_report_declining_racer(tmp_path: Path) -> None:
    """Shows decline indicator with penalty."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        r = await repo.create_racer(
            session, name="Brimstone", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=18, cornering=16, stamina=14,
            rank="B", career_length=30, peak_end=18,
        )
        await repo.update_racer(session, r.id, races_completed=21)

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "\U0001f4c9" in status  # chart declining
    assert "Declining (-3)" in status


@pytest.mark.asyncio
async def test_report_training_needed(tmp_path: Path) -> None:
    """Shows training status for bred racers below the gate."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        # Bred racer (has sire_id) with low training
        await repo.create_racer(
            session, name="Daisy Jr", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=8, cornering=6, stamina=5,
            rank="D", career_length=30, sire_id=99,
            training_count=2,
        )

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "\U0001f7e1" in status  # yellow
    assert "Training: 2/5" in status


@pytest.mark.asyncio
async def test_report_breed_cooldown(tmp_path: Path) -> None:
    """Shows breed cooldown when active."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        r = await repo.create_racer(
            session, name="BreedCool", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=15, cornering=12, stamina=10,
            rank="B", career_length=30,
        )
        await repo.update_racer(session, r.id, breed_cooldown=4)

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "Breed cooldown: 4 races" in status


@pytest.mark.asyncio
async def test_report_tournament_registered(tmp_path: Path) -> None:
    """Shows registered status for tournaments."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        r = await repo.create_racer(
            session, name="Champ", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=20, cornering=18, stamina=15,
            rank="B", career_length=30,
        )
        t = Tournament(guild_id=GUILD_ID, rank="B", status="pending")
        session.add(t)
        await session.commit()
        await session.refresh(t)

        entry = TournamentEntry(
            tournament_id=t.id, racer_id=r.id,
            owner_id=USER_ID, is_pool_filler=False,
        )
        session.add(entry)
        await session.commit()

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    tournaments = _field_value(embed, "Tournament")
    assert tournaments is not None
    assert "\u2705" in tournaments  # checkmark
    assert "Registered" in tournaments


@pytest.mark.asyncio
async def test_report_tournament_not_registered(tmp_path: Path) -> None:
    """Shows not-registered status for eligible but unregistered."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        await repo.create_racer(
            session, name="Eligible", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=20, cornering=18, stamina=15,
            rank="A", career_length=30,
        )
        t = Tournament(guild_id=GUILD_ID, rank="A", status="pending")
        session.add(t)
        await session.commit()

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    tournaments = _field_value(embed, "Tournament")
    assert tournaments is not None
    assert "\u274c" in tournaments  # X
    assert "Not registered" in tournaments


@pytest.mark.asyncio
async def test_report_retired_count(tmp_path: Path) -> None:
    """Shows collapsed count of retired racers."""
    bot = _make_bot()
    sched = await _make_scheduler(bot, tmp_path)
    from cogs.stable import Stable

    async with sched.sessionmaker() as session:
        # Active racer
        await repo.create_racer(
            session, name="Active", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=10, cornering=10, stamina=10,
            rank="D", career_length=30,
        )
        # Two retired racers
        r1 = await repo.create_racer(
            session, name="OldTimer", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=5, cornering=5, stamina=5,
            rank="D", career_length=30, retired=True,
        )
        r2 = await repo.create_racer(
            session, name="Legend", owner_id=USER_ID,
            guild_id=GUILD_ID, speed=5, cornering=5, stamina=5,
            rank="D", career_length=30, retired=True,
        )

    cog = Stable(bot)
    ctx = DummyContext(bot)
    await cog.stable_report.callback(cog, ctx)
    embed = _get_embed(ctx)
    status = _field_value(embed, "Racer Status")
    assert "2 retired racers" in status
    # Footer should show counts
    assert "Active: 1" in embed.footer.text
    assert "Retired: 2" in embed.footer.text
