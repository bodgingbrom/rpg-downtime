"""Tests for dungeon/logic.py variant, phase, and description helpers."""
from __future__ import annotations

import random

import pytest

from dungeon import logic


def test_apply_variant_none_returns_unchanged():
    m = {"id": "x", "name": "Thing", "hp": 10, "defense": 0, "attack_bonus": 0}
    out = logic.apply_variant(m, None)
    assert out == m


def test_apply_variant_deltas_additive():
    m = {"id": "x", "name": "Swatch", "hp": 10, "defense": 2, "attack_bonus": 1}
    variant = {
        "key": "mountain",
        "name_suffix": "(Mountain)",
        "hp_delta": 5,
        "defense_delta": 2,
        "attack_bonus_delta": -1,
    }
    out = logic.apply_variant(m, variant)
    assert out["hp"] == 15
    assert out["defense"] == 4
    assert out["attack_bonus"] == 0
    assert out["name"] == "Swatch (Mountain)"
    # Original untouched
    assert m["hp"] == 10


def test_apply_variant_replaces_attack_dice():
    m = {"id": "x", "name": "Thing", "hp": 10, "attack_dice": "1d6"}
    variant = {"key": "big", "attack_dice": "1d10"}
    out = logic.apply_variant(m, variant)
    assert out["attack_dice"] == "1d10"


def test_apply_variant_passes_through_effect_fields():
    m = {"id": "x", "name": "Thing"}
    variant = {
        "key": "swamp",
        "on_hit_effect": {"type": "bleed", "damage": 2, "turns": 2},
    }
    out = logic.apply_variant(m, variant)
    assert out["on_hit_effect"]["type"] == "bleed"


def test_compute_phase_baseline_when_full_hp():
    phases = [{"hp_below_pct": 66}, {"hp_below_pct": 33}]
    assert logic.compute_phase(10, 10, phases) == 0


def test_compute_phase_crosses_first_threshold():
    phases = [{"hp_below_pct": 66}, {"hp_below_pct": 33}]
    # 60% of 10 = 6 → below 66%
    assert logic.compute_phase(6, 10, phases) == 1


def test_compute_phase_crosses_both_thresholds():
    phases = [{"hp_below_pct": 66}, {"hp_below_pct": 33}]
    # 30% of 10 = 3 → below 33%
    assert logic.compute_phase(3, 10, phases) == 2


def test_compute_phase_no_phases_returns_zero():
    assert logic.compute_phase(5, 10, None) == 0
    assert logic.compute_phase(5, 10, []) == 0


def test_compute_phase_handles_zero_max():
    assert logic.compute_phase(0, 0, [{"hp_below_pct": 50}]) == 0


def test_pick_description_returns_none_when_no_pool():
    m = {"id": "x"}
    assert logic.pick_description(m, random.Random(0)) is None


def test_pick_description_from_pool():
    m = {"id": "x", "description_pool": ["one", "two", "three"]}
    rng = random.Random(42)
    desc = logic.pick_description(m, rng)
    assert desc in {"one", "two", "three"}


def test_merge_phase_overrides_no_phase_returns_unchanged():
    m = {"id": "x", "attack_dice": "1d6"}
    assert logic.merge_phase_overrides(m, 0) is m


def test_merge_phase_overrides_replaces_fields():
    m = {
        "id": "x",
        "attack_dice": "1d6",
        "attack_bonus": 1,
        "defense": 1,
        "ai": {"attack": 70, "heavy": 30},
        "phases": [
            {"hp_below_pct": 66, "attack_dice": "1d8", "ai": {"attack": 50, "heavy": 50}},
        ],
    }
    out = logic.merge_phase_overrides(m, 1)
    assert out["attack_dice"] == "1d8"
    assert out["ai"] == {"attack": 50, "heavy": 50}
    # Fields not overridden preserved.
    assert out["defense"] == 1


def test_merge_phase_overrides_appends_abilities_add():
    m = {
        "id": "x",
        "abilities": [{"type": "narrate", "text": "base"}],
        "phases": [
            {"hp_below_pct": 50, "abilities_add": [{"type": "narrate", "text": "phase"}]},
        ],
    }
    out = logic.merge_phase_overrides(m, 1)
    texts = [a.get("text") for a in out["abilities"]]
    assert texts == ["base", "phase"]


def test_get_phase_def_one_indexed():
    m = {"phases": [{"hp_below_pct": 66}, {"hp_below_pct": 33}]}
    assert logic.get_phase_def(m, 0) is None
    assert logic.get_phase_def(m, 1) == m["phases"][0]
    assert logic.get_phase_def(m, 2) == m["phases"][1]
    assert logic.get_phase_def(m, 3) is None
