"""Integration tests for target-swap logic and combat-state transitions.

These test the parts of ``cogs/dungeon.py`` that orchestrate the combat
state machine around summoner bosses (Alaric's adds), without requiring
a full Discord interaction harness.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cogs.dungeon import _find_monster_def, _spawn_pending_adds, _swap_back_to_primary
from dungeon import effects as fx
from dungeon import logic as dungeon_logic
from dungeon import resolver


# ---------------------------------------------------------------------------
# Fixtures — a tiny in-memory "dungeon_data" and a run-like namespace.
# ---------------------------------------------------------------------------


def _fake_dungeon():
    return {
        "id": "test_folly",
        "name": "Test Folly",
        "floors": [
            {
                "floor": 1,
                "monsters": [
                    {
                        "id": "scribbled_hound",
                        "name": "Scribbled Hound",
                        "hp": 6,
                        "defense": 0,
                        "attack_dice": "1d4",
                        "attack_bonus": 0,
                        "xp": 4,
                        "gold": [1, 2],
                        "ai": {"attack": 100},
                    },
                ],
                "boss": {
                    "id": "alaric",
                    "name": "Alaric Venn",
                    "hp": 50,
                    "defense": 2,
                    "attack_dice": "1d8",
                    "attack_bonus": 3,
                    "xp": 50,
                    "gold": [25, 50],
                    "ai": {"attack": 60, "heavy": 30, "defend": 10},
                    "abilities": [
                        {
                            "type": "summon_add", "trigger": "on_turn", "every": 2,
                            "add_id": "scribbled_hound", "max_active": 1,
                            "untargetable_self": True,
                        },
                    ],
                    "phases": [
                        {
                            "hp_below_pct": 33,
                            "attack_dice": "2d8",
                            "on_enter": {"type": "narrate", "text": "Alaric lifts his quill..."},
                        },
                    ],
                },
            },
        ],
    }


def _fake_run(**overrides):
    """A minimal stand-in for the DungeonRun row."""
    defaults = dict(monster_id="alaric", monster_hp=50, monster_max_hp=50)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# _find_monster_def
# ---------------------------------------------------------------------------


def test_find_monster_def_locates_regular_monster():
    d = _fake_dungeon()
    assert _find_monster_def(d, "scribbled_hound")["name"] == "Scribbled Hound"


def test_find_monster_def_locates_boss():
    d = _fake_dungeon()
    assert _find_monster_def(d, "alaric")["hp"] == 50


def test_find_monster_def_returns_none_for_unknown():
    d = _fake_dungeon()
    assert _find_monster_def(d, "ghost_of_christmas_past") is None


# ---------------------------------------------------------------------------
# _spawn_pending_adds — the summon target-swap.
# ---------------------------------------------------------------------------


def test_spawn_pending_adds_materializes_and_swaps_target():
    d = _fake_dungeon()
    run = _fake_run(monster_hp=40)
    state = {
        "turn": 2,
        "phase": 0,
        "active": "primary",
        "primary": None,
        "adds": [{"def_id": "scribbled_hound", "pending_spawn": True}],
        "player_effects": [],
        "monster_effects": [],
    }
    narrative: list[str] = []
    _spawn_pending_adds(state, d, run, narrative)

    # Add is materialized with correct stats.
    assert len(state["adds"]) == 1
    add = state["adds"][0]
    assert add["hp"] == 6
    assert add["max_hp"] == 6
    assert add["pending_spawn"] is False
    assert add["id"] == "add_0"

    # Primary snapshotted, active swapped to the add.
    assert state["active"] == "add_0"
    assert state["primary"]["monster_id"] == "alaric"
    assert state["primary"]["hp"] == 40

    # run.monster_* now reflects the add.
    assert run.monster_id == "scribbled_hound"
    assert run.monster_hp == 6
    assert run.monster_max_hp == 6

    # Narrative was appended.
    assert any("Scribbled" in s or "scribbled_hound" in s for s in narrative)


def test_spawn_pending_adds_assigns_unique_add_ids():
    d = _fake_dungeon()
    run = _fake_run()
    state = {
        "active": "primary", "primary": None,
        "adds": [
            {"def_id": "scribbled_hound", "pending_spawn": True},
            {"def_id": "scribbled_hound", "pending_spawn": True},
        ],
        "player_effects": [], "monster_effects": [],
    }
    _spawn_pending_adds(state, d, run, [])
    ids = sorted(a["id"] for a in state["adds"])
    assert ids == ["add_0", "add_1"]
    # Only the first add becomes active (swap-target model, one target at a time).
    assert state["active"] == "add_0"


def test_spawn_pending_adds_noop_when_add_def_missing():
    d = _fake_dungeon()
    run = _fake_run()
    state = {
        "active": "primary", "primary": None,
        "adds": [{"def_id": "nonexistent", "pending_spawn": True}],
        "player_effects": [], "monster_effects": [],
    }
    _spawn_pending_adds(state, d, run, [])
    # Unknown def: add is collapsed to 0 hp and not activated.
    assert state["adds"][0]["hp"] == 0
    assert state["active"] == "primary"


def test_spawn_pending_adds_idempotent_when_none_pending():
    d = _fake_dungeon()
    run = _fake_run()
    state = {
        "active": "primary", "primary": None,
        "adds": [],
        "player_effects": [], "monster_effects": [],
    }
    _spawn_pending_adds(state, d, run, [])
    assert state["active"] == "primary"
    assert state["adds"] == []


# ---------------------------------------------------------------------------
# _swap_back_to_primary — restore primary target on add death.
# ---------------------------------------------------------------------------


def test_swap_back_to_primary_restores_from_snapshot():
    run = _fake_run(monster_id="scribbled_hound", monster_hp=0, monster_max_hp=6)
    state = {
        "active": "add_0",
        "primary": {"monster_id": "alaric", "hp": 40, "max_hp": 50},
        "primary_monster_id": "alaric",
        "adds": [{"id": "add_0", "def_id": "scribbled_hound", "hp": 0, "max_hp": 6}],
        "untargetable_primary": True,
    }
    _swap_back_to_primary(state, run)

    assert run.monster_id == "alaric"
    assert run.monster_hp == 40
    assert run.monster_max_hp == 50
    assert state["active"] == "primary"
    assert "primary" not in state
    assert state["untargetable_primary"] is False


def test_swap_back_uses_primary_monster_id_fallback():
    """If primary snapshot is missing monster_id, fall back to primary_monster_id."""
    run = _fake_run()
    state = {
        "active": "add_0",
        "primary": {"hp": 20, "max_hp": 50},  # no monster_id
        "primary_monster_id": "alaric",
        "adds": [],
    }
    _swap_back_to_primary(state, run)
    assert run.monster_id == "alaric"


# ---------------------------------------------------------------------------
# End-to-end state threading: initial_combat_state → apply_variant → resolver
# ---------------------------------------------------------------------------


def test_variant_monster_hp_flows_through_effective_stats():
    """Spawning a Mountain-variant Terrain Swatch should yield the buffed HP."""
    monster = {
        "id": "terrain_swatch",
        "name": "Terrain Swatch",
        "hp": 12,
        "defense": 1,
        "attack_dice": "1d6",
        "attack_bonus": 1,
        "variants": [
            {"key": "mountain", "name_suffix": "(Mountain)", "defense_delta": 2, "attack_bonus_delta": -1},
        ],
    }
    import random
    state = fx.initial_combat_state(monster, random.Random(0))
    effective = dungeon_logic.apply_variant(monster, state.get("variant"))
    assert effective["defense"] == 3
    assert effective["attack_bonus"] == 0
    assert "(Mountain)" in effective["name"]


def test_scale_bar_dice_step_schedule_escalates_over_turns():
    """Simulate the Scale Bar boss escalating across turns 1, 3, 5, 7."""
    import random
    ability = {
        "type": "dice_step_self",
        "step_schedule": [
            {"turn": 1, "step": 0},
            {"turn": 3, "step": 1},
            {"turn": 5, "step": 2},
            {"turn": 7, "step": 3},
        ],
    }
    state = {
        "turn": 0, "phase": 0,
        "player_effects": [], "monster_effects": [],
        "adds": [], "active": "primary",
    }

    expected = [
        (1, "1d4"),   # step 0
        (3, "1d6"),   # step 1
        (5, "1d8"),   # step 2
        (7, "1d10"),  # step 3
    ]
    for turn, expected_dice in expected:
        fx.dispatch(
            fx.EncounterCtx(
                state=state, monster_def={"name": "Scale Bar"},
                monster_hp=20, monster_max_hp=20,
                turn=turn, phase=0, rng=random.Random(0),
            ),
            ability,
        )
        mods = resolver.resolve_monster_attack_mods(state=state)
        actual_dice = resolver.bump_dice("1d4", mods.attack_dice_step)
        assert actual_dice == expected_dice, (
            f"turn {turn}: expected {expected_dice}, got {actual_dice}"
        )


def test_phase_transition_fires_on_enter_narration_once():
    """Simulate HP dropping below a phase threshold; verify phase transitions correctly."""
    monster = {
        "id": "test_boss",
        "name": "Test Boss",
        "phases": [
            {"hp_below_pct": 66, "on_enter": {"type": "narrate", "text": "THRESHOLD 1"}},
            {"hp_below_pct": 33, "on_enter": {"type": "narrate", "text": "THRESHOLD 2"}},
        ],
    }
    # HP 70% → phase 0
    assert dungeon_logic.compute_phase(70, 100, monster["phases"]) == 0
    # HP 60% → phase 1 (below 66)
    assert dungeon_logic.compute_phase(60, 100, monster["phases"]) == 1
    # HP 25% → phase 2
    assert dungeon_logic.compute_phase(25, 100, monster["phases"]) == 2
