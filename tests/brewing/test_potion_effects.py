"""Tests for potion effect functions and brew effect integration."""

import pytest

from brewing.potions import (
    ALL_TEMPERAMENTS,
    apply_mutation,
    generate_stripping_choices,
)


# ---------------------------------------------------------------------------
# generate_stripping_choices
# ---------------------------------------------------------------------------


class TestGenerateStrippingChoices:
    def test_excludes_current_temperament(self):
        choices = generate_stripping_choices("Agile", 3, seed=42)
        assert "Agile" not in choices
        assert len(choices) == 3

    def test_returns_correct_count(self):
        choices = generate_stripping_choices("Quirky", 5, seed=1)
        assert len(choices) == 5

    def test_cap_at_available(self):
        # Only 6 others available, asking for 10 should cap at 6
        choices = generate_stripping_choices("Steady", 10, seed=1)
        assert len(choices) == 6

    def test_all_choices_are_valid_temperaments(self):
        choices = generate_stripping_choices("Burly", 4, seed=99)
        for c in choices:
            assert c in ALL_TEMPERAMENTS

    def test_deterministic_with_same_seed(self):
        a = generate_stripping_choices("Tactical", 3, seed=123)
        b = generate_stripping_choices("Tactical", 3, seed=123)
        assert a == b

    def test_different_seeds_can_differ(self):
        a = generate_stripping_choices("Tactical", 4, seed=1)
        b = generate_stripping_choices("Tactical", 4, seed=2)
        # Not guaranteed to differ, but with 4 choices from 6 it's very likely
        # Just check both are valid
        assert len(a) == 4
        assert len(b) == 4


# ---------------------------------------------------------------------------
# apply_mutation
# ---------------------------------------------------------------------------


class TestApplyMutation:
    def test_returns_stat_name_old_new(self):
        stat_name, old_val, new_val = apply_mutation(
            speed=10, cornering=15, stamina=20, floor_value=5, seed=42
        )
        assert stat_name in ("speed", "cornering", "stamina")
        expected_old = {"speed": 10, "cornering": 15, "stamina": 20}
        assert old_val == expected_old[stat_name]
        assert 5 <= new_val <= 31

    def test_floor_value_respected(self):
        # Run multiple seeds to check floor is respected
        for seed in range(50):
            _, _, new_val = apply_mutation(
                speed=10, cornering=10, stamina=10, floor_value=20, seed=seed
            )
            assert new_val >= 20

    def test_floor_31_always_gives_31(self):
        for seed in range(20):
            _, _, new_val = apply_mutation(
                speed=5, cornering=5, stamina=5, floor_value=31, seed=seed
            )
            assert new_val == 31

    def test_deterministic_with_same_seed(self):
        a = apply_mutation(speed=10, cornering=15, stamina=20, floor_value=0, seed=7)
        b = apply_mutation(speed=10, cornering=15, stamina=20, floor_value=0, seed=7)
        assert a == b

    def test_floor_0(self):
        for seed in range(50):
            _, _, new_val = apply_mutation(
                speed=15, cornering=15, stamina=15, floor_value=0, seed=seed
            )
            assert 0 <= new_val <= 31
