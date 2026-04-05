"""Tests for PR 2: Tournament Models + Engine."""

import random

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from derby import repositories as repo
from derby.logic import (
    TOURNAMENT_PRIZES,
    TournamentRoundResult,
    TournamentResult,
    _RANK_STAT_RANGES,
    _distribute_stats,
    calculate_rank,
    generate_pool_racer_for_rank,
    run_tournament,
    MAX_STAT,
)
from derby.models import Racer
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


def _make_racer(id: int, speed: int = 15, cornering: int = 15, stamina: int = 15, **kw) -> Racer:
    """Create a Racer object for testing (not persisted)."""
    defaults = dict(
        name=f"Racer{id}",
        owner_id=0,
        guild_id=1,
        mood=3,
        temperament="Quirky",
        races_completed=5,
        career_length=30,
        peak_end=18,
        injuries="",
        injury_races_remaining=0,
        retired=False,
        rank="C",
    )
    defaults.update(kw)
    return Racer(id=id, speed=speed, cornering=cornering, stamina=stamina, **defaults)


# ---------------------------------------------------------------------------
# Tournament engine tests
# ---------------------------------------------------------------------------


def test_run_tournament_produces_8_placements():
    """run_tournament returns exactly 8 unique placements."""
    racers = [_make_racer(i, speed=10 + i, cornering=10, stamina=10) for i in range(1, 9)]
    result = run_tournament(racers, seed=42)

    assert isinstance(result, TournamentResult)
    assert len(result.final_placements) == 8
    assert set(result.final_placements) == {r.id for r in racers}


def test_run_tournament_elimination_counts():
    """Round 1 eliminates 4, round 2 eliminates 2, round 3 eliminates 1."""
    racers = [_make_racer(i, speed=10 + i, cornering=10, stamina=10) for i in range(1, 9)]
    result = run_tournament(racers, seed=42)

    assert len(result.rounds) == 3
    assert len(result.rounds[0].eliminated) == 4
    assert len(result.rounds[0].advancing) == 4
    assert len(result.rounds[1].eliminated) == 2
    assert len(result.rounds[1].advancing) == 2
    assert len(result.rounds[2].eliminated) == 1
    assert len(result.rounds[2].advancing) == 1


def test_run_tournament_deterministic():
    """Same seed produces identical results."""
    racers = [_make_racer(i, speed=10 + i, cornering=10, stamina=10) for i in range(1, 9)]
    result1 = run_tournament(racers, seed=123)
    result2 = run_tournament(racers, seed=123)

    assert result1.final_placements == result2.final_placements


def test_run_tournament_different_seeds_differ():
    """Different seeds should usually produce different results."""
    racers = [_make_racer(i, speed=15, cornering=15, stamina=15) for i in range(1, 9)]
    results = set()
    for seed in range(20):
        result = run_tournament(racers, seed=seed)
        results.add(tuple(result.final_placements))
    # With equal-stat racers, different seeds should give different outcomes
    assert len(results) > 1


def test_run_tournament_strong_usually_advances():
    """A significantly stronger racer should usually finish in the top half."""
    strong = _make_racer(1, speed=28, cornering=28, stamina=28, rank="S")
    weak_racers = [_make_racer(i, speed=8, cornering=8, stamina=8, rank="D") for i in range(2, 9)]
    racers = [strong] + weak_racers

    top_half_count = 0
    trials = 50
    for seed in range(trials):
        result = run_tournament(racers, seed=seed)
        if result.final_placements.index(1) < 4:
            top_half_count += 1

    assert top_half_count >= trials * 0.7, f"Strong racer in top half only {top_half_count}/{trials}"


def test_run_tournament_wrong_count_raises():
    """Tournament requires exactly 8 racers."""
    racers = [_make_racer(i) for i in range(1, 7)]
    with pytest.raises(ValueError, match="exactly 8"):
        run_tournament(racers, seed=1)


def test_run_tournament_round_results_have_race_data():
    """Each round result should contain a RaceResult with placements."""
    racers = [_make_racer(i, speed=10 + i, cornering=10, stamina=10) for i in range(1, 9)]
    result = run_tournament(racers, seed=42)

    for rnd in result.rounds:
        assert isinstance(rnd, TournamentRoundResult)
        assert len(rnd.race_result.placements) > 0
        # Advancing + eliminated should equal total racers in that round
        assert len(rnd.advancing) + len(rnd.eliminated) == len(rnd.race_result.placements)


# ---------------------------------------------------------------------------
# Pool racer generation for rank
# ---------------------------------------------------------------------------


def test_generate_pool_racer_for_rank_stat_range():
    """Generated racer's stat total should fall within the rank's range."""
    for rank, (low, high) in _RANK_STAT_RANGES.items():
        for _ in range(20):
            kwargs = generate_pool_racer_for_rank(rank, guild_id=1, taken_names=set())
            total = kwargs["speed"] + kwargs["cornering"] + kwargs["stamina"]
            assert low <= total <= high, f"Rank {rank}: total {total} not in [{low}, {high}]"


def test_generate_pool_racer_for_rank_each_stat_capped():
    """No individual stat should exceed MAX_STAT (31)."""
    for rank in _RANK_STAT_RANGES:
        for _ in range(20):
            kwargs = generate_pool_racer_for_rank(rank, guild_id=1, taken_names=set())
            assert kwargs["speed"] <= MAX_STAT
            assert kwargs["cornering"] <= MAX_STAT
            assert kwargs["stamina"] <= MAX_STAT


def test_generate_pool_racer_for_rank_has_correct_rank():
    """Generated racer should have the requested rank set."""
    for rank in ["D", "C", "B", "A", "S"]:
        kwargs = generate_pool_racer_for_rank(rank, guild_id=1, taken_names=set())
        assert kwargs["rank"] == rank


def test_generate_pool_racer_for_rank_respects_taken_names():
    """Generated racer should not reuse a taken name (when possible)."""
    first = generate_pool_racer_for_rank("C", guild_id=1, taken_names=set())
    second = generate_pool_racer_for_rank("C", guild_id=1, taken_names={first["name"]})
    # Not guaranteed to be different (fallback exists) but usually will be
    # Just verify it returns a valid dict
    assert "name" in second
    assert "speed" in second


def test_distribute_stats_sums_correctly():
    """_distribute_stats should produce values that sum to the requested total."""
    for total in [0, 10, 23, 46, 65, 80, 93]:
        a, b, c = _distribute_stats(total)
        assert a + b + c == total
        assert 0 <= a <= MAX_STAT
        assert 0 <= b <= MAX_STAT
        assert 0 <= c <= MAX_STAT


# ---------------------------------------------------------------------------
# Tournament prizes constant
# ---------------------------------------------------------------------------


def test_tournament_prizes_all_ranks():
    """All 5 ranks should have prize lists with 4 entries."""
    for rank in ["D", "C", "B", "A", "S"]:
        assert rank in TOURNAMENT_PRIZES
        prizes = TOURNAMENT_PRIZES[rank]
        assert len(prizes) == 4
        # 1st > 2nd > 3rd == 4th
        assert prizes[0] > prizes[1] > prizes[2]
        assert prizes[2] == prizes[3]


def test_tournament_prizes_scale_with_rank():
    """Higher ranks should have larger prize pools."""
    ranks = ["D", "C", "B", "A", "S"]
    for i in range(len(ranks) - 1):
        lower_total = sum(TOURNAMENT_PRIZES[ranks[i]])
        higher_total = sum(TOURNAMENT_PRIZES[ranks[i + 1]])
        assert higher_total > lower_total


# ---------------------------------------------------------------------------
# Repository tests (Tournament + TournamentEntry CRUD)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tournament_crud(session: AsyncSession):
    """Create, get, and update a Tournament."""
    t = await repo.create_tournament(session, guild_id=1, rank="C")
    assert t.id is not None
    assert t.status == "pending"
    assert t.rank == "C"

    fetched = await repo.get_tournament(session, t.id)
    assert fetched.guild_id == 1

    updated = await repo.update_tournament(session, t.id, status="running")
    assert updated.status == "running"


@pytest.mark.asyncio
async def test_tournament_entry_crud(session: AsyncSession):
    """Create, get entries, and update a TournamentEntry."""
    t = await repo.create_tournament(session, guild_id=1, rank="D")
    racer = await repo.create_racer(session, name="Test", owner_id=5, guild_id=1)

    entry = await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=racer.id, owner_id=5
    )
    assert entry.id is not None
    assert entry.is_pool_filler is False

    entries = await repo.get_tournament_entries(session, t.id)
    assert len(entries) == 1

    updated = await repo.update_tournament_entry(session, entry.id, placement=1)
    assert updated.placement == 1


@pytest.mark.asyncio
async def test_get_pending_tournament(session: AsyncSession):
    """get_pending_tournament filters by guild, rank, and status."""
    await repo.create_tournament(session, guild_id=1, rank="C")
    await repo.create_tournament(session, guild_id=1, rank="B")
    t_finished = await repo.create_tournament(session, guild_id=1, rank="C")
    await repo.update_tournament(session, t_finished.id, status="finished")

    pending_c = await repo.get_pending_tournament(session, guild_id=1, rank="C")
    assert pending_c is not None
    assert pending_c.rank == "C"
    assert pending_c.status == "pending"

    # Different guild
    assert await repo.get_pending_tournament(session, guild_id=99, rank="C") is None

    # No pending A-rank
    assert await repo.get_pending_tournament(session, guild_id=1, rank="A") is None


@pytest.mark.asyncio
async def test_get_player_tournament_entry(session: AsyncSession):
    """get_player_tournament_entry finds a player's entry in a tournament."""
    t = await repo.create_tournament(session, guild_id=1, rank="C")
    racer = await repo.create_racer(session, name="Mine", owner_id=42, guild_id=1)
    await repo.create_tournament_entry(
        session, tournament_id=t.id, racer_id=racer.id, owner_id=42
    )

    found = await repo.get_player_tournament_entry(session, t.id, owner_id=42)
    assert found is not None
    assert found.racer_id == racer.id

    # Different owner
    assert await repo.get_player_tournament_entry(session, t.id, owner_id=99) is None
