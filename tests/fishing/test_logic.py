"""Unit tests for fishing/logic.py — pure simulation primitives.

Covers:
  - select_catch:  weight filters, bait gating, mode filters, rod boosts
  - leveling:      get_level, get_xp_for_next_level, can_fish_at_location
  - cast time:     calculate_active_cast_time bounds + reductions

LLM-bound functions (in fishing/llm.py) are NOT tested here — they're
mocked at the seam in test_handlers.py instead.
"""

from __future__ import annotations

import random

import pytest

from fishing import logic as fish_logic


# ---------------------------------------------------------------------------
# select_catch
# ---------------------------------------------------------------------------


def test_select_catch_returns_fish_when_pool_has_fish(sample_location, sample_rod):
    random.seed(0)
    catch = fish_logic.select_catch(
        sample_location, sample_rod, bait_type="worm",
    )
    assert catch["name"]
    # Must be one of the YAML-defined fish/trash names
    valid = {"Bluegill", "Silverscale Trout", "Glasswing Pike", "Old One", "Old Boot"}
    assert catch["name"] in valid


def test_select_catch_returns_nothing_when_pool_empty(sample_rod):
    """Empty pool (no fish, no trash) → fallback "Nothing" entry."""
    empty_location = {"name": "Empty Pool", "fish": [], "trash": []}
    catch = fish_logic.select_catch(empty_location, sample_rod, bait_type="worm")
    assert catch == {
        "name": "Nothing",
        "is_trash": True,
        "rarity": None,
        "value": 0,
        "length": None,
    }


def test_select_catch_excludes_trash_in_active_mode(sample_location, sample_rod):
    """include_trash=False: ``Old Boot`` should never be returned."""
    random.seed(0)
    for _ in range(50):
        catch = fish_logic.select_catch(
            sample_location, sample_rod, bait_type="worm", include_trash=False,
        )
        assert catch["name"] != "Old Boot"
        assert catch["is_trash"] is False


def test_select_catch_excludes_legendaries_in_afk_mode(sample_location, sample_rod):
    """include_legendary=False: ``Old One`` should never be returned."""
    random.seed(0)
    for _ in range(100):
        catch = fish_logic.select_catch(
            sample_location, sample_rod, bait_type="worm", include_legendary=False,
        )
        assert catch["name"] != "Old One"


def test_select_catch_skips_fish_requiring_other_bait(sample_location, sample_rod):
    """Glasswing Pike requires shiny_lure — must never appear with worm."""
    random.seed(0)
    for _ in range(200):
        catch = fish_logic.select_catch(
            sample_location, sample_rod, bait_type="worm", include_trash=False,
        )
        assert catch["name"] != "Glasswing Pike"


def test_select_catch_can_return_required_bait_fish_when_bait_matches(
    sample_location, sample_rod,
):
    """With shiny_lure, Glasswing Pike is in the pool. Verify it can be selected."""
    random.seed(0)
    seen = set()
    for _ in range(500):
        catch = fish_logic.select_catch(
            sample_location, sample_rod, bait_type="shiny_lure",
            include_trash=False, include_legendary=False,
        )
        seen.add(catch["name"])
    assert "Glasswing Pike" in seen


def test_select_catch_returns_value_within_range(sample_location, sample_rod):
    """value/length are random within the YAML's ranges."""
    random.seed(0)
    for _ in range(50):
        catch = fish_logic.select_catch(
            sample_location, sample_rod, bait_type="worm",
            include_trash=False, include_legendary=False,
        )
        if catch["name"] == "Bluegill":
            assert 1 <= catch["value"] <= 4
            assert 4 <= catch["length"] <= 10


# ---------------------------------------------------------------------------
# Leveling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "xp,expected_level",
    [
        (0, 1),
        (99, 1),
        (100, 2),
        (299, 2),
        (300, 3),
        (1000, 5),
        (10_000, 5),  # capped at max
    ],
)
def test_get_level_xp_curve(xp, expected_level):
    assert fish_logic.get_level(xp) == expected_level


def test_get_xp_for_next_level_returns_distance_and_target():
    needed, next_level = fish_logic.get_xp_for_next_level(50)
    assert needed == 50  # 100 - 50
    assert next_level == 2


def test_get_xp_for_next_level_returns_none_at_max():
    assert fish_logic.get_xp_for_next_level(10_000) is None


def test_can_fish_at_location_blocked_by_min_level():
    high_level_loc = {"skill_level": 4}
    assert fish_logic.can_fish_at_location(player_level=3, location_data=high_level_loc) is False
    assert fish_logic.can_fish_at_location(player_level=4, location_data=high_level_loc) is True


def test_get_skill_cast_reduction_zero_when_at_or_below_requirement():
    assert fish_logic.get_skill_cast_reduction(player_level=1, location_skill_level=3) == 0.0
    assert fish_logic.get_skill_cast_reduction(player_level=3, location_skill_level=3) == 0.0


def test_get_skill_cast_reduction_scales_with_levels_above():
    # 2% per level above
    assert fish_logic.get_skill_cast_reduction(player_level=4, location_skill_level=3) == pytest.approx(0.02)
    assert fish_logic.get_skill_cast_reduction(player_level=6, location_skill_level=3) == pytest.approx(0.06)


# ---------------------------------------------------------------------------
# Cast time
# ---------------------------------------------------------------------------


def test_calculate_active_cast_time_respects_floor(sample_rod):
    """Even with maximum reductions, never below ACTIVE_BITE_FLOOR (15s)."""
    random.seed(0)
    # Stack every reduction to drive the result toward zero
    result = fish_logic.calculate_active_cast_time(
        rod_data={"cast_reduction": 0.5},
        bait_type="premium",  # 0.08
        skill_reduction=0.5,
        trophy_reduction=0.5,
        cast_multiplier=0.1,
    )
    assert result >= fish_logic.ACTIVE_BITE_FLOOR


def test_calculate_active_cast_time_within_random_base_window(sample_rod):
    """No reductions → result is between 30-90s (the random base range)."""
    random.seed(0)
    for _ in range(20):
        result = fish_logic.calculate_active_cast_time(
            rod_data=sample_rod, bait_type="worm",
        )
        assert fish_logic.ACTIVE_BITE_MIN_BASE <= result <= fish_logic.ACTIVE_BITE_MAX_BASE


# ---------------------------------------------------------------------------
# Catch XP
# ---------------------------------------------------------------------------


def test_calculate_catch_xp_scales_with_location_skill():
    location_low = {"skill_level": 1}
    location_high = {"skill_level": 3}
    catch = {"is_trash": False, "rarity": "uncommon"}
    assert fish_logic.calculate_catch_xp(catch, location_low) == 15  # 15 * 1
    assert fish_logic.calculate_catch_xp(catch, location_high) == 45  # 15 * 3


def test_calculate_catch_xp_uses_trash_xp_for_trash():
    catch = {"is_trash": True, "rarity": "common"}
    assert fish_logic.calculate_catch_xp(catch, {"skill_level": 1}) == 1


# ---------------------------------------------------------------------------
# Trophy detection
# ---------------------------------------------------------------------------


def test_has_location_trophy_true_when_all_species_caught(sample_location):
    caught = {"Bluegill", "Silverscale Trout", "Glasswing Pike", "Old One"}
    assert fish_logic.has_location_trophy(caught, sample_location) is True


def test_has_location_trophy_false_when_one_missing(sample_location):
    caught = {"Bluegill", "Silverscale Trout"}  # missing pike + legendary
    assert fish_logic.has_location_trophy(caught, sample_location) is False
