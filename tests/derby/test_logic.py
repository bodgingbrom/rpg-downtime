import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from derby.logic import (
    MapSegment,
    RaceMap,
    RaceResult,
    SegmentResult,
    apply_temperament,
    calculate_odds,
    load_all_maps,
    load_map,
    pick_map,
    resolve_payouts,
    simulate_race,
)
from db_base import Base
from derby.models import Bet, Race, Racer
from economy.models import Wallet


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


def test_calculate_odds_equal_stats():
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=10, cornering=10, stamina=10),
        Racer(id=2, name="B", owner_id=2, speed=10, cornering=10, stamina=10),
    ]
    odds = calculate_odds(racers, [], 0.1)
    assert odds[1] == odds[2]


def test_calculate_odds_stat_weighted():
    strong = Racer(id=1, name="A", owner_id=1, speed=30, cornering=30, stamina=30)
    weak = Racer(id=2, name="B", owner_id=2, speed=0, cornering=0, stamina=0)
    odds = calculate_odds([strong, weak], [], 0.1)
    # Strong racer should have lower payout (more likely to win)
    assert odds[1] < odds[2]


def test_simulate_race():
    race = {"racers": [1, 2, 3], "course_segments": [1, 2]}
    result = simulate_race(race, seed=123)
    assert isinstance(result, RaceResult)
    assert set(result.placements) == {1, 2, 3}
    assert result.segments == []  # no map = legacy path


def test_apply_temperament() -> None:
    stats = {"speed": 10, "cornering": 10, "stamina": 10}
    result = apply_temperament(stats, "Agile", 0.1)
    assert result["speed"] == 11
    assert result["stamina"] == 9


@pytest.mark.asyncio
async def test_resolve_payouts(session: AsyncSession):
    r1 = Racer(name="A", owner_id=1)
    r2 = Racer(name="B", owner_id=2)
    session.add_all([r1, r2])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)

    race = Race(guild_id=1)
    session.add(race)
    await session.commit()
    await session.refresh(race)

    session.add_all(
        [
            Bet(race_id=race.id, user_id=1, racer_id=r1.id, amount=10),
            Bet(race_id=race.id, user_id=2, racer_id=r2.id, amount=20),
        ]
    )
    session.add(Wallet(user_id=1, balance=50))
    await session.commit()

    await resolve_payouts(session, race.id, winner_id=r1.id)

    w1 = await session.get(Wallet, 1)
    w2 = await session.get(Wallet, 2)
    assert w1.balance == 70
    assert w2.balance == 0

    bets = (await session.execute(select(Bet))).scalars().all()
    assert bets == []


def test_simulate_race_stat_influence():
    strong = Racer(id=1, name="Strong", owner_id=1, speed=31, cornering=31, stamina=31)
    weak = Racer(id=2, name="Weak", owner_id=2, speed=0, cornering=0, stamina=0)
    wins = 0
    for seed in range(100):
        result = simulate_race({"racers": [strong, weak]}, seed=seed)
        if result.placements[0] == 1:
            wins += 1
    # Strong racer should win the vast majority
    assert wins > 80


def test_simulate_race_returns_race_result():
    r1 = Racer(id=1, name="A", owner_id=1, speed=20, cornering=20, stamina=20)
    r2 = Racer(id=2, name="B", owner_id=2, speed=10, cornering=10, stamina=10)
    result = simulate_race({"racers": [r1, r2]}, seed=42)
    assert isinstance(result, RaceResult)
    assert set(result.placements) == {1, 2}
    assert result.racer_names == {1: "A", 2: "B"}


def test_simulate_race_legacy_no_segments():
    """Without a map, legacy path returns empty segments."""
    r1 = Racer(id=1, name="A", owner_id=1, speed=20, cornering=20, stamina=20)
    result = simulate_race({"racers": [r1]}, seed=1)
    assert result.segments == []
    assert result.map_name == ""


def test_simulate_race_with_map():
    """With a map, segment-by-segment simulation runs."""
    r1 = Racer(id=1, name="Flash", owner_id=1, speed=30, cornering=10, stamina=10)
    r2 = Racer(id=2, name="Turner", owner_id=2, speed=10, cornering=30, stamina=10)
    race_map = RaceMap(
        name="Test Track",
        theme="test",
        description="A test track",
        segments=[
            MapSegment(type="straight", distance=2, description="The straight"),
            MapSegment(type="corner", distance=2, description="The turn"),
        ],
    )
    result = simulate_race({"racers": [r1, r2]}, seed=42, race_map=race_map)
    assert isinstance(result, RaceResult)
    assert len(result.segments) == 2
    assert result.map_name == "Test Track"
    assert result.segments[0].segment_type == "straight"
    assert result.segments[1].segment_type == "corner"
    # Each segment has standings for both racers
    assert len(result.segments[0].standings) == 2


def test_simulate_race_speed_track_favors_speed():
    """A speed-heavy track should favor the faster racer."""
    fast = Racer(id=1, name="Fast", owner_id=1, speed=31, cornering=5, stamina=5)
    slow = Racer(id=2, name="Slow", owner_id=2, speed=5, cornering=5, stamina=5)
    speed_map = RaceMap(
        name="Speed Test",
        theme="test",
        description="",
        segments=[
            MapSegment(type="straight", distance=3),
            MapSegment(type="straight", distance=3),
            MapSegment(type="straight", distance=3),
        ],
    )
    wins = 0
    for seed in range(100):
        result = simulate_race({"racers": [fast, slow]}, seed=seed, race_map=speed_map)
        if result.placements[0] == 1:
            wins += 1
    assert wins > 75


def test_simulate_race_corner_track_favors_cornering():
    """A corner-heavy track should favor the agile racer."""
    agile = Racer(id=1, name="Agile", owner_id=1, speed=5, cornering=31, stamina=5)
    stiff = Racer(id=2, name="Stiff", owner_id=2, speed=5, cornering=5, stamina=5)
    corner_map = RaceMap(
        name="Corner Test",
        theme="test",
        description="",
        segments=[
            MapSegment(type="corner", distance=3),
            MapSegment(type="corner", distance=3),
            MapSegment(type="corner", distance=3),
        ],
    )
    wins = 0
    for seed in range(100):
        result = simulate_race(
            {"racers": [agile, stiff]}, seed=seed, race_map=corner_map
        )
        if result.placements[0] == 1:
            wins += 1
    assert wins > 75


def test_simulate_race_deterministic_with_seed():
    """Same seed should produce identical results."""
    r1 = Racer(id=1, name="A", owner_id=1, speed=20, cornering=15, stamina=25)
    r2 = Racer(id=2, name="B", owner_id=2, speed=15, cornering=25, stamina=20)
    race_map = pick_map()
    result1 = simulate_race({"racers": [r1, r2]}, seed=99, race_map=race_map)
    result2 = simulate_race({"racers": [r1, r2]}, seed=99, race_map=race_map)
    assert result1.placements == result2.placements
    assert len(result1.segments) == len(result2.segments)


def test_segment_events_detected():
    """Events should be generated for segments."""
    r1 = Racer(id=1, name="Alpha", owner_id=1, speed=30, cornering=30, stamina=30)
    r2 = Racer(id=2, name="Beta", owner_id=2, speed=1, cornering=1, stamina=1)
    race_map = RaceMap(
        name="Event Test",
        theme="test",
        description="",
        segments=[
            MapSegment(type="straight", distance=2),
            MapSegment(type="corner", distance=2),
            MapSegment(type="climb", distance=2),
        ],
    )
    result = simulate_race({"racers": [r1, r2]}, seed=42, race_map=race_map)
    all_events = [e for seg in result.segments for e in seg.events]
    # With such a stat disparity, there should be at least some events
    assert len(all_events) > 0


def test_calculate_odds_with_map():
    """Map-weighted odds should differ from flat odds."""
    fast = Racer(id=1, name="Fast", owner_id=1, speed=31, cornering=5, stamina=5)
    agile = Racer(id=2, name="Agile", owner_id=2, speed=5, cornering=31, stamina=5)
    speed_map = RaceMap(
        name="Speed Track",
        theme="test",
        description="",
        segments=[MapSegment(type="straight", distance=3)] * 5,
    )
    corner_map = RaceMap(
        name="Corner Track",
        theme="test",
        description="",
        segments=[MapSegment(type="corner", distance=3)] * 5,
    )
    speed_odds = calculate_odds([fast, agile], [], 0.1, race_map=speed_map)
    corner_odds = calculate_odds([fast, agile], [], 0.1, race_map=corner_map)
    # Fast racer should have lower payout (higher win chance) on speed track
    assert speed_odds[1] < speed_odds[2]
    # Agile racer should have lower payout on corner track
    assert corner_odds[2] < corner_odds[1]


# ---------------------------------------------------------------------------
# Map loading tests
# ---------------------------------------------------------------------------


def test_load_all_maps_returns_starter_maps():
    maps = load_all_maps()
    assert len(maps) >= 4
    names = {m.name for m in maps}
    assert "Frozen Circuit" in names
    assert "Desert Sprint" in names
    assert "Mountain Pass" in names
    assert "The Gauntlet" in names


def test_load_map_has_segments():
    maps = load_all_maps()
    for m in maps:
        assert len(m.segments) >= 3
        for seg in m.segments:
            assert seg.type in ("straight", "corner", "climb", "descent", "hazard")
            assert 1 <= seg.distance <= 3


def test_pick_map_returns_a_map():
    m = pick_map()
    assert m is not None
    assert isinstance(m, RaceMap)
    assert len(m.segments) > 0


def test_load_map_frozen_circuit(tmp_path):
    """Test loading a specific map file."""
    import os
    maps_dir = os.path.join(os.path.dirname(__file__), "..", "..", "derby", "maps")
    path = os.path.join(maps_dir, "frozen_circuit.yaml")
    m = load_map(path)
    assert m.name == "Frozen Circuit"
    assert m.theme == "frozen"
    assert len(m.segments) == 5
    assert m.segments[0].type == "straight"
    assert m.segments[1].type == "corner"
