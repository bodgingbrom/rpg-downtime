"""Unit tests for dungeon/explore.py — v2 floor graph, tick, action handlers."""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from dungeon import explore


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _floor():
    """A small v2 floor — entrance + 1 corridor + boss, with one wandering option."""
    return {
        "floor": 1,
        "layout": {"rooms_per_run": [3, 3]},
        "anchors": [
            {"position": "entrance", "room_id": "alcove"},
            {"position": "boss", "room_id": "endroom"},
        ],
        "wandering_threshold": 5,
        "wandering_pool": [{"monster_id": "test_rat", "weight": 100}],
        "monsters": [
            {
                "id": "test_rat", "name": "Test Rat", "hp": 6, "defense": 0,
                "attack_dice": "1d4", "attack_bonus": 0, "xp": 5,
                "gold": [1, 3], "ai": {"attack": 100},
            },
        ],
        "boss": {
            "id": "test_boss", "name": "Test Boss", "hp": 20, "defense": 1,
            "attack_dice": "1d6", "attack_bonus": 1, "xp": 30,
            "gold": [10, 20], "ai": {"attack": 70, "heavy": 30},
        },
        "room_pool": [
            {
                "id": "alcove", "weight": 100,
                "description_pool": ["A bare alcove."],
            },
            {
                "id": "corridor", "weight": 100,
                "description_pool": ["A corridor."],
                "ambush": {"armed": True, "monster_id": "test_rat"},
            },
            {
                "id": "endroom", "weight": 100,
                "description_pool": ["The end."],
            },
        ],
    }


def _v1_dungeon():
    """Shape resembling Goblin Warrens — has monsters/boss but no room_pool."""
    return {
        "id": "v1_thing",
        "floors": [{"floor": 1, "monsters": [], "boss": {}}],
    }


def _v2_dungeon():
    return {
        "id": "v2_thing",
        "floors": [_floor()],
    }


# ---------------------------------------------------------------------------
# Detection.
# ---------------------------------------------------------------------------


def test_is_v2_dungeon_true_when_room_pool_present():
    assert explore.is_v2_dungeon(_v2_dungeon()) is True


def test_is_v2_dungeon_false_when_no_room_pool():
    assert explore.is_v2_dungeon(_v1_dungeon()) is False


def test_is_v2_dungeon_handles_missing_floors():
    assert explore.is_v2_dungeon({}) is False


# ---------------------------------------------------------------------------
# Graph generation.
# ---------------------------------------------------------------------------


def test_generate_floor_graph_respects_anchors_and_room_count():
    rng = random.Random(42)
    graph = explore.generate_floor_graph(_floor(), rng)
    rooms = graph["rooms"]
    assert len(rooms) == 3
    # Entrance is always r0; boss is at the deepest node.
    assert graph["entrance"] == "r0"
    assert rooms["r0"]["room_def_id"] == "alcove"
    assert rooms[graph["boss"]]["room_def_id"] == "endroom"


def test_generate_floor_graph_links_sequentially():
    """Linear chain — each room's exits point to neighbors only."""
    rng = random.Random(0)
    graph = explore.generate_floor_graph(_floor(), rng)
    rooms = graph["rooms"]
    assert rooms["r0"]["exits"] == ["r1"]
    assert sorted(rooms["r1"]["exits"]) == ["r0", "r2"]
    assert rooms["r2"]["exits"] == ["r1"]


def test_generate_floor_graph_seeded_is_deterministic():
    g1 = explore.generate_floor_graph(_floor(), random.Random(123))
    g2 = explore.generate_floor_graph(_floor(), random.Random(123))
    assert g1 == g2


# ---------------------------------------------------------------------------
# Floor state init.
# ---------------------------------------------------------------------------


def test_initial_floor_state_marks_entrance_visited_only():
    state = explore.initial_floor_state(_floor(), random.Random(0))
    assert state["current"] == "r0"
    assert state["discovered"] == ["r0"]
    assert state["room_states"]["r0"]["visited"] is True
    # Other rooms not visited yet.
    assert state["room_states"]["r1"]["visited"] is False
    assert state["room_states"]["r2"]["visited"] is False


def test_initial_floor_state_picks_descriptions_and_arms_ambush():
    state = explore.initial_floor_state(_floor(), random.Random(0))
    # The corridor has armed ambush in our fixture.
    corridor_node = next(
        n for n, r in state["graph"]["rooms"].items()
        if r["room_def_id"] == "corridor"
    )
    assert state["room_states"][corridor_node]["ambush_armed"] is True
    # Description was picked from the pool.
    assert state["room_states"]["r0"]["description"] == "A bare alcove."


def test_initial_floor_state_uses_default_threshold_when_unset():
    floor = _floor()
    floor.pop("wandering_threshold")
    state = explore.initial_floor_state(floor, random.Random(0))
    assert state["wandering_threshold"] == explore.WANDERING_THRESHOLD_DEFAULT


# ---------------------------------------------------------------------------
# Tick / danger.
# ---------------------------------------------------------------------------


def test_advance_tick_below_threshold_no_encounter():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    narrative, monster = explore.advance_tick(state, floor, random.Random(0), cost=1)
    assert monster is None
    assert state["tension"] == 1


def test_advance_tick_emits_tell_in_mid_range():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    state["tension"] = 2
    narrative, _ = explore.advance_tick(state, floor, random.Random(0), cost=1)
    # tension is 3 after — should emit a tell.
    assert any("stir" in line.lower() for line in narrative)


def test_advance_tick_at_threshold_triggers_wandering():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    state["tension"] = 4  # one tick away from threshold (5)
    _, monster = explore.advance_tick(state, floor, random.Random(0), cost=1)
    assert monster == "test_rat"
    # Tension resets after a wandering trigger.
    assert state["tension"] == 0


def test_advance_tick_no_pool_resets_tension_silently():
    floor = _floor()
    floor["wandering_pool"] = []
    state = explore.initial_floor_state(floor, random.Random(0))
    state["tension"] = 100
    narrative, monster = explore.advance_tick(state, floor, random.Random(0), cost=1)
    assert monster is None
    assert state["tension"] == 0


# ---------------------------------------------------------------------------
# Action handlers.
# ---------------------------------------------------------------------------


def test_take_look_around_first_time_marks_room():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    result = explore.take_look_around(state, floor, random.Random(0))
    assert result.next_step == "explore"
    assert state["room_states"]["r0"]["looked_around"] is True


def test_take_look_around_repeat_narrates_already_looked():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.take_look_around(state, floor, random.Random(0))
    result = explore.take_look_around(state, floor, random.Random(0))
    assert any("already" in line.lower() for line in result.narrative)
    # Still costs a tick.
    assert state["tension"] >= 2


def test_take_listen_repeats_safely():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    for _ in range(3):
        result = explore.take_listen(state, floor, random.Random(0))
        if result.next_step == "combat":
            break
    # Tension should have advanced.
    assert state["tension"] > 0 or result.next_step == "combat"


def test_take_move_on_to_valid_exit_transitions():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    result = explore.take_move_on(state, floor, random.Random(0), target_node="r1")
    # The corridor (r1) has armed ambush — move triggers combat on first visit.
    assert result.next_step == "combat"
    assert state["pending_combat"]["kind"] == "ambush"
    assert state["current"] == "r1"


def test_take_move_on_invalid_exit_blocks():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    result = explore.take_move_on(state, floor, random.Random(0), target_node="r99")
    assert result.next_step == "explore"
    assert any("can't" in line.lower() for line in result.narrative)


def test_take_move_on_to_boss_starts_combat():
    floor = _floor()
    # Pre-clear corridor ambush so we can travel through.
    state = explore.initial_floor_state(floor, random.Random(0))
    state["current"] = "r1"
    state["room_states"]["r1"]["visited"] = True
    state["room_states"]["r1"]["ambush_resolved"] = True
    result = explore.take_move_on(state, floor, random.Random(0), target_node="r2")
    assert result.next_step == "combat"
    assert state["pending_combat"]["kind"] == "boss"
    assert state["pending_combat"]["monster_id"] == "test_boss"


# ---------------------------------------------------------------------------
# Render helpers.
# ---------------------------------------------------------------------------


def test_render_room_intro_returns_description_and_exits():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    desc, ambient, exits = explore.render_room_intro(state, floor)
    assert "alcove" in desc.lower()
    assert len(exits) == 1
    assert exits[0]["node_id"] == "r1"


def test_available_actions_includes_look_listen_initially():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    actions = explore.available_exploration_actions(state, floor)
    assert "look_around" in actions
    assert "listen" in actions


def test_available_actions_drops_look_after_use():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    explore.take_look_around(state, floor, random.Random(0))
    actions = explore.available_exploration_actions(state, floor)
    assert "look_around" not in actions
    assert "listen" in actions


# ---------------------------------------------------------------------------
# Persistence helpers.
# ---------------------------------------------------------------------------


def test_load_floor_state_handles_garbage():
    assert explore.load_floor_state(None) == {}
    assert explore.load_floor_state("") == {}
    assert explore.load_floor_state("not json") == {}
    assert explore.load_floor_state("{}") == {}


def test_find_floor_monster_finds_boss():
    floor = _floor()
    found = explore.find_floor_monster(floor, "test_boss")
    assert found is not None
    assert found["name"] == "Test Boss"


def test_find_floor_monster_finds_regular():
    floor = _floor()
    found = explore.find_floor_monster(floor, "test_rat")
    assert found is not None
    assert found["name"] == "Test Rat"


def test_find_floor_monster_missing_returns_none():
    floor = _floor()
    assert explore.find_floor_monster(floor, "nonexistent") is None
    assert explore.find_floor_monster(floor, None) is None
    assert explore.find_floor_monster(floor, "") is None


def test_find_floor_monster_handles_empty_floor():
    assert explore.find_floor_monster({}, "anything") is None


def test_dump_load_round_trip():
    floor = _floor()
    state = explore.initial_floor_state(floor, random.Random(0))
    raw = explore.dump_floor_state(state)
    restored = explore.load_floor_state(raw)
    assert restored["graph"] == state["graph"]
    assert restored["current"] == state["current"]
    assert restored["wandering_threshold"] == state["wandering_threshold"]
