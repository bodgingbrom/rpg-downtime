import random

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from derby.logic import (
    INJURY_DESCRIPTIONS,
    MAX_STAT,
    MOOD_BONUS,
    MOOD_THRESHOLDS,
    TRAINABLE_STATS,
    apply_feed,
    apply_rest,
    MapSegment,
    RaceMap,
    RaceResult,
    SegmentResult,
    apply_injuries,
    apply_mood_drift,
    apply_temperament,
    calculate_buy_price,
    calculate_odds,
    calculate_sell_price,
    calculate_training_cost,
    career_phase,
    check_injury_risk,
    effective_stats,
    generate_pool_racer,
    load_all_maps,
    load_map,
    parse_placement_prizes,
    pick_map,
    resolve_payouts,
    resolve_placement_prizes,
    roll_mood_bonus,
    simulate_race,
    training_failure_chance,
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
    session.add(Wallet(user_id=1, guild_id=1, balance=50))
    await session.commit()

    await resolve_payouts(session, race.id, winner_id=r1.id, guild_id=1)

    w1 = (
        await session.execute(
            select(Wallet).where(Wallet.user_id == 1, Wallet.guild_id == 1)
        )
    ).scalars().first()
    w2 = (
        await session.execute(
            select(Wallet).where(Wallet.user_id == 2, Wallet.guild_id == 1)
        )
    ).scalars().first()
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


# ---------------------------------------------------------------------------
# Post-race injury risk tests
# ---------------------------------------------------------------------------


def test_check_injury_risk_stumble_triggers():
    """Racers who stumbled should have injury chances."""
    result = RaceResult(
        placements=[1, 2],
        segments=[],
        racer_names={1: "Stumbler", 2: "Clean"},
        stumble_counts={1: 3, 2: 0},  # racer 1 stumbled 3 times
    )
    # Run many times to confirm stumbler can get injured
    injured_ids = set()
    for seed in range(500):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        for rid, _, _ in injuries:
            injured_ids.add(rid)
    # Racer 1 should get injured at least once in 500 attempts (3 rolls × 5% each)
    assert 1 in injured_ids
    # Racer 2 has 0 stumbles but is last place — gets 1 roll
    assert 2 in injured_ids


def test_check_injury_risk_last_place_extra_roll():
    """Last place racer gets an extra injury roll even with no stumbles."""
    result = RaceResult(
        placements=[1, 2, 3],
        segments=[],
        racer_names={1: "A", 2: "B", 3: "Last"},
        stumble_counts={1: 0, 2: 0, 3: 0},
    )
    injured_count = 0
    for seed in range(2000):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        for rid, _, _ in injuries:
            if rid == 3:
                injured_count += 1
    # ~5% of 2000 = ~100 injuries for last place
    assert 50 < injured_count < 160
    # Racers 1 and 2 should never get injured (0 stumbles, not last)


def test_check_injury_risk_no_stumbles_not_last():
    """Racer with 0 stumbles and not last should never get injured."""
    result = RaceResult(
        placements=[1, 2, 3],
        segments=[],
        racer_names={1: "A", 2: "B", 3: "C"},
        stumble_counts={1: 0, 2: 0, 3: 0},
    )
    for seed in range(500):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        for rid, _, _ in injuries:
            assert rid not in (1, 2), f"Racer {rid} should not be injured"


def test_check_injury_risk_only_one_injury_per_racer():
    """A racer should only get injured once per race even with many stumbles."""
    result = RaceResult(
        placements=[1, 2],
        segments=[],
        racer_names={1: "Clumsy", 2: "Ok"},
        stumble_counts={1: 10, 2: 0},  # 10 stumbles!
    )
    for seed in range(200):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        racer_1_injuries = [i for i in injuries if i[0] == 1]
        assert len(racer_1_injuries) <= 1


def test_check_injury_risk_recovery_is_2d4():
    """Injury recovery should be 2-8 (2d4)."""
    result = RaceResult(
        placements=[1, 2],
        segments=[],
        racer_names={1: "A", 2: "B"},
        stumble_counts={1: 5, 2: 5},
    )
    recoveries = set()
    for seed in range(2000):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        for _, _, recovery in injuries:
            recoveries.add(recovery)
    assert min(recoveries) >= 2
    assert max(recoveries) <= 8


def test_check_injury_risk_description_from_list():
    """Injury descriptions should come from INJURY_DESCRIPTIONS."""
    result = RaceResult(
        placements=[1, 2],
        segments=[],
        racer_names={1: "A", 2: "B"},
        stumble_counts={1: 5, 2: 5},
    )
    for seed in range(500):
        rng = random.Random(seed)
        injuries = check_injury_risk(result, rng)
        for _, desc, _ in injuries:
            assert desc in INJURY_DESCRIPTIONS


@pytest.mark.asyncio
async def test_apply_injuries(session: AsyncSession):
    """apply_injuries should set injuries and recovery on racer."""
    r = Racer(name="Victim", owner_id=1, speed=20, cornering=20, stamina=20)
    session.add(r)
    await session.commit()
    await session.refresh(r)

    await apply_injuries(session, [(r.id, "Pulled hamstring", 4)], [r])
    await session.commit()

    assert r.injuries == "Pulled hamstring"
    assert r.injury_races_remaining == 4


def test_simulate_race_tracks_stumbles():
    """RaceResult should include stumble_counts when using maps."""
    r1 = Racer(id=1, name="A", owner_id=1, speed=20, cornering=20, stamina=20)
    r2 = Racer(id=2, name="B", owner_id=2, speed=20, cornering=20, stamina=20)
    race_map = RaceMap(
        name="Test",
        theme="test",
        description="",
        segments=[MapSegment(type="straight", distance=2)] * 5,
    )
    result = simulate_race({"racers": [r1, r2]}, seed=42, race_map=race_map)
    assert isinstance(result.stumble_counts, dict)
    assert 1 in result.stumble_counts
    assert 2 in result.stumble_counts


# ---------------------------------------------------------------------------
# Career lifecycle
# ---------------------------------------------------------------------------


def test_effective_stats_peak():
    """During peak phase, effective stats equal base stats."""
    r = Racer(
        id=1, name="A", owner_id=1, speed=20, cornering=15, stamina=25,
        races_completed=10, career_length=30, peak_end=18,
    )
    stats = effective_stats(r)
    assert stats == {"speed": 20, "cornering": 15, "stamina": 25}


def test_effective_stats_decline():
    """During decline, each stat is reduced by (races_completed - peak_end)."""
    r = Racer(
        id=1, name="A", owner_id=1, speed=20, cornering=15, stamina=25,
        races_completed=25, career_length=30, peak_end=18,
    )
    stats = effective_stats(r)
    penalty = 25 - 18  # 7
    assert stats == {"speed": 13, "cornering": 8, "stamina": 18}


def test_effective_stats_floor_zero():
    """Effective stats never go below zero."""
    r = Racer(
        id=1, name="A", owner_id=1, speed=3, cornering=2, stamina=1,
        races_completed=28, career_length=30, peak_end=18,
    )
    stats = effective_stats(r)
    assert all(v >= 0 for v in stats.values())
    assert stats["stamina"] == 0  # 1 - 10 = 0 (clamped)


def test_career_phase_peak():
    r = Racer(
        id=1, name="A", owner_id=1, races_completed=10,
        career_length=30, peak_end=18,
    )
    assert career_phase(r) == "Peak"


def test_career_phase_declining():
    r = Racer(
        id=1, name="A", owner_id=1, races_completed=22,
        career_length=30, peak_end=18,
    )
    assert "Declining" in career_phase(r)
    assert "-4" in career_phase(r)


def test_career_phase_retiring_soon():
    r = Racer(
        id=1, name="A", owner_id=1, races_completed=28,
        career_length=30, peak_end=18,
    )
    assert career_phase(r) == "Retiring Soon"


def test_career_phase_retired():
    r = Racer(
        id=1, name="A", owner_id=1, races_completed=30,
        career_length=30, peak_end=18,
    )
    assert career_phase(r) == "Retired"


def test_decline_affects_simulation():
    """A declining racer should perform worse than the same racer at peak."""
    peak = Racer(
        id=1, name="Peak", owner_id=1, speed=25, cornering=25, stamina=25,
        races_completed=10, career_length=30, peak_end=18,
    )
    declining = Racer(
        id=2, name="Old", owner_id=2, speed=25, cornering=25, stamina=25,
        races_completed=28, career_length=30, peak_end=18,
    )
    race_map = RaceMap(
        name="Test", theme="test", description="",
        segments=[MapSegment(type="straight", distance=2)] * 3,
    )
    # Run many races to check statistical tendency
    peak_wins = 0
    for seed in range(100):
        result = simulate_race({"racers": [peak, declining]}, seed=seed, race_map=race_map)
        if result.placements[0] == peak.id:
            peak_wins += 1
    # Peak racer should win significantly more often
    assert peak_wins > 65


# ---------------------------------------------------------------------------
# Pricing tests
# ---------------------------------------------------------------------------


def test_calculate_buy_price():
    racer = Racer(id=1, name="A", owner_id=0, speed=15, cornering=15, stamina=15)
    assert calculate_buy_price(racer, base_cost=20, multiplier=2) == 20 + 45 * 2

    zero = Racer(id=2, name="B", owner_id=0, speed=0, cornering=0, stamina=0)
    assert calculate_buy_price(zero, base_cost=20, multiplier=2) == 20

    maxed = Racer(id=3, name="C", owner_id=0, speed=31, cornering=31, stamina=31)
    assert calculate_buy_price(maxed, base_cost=20, multiplier=2) == 20 + 93 * 2


def test_calculate_sell_price():
    racer = Racer(id=1, name="A", owner_id=0, speed=15, cornering=15, stamina=15)
    buy = calculate_buy_price(racer, 20, 2)
    sell = calculate_sell_price(racer, 20, 2, 0.5)
    assert sell == int(buy * 0.5)
    assert sell < buy  # money sink


def test_generate_pool_racer():
    kwargs = generate_pool_racer(guild_id=42, taken_names=set())
    assert kwargs["owner_id"] == 0
    assert kwargs["guild_id"] == 42
    assert 0 <= kwargs["speed"] <= 31
    assert 0 <= kwargs["cornering"] <= 31
    assert 0 <= kwargs["stamina"] <= 31
    assert kwargs["temperament"] in (
        "Agile", "Reckless", "Tactical", "Burly", "Steady", "Sharpshift", "Quirky"
    )
    assert kwargs["name"]  # not empty
    assert 25 <= kwargs["career_length"] <= 40
    assert kwargs["gender"] in ("M", "F")
    assert kwargs["peak_end"] == int(kwargs["career_length"] * 0.6)


def test_generate_pool_racer_avoids_taken():
    kwargs1 = generate_pool_racer(guild_id=1, taken_names=set())
    # The generated name should not repeat when added to taken
    kwargs2 = generate_pool_racer(guild_id=1, taken_names={kwargs1["name"]})
    assert kwargs2["name"] != kwargs1["name"]


# ---------------------------------------------------------------------------
# Placement prize tests
# ---------------------------------------------------------------------------


def test_parse_placement_prizes():
    assert parse_placement_prizes("50,30,20") == [50, 30, 20]
    assert parse_placement_prizes("100") == [100]
    assert parse_placement_prizes("") == []
    assert parse_placement_prizes("  50 , 30 , 20 ") == [50, 30, 20]


@pytest.mark.asyncio
async def test_resolve_placement_prizes(session: AsyncSession):
    """Owned racers get prizes; unowned racers are skipped."""
    r1 = Racer(id=1, name="Owned1st", owner_id=10, guild_id=1, speed=10, cornering=10, stamina=10)
    r2 = Racer(id=2, name="Unowned2nd", owner_id=0, guild_id=1, speed=10, cornering=10, stamina=10)
    r3 = Racer(id=3, name="Owned3rd", owner_id=20, guild_id=1, speed=10, cornering=10, stamina=10)
    participants = [r1, r2, r3]
    placements = [1, 2, 3]  # racer IDs in finish order

    # Create wallet for owner 10
    w1 = Wallet(user_id=10, guild_id=1, balance=100)
    session.add(w1)
    await session.commit()

    awards = await resolve_placement_prizes(
        session, placements, participants, guild_id=1, prize_list=[50, 30, 20]
    )

    # Owner 10 (1st) gets 50, owner 0 (2nd) skipped, owner 20 (3rd) gets 20
    assert len(awards) == 2
    assert awards[0] == (10, 1, 50)
    assert awards[1] == (20, 3, 20)

    # Check wallet balances
    await session.refresh(w1)
    assert w1.balance == 150  # 100 + 50

    # Owner 20's wallet was auto-created
    from sqlalchemy import select as sel
    w2 = (await session.execute(sel(Wallet).where(Wallet.user_id == 20))).scalars().first()
    assert w2 is not None
    assert w2.balance == 20


@pytest.mark.asyncio
async def test_resolve_placement_prizes_beyond_list(session: AsyncSession):
    """Positions beyond the prize list get nothing."""
    r1 = Racer(id=1, name="First", owner_id=10, guild_id=1, speed=10, cornering=10, stamina=10)
    r2 = Racer(id=2, name="Fourth", owner_id=20, guild_id=1, speed=10, cornering=10, stamina=10)
    participants = [r1, r2]
    placements = [1, 2]

    awards = await resolve_placement_prizes(
        session, placements, participants, guild_id=1, prize_list=[50]  # only 1st place
    )

    assert len(awards) == 1
    assert awards[0] == (10, 1, 50)


# ---------------------------------------------------------------------------
# Training tests
# ---------------------------------------------------------------------------


def test_calculate_training_cost():
    # Default: base=10, multiplier=2
    assert calculate_training_cost(0, 10, 2) == 10
    assert calculate_training_cost(15, 10, 2) == 40
    assert calculate_training_cost(30, 10, 2) == 70
    # Custom base/multiplier
    assert calculate_training_cost(10, 20, 3) == 50


def test_training_failure_chance():
    # Normal+ mood, no injury → 0%
    assert training_failure_chance(3, False) == 0.0
    assert training_failure_chance(4, False) == 0.0
    assert training_failure_chance(5, False) == 0.0

    # Awful mood, no injury → 50%
    assert training_failure_chance(1, False) == pytest.approx(0.50)

    # Bad mood, no injury → 25%
    assert training_failure_chance(2, False) == pytest.approx(0.25)

    # Normal mood, injured → 25%
    assert training_failure_chance(3, True) == pytest.approx(0.25)

    # Awful mood, injured → 1 - (0.5 * 0.75) = 0.625
    assert training_failure_chance(1, True) == pytest.approx(0.625)

    # Bad mood, injured → 1 - (0.75 * 0.75) = 0.4375
    assert training_failure_chance(2, True) == pytest.approx(0.4375)


def test_trainable_stats_constant():
    assert TRAINABLE_STATS == {"speed", "cornering", "stamina"}
    assert MAX_STAT == 31


# ---------------------------------------------------------------------------
# Mood care tests
# ---------------------------------------------------------------------------


def test_apply_rest():
    # Normal case: mood increases by 1
    new, err = apply_rest(3)
    assert new == 4
    assert err is None

    # Cap at 5
    new, err = apply_rest(4)
    assert new == 5
    assert err is None

    # Already max → rejected
    new, err = apply_rest(5)
    assert new == 5
    assert err is not None
    assert "great spirits" in err.lower()


def test_apply_feed():
    # Normal case: mood increases by 2
    new, err = apply_feed(2)
    assert new == 4
    assert err is None

    # Caps at 5 (mood 4 + 2 = 6 → capped to 5)
    new, err = apply_feed(4)
    assert new == 5
    assert err is None

    # From mood 1 → 3
    new, err = apply_feed(1)
    assert new == 3
    assert err is None

    # Already max → rejected
    new, err = apply_feed(5)
    assert new == 5
    assert err is not None
    assert "great spirits" in err.lower()


# ---------------------------------------------------------------------------
# Gender + lineage tests
# ---------------------------------------------------------------------------


def test_generate_pool_racer_gender_distribution():
    """Over many generations, gender should be roughly 50/50."""
    genders = [
        generate_pool_racer(guild_id=1, taken_names=set())["gender"]
        for _ in range(200)
    ]
    m_count = genders.count("M")
    f_count = genders.count("F")
    # Allow wide tolerance — just make sure both appear
    assert m_count > 30
    assert f_count > 30


def test_gender_labels():
    from derby.logic import GENDER_LABELS
    assert GENDER_LABELS["M"] == "\u2642"
    assert GENDER_LABELS["F"] == "\u2640"
