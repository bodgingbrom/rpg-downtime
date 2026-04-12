"""Tests for training limits and placement stat gains."""
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db_base import Base
from derby import logic, models
from derby import repositories as repo


GUILD = 1


async def _make_db(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sm


def _make_map(segments: list[tuple[str, int]]) -> logic.RaceMap:
    """Helper to build a RaceMap from [(type, distance), ...]."""
    return logic.RaceMap(
        name="Test Track",
        theme="test",
        description="",
        segments=[logic.MapSegment(type=t, distance=d) for t, d in segments],
    )


# ---------------------------------------------------------------------------
# Training limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trains_since_race_defaults_to_zero(tmp_path: Path):
    sm = await _make_db(tmp_path)
    async with sm() as session:
        r = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD, speed=10,
        )
    assert r.trains_since_race == 0


@pytest.mark.asyncio
async def test_trains_since_race_increments(tmp_path: Path):
    sm = await _make_db(tmp_path)
    async with sm() as session:
        r = await repo.create_racer(
            session, name="A", owner_id=1, guild_id=GUILD, speed=10,
        )
        r.trains_since_race += 1
        await session.commit()
        await session.refresh(r)
    assert r.trains_since_race == 1


# ---------------------------------------------------------------------------
# Placement stat gains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stat_gain_weighted_by_track(tmp_path: Path):
    """An all-climb track with maxed speed/cornering must give stamina."""
    sm = await _make_db(tmp_path)
    race_map = _make_map([("climb", 10)])
    async with sm() as session:
        # Max out speed and cornering so only stamina is eligible
        r = await repo.create_racer(
            session, name="A", owner_id=42, guild_id=GUILD,
            speed=logic.MAX_STAT, cornering=logic.MAX_STAT, stamina=10,
        )
        placements = [r.id]
        participants = [r]
        prize_list = [50]

        gains = await logic.apply_placement_stat_gains(
            session, placements, participants, race_map, prize_list,
        )
        await session.commit()

    assert r.id in gains
    stat_name, new_val = gains[r.id]
    assert stat_name == "stamina"
    assert new_val == 11


@pytest.mark.asyncio
async def test_stat_gain_skips_maxed_stat(tmp_path: Path):
    """If the dominant stat is maxed, a different stat should be chosen."""
    sm = await _make_db(tmp_path)
    race_map = _make_map([("climb", 10)])  # heavily stamina-weighted
    async with sm() as session:
        r = await repo.create_racer(
            session, name="A", owner_id=42, guild_id=GUILD,
            speed=10, cornering=10, stamina=logic.MAX_STAT,
        )
        placements = [r.id]
        participants = [r]
        prize_list = [50]

        gains = await logic.apply_placement_stat_gains(
            session, placements, participants, race_map, prize_list,
        )
        await session.commit()

    assert r.id in gains
    stat_name, _ = gains[r.id]
    assert stat_name in ("speed", "cornering")


@pytest.mark.asyncio
async def test_stat_gain_skipped_when_all_maxed(tmp_path: Path):
    """Racer with all stats at MAX_STAT gets no gain."""
    sm = await _make_db(tmp_path)
    race_map = _make_map([("straight", 5)])
    async with sm() as session:
        r = await repo.create_racer(
            session, name="A", owner_id=42, guild_id=GUILD,
            speed=logic.MAX_STAT, cornering=logic.MAX_STAT,
            stamina=logic.MAX_STAT,
        )
        placements = [r.id]
        participants = [r]
        prize_list = [50]

        gains = await logic.apply_placement_stat_gains(
            session, placements, participants, race_map, prize_list,
        )

    assert r.id not in gains


@pytest.mark.asyncio
async def test_stat_gain_skips_npc_racers(tmp_path: Path):
    """NPC-owned racers should not receive stat gains."""
    sm = await _make_db(tmp_path)
    race_map = _make_map([("straight", 5)])
    async with sm() as session:
        r = await repo.create_racer(
            session, name="NPC Racer", owner_id=0, guild_id=GUILD,
            speed=10, cornering=10, stamina=10,
        )
        # Simulate NPC ownership
        r.npc_id = 99
        await session.commit()

        placements = [r.id]
        participants = [r]
        prize_list = [50]

        gains = await logic.apply_placement_stat_gains(
            session, placements, participants, race_map, prize_list,
        )

    assert r.id not in gains


@pytest.mark.asyncio
async def test_stat_gain_skips_non_placing(tmp_path: Path):
    """Only racers in prize positions get stat gains."""
    sm = await _make_db(tmp_path)
    race_map = _make_map([("straight", 5)])
    async with sm() as session:
        r1 = await repo.create_racer(
            session, name="First", owner_id=42, guild_id=GUILD,
            speed=10, cornering=10, stamina=10,
        )
        r2 = await repo.create_racer(
            session, name="Last", owner_id=43, guild_id=GUILD,
            speed=10, cornering=10, stamina=10,
        )
        placements = [r1.id, r2.id]
        participants = [r1, r2]
        prize_list = [50]  # only 1st place gets a prize

        gains = await logic.apply_placement_stat_gains(
            session, placements, participants, race_map, prize_list,
        )

    assert r1.id in gains
    assert r2.id not in gains


@pytest.mark.asyncio
async def test_stat_gain_none_without_map(tmp_path: Path):
    """No stat gains when race_map is None."""
    sm = await _make_db(tmp_path)
    async with sm() as session:
        r = await repo.create_racer(
            session, name="A", owner_id=42, guild_id=GUILD,
            speed=10, cornering=10, stamina=10,
        )
        gains = await logic.apply_placement_stat_gains(
            session, [r.id], [r], None, [50],
        )

    assert gains == {}


@pytest.mark.asyncio
async def test_stat_gain_triggers_rank_recalc(tmp_path: Path):
    """Stat gain should recalculate rank when crossing a threshold."""
    sm = await _make_db(tmp_path)
    # B-rank threshold is 47. Give racer total 46 so +1 pushes to B.
    race_map = _make_map([("straight", 10)])
    async with sm() as session:
        r = await repo.create_racer(
            session, name="AlmostB", owner_id=42, guild_id=GUILD,
            speed=16, cornering=15, stamina=15,  # total = 46 = C-rank
        )
        logic.recalculate_rank(r)
        assert r.rank == "C"

        gains = await logic.apply_placement_stat_gains(
            session, [r.id], [r], race_map, [50],
        )
        await session.commit()

    assert r.id in gains
    # Total is now 47 → B-rank
    assert r.rank == "B"
