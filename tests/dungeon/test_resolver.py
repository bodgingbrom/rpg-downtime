"""Unit tests for dungeon/resolver.py — modifier composition.

Exercises the stacking rules per the design contract.
"""
from __future__ import annotations

from dungeon import resolver


def test_bump_dice_clamps_upper():
    assert resolver.bump_dice("1d10", 5) == "1d12"


def test_bump_dice_clamps_lower():
    assert resolver.bump_dice("1d4", -5) == "1d4"


def test_bump_dice_passthrough_unknown():
    assert resolver.bump_dice("2d6", 2) == "2d6"


def test_bump_dice_standard_ladder():
    assert resolver.bump_dice("1d4", 1) == "1d6"
    assert resolver.bump_dice("1d6", 1) == "1d8"
    assert resolver.bump_dice("1d8", 1) == "1d10"


def test_player_attack_mods_default_no_state():
    mods = resolver.resolve_player_attack_mods(
        race="human", run_current_hp=10, run_max_hp=10, state=None,
    )
    assert mods.damage_advantage is False
    assert mods.damage_disadvantage is False
    assert mods.bonus_penalty == 0
    assert mods.weapon_dice_step == 0
    assert mods.damage_bonus == 0


def test_player_attack_mods_orc_bloodrage_below_half():
    mods = resolver.resolve_player_attack_mods(
        race="orc", run_current_hp=5, run_max_hp=20, state=None,
    )
    assert mods.damage_advantage is True


def test_player_attack_mods_orc_bloodrage_above_half():
    mods = resolver.resolve_player_attack_mods(
        race="orc", run_current_hp=15, run_max_hp=20, state=None,
    )
    assert mods.damage_advantage is False


def test_player_attack_mods_halfling_penalty():
    mods = resolver.resolve_player_attack_mods(
        race="halfling", run_current_hp=10, run_max_hp=10, state=None,
    )
    assert mods.bonus_penalty == 1


def test_player_attack_mods_invert_sets_disadvantage():
    state = {"player_effects": [{"type": "invert_next_attack", "remaining": 1}]}
    mods = resolver.resolve_player_attack_mods(
        race="human", run_current_hp=10, run_max_hp=10, state=state,
    )
    assert mods.damage_disadvantage is True


def test_player_attack_mods_advantage_flag():
    state = {"player_effects": [{"type": "advantage_next_attack", "remaining": 1}]}
    mods = resolver.resolve_player_attack_mods(
        race="human", run_current_hp=10, run_max_hp=10, state=state,
    )
    assert mods.damage_advantage is True


def test_monster_attack_mods_dice_step_sums_multiple_sources():
    state = {
        "monster_effects": [
            {"type": "dice_step_self_active", "step": 1},
            {"type": "dice_step_self_active", "step": 2},
        ]
    }
    mods = resolver.resolve_monster_attack_mods(state=state)
    assert mods.attack_dice_step == 3


def test_monster_attack_mods_flat_damage_sums():
    state = {
        "monster_effects": [
            {"type": "flat_damage_bonus_self", "amount": 2},
            {"type": "flat_damage_bonus_self", "amount": 3},
        ]
    }
    mods = resolver.resolve_monster_attack_mods(state=state)
    assert mods.flat_damage_bonus == 5


def test_monster_defense_bonus_sums():
    state = {
        "monster_effects": [
            {"type": "defense_bonus_self", "amount": 1, "remaining": 1},
            {"type": "defense_bonus_self", "amount": 3, "remaining": 2},
        ]
    }
    assert resolver.resolve_monster_defense_bonus(state) == 4


def test_player_defense_hit_chance_multiplicative():
    state = {
        "player_effects": [
            {"type": "hit_chance_reduction", "amount": 0.5, "remaining": 1},
            {"type": "hit_chance_reduction", "amount": 0.5, "remaining": 1},
        ]
    }
    mods = resolver.resolve_player_defense_mods(state)
    # 0.5 * 0.5 == 0.25
    assert abs(mods.hit_chance_multiplier - 0.25) < 1e-6


def test_bleed_damage_sums_across_sources():
    state = {
        "player_effects": [
            {"type": "bleed", "damage": 2, "remaining": 1},
            {"type": "bleed", "damage": 3, "remaining": 2},
        ]
    }
    assert resolver.resolve_bleed_damage(state) == 5
