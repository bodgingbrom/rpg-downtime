import random

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from derby.logic import (
    MOOD_BONUS,
    MOOD_THRESHOLDS,
    MapSegment,
    RaceMap,
    RaceResult,
    SegmentResult,
    apply_mood_drift,
    apply_temperament,
    calculate_odds,
    load_all_maps,
    load_map,
    pick_map,
    resolve_payouts,
    roll_mood_bonus,
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


# ---------------------------------------------------------------------------
# Mood d20 roll tests
# ---------------------------------------------------------------------------


def test_roll_mood_bonus_great_mood_bonus():
    """Mood 5 (Great) should grant bonus on rolls 17+."""
    rng = random.Random(0)
    bonus_count = 0
    penalty_count = 0
    for _ in range(1000):
        roll, bonus = roll_mood_bonus(5, rng)
        if bonus > 0:
            assert roll >= 17
            bonus_count += 1
        elif bonus < 0:
            assert roll <= 1
            penalty_count += 1
    # ~20% bonus chance, ~5% penalty → expect roughly 200 and 50
    assert 150 < bonus_count < 260
    assert 20 < penalty_count < 90


def test_roll_mood_bonus_awful_mood_penalty():
    """Mood 1 (Awful) should never get a bonus, 20% penalty."""
    rng = random.Random(42)
    for _ in range(1000):
        roll, bonus = roll_mood_bonus(1, rng)
        assert bonus <= 0  # never positive
    # Re-run and count penalties
    rng = random.Random(42)
    penalty_count = sum(1 for _ in range(1000) if roll_mood_bonus(1, rng)[1] < 0)
    assert 150 < penalty_count < 260  # ~20%


def test_roll_mood_bonus_normal_symmetric():
    """Mood 3 (Normal) should have equal bonus/penalty chances (10% each)."""
    rng = random.Random(99)
    bonuses = 0
    penalties = 0
    for _ in range(2000):
        _, bonus = roll_mood_bonus(3, rng)
        if bonus > 0:
            bonuses += 1
        elif bonus < 0:
            penalties += 1
    # Both should be ~10% of 2000 = ~200
    assert 140 < bonuses < 280
    assert 140 < penalties < 280


def test_roll_mood_bonus_values():
    """Bonus and penalty should be exactly ±MOOD_BONUS."""
    rng = random.Random(0)
    seen_bonus = False
    seen_penalty = False
    for _ in range(500):
        _, bonus = roll_mood_bonus(5, rng)
        if bonus > 0:
            assert bonus == MOOD_BONUS
            seen_bonus = True
        elif bonus < 0:
            assert bonus == -MOOD_BONUS
            seen_penalty = True
    assert seen_bonus
    # May or may not see penalty with mood 5 in 500 rolls — that's ok


def test_mood_affects_simulation_outcome():
    """Over many races, great mood should win more than awful mood (same stats)."""
    great = Racer(id=1, name="Happy", owner_id=1, speed=15, cornering=15, stamina=15, mood=5)
    awful = Racer(id=2, name="Grumpy", owner_id=2, speed=15, cornering=15, stamina=15, mood=1)
    race_map = RaceMap(
        name="Mood Test",
        theme="test",
        description="",
        segments=[
            MapSegment(type="straight", distance=2),
            MapSegment(type="corner", distance=2),
            MapSegment(type="climb", distance=2),
            MapSegment(type="straight", distance=2),
        ],
    )
    wins = 0
    for seed in range(200):
        result = simulate_race({"racers": [great, awful]}, seed=seed, race_map=race_map)
        if result.placements[0] == 1:
            wins += 1
    # Great mood should win more than half — expect ~55-70%
    assert wins > 100, f"Great mood racer won only {wins}/200 times"


def test_mood_events_in_segments():
    """Mood roll events should appear in segment events."""
    r1 = Racer(id=1, name="Lucky", owner_id=1, speed=20, cornering=20, stamina=20, mood=5)
    r2 = Racer(id=2, name="Unlucky", owner_id=2, speed=20, cornering=20, stamina=20, mood=1)
    race_map = RaceMap(
        name="Event Test",
        theme="test",
        description="",
        segments=[
            MapSegment(type="straight", distance=2),
            MapSegment(type="corner", distance=2),
            MapSegment(type="climb", distance=2),
            MapSegment(type="straight", distance=2),
            MapSegment(type="corner", distance=2),
        ],
    )
    # Run enough seeds until we find mood events
    found_mood_event = False
    for seed in range(50):
        result = simulate_race({"racers": [r1, r2]}, seed=seed, race_map=race_map)
        all_events = [e for seg in result.segments for e in seg.events]
        for event in all_events:
            if "d20" in event or "natural" in event or "confidence" in event or "focus" in event:
                found_mood_event = True
                break
        if found_mood_event:
            break
    assert found_mood_event, "No mood events found in 50 race seeds"


def test_mood_odds_shift():
    """Odds should reflect mood — great mood racer gets lower payout."""
    happy = Racer(id=1, name="Happy", owner_id=1, speed=15, cornering=15, stamina=15, mood=5)
    grumpy = Racer(id=2, name="Grumpy", owner_id=2, speed=15, cornering=15, stamina=15, mood=1)
    race_map = RaceMap(
        name="Odds Test",
        theme="test",
        description="",
        segments=[MapSegment(type="straight", distance=2)] * 3,
    )
    odds = calculate_odds([happy, grumpy], [], 0.1, race_map=race_map)
    # Happy (mood 5) should have lower payout (more likely to win)
    assert odds[1] < odds[2], f"Expected happy odds {odds[1]} < grumpy odds {odds[2]}"


# ---------------------------------------------------------------------------
# Mood drift tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mood_drift_winner_increases(session: AsyncSession):
    """Winner's mood should increase by 1."""
    r1 = Racer(name="Winner", owner_id=1, speed=20, cornering=20, stamina=20, mood=3)
    r2 = Racer(name="Loser", owner_id=2, speed=20, cornering=20, stamina=20, mood=3)
    session.add_all([r1, r2])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)

    changes = await apply_mood_drift(session, [r1.id, r2.id], [r1, r2])
    await session.commit()

    assert r1.mood == 4  # winner goes up
    assert r2.mood == 2  # loser goes down
    assert r1.id in changes
    assert r2.id in changes
    assert changes[r1.id] == (3, 4)
    assert changes[r2.id] == (3, 2)


@pytest.mark.asyncio
async def test_mood_drift_clamped_at_boundaries(session: AsyncSession):
    """Mood should not go above 5 or below 1."""
    r1 = Racer(name="Max", owner_id=1, speed=20, cornering=20, stamina=20, mood=5)
    r2 = Racer(name="Min", owner_id=2, speed=20, cornering=20, stamina=20, mood=1)
    session.add_all([r1, r2])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)

    changes = await apply_mood_drift(session, [r1.id, r2.id], [r1, r2])
    await session.commit()

    assert r1.mood == 5  # already max, stays at 5
    assert r2.mood == 1  # already min, stays at 1
    # No changes since they're already at boundaries
    assert r1.id not in changes
    assert r2.id not in changes


@pytest.mark.asyncio
async def test_mood_drift_middle_racers_toward_neutral(session: AsyncSession):
    """Middle-placed racers drift toward mood 3 (neutral)."""
    r1 = Racer(name="Winner", owner_id=1, speed=20, cornering=20, stamina=20, mood=3)
    r2 = Racer(name="HighMood", owner_id=2, speed=20, cornering=20, stamina=20, mood=5)
    r3 = Racer(name="LowMood", owner_id=3, speed=20, cornering=20, stamina=20, mood=1)
    r4 = Racer(name="Loser", owner_id=4, speed=20, cornering=20, stamina=20, mood=3)
    session.add_all([r1, r2, r3, r4])
    await session.commit()
    for r in [r1, r2, r3, r4]:
        await session.refresh(r)

    # Placements: r1 wins, r4 is last
    # r2 and r3 are middle — should drift toward 3
    changes = await apply_mood_drift(
        session, [r1.id, r2.id, r3.id, r4.id], [r1, r2, r3, r4]
    )
    await session.commit()

    assert r1.mood == 4  # winner +1
    assert r2.mood == 4  # was 5, drifts toward 3 → 4
    assert r3.mood == 2  # was 1, drifts toward 3 → 2
    assert r4.mood == 2  # loser -1


@pytest.mark.asyncio
async def test_mood_drift_neutral_middle_unchanged(session: AsyncSession):
    """A middle-placed racer at mood 3 stays at 3."""
    r1 = Racer(name="Winner", owner_id=1, speed=20, cornering=20, stamina=20, mood=3)
    r2 = Racer(name="Middle", owner_id=2, speed=20, cornering=20, stamina=20, mood=3)
    r3 = Racer(name="Loser", owner_id=3, speed=20, cornering=20, stamina=20, mood=3)
    session.add_all([r1, r2, r3])
    await session.commit()
    for r in [r1, r2, r3]:
        await session.refresh(r)

    changes = await apply_mood_drift(
        session, [r1.id, r2.id, r3.id], [r1, r2, r3]
    )
    await session.commit()

    assert r2.mood == 3  # no change for neutral middle
    assert r2.id not in changes


@pytest.mark.asyncio
async def test_mood_drift_empty_placements(session: AsyncSession):
    """Empty placements should return empty changes."""
    changes = await apply_mood_drift(session, [], None)
    assert changes == {}


# ---------------------------------------------------------------------------
# Injury exclusion tests
# ---------------------------------------------------------------------------


def test_injured_racer_excluded_from_simulation():
    """Injured racers (injury_races_remaining > 0) should be filtered before
    being passed to simulate_race. This tests the filtering logic pattern."""
    healthy = Racer(id=1, name="Healthy", owner_id=1, speed=20, cornering=20, stamina=20,
                    injuries="", injury_races_remaining=0)
    injured = Racer(id=2, name="Injured", owner_id=2, speed=20, cornering=20, stamina=20,
                    injuries="Broken leg", injury_races_remaining=3)

    # Simulate the filter that scheduler/cog applies
    eligible = [r for r in [healthy, injured] if r.injury_races_remaining == 0]
    assert len(eligible) == 1
    assert eligible[0].id == 1


@pytest.mark.asyncio
async def test_injury_recovery_countdown(session: AsyncSession):
    """injury_races_remaining should decrement and auto-clear at 0."""
    r = Racer(name="Hurt", owner_id=1, speed=20, cornering=20, stamina=20,
              injuries="Sprained ankle", injury_races_remaining=2)
    session.add(r)
    await session.commit()
    await session.refresh(r)

    # Simulate one race tick
    r.injury_races_remaining -= 1
    await session.commit()
    await session.refresh(r)
    assert r.injury_races_remaining == 1
    assert r.injuries == "Sprained ankle"  # still injured

    # Second tick — should heal
    r.injury_races_remaining -= 1
    if r.injury_races_remaining <= 0:
        r.injuries = ""
        r.injury_races_remaining = 0
    await session.commit()
    await session.refresh(r)
    assert r.injury_races_remaining == 0
    assert r.injuries == ""


def test_2d4_recovery_range():
    """2d4 should produce values between 2 and 8."""
    results = set()
    rng = random.Random(42)
    for _ in range(1000):
        roll = rng.randint(1, 4) + rng.randint(1, 4)
        results.add(roll)
    assert min(results) == 2
    assert max(results) == 8
