"""Tests for potion buff integration with race simulation."""

import pytest

from derby.logic import (
    RaceMap,
    MapSegment,
    convert_buffs,
    effective_stats,
    run_tournament,
    simulate_race,
)
from derby.models import Racer, RacerBuff


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _racer(id: int, speed: int = 15, cornering: int = 15, stamina: int = 15,
           mood: int = 3, **kw) -> Racer:
    defaults = dict(
        name=f"Racer{id}", owner_id=1, temperament="Quirky",
        races_completed=5, career_length=30, peak_end=18,
    )
    defaults.update(kw)
    return Racer(id=id, speed=speed, cornering=cornering, stamina=stamina,
                 mood=mood, **defaults)


def _buff(racer_id: int, buff_type: str, value: int, races_remaining: int = 1) -> RacerBuff:
    return RacerBuff(
        racer_id=racer_id, guild_id=100,
        buff_type=buff_type, value=value, races_remaining=races_remaining,
    )


SIMPLE_MAP = RaceMap(
    name="Test Track",
    theme="test",
    description="A simple test track",
    segments=[
        MapSegment(type="straight", distance=100),
        MapSegment(type="corner", distance=80),
        MapSegment(type="straight", distance=100),
    ],
)


# ---------------------------------------------------------------------------
# effective_stats with buffs
# ---------------------------------------------------------------------------


class TestEffectiveStatsWithBuffs:
    def test_no_buffs_unchanged(self):
        r = _racer(1, speed=20, cornering=15, stamina=10)
        assert effective_stats(r) == {"speed": 20, "cornering": 15, "stamina": 10}
        assert effective_stats(r, buffs=None) == {"speed": 20, "cornering": 15, "stamina": 10}

    def test_single_stat_buff(self):
        r = _racer(1, speed=20, cornering=15, stamina=10)
        stats = effective_stats(r, buffs={"speed": 5})
        assert stats["speed"] == 25
        assert stats["cornering"] == 15
        assert stats["stamina"] == 10

    def test_multiple_stat_buffs(self):
        r = _racer(1, speed=20, cornering=15, stamina=10)
        stats = effective_stats(r, buffs={"speed": 3, "cornering": 2, "stamina": 4})
        assert stats == {"speed": 23, "cornering": 17, "stamina": 14}

    def test_exceeds_31_cap(self):
        r = _racer(1, speed=30, cornering=28, stamina=25)
        stats = effective_stats(r, buffs={"speed": 5})
        assert stats["speed"] == 35  # buffs can exceed 31

    def test_buff_with_decline(self):
        r = _racer(1, speed=20, cornering=15, stamina=10,
                   races_completed=23, peak_end=18)
        # Decline penalty = 23 - 18 = 5
        stats = effective_stats(r, buffs={"speed": 8})
        assert stats["speed"] == 23  # (20 - 5) + 8
        assert stats["cornering"] == 10  # 15 - 5, no buff

    def test_buff_ignores_unknown_stat(self):
        r = _racer(1, speed=20, cornering=15, stamina=10)
        stats = effective_stats(r, buffs={"agility": 5})
        assert stats == {"speed": 20, "cornering": 15, "stamina": 10}


# ---------------------------------------------------------------------------
# convert_buffs
# ---------------------------------------------------------------------------


class TestConvertBuffs:
    def test_empty(self):
        stat_buffs, mood_buffs = convert_buffs({})
        assert stat_buffs == {}
        assert mood_buffs == {}

    def test_single_stat_buff(self):
        raw = {1: [_buff(1, "speed", 3)]}
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs == {1: {"speed": 3}}
        assert mood_buffs == {}

    def test_mood_buff(self):
        raw = {1: [_buff(1, "mood", 2)]}
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs == {}
        assert mood_buffs == {1: 2}

    def test_all_stats_expands(self):
        raw = {1: [_buff(1, "all_stats", 2)]}
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs == {1: {"speed": 2, "cornering": 2, "stamina": 2}}
        # all_stats also adds to mood
        assert mood_buffs == {1: 2}

    def test_multiple_buffs_stack(self):
        raw = {1: [_buff(1, "speed", 3), _buff(1, "speed", 2)]}
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs[1]["speed"] == 5

    def test_multiple_racers(self):
        raw = {
            1: [_buff(1, "speed", 3)],
            2: [_buff(2, "cornering", 4)],
        }
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs[1] == {"speed": 3}
        assert stat_buffs[2] == {"cornering": 4}

    def test_all_stats_plus_individual(self):
        raw = {1: [_buff(1, "all_stats", 2), _buff(1, "speed", 3)]}
        stat_buffs, mood_buffs = convert_buffs(raw)
        assert stat_buffs[1] == {"speed": 5, "cornering": 2, "stamina": 2}
        assert mood_buffs[1] == 2


# ---------------------------------------------------------------------------
# simulate_race with buffs
# ---------------------------------------------------------------------------


class TestSimulateRaceWithBuffs:
    def test_buffed_racer_scores_higher(self):
        """A heavily buffed racer should generally place better."""
        r1 = _racer(1, speed=15, cornering=15, stamina=15, mood=3)
        r2 = _racer(2, speed=15, cornering=15, stamina=15, mood=3)
        r3 = _racer(3, speed=15, cornering=15, stamina=15, mood=3)
        r4 = _racer(4, speed=15, cornering=15, stamina=15, mood=3)

        # Run many races; buffed racer should win majority
        wins = 0
        trials = 50
        for seed in range(trials):
            result = simulate_race(
                {"racers": [r1, r2, r3, r4]}, seed, race_map=SIMPLE_MAP,
                stat_buffs={1: {"speed": 10, "cornering": 10, "stamina": 10}},
            )
            if result.placements[0] == 1:
                wins += 1

        # With +10 to all stats, racer 1 should win most races
        assert wins > trials * 0.5

    def test_mood_buff_applied(self):
        """Mood buff should be capped at 5."""
        r1 = _racer(1, speed=15, cornering=15, stamina=15, mood=4)
        r2 = _racer(2, speed=15, cornering=15, stamina=15, mood=1)

        # With mood buff of 3, racer 2 goes from mood 1 → 4
        # This should make them more competitive
        wins_without_buff = 0
        wins_with_buff = 0
        trials = 50
        for seed in range(trials):
            result = simulate_race(
                {"racers": [r1, r2]}, seed, race_map=SIMPLE_MAP,
            )
            if result.placements[0] == 2:
                wins_without_buff += 1

            result = simulate_race(
                {"racers": [r1, r2]}, seed, race_map=SIMPLE_MAP,
                mood_buffs={2: 3},
            )
            if result.placements[0] == 2:
                wins_with_buff += 1

        # With mood buff, racer 2 should win more often
        assert wins_with_buff >= wins_without_buff

    def test_no_buffs_unchanged(self):
        """Passing empty buffs produces same result as no buffs."""
        r1 = _racer(1)
        r2 = _racer(2)
        seed = 42
        result_none = simulate_race(
            {"racers": [r1, r2]}, seed, race_map=SIMPLE_MAP,
        )
        result_empty = simulate_race(
            {"racers": [r1, r2]}, seed, race_map=SIMPLE_MAP,
            stat_buffs={}, mood_buffs={},
        )
        assert result_none.placements == result_empty.placements


# ---------------------------------------------------------------------------
# run_tournament with buffs
# ---------------------------------------------------------------------------


class TestRunTournamentWithBuffs:
    def test_buffed_racer_tends_to_win(self):
        """A buffed racer in a tournament should place well more often."""
        racers = [_racer(i, speed=12, cornering=12, stamina=12) for i in range(1, 9)]

        # Run several tournaments; racer 1 with big buff should often be top 2
        top2_count = 0
        trials = 20
        for seed in range(trials):
            result = run_tournament(
                racers, seed, race_map=SIMPLE_MAP,
                stat_buffs={1: {"speed": 10, "cornering": 10, "stamina": 10}},
            )
            if 1 in result.final_placements[:2]:
                top2_count += 1

        assert top2_count > trials * 0.4

    def test_no_buffs_unchanged(self):
        """Tournament without buffs produces same result as empty buffs."""
        racers = [_racer(i) for i in range(1, 9)]
        seed = 42
        result_none = run_tournament(racers, seed, race_map=SIMPLE_MAP)
        result_empty = run_tournament(
            racers, seed, race_map=SIMPLE_MAP,
            stat_buffs={}, mood_buffs={},
        )
        assert result_none.final_placements == result_empty.final_placements
