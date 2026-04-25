"""CRUD tests for fishing/repositories.py.

Covers the most-used and trickiest paths: idempotent get-or-create,
bait UniqueConstraint upsert, session lifecycle, the AFK-only filter on
``get_all_due_sessions``, fish-catch upsert aggregation, daily summary
one-row-per-day invariant, haiku round-trip, legendary lifecycle, and
orphan recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fishing import repositories as fish_repo

GUILD_ID = 100
USER_ID = 1
USER_ID_2 = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_get_or_create_player_idempotent(sessionmaker):
    async with sessionmaker() as session:
        first = await fish_repo.get_or_create_player(session, USER_ID, GUILD_ID)
        second = await fish_repo.get_or_create_player(session, USER_ID, GUILD_ID)
    assert first.user_id == second.user_id
    assert first.guild_id == second.guild_id
    assert first.fishing_xp == 0  # default


@pytest.mark.asyncio
async def test_add_bait_creates_then_increments(sessionmaker):
    async with sessionmaker() as session:
        first = await fish_repo.add_bait(session, USER_ID, GUILD_ID, "worm", 5)
        assert first.quantity == 5

        second = await fish_repo.add_bait(session, USER_ID, GUILD_ID, "worm", 3)
        assert second.quantity == 8
        assert second.id == first.id  # same row


@pytest.mark.asyncio
async def test_consume_bait_decrements_and_returns_true(sessionmaker):
    async with sessionmaker() as session:
        await fish_repo.add_bait(session, USER_ID, GUILD_ID, "worm", 5)
        ok = await fish_repo.consume_bait(session, USER_ID, GUILD_ID, "worm", 2)
        assert ok is True

        bait = await fish_repo.get_bait(session, USER_ID, GUILD_ID, "worm")
        assert bait.quantity == 3


@pytest.mark.asyncio
async def test_consume_bait_returns_false_when_insufficient(sessionmaker):
    async with sessionmaker() as session:
        await fish_repo.add_bait(session, USER_ID, GUILD_ID, "worm", 1)
        ok = await fish_repo.consume_bait(session, USER_ID, GUILD_ID, "worm", 5)
        assert ok is False

        # Quantity unchanged on failure
        bait = await fish_repo.get_bait(session, USER_ID, GUILD_ID, "worm")
        assert bait.quantity == 1


@pytest.mark.asyncio
async def test_consume_bait_returns_false_when_no_row(sessionmaker):
    async with sessionmaker() as session:
        ok = await fish_repo.consume_bait(session, USER_ID, GUILD_ID, "worm", 1)
        assert ok is False


@pytest.mark.asyncio
async def test_create_session_then_get_active_returns_it(sessionmaker):
    async with sessionmaker() as session:
        now = _now()
        fs = await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=999, message_id=1234,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
        )

        active = await fish_repo.get_active_session(session, USER_ID, GUILD_ID)
    assert active is not None
    assert active.id == fs.id


@pytest.mark.asyncio
async def test_end_session_marks_inactive(sessionmaker):
    async with sessionmaker() as session:
        now = _now()
        fs = await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=999, message_id=1234,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
        )
        await fish_repo.end_session(session, fs.id)

        active = await fish_repo.get_active_session(session, USER_ID, GUILD_ID)
    assert active is None


@pytest.mark.asyncio
async def test_get_all_due_sessions_returns_only_afk_due(sessionmaker):
    """The scheduler tick must only see AFK sessions whose time has elapsed.

    Active-mode sessions are driven by their own asyncio task — including
    them here would make the scheduler double-process them.
    """
    async with sessionmaker() as session:
        now = _now()
        # Due AFK session — should be returned
        due_afk = await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=1, message_id=1,
            started_at=now - timedelta(minutes=20),
            next_catch_at=now - timedelta(seconds=10),
            mode="afk",
        )
        # Future AFK — not yet due
        await fish_repo.create_session(
            session,
            user_id=USER_ID_2, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=2, message_id=2,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
            mode="afk",
        )
        # Active-mode session — must NEVER appear in due list, even if "due"
        await fish_repo.create_session(
            session,
            user_id=999, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=3, message_id=3,
            started_at=now - timedelta(minutes=20),
            next_catch_at=now - timedelta(seconds=10),
            mode="active",
        )

        due = await fish_repo.get_all_due_sessions(session, now)

    assert [s.id for s in due] == [due_afk.id]


@pytest.mark.asyncio
async def test_get_orphaned_active_sessions_finds_only_active_mode(sessionmaker):
    """After a bot restart, active-mode sessions need cleanup; AFK ones don't."""
    async with sessionmaker() as session:
        now = _now()
        active_orphan = await fish_repo.create_session(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=1, message_id=1,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
            mode="active",
        )
        # AFK session — should NOT appear
        await fish_repo.create_session(
            session,
            user_id=USER_ID_2, guild_id=GUILD_ID,
            location_name="calm_pond", rod_id="basic", bait_type="worm",
            bait_remaining=10, channel_id=2, message_id=2,
            started_at=now, next_catch_at=now + timedelta(minutes=15),
            mode="afk",
        )

        orphans = await fish_repo.get_orphaned_active_sessions(session)

    assert [s.id for s in orphans] == [active_orphan.id]


@pytest.mark.asyncio
async def test_upsert_fish_catch_aggregates_on_repeat(sessionmaker):
    async with sessionmaker() as session:
        now = _now()
        first = await fish_repo.upsert_fish_catch(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            fish_name="Bluegill", location_name="calm_pond",
            rarity="common", length=5, value=3, now=now,
        )
        assert first.catch_count == 1

        # Second catch: bigger and worth more — should update best_*
        second = await fish_repo.upsert_fish_catch(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            fish_name="Bluegill", location_name="calm_pond",
            rarity="common", length=8, value=10, now=now + timedelta(minutes=5),
        )
        assert second.id == first.id
        assert second.catch_count == 2
        assert second.best_length == 8
        assert second.best_value == 10


@pytest.mark.asyncio
async def test_upsert_fish_catch_keeps_best_when_new_is_smaller(sessionmaker):
    async with sessionmaker() as session:
        now = _now()
        await fish_repo.upsert_fish_catch(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            fish_name="Bluegill", location_name="calm_pond",
            rarity="common", length=10, value=20, now=now,
        )
        smaller = await fish_repo.upsert_fish_catch(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            fish_name="Bluegill", location_name="calm_pond",
            rarity="common", length=3, value=2, now=now + timedelta(minutes=5),
        )
    assert smaller.best_length == 10  # kept
    assert smaller.best_value == 20   # kept


@pytest.mark.asyncio
async def test_upsert_daily_summary_aggregates_same_day(sessionmaker):
    """Multiple catches on the same date update the same row, not insert new ones."""
    async with sessionmaker() as session:
        first = await fish_repo.upsert_daily_summary(
            session, USER_ID, GUILD_ID, "2026-04-25",
            catch={"name": "Bluegill", "is_trash": False, "value": 3, "length": 5},
        )
        assert first.total_fish == 1
        assert first.total_coins == 3

        second = await fish_repo.upsert_daily_summary(
            session, USER_ID, GUILD_ID, "2026-04-25",
            catch={"name": "Trout", "is_trash": False, "value": 8, "length": 12},
        )
    assert second.id == first.id
    assert second.total_fish == 2
    assert second.total_coins == 11
    # Bigger catch becomes the new "biggest"
    assert second.biggest_catch_name == "Trout"
    assert second.biggest_catch_length == 12


@pytest.mark.asyncio
async def test_save_haiku_then_get_player_haikus(sessionmaker):
    async with sessionmaker() as session:
        now = _now()
        await fish_repo.save_haiku(
            session,
            user_id=USER_ID, guild_id=GUILD_ID,
            location_name="calm_pond", fish_species="Glasswing Pike",
            line_1="silver beneath glass",
            line_2="the pike circles patiently",
            line_3="moonlight on its scales",
            created_at=now,
        )
        haikus = await fish_repo.get_player_haikus(session, USER_ID, GUILD_ID)
    assert len(haikus) == 1
    assert haikus[0].line_2 == "the pike circles patiently"
    assert haikus[0].fish_species == "Glasswing Pike"


@pytest.mark.asyncio
async def test_legendary_lifecycle(sessionmaker):
    """create → encounter → mark_caught → no longer active."""
    async with sessionmaker() as session:
        now = _now()
        leg = await fish_repo.create_legendary(
            session,
            guild_id=GUILD_ID, location_name="calm_pond",
            species_name="Old One", name="Whisperfin",
            personality="A patient ancient.",
            created_at=now,
        )
        assert leg.active is True

        active = await fish_repo.get_active_legendary(
            session, GUILD_ID, "calm_pond",
        )
        assert active is not None
        assert active.id == leg.id

        # Record an encounter, then catch
        await fish_repo.save_encounter(
            session,
            legendary_id=leg.id, user_id=USER_ID,
            outcome="unconvinced",
            dialogue_summary="Walked away thoughtful.",
            created_at=now,
        )
        await fish_repo.mark_legendary_caught(
            session, legendary_id=leg.id,
            caught_by=USER_ID_2, caught_at=now + timedelta(hours=1),
        )

        # Active query no longer returns it
        active_after = await fish_repo.get_active_legendary(
            session, GUILD_ID, "calm_pond",
        )
    assert active_after is None


@pytest.mark.asyncio
async def test_get_recent_legendary_encounters_excludes_self(sessionmaker):
    """A legendary's "memory of others" must filter out the asking player."""
    async with sessionmaker() as session:
        now = _now()
        leg = await fish_repo.create_legendary(
            session,
            guild_id=GUILD_ID, location_name="calm_pond",
            species_name="Old One", name="Whisperfin",
            personality="A patient ancient.",
            created_at=now,
        )
        await fish_repo.save_encounter(
            session, legendary_id=leg.id, user_id=USER_ID,
            outcome="escaped", dialogue_summary="Lost the line.",
            created_at=now,
        )
        await fish_repo.save_encounter(
            session, legendary_id=leg.id, user_id=USER_ID_2,
            outcome="unconvinced", dialogue_summary="Spoke too softly.",
            created_at=now + timedelta(minutes=1),
        )

        others = await fish_repo.get_recent_legendary_encounters(
            session, legendary_id=leg.id, exclude_user_id=USER_ID,
        )

    assert [e.user_id for e in others] == [USER_ID_2]
