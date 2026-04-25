"""Unit tests for dungeon/map_render.py — fog-of-war floor map."""
from __future__ import annotations

from dungeon import map_render


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _linear(n: int = 3) -> dict:
    """Build a linear graph of N rooms (r0..r{N-1})."""
    rooms = {}
    for i in range(n):
        exits = []
        if i > 0:
            exits.append(f"r{i - 1}")
        if i < n - 1:
            exits.append(f"r{i + 1}")
        rooms[f"r{i}"] = {"exits": exits}
    return {
        "graph": {"rooms": rooms, "entrance": "r0", "boss": f"r{n - 1}"},
        "discovered": ["r0"],
        "current": "r0",
    }


def _branched_y() -> dict:
    """A branched layout:

        r0 ── r1 ── r2
        │
        r3
    """
    return {
        "graph": {
            "rooms": {
                "r0": {"exits": ["r1", "r3"]},
                "r1": {"exits": ["r0", "r2"]},
                "r2": {"exits": ["r1"]},
                "r3": {"exits": ["r0"]},
            },
            "entrance": "r0",
        },
        "discovered": ["r0"],
        "current": "r0",
    }


def _stripped(rendered: str) -> str:
    """Strip the surrounding code fence markers and trim trailing whitespace."""
    if not rendered:
        return ""
    text = rendered.strip()
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip("\n")


# ---------------------------------------------------------------------------
# layout_graph.
# ---------------------------------------------------------------------------


def test_layout_linear_chain_extends_east():
    rooms = {
        "r0": {"exits": ["r1"]},
        "r1": {"exits": ["r0", "r2"]},
        "r2": {"exits": ["r1"]},
    }
    pos = map_render.layout_graph(rooms, "r0")
    assert pos["r0"] == (0, 0)
    assert pos["r1"] == (1, 0)
    assert pos["r2"] == (2, 0)


def test_layout_handles_missing_entrance():
    assert map_render.layout_graph({}, None) == {}
    assert map_render.layout_graph({"r0": {"exits": []}}, "missing") == {}


def test_layout_branch_goes_perpendicular():
    """A second exit from the entrance goes off the chain axis."""
    rooms = {
        "r0": {"exits": ["r1", "r2"]},
        "r1": {"exits": ["r0"]},
        "r2": {"exits": ["r0"]},
    }
    pos = map_render.layout_graph(rooms, "r0")
    # Entrance at origin.
    assert pos["r0"] == (0, 0)
    # First child placed in default direction (east).
    assert pos["r1"] == (1, 0)
    # Second child: east is taken, so try perpendicular (north = y=-1).
    assert pos["r2"] == (0, -1)


def test_layout_complex_tree_keeps_disjoint_cells():
    """No two rooms share the same coordinate."""
    rooms = {
        "r0": {"exits": ["r1", "r2", "r3"]},
        "r1": {"exits": ["r0", "r4"]},
        "r2": {"exits": ["r0"]},
        "r3": {"exits": ["r0"]},
        "r4": {"exits": ["r1"]},
    }
    pos = map_render.layout_graph(rooms, "r0")
    cells = list(pos.values())
    assert len(set(cells)) == len(cells)


# ---------------------------------------------------------------------------
# known_rooms.
# ---------------------------------------------------------------------------


def test_known_rooms_includes_neighbors_of_visited():
    rooms = {
        "r0": {"exits": ["r1"]},
        "r1": {"exits": ["r0", "r2"]},
        "r2": {"exits": ["r1"]},
    }
    known = map_render.known_rooms(rooms, {"r0"})
    assert known == {"r0", "r1"}
    known = map_render.known_rooms(rooms, {"r0", "r1"})
    assert known == {"r0", "r1", "r2"}


def test_known_rooms_empty_discovery():
    rooms = {"r0": {"exits": []}}
    assert map_render.known_rooms(rooms, set()) == set()


# ---------------------------------------------------------------------------
# render_map — content.
# ---------------------------------------------------------------------------


def test_render_map_linear_initial_visit_shows_current_and_one_unexplored():
    state = _linear(3)
    rendered = _stripped(map_render.render_map(state))
    # Should look like: ▣─□  (r0 current, r1 known unexplored)
    assert map_render.GLYPH_CURRENT in rendered
    assert map_render.GLYPH_KNOWN in rendered
    # r2 not yet known
    assert rendered.count(map_render.GLYPH_KNOWN) == 1
    # r0 is current, not visited-marker
    assert map_render.GLYPH_VISITED not in rendered


def test_render_map_linear_after_movement_shows_both_visited_and_current():
    state = _linear(3)
    state["discovered"] = ["r0", "r1"]
    state["current"] = "r1"
    rendered = _stripped(map_render.render_map(state))
    # Expect: ■─▣─□
    assert rendered.count(map_render.GLYPH_VISITED) == 1
    assert rendered.count(map_render.GLYPH_CURRENT) == 1
    assert rendered.count(map_render.GLYPH_KNOWN) == 1


def test_render_map_at_boss_no_unexplored_remaining():
    state = _linear(3)
    state["discovered"] = ["r0", "r1", "r2"]
    state["current"] = "r2"
    rendered = _stripped(map_render.render_map(state))
    # Expect: ■─■─▣
    assert rendered.count(map_render.GLYPH_VISITED) == 2
    assert rendered.count(map_render.GLYPH_CURRENT) == 1
    assert map_render.GLYPH_KNOWN not in rendered


def test_render_map_branched_layout_uses_vertical_connector():
    state = _branched_y()
    state["discovered"] = ["r0", "r1"]
    state["current"] = "r1"
    rendered = _stripped(map_render.render_map(state))
    assert map_render.GLYPH_V_CONN in rendered, (
        f"branched layout should produce a vertical connector. got:\n{rendered}"
    )
    assert map_render.GLYPH_H_CONN in rendered


def test_render_map_wraps_in_code_block():
    state = _linear(3)
    rendered = map_render.render_map(state)
    assert rendered.startswith("```\n")
    assert rendered.endswith("\n```")


def test_render_map_returns_empty_for_empty_state():
    assert map_render.render_map({}) == ""
    assert map_render.render_map({"graph": {}}) == ""
    assert map_render.render_map({"graph": {"rooms": {}, "entrance": None}}) == ""


def test_render_map_unvisited_neighbor_shows_known_glyph():
    """When the player is in r0 of a linear chain, r1 should display as
    'known but unentered' even though they haven't visited it."""
    state = _linear(2)
    rendered = _stripped(map_render.render_map(state))
    # r0 = current, r1 = known
    assert map_render.GLYPH_CURRENT in rendered
    assert map_render.GLYPH_KNOWN in rendered


def test_render_map_legend_mentions_each_glyph():
    legend = map_render.map_legend()
    assert map_render.GLYPH_CURRENT in legend
    assert map_render.GLYPH_VISITED in legend
    assert map_render.GLYPH_KNOWN in legend


# ---------------------------------------------------------------------------
# Layout integration with existing graph generator.
# ---------------------------------------------------------------------------


def test_render_uses_explore_module_floor_state_shape():
    """The renderer should consume the exact shape produced by
    dungeon.explore.initial_floor_state."""
    import random

    from dungeon import explore as dungeon_explore

    floor = {
        "layout": {"rooms_per_run": [3, 3]},
        "anchors": [
            {"position": "entrance", "room_id": "a"},
            {"position": "boss", "room_id": "c"},
        ],
        "wandering_pool": [],
        "room_pool": [
            {"id": "a", "weight": 100, "description_pool": ["A."]},
            {"id": "b", "weight": 100, "description_pool": ["B."]},
            {"id": "c", "weight": 100, "description_pool": ["C."]},
        ],
    }
    state = dungeon_explore.initial_floor_state(floor, random.Random(0))
    rendered = map_render.render_map(state)
    assert rendered.startswith("```")
    # First-room render should have current + one known.
    body = _stripped(rendered)
    assert map_render.GLYPH_CURRENT in body
