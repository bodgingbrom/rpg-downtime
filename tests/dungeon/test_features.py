"""Unit tests for the v2 feature system — visibility tiers, perception,
investigate, content rewards, secret reveal, schema validation.
"""
from __future__ import annotations

import random

import pytest

from dungeon import explore


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _floor_with_features():
    """A floor with one room exercising every visibility tier."""
    return {
        "floor": 1,
        "layout": {"rooms_per_run": [3, 3]},
        "anchors": [
            {"position": "entrance", "room_id": "alcove"},
            {"position": "boss", "room_id": "endroom"},
        ],
        "wandering_threshold": 99,   # never trigger wandering in these tests
        "wandering_pool": [],
        "monsters": [],
        "boss": {
            "id": "test_boss", "hp": 10, "defense": 0,
            "attack_dice": "1d4", "attack_bonus": 0, "xp": 5, "gold": [1, 2],
            "ai": {"attack": 100},
        },
        "room_pool": [
            {
                "id": "alcove",
                "weight": 100,
                "description_pool": ["The starting alcove."],
                "features": [
                    {
                        "id": "obvious_chest",
                        "name": "obvious chest",
                        "visibility": "visible",
                        "investigate_label": "Open the chest",
                        "noise": 1,
                        "content": [
                            {"type": "gold", "amount": [10, 10], "chance": 1.0},
                        ],
                        "flavor_success": "_You open it._",
                    },
                    {
                        "id": "loose_stone",
                        "name": "loose stone",
                        "visibility": "concealed",
                        "perception_dc": 12,
                        "noise": 1,
                        "content": [
                            {"type": "gold", "amount": [5, 5], "chance": 1.0},
                        ],
                    },
                    {
                        "id": "hidden_panel",
                        "name": "hidden panel",
                        "visibility": "secret",
                        "revealed_by": "obvious_chest",
                        "noise": 1,
                        "content": [
                            {"type": "item", "item_id": "health_potion", "chance": 1.0},
                        ],
                    },
                ],
            },
            {"id": "filler", "weight": 100, "description_pool": ["A filler room."]},
            {"id": "endroom", "weight": 100, "description_pool": ["The end."]},
        ],
    }


def _state_in_alcove():
    floor = _floor_with_features()
    state = explore.initial_floor_state(floor, random.Random(0))
    # Make sure the entry room is the alcove.
    assert state["graph"]["rooms"][state["current"]]["room_def_id"] == "alcove"
    return floor, state


# ---------------------------------------------------------------------------
# Look Around — perception.
# ---------------------------------------------------------------------------


def test_look_around_reveals_concealed_when_perception_high_enough():
    floor, state = _state_in_alcove()
    # DC 12, perception modifier +20 means we always pass.
    result = explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=20,
    )
    rs = state["room_states"][state["current"]]
    assert "loose_stone" in rs["revealed_concealed"]
    assert any("loose stone" in line.lower() for line in result.narrative)


def test_look_around_misses_concealed_when_perception_too_low():
    floor, state = _state_in_alcove()
    # DC 12, perception modifier -20 means we never pass.
    result = explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=-20,
    )
    rs = state["room_states"][state["current"]]
    assert "loose_stone" not in rs["revealed_concealed"]


def test_look_around_only_surfaces_concealed_not_secret():
    """Look Around never reveals secret features (those need Investigate)."""
    floor, state = _state_in_alcove()
    explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=20,
    )
    rs = state["room_states"][state["current"]]
    assert "hidden_panel" not in rs.get("revealed_concealed", [])
    assert "hidden_panel" not in rs.get("revealed_secrets", [])


def test_look_around_marks_room_so_repeats_dont_reroll():
    floor, state = _state_in_alcove()
    explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=-20,
    )
    # Re-look — would pass on the second try with high modifier, but we
    # already burned the look, so revealed_concealed remains empty.
    explore.take_look_around(
        state, floor, random.Random(99), perception_modifier=20,
    )
    rs = state["room_states"][state["current"]]
    assert rs["revealed_concealed"] == []


# ---------------------------------------------------------------------------
# Investigate — content + rewards.
# ---------------------------------------------------------------------------


def test_investigate_visible_feature_rolls_content_and_returns_rewards():
    floor, state = _state_in_alcove()
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="obvious_chest",
    )
    assert result.next_step == "explore"
    # Gold reward emitted via rewards list.
    gold_rewards = [r for r in result.rewards if r["type"] == "gold"]
    assert len(gold_rewards) == 1
    assert gold_rewards[0]["amount"] == 10


def test_investigate_marks_searched_and_blocks_repeat():
    floor, state = _state_in_alcove()
    explore.take_investigate(
        state, floor, random.Random(0), feature_id="obvious_chest",
    )
    rs = state["room_states"][state["current"]]
    assert "obvious_chest" in rs["searched"]
    # Second click — short-circuits with "already searched", no rewards.
    result2 = explore.take_investigate(
        state, floor, random.Random(0), feature_id="obvious_chest",
    )
    assert result2.rewards == []
    assert any("already" in line.lower() for line in result2.narrative)


def test_investigate_unknown_feature_returns_explore_with_msg():
    floor, state = _state_in_alcove()
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="nope",
    )
    assert result.next_step == "explore"
    assert any("can't find" in line.lower() for line in result.narrative)


def test_investigate_visible_feature_unlocks_secret_revealed_by_it():
    floor, state = _state_in_alcove()
    explore.take_investigate(
        state, floor, random.Random(0), feature_id="obvious_chest",
    )
    rs = state["room_states"][state["current"]]
    assert "hidden_panel" in rs["revealed_secrets"]


def test_investigate_secret_then_works_after_reveal():
    """After revealing the secret, investigating it returns the item reward."""
    floor, state = _state_in_alcove()
    explore.take_investigate(state, floor, random.Random(0), feature_id="obvious_chest")
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="hidden_panel",
    )
    item_rewards = [r for r in result.rewards if r["type"] == "item"]
    assert len(item_rewards) == 1
    assert item_rewards[0]["item_id"] == "health_potion"


def test_investigate_concealed_feature_works_after_look_reveals_it():
    floor, state = _state_in_alcove()
    explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=20,
    )
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="loose_stone",
    )
    gold_rewards = [r for r in result.rewards if r["type"] == "gold"]
    assert len(gold_rewards) == 1


def test_investigate_combat_interrupt_does_not_mark_searched():
    """If a wandering encounter triggers mid-investigate, the action is
    cancelled — the feature remains unsearched so the player can retry.
    """
    floor = _floor_with_features()
    floor["wandering_threshold"] = 0  # any tick triggers
    floor["wandering_pool"] = [{"monster_id": "wanderer", "weight": 100}]
    state = explore.initial_floor_state(floor, random.Random(0))
    result = explore.take_investigate(
        state, floor, random.Random(0), feature_id="obvious_chest",
    )
    assert result.next_step == "combat"
    rs = state["room_states"][state["current"]]
    # Critical: feature NOT marked as searched, so player can re-attempt
    # after combat resolves.
    assert "obvious_chest" not in rs.get("searched", [])
    # But the encounter was queued.
    assert state["pending_combat"]["monster_id"] == "wanderer"


# ---------------------------------------------------------------------------
# visible_feature_buttons — what buttons get rendered.
# ---------------------------------------------------------------------------


def test_visible_buttons_only_includes_surfaced_features():
    floor, state = _state_in_alcove()
    btns = explore.visible_feature_buttons(state, floor)
    feature_ids = [b["feature_id"] for b in btns]
    # Only the visible feature appears initially.
    assert feature_ids == ["obvious_chest"]


def test_visible_buttons_after_look_includes_concealed():
    floor, state = _state_in_alcove()
    explore.take_look_around(
        state, floor, random.Random(0), perception_modifier=20,
    )
    btns = explore.visible_feature_buttons(state, floor)
    ids = sorted(b["feature_id"] for b in btns)
    assert ids == ["loose_stone", "obvious_chest"]


def test_visible_buttons_after_secret_reveal_includes_secret():
    floor, state = _state_in_alcove()
    explore.take_investigate(state, floor, random.Random(0), feature_id="obvious_chest")
    btns = explore.visible_feature_buttons(state, floor)
    ids = sorted(b["feature_id"] for b in btns)
    # obvious_chest now searched (excluded). hidden_panel revealed.
    assert ids == ["hidden_panel"]


def test_visible_buttons_excludes_searched_features():
    floor, state = _state_in_alcove()
    explore.take_investigate(state, floor, random.Random(0), feature_id="obvious_chest")
    btns = explore.visible_feature_buttons(state, floor)
    ids = [b["feature_id"] for b in btns]
    assert "obvious_chest" not in ids


def test_visible_buttons_passive_features_never_appear():
    """A passive feature — described in narration — has no button."""
    floor = _floor_with_features()
    floor["room_pool"][0]["features"].append({
        "id": "ambient_corpse",
        "name": "corpse against the wall",
        "visibility": "passive",
    })
    state = explore.initial_floor_state(floor, random.Random(0))
    btns = explore.visible_feature_buttons(state, floor)
    ids = [b["feature_id"] for b in btns]
    assert "ambient_corpse" not in ids


# ---------------------------------------------------------------------------
# Schema validation.
# ---------------------------------------------------------------------------


def test_validate_room_accepts_clean_room():
    floor = _floor_with_features()
    errs = explore.validate_room(floor["room_pool"][0])
    assert errs == []


def test_validate_room_rejects_unknown_visibility():
    bad = {"features": [{"id": "x", "visibility": "wat"}]}
    errs = explore.validate_room(bad)
    assert errs and any("visibility" in e for e in errs)


def test_validate_room_rejects_concealed_without_dc():
    bad = {"features": [{"id": "x", "visibility": "concealed"}]}
    errs = explore.validate_room(bad)
    assert errs and any("perception_dc" in e for e in errs)


def test_validate_room_rejects_secret_without_revealed_by():
    bad = {"features": [{"id": "x", "visibility": "secret"}]}
    errs = explore.validate_room(bad)
    assert errs and any("revealed_by" in e for e in errs)


def test_validate_room_rejects_secret_revealed_by_unknown_feature():
    bad = {"features": [
        {"id": "a", "visibility": "visible"},
        {"id": "b", "visibility": "secret", "revealed_by": "ghost"},
    ]}
    errs = explore.validate_room(bad)
    assert errs and any("ghost" in e for e in errs)


def test_validate_room_accepts_secret_pointing_to_real_feature():
    ok = {"features": [
        {"id": "a", "visibility": "visible"},
        {"id": "b", "visibility": "secret", "revealed_by": "a"},
    ]}
    assert explore.validate_room(ok) == []


def test_validate_room_rejects_unknown_content_type():
    bad = {"features": [
        {"id": "x", "visibility": "visible", "content": [{"type": "vaporize"}]},
    ]}
    errs = explore.validate_room(bad)
    assert errs and any("vaporize" in e for e in errs)


def test_validate_room_rejects_duplicate_feature_ids():
    bad = {"features": [
        {"id": "x", "visibility": "visible"},
        {"id": "x", "visibility": "visible"},
    ]}
    errs = explore.validate_room(bad)
    assert errs and any("duplicated" in e for e in errs)


def test_validate_room_recurses_into_variant_features_add():
    bad = {
        "variants": [
            {"key": "v", "features_add": [{"id": "x", "visibility": "wat"}]},
        ],
    }
    errs = explore.validate_room(bad)
    assert errs and any("visibility" in e for e in errs)


# ---------------------------------------------------------------------------
# Smoke test: dev_v2_skeleton loads and validates.
# ---------------------------------------------------------------------------


def test_dev_v2_skeleton_loads_with_features():
    from dungeon import logic
    logic._dungeons_cache = None
    dungeons = logic.load_dungeons()
    assert "dev_v2_skeleton" in dungeons
    floor = dungeons["dev_v2_skeleton"]["floors"][0]
    pool = floor["room_pool"]
    alcove = next(r for r in pool if r["id"] == "dev_alcove")
    feature_ids = {f["id"] for f in alcove.get("features", [])}
    assert "scratched_wall" in feature_ids
    assert "loose_stone" in feature_ids
    endroom = next(r for r in pool if r["id"] == "dev_endroom")
    feature_ids_end = {f["id"] for f in endroom.get("features", [])}
    assert "rusted_chest" in feature_ids_end
    assert "false_bottom" in feature_ids_end
