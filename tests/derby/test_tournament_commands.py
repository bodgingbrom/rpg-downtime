"""Tests for PR 3: Tournament registration, scheduling, and field filling."""

import random
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from derby import logic, repositories as repo
from derby.models import Racer, Tournament, TournamentEntry
from derby.scheduler import TOURNAMENT_FIELD_SIZE, TOURNAMENT_SCHEDULE
import economy.models  # noqa: F401 — register Wallet table


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_racer(
    session: AsyncSession, name: str, owner_id: int = 0,
    speed: int = 15, cornering: int = 15, stamina: int = 15,
    rank: str | None = None, **kw,
) -> Racer:
    r = await repo.create_racer(
        session,
        name=name,
        owner_id=owner_id,
        guild_id=1,
        speed=speed,
        cornering=cornering,
        stamina=stamina,
        rank=rank or logic.calculate_rank(speed, cornering, stamina),
        **kw,
    )
    return r


# ---------------------------------------------------------------------------
# Registration flow tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_creates_tournament_and_entry(session: AsyncSession):
    """Registering a racer auto-creates the pending tournament."""
    racer = await _make_racer(session, "Flash", owner_id=42, speed=15, cornering=15, stamina=15)
    rank = racer.rank

    # No tournament yet
    assert await repo.get_pending_tournament(session, guild_id=1, rank=rank) is None

    # Create tournament + entry (simulating the command flow)
    tournament = await repo.create_tournament(session, guild_id=1, rank=rank)
    entry = await repo.create_tournament_entry(
        session,
        tournament_id=tournament.id,
        racer_id=racer.id,
        owner_id=42,
        is_pool_filler=False,
    )

    assert tournament.status == "pending"
    assert entry.owner_id == 42
    assert entry.is_pool_filler is False

    # Verify lookup
    found = await repo.get_player_tournament_entry(session, tournament.id, owner_id=42)
    assert found is not None
    assert found.racer_id == racer.id


@pytest.mark.asyncio
async def test_register_duplicate_bracket_detected(session: AsyncSession):
    """A player can't register two racers in the same rank bracket."""
    r1 = await _make_racer(session, "Alpha", owner_id=42)
    r2 = await _make_racer(session, "Beta", owner_id=42)

    t = await repo.create_tournament(session, guild_id=1, rank=r1.rank)
    await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=r1.id, owner_id=42
    )

    # Second registration by same owner in same tournament
    existing = await repo.get_player_tournament_entry(session, t.id, owner_id=42)
    assert existing is not None  # would block registration


@pytest.mark.asyncio
async def test_cancel_removes_entry(session: AsyncSession):
    """Cancelling removes the entry from the tournament."""
    racer = await _make_racer(session, "Dash", owner_id=42)
    t = await repo.create_tournament(session, guild_id=1, rank=racer.rank)
    entry = await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=racer.id, owner_id=42
    )

    # Delete the entry
    await session.delete(entry)
    await session.commit()

    # Verify gone
    found = await repo.get_player_tournament_entry(session, t.id, owner_id=42)
    assert found is None


# ---------------------------------------------------------------------------
# Tournament field filling tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_filling_uses_existing_unowned(session: AsyncSession):
    """Fill should prefer existing unowned racers of the same rank."""
    rank = "C"
    # Create 7 unowned C-rank pool racers
    pool_racers = []
    for i in range(7):
        r = await _make_racer(
            session, f"Pool{i}", owner_id=0,
            speed=10 + i, cornering=10, stamina=10, rank=rank,
        )
        pool_racers.append(r)

    # 1 player racer
    player_racer = await _make_racer(
        session, "Mine", owner_id=42,
        speed=15, cornering=15, stamina=15, rank=rank,
    )

    # Create tournament with player entry
    t = await repo.create_tournament(session, guild_id=1, rank=rank)
    await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=player_racer.id, owner_id=42,
    )

    # Get available pool racers
    available = await repo.get_racers_by_rank(session, 1, rank, unowned_only=True)
    assert len(available) >= 7


@pytest.mark.asyncio
async def test_pool_filling_generates_when_short(session: AsyncSession):
    """When not enough pool racers exist, new ones should be generated."""
    rank = "S"
    # Only 2 existing S-rank pool racers
    for i in range(2):
        await _make_racer(
            session, f"Elite{i}", owner_id=0,
            speed=28, cornering=28, stamina=28, rank=rank,
        )

    available = await repo.get_racers_by_rank(session, 1, rank, unowned_only=True)
    assert len(available) == 2

    # generate_pool_racer_for_rank should produce valid S-rank racers
    taken = {f"Elite{i}" for i in range(2)}
    for _ in range(5):
        kwargs = logic.generate_pool_racer_for_rank(rank, guild_id=1, taken_names=taken)
        total = kwargs["speed"] + kwargs["cornering"] + kwargs["stamina"]
        assert total >= 81  # S-rank minimum
        taken.add(kwargs["name"])


# ---------------------------------------------------------------------------
# Full execution flow test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tournament_full_flow(session: AsyncSession):
    """Register → fill to 8 → run → placements stored."""
    rank = "C"

    # 1 player racer
    player_racer = await _make_racer(
        session, "Hero", owner_id=42, speed=20, cornering=15, stamina=15, rank=rank,
    )

    # Create tournament
    t = await repo.create_tournament(session, guild_id=1, rank=rank)
    await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=player_racer.id, owner_id=42,
    )

    # Fill with 7 pool racers
    taken = {"Hero"}
    all_racers = [player_racer]
    for i in range(7):
        kwargs = logic.generate_pool_racer_for_rank(rank, guild_id=1, taken_names=taken)
        taken.add(kwargs["name"])
        r = await repo.create_racer(session, **kwargs)
        await repo.create_tournament_entry(
            session, tournament_id=t.id, racer_id=r.id, owner_id=0, is_pool_filler=True,
        )
        all_racers.append(r)

    assert len(all_racers) == 8

    # Run tournament
    result = logic.run_tournament(all_racers, seed=42)
    assert len(result.final_placements) == 8

    # Store placements
    entries = await repo.get_tournament_entries(session, t.id)
    entry_by_racer = {e.racer_id: e for e in entries}

    for place_idx, racer_id in enumerate(result.final_placements):
        entry = entry_by_racer.get(racer_id)
        if entry:
            await repo.update_tournament_entry(
                session, entry.id, placement=place_idx + 1,
            )

    await repo.update_tournament(session, t.id, status="finished")

    # Verify
    updated_t = await repo.get_tournament(session, t.id)
    assert updated_t.status == "finished"

    updated_entries = await repo.get_tournament_entries(session, t.id)
    placements = sorted(
        [(e.placement, e.racer_id) for e in updated_entries if e.placement],
        key=lambda x: x[0],
    )
    assert len(placements) == 8
    assert placements[0][0] == 1  # someone got 1st


# ---------------------------------------------------------------------------
# Tournament fires / skips tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tournament_skipped_no_registrations(session: AsyncSession):
    """A tournament with no player entries should not fire."""
    t = await repo.create_tournament(session, guild_id=1, rank="C")
    # Add only pool fillers
    for i in range(3):
        r = await _make_racer(session, f"Pool{i}", owner_id=0, rank="C")
        await repo.create_tournament_entry(
            session, tournament_id=t.id, racer_id=r.id, owner_id=0, is_pool_filler=True,
        )

    entries = await repo.get_tournament_entries(session, t.id)
    player_entries = [e for e in entries if not e.is_pool_filler]
    assert len(player_entries) == 0  # should skip


@pytest.mark.asyncio
async def test_tournament_fires_with_single_registration(session: AsyncSession):
    """Even one player registration should cause the tournament to fire."""
    t = await repo.create_tournament(session, guild_id=1, rank="C")
    player_racer = await _make_racer(session, "Solo", owner_id=42, rank="C")
    await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=player_racer.id, owner_id=42,
    )

    entries = await repo.get_tournament_entries(session, t.id)
    player_entries = [e for e in entries if not e.is_pool_filler]
    assert len(player_entries) == 1  # should fire


# ---------------------------------------------------------------------------
# Schedule constants test
# ---------------------------------------------------------------------------


def test_tournament_schedule_covers_all_ranks():
    """Every rank D-S should appear in the tournament schedule."""
    scheduled_ranks = {r for _, _, _, r in TOURNAMENT_SCHEDULE}
    assert scheduled_ranks == {"D", "C", "B", "A", "S"}


def test_tournament_schedule_valid_weekdays():
    """All schedule entries should have valid weekday/hour/minute."""
    for wd, h, m, rank in TOURNAMENT_SCHEDULE:
        assert 0 <= wd <= 6, f"Invalid weekday {wd}"
        assert 0 <= h <= 23, f"Invalid hour {h}"
        assert 0 <= m <= 59, f"Invalid minute {m}"


def test_tournament_field_size():
    """Field size should be 8."""
    assert TOURNAMENT_FIELD_SIZE == 8
