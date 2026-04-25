"""Smoke tests for the_cartographers_folly_v2 — content authoring is
verified end-to-end against the v2 engine.

Doesn't rerun any logic tests (those live in their own modules); this
is purely structural — does the dungeon YAML load, does it conform to
the v2 schema, does the engine successfully generate a playable floor
state on top of it.
"""
from __future__ import annotations

import random

import pytest

from dungeon import explore, logic


DUNGEON_KEY = "the_cartographers_folly_v2"


@pytest.fixture(autouse=True)
def _reset_dungeon_cache():
    logic._dungeons_cache = None
    yield
    logic._dungeons_cache = None


def _dungeon():
    d = logic.load_dungeons().get(DUNGEON_KEY)
    assert d is not None, f"{DUNGEON_KEY} did not load — check schema validation"
    return d


# ---------------------------------------------------------------------------
# Top-level metadata.
# ---------------------------------------------------------------------------


def test_loads_and_is_v2():
    d = _dungeon()
    assert explore.is_v2_dungeon(d) is True


def test_admin_role_gate():
    d = _dungeon()
    assert d.get("min_role") == "Race Admin"


def test_background_block_complete():
    bg = _dungeon().get("background") or {}
    assert bg.get("tone")
    assert bg.get("pitch")
    assert bg.get("lore")
    hooks = bg.get("dm_hooks") or []
    assert isinstance(hooks, list) and len(hooks) >= 3
    assert bg.get("style_notes")


def test_legendary_reward_authored():
    legendary = _dungeon().get("legendary_reward") or {}
    assert legendary.get("item_id") == "vorpal_dagger"
    assert legendary.get("name") == "Alaric's Quill"
    assert legendary.get("flavor")


def test_lore_fragments_count_and_uniqueness():
    """12 fragments per the design doc range (12-20)."""
    fragments = _dungeon().get("lore_fragments") or []
    assert len(fragments) == 12
    ids = [f["id"] for f in fragments]
    assert sorted(ids) == list(range(1, 13))
    # Each fragment has substantive text.
    for f in fragments:
        assert isinstance(f.get("text"), str) and len(f["text"]) > 50


# ---------------------------------------------------------------------------
# Floor structure.
# ---------------------------------------------------------------------------


def test_three_floors():
    floors = _dungeon().get("floors") or []
    assert [f.get("floor") for f in floors] == [1, 2, 3]


def test_each_floor_has_anchors_layout_and_pool():
    for f in _dungeon()["floors"]:
        assert "layout" in f
        anchors = {a["position"]: a["room_id"] for a in (f.get("anchors") or [])}
        assert "entrance" in anchors and "boss" in anchors
        pool = f.get("room_pool") or []
        assert len(pool) >= 4
        # Both anchors must be in the pool so the generator can place them.
        ids = {r["id"] for r in pool}
        assert anchors["entrance"] in ids
        assert anchors["boss"] in ids


def test_each_floor_has_wandering_pool():
    for f in _dungeon()["floors"]:
        wp = f.get("wandering_pool") or []
        assert len(wp) >= 1
        # Each entry references a real monster on the floor.
        monster_ids = {m["id"] for m in (f.get("monsters") or [])}
        # Bosses don't go in wandering pools; restrict to floor monsters.
        for entry in wp:
            mid = entry["monster_id"] if isinstance(entry, dict) else entry
            assert mid in monster_ids, (
                f"Floor {f['floor']} wandering_pool references unknown monster '{mid}'"
            )


# ---------------------------------------------------------------------------
# Boss authoring (PR 1 / PR 4 abilities reused).
# ---------------------------------------------------------------------------


def test_floor1_boss_is_scale_bar_with_dice_escalation():
    f1 = _dungeon()["floors"][0]
    boss = f1["boss"]
    assert boss["id"] == "the_scale_bar"
    abilities = boss.get("abilities") or []
    dice_step = next((a for a in abilities if a.get("type") == "dice_step_self"), None)
    assert dice_step is not None
    schedule = {e["turn"]: e["step"] for e in dice_step["step_schedule"]}
    assert schedule == {1: 0, 3: 1, 5: 2, 7: 3}


def test_floor2_boss_is_key_legend_with_phases_and_random_pool():
    f2 = _dungeon()["floors"][1]
    boss = f2["boss"]
    assert boss["id"] == "the_key_legend"
    phases = [p["hp_below_pct"] for p in (boss.get("phases") or [])]
    assert sorted(phases) == [33, 66]
    abilities = boss.get("abilities") or []
    pool = next((a for a in abilities if a.get("type") == "random_effect_pool"), None)
    assert pool is not None
    assert pool.get("every") == 2
    pool_types = {p["type"] for p in pool["pool"]}
    assert "player_next_attack_invert" in pool_types
    assert "player_next_attack_advantage" in pool_types


def test_floor3_boss_alaric_summon_phase_gated_with_pool():
    f3 = _dungeon()["floors"][2]
    boss = f3["boss"]
    assert boss["id"] == "alaric_venn"
    summons = [a for a in (boss.get("abilities") or []) if a.get("type") == "summon_add"]
    assert summons, "Alaric must have a summon_add ability"
    summon = summons[0]
    assert summon.get("phase_max") == 0
    pool = summon.get("add_pool") or []
    assert "scribbled_hound" in pool and "inkwash_wraith" in pool
    # Both adds defined as floor-3 monsters so the engine can resolve them.
    monster_ids = {m["id"] for m in f3.get("monsters") or []}
    for add_id in pool:
        assert add_id in monster_ids


def test_floor3_boss_has_signature_death_line():
    boss = _dungeon()["floors"][2]["boss"]
    death = boss.get("on_death_narration") or ""
    assert "A. Venn" in death or "Cartographer Royal" in death


# ---------------------------------------------------------------------------
# Lore-fragment placement: every fragment id is referenced by some feature.
# ---------------------------------------------------------------------------


def test_every_lore_fragment_id_is_reachable_from_some_feature():
    d = _dungeon()
    declared = {f["id"] for f in (d.get("lore_fragments") or [])}
    referenced: set[int] = set()
    for floor in d["floors"]:
        for room in floor.get("room_pool") or []:
            for feat in room.get("features") or []:
                for content in feat.get("content") or []:
                    if content.get("type") == "lore_fragment":
                        referenced.add(content["fragment_id"])
    missing = declared - referenced
    assert not missing, (
        f"Authored fragments not referenced by any feature: {sorted(missing)}. "
        "These are unreachable and would block the legendary unlock."
    )


def test_lore_fragment_distribution_across_floors():
    """Some fragments on every floor — collecting requires touching all three."""
    d = _dungeon()
    by_floor: dict[int, set[int]] = {}
    for floor in d["floors"]:
        fid = floor["floor"]
        by_floor[fid] = set()
        for room in floor.get("room_pool") or []:
            for feat in room.get("features") or []:
                for content in feat.get("content") or []:
                    if content.get("type") == "lore_fragment":
                        by_floor[fid].add(content["fragment_id"])
    for fid, ids in by_floor.items():
        assert len(ids) >= 2, (
            f"Floor {fid} has too few fragments ({len(ids)}); the legendary "
            "should require traversing all three floors."
        )


# ---------------------------------------------------------------------------
# Engine integration — generate every floor and confirm pre-roll runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("floor_num", [1, 2, 3])
def test_engine_generates_playable_floor_state(floor_num: int):
    d = _dungeon()
    floor = logic.get_floor_data(d, floor_num)
    state = explore.initial_floor_state(floor, random.Random(7))

    # Entrance is r0, anchored.
    assert state["current"] == "r0"
    # Boss room is the deepest node.
    boss_node = state["graph"]["boss"]
    rooms = state["graph"]["rooms"]
    boss_room_def = rooms[boss_node]["room_def_id"]
    expected_boss_room = next(
        a["room_id"] for a in floor["anchors"] if a["position"] == "boss"
    )
    assert boss_room_def == expected_boss_room

    # Pre-rolled rewards exist for every authored feature in every room.
    pool_by_id = {r["id"]: r for r in floor["room_pool"]}
    for nid, n in rooms.items():
        room_def = pool_by_id[n["room_def_id"]]
        feats = room_def.get("features") or []
        pre = state["room_states"][nid].get("pre_rolled_rewards") or {}
        for feat in feats:
            assert feat["id"] in pre, (
                f"floor {floor_num} {nid}/{feat['id']} not pre-rolled"
            )


def test_no_free_tier_brewing_ingredients_in_loot():
    """Per existing policy — dungeons never drop free-tier ingredients
    because players can forage them via brewing."""
    FREE = {
        "Ember Salt", "Moonpetal", "Wraith Moss",
        "Iron Root", "Gloomcap", "Brimstone Dust",
    }
    offenders: list[str] = []
    d = _dungeon()
    for floor in d["floors"]:
        for src in list(floor.get("monsters", []) or []) + [floor.get("boss") or {}]:
            for drop in src.get("loot", []) or []:
                if drop.get("type") == "cross_game_ingredient":
                    if drop.get("item_id") in FREE:
                        offenders.append(f"{src.get('id')}:{drop['item_id']}")
    assert not offenders, f"free-tier ingredient drops: {offenders}"


# ---------------------------------------------------------------------------
# Schema-validator pass-through (would have already failed loading if broken).
# ---------------------------------------------------------------------------


def test_validate_dungeon_meta_passes():
    d = _dungeon()
    assert explore.validate_dungeon_meta(d) == []


def test_validate_room_passes_for_every_room():
    d = _dungeon()
    for floor in d["floors"]:
        for room in floor.get("room_pool") or []:
            errs = explore.validate_room(room, path=f"floor[{floor['floor']}].{room['id']}.")
            assert errs == [], errs
