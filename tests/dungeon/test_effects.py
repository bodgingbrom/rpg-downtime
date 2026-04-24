"""Unit tests for dungeon/effects.py — effect handler registry.

Scope: verify each handler mutates the combat state the way the design
contract says it does, and that durations tick correctly.
"""
from __future__ import annotations

import random

import pytest

from dungeon import effects as fx


def _blank_state():
    return {
        "turn": 0,
        "phase": 0,
        "player_effects": [],
        "monster_effects": [],
        "adds": [],
        "active": "primary",
        "fired_hp_triggers": [],
    }


def _ctx(state=None, monster_def=None, turn=1, phase=0, seed=0):
    state = state if state is not None else _blank_state()
    monster_def = monster_def if monster_def is not None else {"name": "Test", "id": "test"}
    return fx.EncounterCtx(
        state=state,
        monster_def=monster_def,
        monster_hp=10,
        monster_max_hp=10,
        turn=turn,
        phase=phase,
        rng=random.Random(seed),
    )


def test_initial_combat_state_picks_variant_and_description():
    monster = {
        "id": "m1",
        "variants": [{"key": "a"}, {"key": "b"}],
        "description_pool": ["desc1", "desc2"],
    }
    state = fx.initial_combat_state(monster, random.Random(0))
    assert state["variant"]["key"] in {"a", "b"}
    assert state["description"] in {"desc1", "desc2"}
    assert state["turn"] == 0
    assert state["player_effects"] == []


def test_initial_combat_state_empty_for_plain_monster():
    monster = {"id": "m1"}
    state = fx.initial_combat_state(monster, random.Random(0))
    assert "variant" not in state
    assert "description" not in state
    assert state["adds"] == []


def test_should_trigger_on_turn_every_2():
    ability = {"type": "random_effect_pool", "trigger": "on_turn", "every": 2}
    state = _blank_state()
    assert fx.should_trigger(ability, turn=1, monster_hp=10, monster_max_hp=10, state=state) is False
    assert fx.should_trigger(ability, turn=2, monster_hp=10, monster_max_hp=10, state=state) is True
    assert fx.should_trigger(ability, turn=3, monster_hp=10, monster_max_hp=10, state=state) is False
    assert fx.should_trigger(ability, turn=4, monster_hp=10, monster_max_hp=10, state=state) is True


def test_should_trigger_on_spawn_only_turn_1():
    ability = {"type": "narrate", "trigger": "on_spawn"}
    state = _blank_state()
    assert fx.should_trigger(ability, turn=1, monster_hp=10, monster_max_hp=10, state=state) is True
    assert fx.should_trigger(ability, turn=2, monster_hp=10, monster_max_hp=10, state=state) is False


def test_should_trigger_on_hp_below_fires_once():
    ability = {"type": "redraw_strike", "trigger": "on_hp_below_pct", "pct": 50}
    state = _blank_state()
    # Above threshold: no trigger.
    assert fx.should_trigger(ability, turn=3, monster_hp=8, monster_max_hp=10, state=state) is False
    # Below threshold first time: trigger.
    assert fx.should_trigger(ability, turn=3, monster_hp=4, monster_max_hp=10, state=state) is True
    fx.mark_hp_trigger_fired(ability, state)
    # Subsequent turns below threshold: no trigger.
    assert fx.should_trigger(ability, turn=4, monster_hp=4, monster_max_hp=10, state=state) is False


def test_dice_step_self_writes_active_step_based_on_turn():
    schedule = [
        {"turn": 1, "step": 0},
        {"turn": 3, "step": 1},
        {"turn": 5, "step": 2},
        {"turn": 7, "step": 3},
    ]
    state = _blank_state()

    # Turn 1 → step 0 → no effect.
    fx.dispatch(_ctx(state=state, turn=1), {"type": "dice_step_self", "step_schedule": schedule})
    assert not any(e["type"] == "dice_step_self_active" for e in state["monster_effects"])

    # Turn 3 → step 1.
    fx.dispatch(_ctx(state=state, turn=3), {"type": "dice_step_self", "step_schedule": schedule})
    active = [e for e in state["monster_effects"] if e["type"] == "dice_step_self_active"]
    assert len(active) == 1
    assert active[0]["step"] == 1

    # Turn 7 → step 3 replaces prior entry (no stacking from same source).
    fx.dispatch(_ctx(state=state, turn=7), {"type": "dice_step_self", "step_schedule": schedule})
    active = [e for e in state["monster_effects"] if e["type"] == "dice_step_self_active"]
    assert len(active) == 1
    assert active[0]["step"] == 3


def test_random_effect_pool_picks_one():
    state = _blank_state()
    pool = [
        {"type": "player_next_attack_invert"},
        {"type": "player_next_attack_advantage"},
    ]
    ctx = _ctx(state=state, seed=0)
    fx.dispatch(ctx, {"type": "random_effect_pool", "pool": pool})
    # Exactly one of the two flags should be present.
    types = [e["type"] for e in state["player_effects"]]
    assert len(types) == 1
    assert types[0] in {"invert_next_attack", "advantage_next_attack"}


def test_player_next_attack_invert_sets_flag():
    state = _blank_state()
    fx.dispatch(_ctx(state=state), {"type": "player_next_attack_invert"})
    assert state["player_effects"][0]["type"] == "invert_next_attack"
    assert state["player_effects"][0]["remaining"] == 2


def test_bleed_writes_entry_and_narrates():
    state = _blank_state()
    ctx = _ctx(state=state)
    fx.dispatch(ctx, {"type": "bleed", "damage": 2, "turns": 3})
    bleed = [e for e in state["player_effects"] if e["type"] == "bleed"][0]
    assert bleed["damage"] == 2
    # +1 because we decrement end-of-turn (so "turns: 3" means 3 applications).
    assert bleed["remaining"] == 4
    assert any("Bleed" in s or "bleed" in s for s in ctx.narrative)


def test_summon_add_appends_pending():
    state = _blank_state()
    fx.dispatch(_ctx(state=state), {
        "type": "summon_add", "add_id": "wolf", "max_active": 1, "untargetable_self": True,
    })
    assert len(state["adds"]) == 1
    assert state["adds"][0]["def_id"] == "wolf"
    assert state["adds"][0]["pending_spawn"] is True
    assert state["untargetable_primary"] is True


def test_summon_add_respects_max_active():
    state = _blank_state()
    state["adds"] = [{"def_id": "wolf", "hp": 5, "max_hp": 5, "id": "add_0"}]
    fx.dispatch(_ctx(state=state), {
        "type": "summon_add", "add_id": "wolf", "max_active": 1,
    })
    # No new pending spawn — already at max.
    assert all(not a.get("pending_spawn") for a in state["adds"])
    assert len(state["adds"]) == 1


def test_defense_bonus_self_has_duration():
    state = _blank_state()
    fx.dispatch(_ctx(state=state), {"type": "defense_bonus_self", "amount": 3, "turns": 2})
    eff = state["monster_effects"][0]
    assert eff["amount"] == 3
    assert eff["remaining"] == 2


def test_existential_strike_arms_special_attack():
    state = _blank_state()
    fx.dispatch(_ctx(state=state), {"type": "existential_strike", "damage_dice": "2d10"})
    assert state["_pending_special_attack"]["kind"] == "existential_strike"
    assert state["_pending_special_attack"]["damage_dice"] == "2d10"


def test_redraw_strike_is_idempotent_when_pending():
    state = _blank_state()
    fx.dispatch(_ctx(state=state), {"type": "redraw_strike", "damage_dice": "2d6", "defense_ignore": 2})
    first = dict(state["_pending_special_attack"])
    fx.dispatch(_ctx(state=state), {"type": "redraw_strike", "damage_dice": "9d9", "defense_ignore": 99})
    # Second dispatch does NOT overwrite if one is already pending.
    assert state["_pending_special_attack"] == first


def test_tick_effects_decrements_and_drops_expired():
    state = {
        "player_effects": [
            {"type": "bleed", "damage": 1, "remaining": 2},
            {"type": "hit_chance_reduction", "amount": 0.3, "remaining": 1},
        ],
        "monster_effects": [
            {"type": "dice_step_self_active", "step": 1, "remaining": -1},
            {"type": "defense_bonus_self", "amount": 3, "remaining": 1},
        ],
    }
    fx.tick_effects(state)
    # Bleed went 2 → 1, hit_chance went 1 → 0 and dropped.
    assert len(state["player_effects"]) == 1
    assert state["player_effects"][0]["type"] == "bleed"
    assert state["player_effects"][0]["remaining"] == 1
    # Dice step is permanent; defense bonus dropped.
    assert len(state["monster_effects"]) == 1
    assert state["monster_effects"][0]["type"] == "dice_step_self_active"


def test_consume_player_flag_removes_single_entry():
    state = {"player_effects": [
        {"type": "advantage_next_attack", "remaining": 2},
        {"type": "bleed", "damage": 1, "remaining": 2},
    ]}
    assert fx.consume_player_flag(state, "advantage_next_attack") is True
    assert [e["type"] for e in state["player_effects"]] == ["bleed"]
    assert fx.consume_player_flag(state, "advantage_next_attack") is False


def test_validate_abilities_accepts_known_types():
    ok = [
        {"type": "dice_step_self", "step_schedule": []},
        {"type": "random_effect_pool", "every": 2, "pool": [
            {"type": "player_next_attack_invert"},
        ]},
    ]
    assert fx.validate_abilities(ok) == []


def test_validate_abilities_rejects_unknown_type():
    bad = [{"type": "vaporize_universe"}]
    errs = fx.validate_abilities(bad)
    assert errs and "vaporize_universe" in errs[0]


def test_validate_abilities_rejects_unknown_trigger():
    bad = [{"type": "narrate", "trigger": "on_full_moon"}]
    errs = fx.validate_abilities(bad)
    assert errs and "on_full_moon" in errs[0]


def test_validate_monster_rejects_bad_description_pool():
    bad = {"description_pool": "this is a string"}
    errs = fx.validate_monster(bad)
    assert errs


def test_validate_monster_rejects_variant_without_key():
    bad = {"variants": [{"name_suffix": "(?)"}]}
    errs = fx.validate_monster(bad)
    assert errs


def test_validate_monster_rejects_phase_without_threshold():
    bad = {"phases": [{"attack_dice": "1d8"}]}
    errs = fx.validate_monster(bad)
    assert errs


def test_validate_monster_recurses_into_phase_abilities_add():
    bad = {"phases": [
        {"hp_below_pct": 50, "abilities_add": [{"type": "nonexistent"}]},
    ]}
    errs = fx.validate_monster(bad)
    assert errs and "nonexistent" in errs[0]
