"""Smoke tests for the_undercrypt_v2 — content authoring conforms to the
v2 schema and the engine generates playable floor states on top of it.

Mirrors test_goblin_warrens_v2 in shape; the assertions specific to the
Undercrypt are the boss IDs and the legendary item.
"""
from __future__ import annotations

import random

import pytest

from dungeon import explore, logic


DUNGEON_KEY = "the_undercrypt"


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
    assert explore.is_v2_dungeon(_dungeon()) is True


def test_no_admin_role_gate():
    # v2 cutover: dungeon is open to all players.
    assert _dungeon().get("min_role") is None


def test_background_block_complete():
    bg = _dungeon().get("background") or {}
    assert bg.get("tone")
    assert bg.get("pitch")
    assert bg.get("lore")
    hooks = bg.get("dm_hooks") or []
    assert isinstance(hooks, list) and len(hooks) >= 3
    assert bg.get("style_notes")


def test_legendary_reward_is_phylactery_shard():
    legendary = _dungeon().get("legendary_reward") or {}
    assert legendary.get("item_id") == "ring_of_precision"
    assert legendary.get("name") == "The Phylactery Shard"
    assert legendary.get("flavor")


def test_lore_fragments_count_and_uniqueness():
    fragments = _dungeon().get("lore_fragments") or []
    assert len(fragments) == 12
    ids = [f["id"] for f in fragments]
    assert sorted(ids) == list(range(1, 13))
    for f in fragments:
        assert isinstance(f.get("text"), str) and len(f["text"]) > 50


# ---------------------------------------------------------------------------
# Floor structure.
# ---------------------------------------------------------------------------


def test_three_floors():
    assert [f.get("floor") for f in _dungeon()["floors"]] == [1, 2, 3]


def test_each_floor_has_anchors_layout_and_pool():
    for f in _dungeon()["floors"]:
        assert "layout" in f
        anchors = {a["position"]: a["room_id"] for a in (f.get("anchors") or [])}
        assert "entrance" in anchors and "boss" in anchors
        pool = f.get("room_pool") or []
        assert len(pool) >= 4
        ids = {r["id"] for r in pool}
        assert anchors["entrance"] in ids
        assert anchors["boss"] in ids


def test_each_floor_has_wandering_pool():
    for f in _dungeon()["floors"]:
        wp = f.get("wandering_pool") or []
        assert len(wp) >= 1
        monster_ids = {m["id"] for m in (f.get("monsters") or [])}
        for entry in wp:
            mid = entry["monster_id"] if isinstance(entry, dict) else entry
            assert mid in monster_ids


# ---------------------------------------------------------------------------
# Bosses match the v1 roster.
# ---------------------------------------------------------------------------


def test_floor_bosses_are_warden_wraith_varenthos():
    floors = _dungeon()["floors"]
    assert floors[0]["boss"]["id"] == "crypt_warden"
    assert floors[1]["boss"]["id"] == "wraith_lord"
    assert floors[2]["boss"]["id"] == "the_lich"


# ---------------------------------------------------------------------------
# Lore-fragment placement.
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
    """Each floor should hold a meaningful share so completing the book
    requires touching all three."""
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
# Engine integration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("floor_num", [1, 2, 3])
def test_engine_generates_playable_floor_state(floor_num: int):
    d = _dungeon()
    floor = logic.get_floor_data(d, floor_num)
    state = explore.initial_floor_state(floor, random.Random(7))
    assert state["current"] == "r0"

    boss_node = state["graph"]["boss"]
    rooms = state["graph"]["rooms"]
    boss_room_def = rooms[boss_node]["room_def_id"]
    expected_boss_room = next(
        a["room_id"] for a in floor["anchors"] if a["position"] == "boss"
    )
    assert boss_room_def == expected_boss_room

    # Pre-rolled rewards exist for every authored feature.
    pool_by_id = {r["id"]: r for r in floor["room_pool"]}
    for nid, n in rooms.items():
        room_def = pool_by_id[n["room_def_id"]]
        feats = room_def.get("features") or []
        pre = state["room_states"][nid].get("pre_rolled_rewards") or {}
        for feat in feats:
            assert feat["id"] in pre, (
                f"floor {floor_num} {nid}/{feat['id']} not pre-rolled"
            )


@pytest.mark.parametrize("seed", [0, 7, 42, 99, 314])
def test_branched_layout_produces_junctions(seed: int):
    """The Undercrypt YAML asks for branches on every floor; the
    generator should produce at least one junction across these seeds."""
    d = _dungeon()
    seen_junction = False
    for floor_num in (1, 2, 3):
        floor = logic.get_floor_data(d, floor_num)
        state = explore.initial_floor_state(floor, random.Random(seed))
        rooms = state["graph"]["rooms"]
        if any(len(r["exits"]) >= 3 for r in rooms.values()):
            seen_junction = True
            break
    assert seen_junction, f"no junctions across all floors at seed {seed}"


def test_no_free_tier_brewing_ingredients_in_loot():
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
# Schema validators.
# ---------------------------------------------------------------------------


def test_validate_dungeon_meta_passes():
    assert explore.validate_dungeon_meta(_dungeon()) == []


def test_validate_room_passes_for_every_room():
    d = _dungeon()
    for floor in d["floors"]:
        for room in floor.get("room_pool") or []:
            errs = explore.validate_room(
                room, path=f"floor[{floor['floor']}].{room['id']}.",
            )
            assert errs == [], errs
