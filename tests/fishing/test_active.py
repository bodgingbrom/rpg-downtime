"""Tests for fishing/active.py — runner helpers + startup cleanup.

The asyncio loop in ``ActiveFishingRunner._run`` is intentionally not
covered (race-y, time-dependent). The interesting bits — name resolution,
post-target lookup, orphan cleanup — are testable in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fishing import repositories as fish_repo
from fishing.active import cleanup_orphaned_sessions

GUILD_ID = 100
USER_ID = 42


# ---------------------------------------------------------------------------
# _resolve_display_name
# ---------------------------------------------------------------------------


def test_resolve_display_name_uses_snapshot_when_set(mock_runner):
    fs = SimpleNamespace(user_id=USER_ID, guild_id=GUILD_ID, angler_name="Bob")
    assert mock_runner._resolve_display_name(fs) == "Bob"


def test_resolve_display_name_falls_back_to_member_cache(mock_runner):
    """No snapshot → look up via guild.get_member(user_id).display_name."""
    member = SimpleNamespace(display_name="GuildMember")
    guild = MagicMock()
    guild.get_member.return_value = member
    mock_runner.bot.get_guild = lambda _gid: guild

    fs = SimpleNamespace(user_id=USER_ID, guild_id=GUILD_ID, angler_name=None)
    assert mock_runner._resolve_display_name(fs) == "GuildMember"
    guild.get_member.assert_called_once_with(USER_ID)


def test_resolve_display_name_returns_angler_when_unknown(mock_runner):
    """No snapshot, no member cache → "Angler" sentinel."""
    fs = SimpleNamespace(user_id=USER_ID, guild_id=GUILD_ID, angler_name=None)
    # mock_runner.bot.get_guild already returns None by default
    assert mock_runner._resolve_display_name(fs) == "Angler"


# ---------------------------------------------------------------------------
# _get_post_target
# ---------------------------------------------------------------------------


def test_get_post_target_returns_thread_when_set(mock_runner):
    """Active sessions have a dedicated thread — prefer it over channel_id."""
    thread = MagicMock(name="thread_object")
    channels: dict[int, MagicMock] = {555: thread}
    mock_runner.bot.get_channel = lambda cid: channels.get(cid)

    fs = SimpleNamespace(thread_id=555, channel_id=999)
    assert mock_runner._get_post_target(fs) is thread


def test_get_post_target_falls_back_to_channel_when_thread_missing(mock_runner):
    """Thread deleted/archived → fall back to original channel_id."""
    channel = MagicMock(name="channel_object")
    channels: dict[int, MagicMock | None] = {555: None, 999: channel}
    mock_runner.bot.get_channel = lambda cid: channels.get(cid)

    fs = SimpleNamespace(thread_id=555, channel_id=999)
    assert mock_runner._get_post_target(fs) is channel


def test_get_post_target_uses_channel_when_no_thread_id(mock_runner):
    """AFK sessions have no thread — go straight to channel_id."""
    channel = MagicMock(name="channel_object")
    mock_runner.bot.get_channel = lambda cid: channel if cid == 999 else None

    fs = SimpleNamespace(thread_id=None, channel_id=999)
    assert mock_runner._get_post_target(fs) is channel


# ---------------------------------------------------------------------------
# cleanup_orphaned_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_orphaned_sessions_refunds_bait_and_marks_inactive(
    sessionmaker,
):
    """Bot-restart recovery: leftover active-mode session is ended, bait refunded."""
    async with sessionmaker() as session:
        now = datetime.now(timezone.utc)
        # Player has 2 worms in inventory before the orphan refund
        await fish_repo.add_bait(session, USER_ID, GUILD_ID, "worm", 2)
        # Orphaned active-mode session with 3 bait remaining
        await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=3, channel_id=999, message_id=1,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
            mode="active",
        )

    bot = SimpleNamespace(scheduler=SimpleNamespace(sessionmaker=sessionmaker))
    count = await cleanup_orphaned_sessions(bot)
    assert count == 1

    async with sessionmaker() as session:
        # Bait refunded: 2 (existing) + 3 (refund) = 5
        bait = await fish_repo.get_bait(session, USER_ID, GUILD_ID, "worm")
        assert bait.quantity == 5
        # No more orphans
        orphans = await fish_repo.get_orphaned_active_sessions(session)
        assert orphans == []


@pytest.mark.asyncio
async def test_cleanup_orphaned_sessions_returns_zero_when_none(sessionmaker):
    bot = SimpleNamespace(scheduler=SimpleNamespace(sessionmaker=sessionmaker))
    count = await cleanup_orphaned_sessions(bot)
    assert count == 0


@pytest.mark.asyncio
async def test_cleanup_orphaned_sessions_skips_afk_sessions(sessionmaker):
    """AFK sessions are scheduler-driven and survive bot restart — don't end them."""
    async with sessionmaker() as session:
        now = datetime.now(timezone.utc)
        await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=5, channel_id=999, message_id=1,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
            mode="afk",
        )

    bot = SimpleNamespace(scheduler=SimpleNamespace(sessionmaker=sessionmaker))
    count = await cleanup_orphaned_sessions(bot)
    assert count == 0

    async with sessionmaker() as session:
        afk = await fish_repo.get_active_session(session, USER_ID, GUILD_ID)
    assert afk is not None
    assert afk.active is True
