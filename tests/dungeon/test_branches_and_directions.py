"""Tests for branch graph generation and directional exit labels.

Covers two pieces shipped together:

1. ``generate_floor_graph`` honors ``layout.branches`` /
   ``layout.branch_length`` — the produced graph has side branches off
   the main spine when authored content asks for them.

2. ``map_render.exit_directions`` returns N/S/E/W labels derived from
   the same layout the map renderer uses, and ``render_room_intro``
   surfaces those labels (with a ``(back)`` suffix on visited exits).
"""
from __future__ import annotations

import random

import pytest

from dungeon import explore, map_render


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _branched_floor(spine: int = 5, branches: int = 2, branch_len: int = 1):
    """A floor with enough pool rooms for the requested spine + branches."""
    rooms_per_run = [spine, spine]
    branches_range = [branches, branches]
    branch_length = [branch_len, branch_len]
    pool = [
        {"id": "entrance", "weight": 100, "description_pool": ["E."]},
        {"id": "boss", "weight": 100, "description_pool": ["B."]},
    ]
    # Plenty of fillers — enough for spine + branches and to spare.
    for i in range(20):
        pool.append({"id": f"f{i}", "weight": 100, "description_pool": [f"f{i}."]})
    return {
        "floor": 1,
        "layout": {
            "rooms_per_run": rooms_per_run,
            "branches": branches_range,
            "branch_length": branch_length,
        },
        "anchors": [
            {"position": "entrance", "room_id": "entrance"},
            {"position": "boss", "room_id": "boss"},
        ],
        "wandering_threshold": 99,
        "wandering_pool": [],
        "monsters": [],
        "boss": {"id": "test_boss", "hp": 5, "defense": 0, "attack_dice": "1d4",
                 "attack_bonus": 0, "xp": 1, "gold": [1, 1], "ai": {"attack": 100}},
        "room_pool": pool,
    }


# ---------------------------------------------------------------------------
# Branch generation.
# ---------------------------------------------------------------------------


def test_branched_layout_produces_more_rooms_than_spine():
    """5-room spine + 2 branches × 1 room each = 7 graph rooms."""
    floor = _branched_floor(spine=5, branches=2, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    assert len(graph["rooms"]) == 7


def test_branched_layout_preserves_anchors():
    floor = _branched_floor(spine=5, branches=2, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    assert rooms[graph["entrance"]]["room_def_id"] == "entrance"
    assert rooms[graph["boss"]]["room_def_id"] == "boss"


def test_branched_layout_creates_junctions():
    """Branches mean some node has 3+ exits (parent + child spine + branch)."""
    floor = _branched_floor(spine=5, branches=2, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    junctions = [n for n, r in graph["rooms"].items() if len(r["exits"]) >= 3]
    assert len(junctions) >= 1


def test_branch_leaves_have_single_exit_back_to_attach():
    """A branch of length 1 produces a leaf node with exactly one exit."""
    floor = _branched_floor(spine=4, branches=1, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    # Branch leaves are the nodes whose ids are >= len(spine).
    spine_size = sum(1 for n in rooms if rooms[n]["room_def_id"] in {"entrance", "boss"}) + (4 - 2)
    leaves = [n for n in rooms if int(n[1:]) >= 4]
    assert leaves, "expected at least one branch leaf"
    for leaf in leaves:
        assert len(rooms[leaf]["exits"]) == 1


def test_zero_branches_yields_pure_linear_chain():
    floor = _branched_floor(spine=5, branches=0, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    assert len(rooms) == 5
    # Each room has 1 (endpoint) or 2 (middle) exits — never 3+.
    for r in rooms.values():
        assert len(r["exits"]) in (1, 2)


def test_branches_only_attach_to_spine_middle_not_endpoints():
    """Entrance and boss should never get a branch attached — they
    remain plain endpoints with one exit."""
    floor = _branched_floor(spine=5, branches=2, branch_len=2)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    assert len(rooms[graph["entrance"]]["exits"]) == 1
    assert len(rooms[graph["boss"]]["exits"]) == 1


def test_branch_length_2_produces_chain_of_2():
    floor = _branched_floor(spine=4, branches=1, branch_len=2)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    # Total = spine 4 + 1 branch * 2 rooms = 6.
    assert len(rooms) == 6


def test_pool_exhaustion_caps_branches_gracefully():
    """If the pool runs out before all branches are filled, we ship
    fewer branches rather than crash."""
    # Spine wants 5 rooms but pool only has 3 fillers + 2 anchors = 5.
    # 2 branches × 1 = 2 more — pool is empty after spine. No branches ship.
    floor = {
        "floor": 1,
        "layout": {"rooms_per_run": [5, 5], "branches": [2, 2], "branch_length": [1, 1]},
        "anchors": [
            {"position": "entrance", "room_id": "e"},
            {"position": "boss", "room_id": "b"},
        ],
        "wandering_pool": [],
        "monsters": [],
        "boss": {"id": "b", "hp": 5, "defense": 0, "attack_dice": "1d4",
                 "attack_bonus": 0, "xp": 1, "gold": [1, 1], "ai": {"attack": 100}},
        "room_pool": [
            {"id": "e", "weight": 100, "description_pool": ["."]},
            {"id": "b", "weight": 100, "description_pool": ["."]},
            {"id": "f1", "weight": 100, "description_pool": ["."]},
            {"id": "f2", "weight": 100, "description_pool": ["."]},
            {"id": "f3", "weight": 100, "description_pool": ["."]},
        ],
    }
    graph = explore.generate_floor_graph(floor, random.Random(0))
    # Should be a clean linear 5 — no branches because the pool exhausted.
    assert len(graph["rooms"]) == 5


def test_seeded_branch_generation_is_deterministic():
    floor = _branched_floor(spine=5, branches=2, branch_len=1)
    g1 = explore.generate_floor_graph(floor, random.Random(123))
    g2 = explore.generate_floor_graph(floor, random.Random(123))
    assert g1 == g2


def test_branch_attach_points_dont_repeat():
    """Two branches never share the same attach node."""
    floor = _branched_floor(spine=5, branches=3, branch_len=1)
    graph = explore.generate_floor_graph(floor, random.Random(0))
    rooms = graph["rooms"]
    # Spine middle is r1, r2, r3 (entrance r0, boss r4). Each spine middle
    # node has at most 1 branch attached, so degree is ≤ 3 (parent + child + branch).
    spine_middle_max_degree = max(
        len(rooms[f"r{i}"]["exits"]) for i in (1, 2, 3)
    )
    assert spine_middle_max_degree <= 3


# ---------------------------------------------------------------------------
# Direction labels — exit_directions helper.
# ---------------------------------------------------------------------------


def test_exit_directions_linear_chain_returns_east_for_forward():
    rooms = {
        "r0": {"exits": ["r1"]},
        "r1": {"exits": ["r0", "r2"]},
        "r2": {"exits": ["r1"]},
    }
    dirs = map_render.exit_directions(rooms, "r0", "r1")
    assert dirs["r0"] == "west"
    assert dirs["r2"] == "east"


def test_exit_directions_branched_layout_perpendicular():
    """When a branch goes off a horizontal spine, the branch direction
    should be perpendicular (north or south)."""
    rooms = {
        "r0": {"exits": ["r1"]},
        "r1": {"exits": ["r0", "r2", "r3"]},  # branch attach
        "r2": {"exits": ["r1"]},
        "r3": {"exits": ["r1"]},  # branch leaf
    }
    dirs = map_render.exit_directions(rooms, "r0", "r1")
    # The chain goes r0 (west) → r1 → r2 (east). The branch goes
    # perpendicular — north or south.
    assert dirs["r0"] == "west"
    assert dirs["r2"] == "east"
    assert dirs["r3"] in {"north", "south"}


def test_exit_directions_handles_missing_inputs():
    assert map_render.exit_directions({}, None, None) == {}
    assert map_render.exit_directions({"r0": {"exits": []}}, "r0", None) == {}
    assert map_render.exit_directions({"r0": {"exits": []}}, "r0", "missing") == {}


# ---------------------------------------------------------------------------
# render_room_intro — surface direction labels in exit buttons.
# ---------------------------------------------------------------------------


def test_render_room_intro_uses_direction_labels():
    floor = _branched_floor(spine=4, branches=0, branch_len=1)
    state = explore.initial_floor_state(floor, random.Random(0))
    _, _, exits = explore.render_room_intro(state, floor)
    # Entrance has one exit forward — should be "Move east".
    assert len(exits) == 1
    assert exits[0]["label"] == "Move east"


def test_render_room_intro_marks_visited_exits_as_back():
    floor = _branched_floor(spine=4, branches=0, branch_len=1)
    state = explore.initial_floor_state(floor, random.Random(0))
    # Walk forward.
    explore.take_move_on(state, floor, random.Random(0), target_node="r1")
    _, _, exits = explore.render_room_intro(state, floor)
    by_label = {e["label"]: e["node_id"] for e in exits}
    # r0 is now "back" — west exit, visited.
    assert "Move west (back)" in by_label
    assert by_label["Move west (back)"] == "r0"
    # Forward to r2 is still unexplored.
    assert "Move east" in by_label


def test_render_room_intro_branched_room_has_three_directional_exits():
    floor = _branched_floor(spine=5, branches=1, branch_len=1)
    state = explore.initial_floor_state(floor, random.Random(0))
    # Find a node with 3 exits (a junction).
    rooms = state["graph"]["rooms"]
    junction = next(
        (n for n, r in rooms.items() if len(r["exits"]) == 3),
        None,
    )
    assert junction is not None, "expected at least one junction"
    state["current"] = junction
    state["room_states"][junction]["visited"] = True
    _, _, exits = explore.render_room_intro(state, floor)
    labels = [e["label"] for e in exits]
    # Should include at least one of north/south (the branch) and at
    # least one of east/west (the spine). Concrete directions vary by
    # layout but the label strings should not be the old "path 1".
    assert all(l.startswith("Move ") for l in labels)
    assert not any("path" in l for l in labels)
