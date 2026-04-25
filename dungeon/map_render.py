"""Floor map rendering — fog-of-war ASCII map for v2 dungeons.

Pure functions over a floor_state graph. The renderer walks the graph
into a 2D grid, applies fog of war (visible rooms only — visited rooms
plus their immediate neighbors), and emits a code-block-wrapped ASCII
art rendering using box-drawing connectors.

## Glyph palette

  ▣  current room (the one the player is in)
  ■  visited room (no current encounter to render)
  □  known but unentered (you can see the exit but haven't taken it)
  ─  horizontal connector
  │  vertical connector

The entire output is wrapped in a Markdown code block so Discord renders
it in monospace and connectors line up with cells.

## Layout

BFS layout from the entrance. Each placed room sits at integer
coordinates; each step is 1 unit. The grid is rendered with 2 chars per
horizontal step (cell + connector or cell + space) and 2 lines per
vertical step. Branches preferentially extend perpendicular to the
incoming direction, so linear chains lay out cleanly east.

The current implementation handles linear-with-branches floors. Hub /
loop topologies would need more sophisticated layout — flagged for a
future iteration.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Glyphs.
# ---------------------------------------------------------------------------

GLYPH_CURRENT = "▣"   # ▣
GLYPH_VISITED = "■"   # ■
GLYPH_KNOWN = "□"     # □
GLYPH_H_CONN = "─"    # ─
GLYPH_V_CONN = "│"    # │


# ---------------------------------------------------------------------------
# Layout.
# ---------------------------------------------------------------------------


def layout_graph(
    rooms: dict[str, dict[str, Any]],
    entrance: str | None,
) -> dict[str, tuple[int, int]]:
    """Compute (x, y) positions for each graph node via BFS from entrance.

    Direction preference: continue the parent's direction first (so chains
    lay out straight), then try perpendicular (north, then south for
    horizontal parents; east, then west for vertical), then back. If a
    target cell is occupied, try the next direction. Unplaced rooms in
    disconnected subgraphs return positions only if reachable from the
    entrance.
    """
    if not entrance or entrance not in rooms:
        return {}

    positions: dict[str, tuple[int, int]] = {entrance: (0, 0)}
    occupied: set[tuple[int, int]] = {(0, 0)}
    incoming_dir: dict[str, tuple[int, int]] = {entrance: (1, 0)}
    queue: list[str] = [entrance]

    while queue:
        node = queue.pop(0)
        x, y = positions[node]
        last_dx, last_dy = incoming_dir.get(node, (1, 0))
        # Build direction preference list: continue, then perpendiculars,
        # then back.
        if last_dx != 0:  # came in horizontally
            dirs = [(last_dx, 0), (0, -1), (0, 1), (-last_dx, 0)]
        else:             # came in vertically
            dirs = [(0, last_dy), (1, 0), (-1, 0), (0, -last_dy)]

        for exit_id in rooms[node].get("exits", []):
            if exit_id in positions:
                continue  # already placed (typically the parent)
            for dx, dy in dirs:
                cell = (x + dx, y + dy)
                if cell not in occupied:
                    positions[exit_id] = cell
                    occupied.add(cell)
                    incoming_dir[exit_id] = (dx, dy)
                    queue.append(exit_id)
                    break

    return positions


# ---------------------------------------------------------------------------
# Fog of war.
# ---------------------------------------------------------------------------


def known_rooms(
    rooms: dict[str, dict[str, Any]],
    discovered: set[str],
) -> set[str]:
    """Rooms the player should see on the map — visited plus adjacent.

    A room is "known" if the player has visited it OR if at least one of
    its neighbors has been visited (the exit is visible from there).
    """
    out = set(discovered)
    for node in discovered:
        for exit_id in rooms.get(node, {}).get("exits", []) or []:
            out.add(exit_id)
    return out


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def render_map(floor_state: dict[str, Any]) -> str:
    """Render the fog-of-war floor map as a Markdown code block.

    Returns an empty string if the state is empty / has no graph. The
    returned string includes the surrounding triple backticks so callers
    can drop it directly into an embed description.
    """
    graph = floor_state.get("graph") or {}
    rooms = graph.get("rooms") or {}
    entrance = graph.get("entrance")
    if not rooms or not entrance:
        return ""

    discovered = set(floor_state.get("discovered") or [])
    if not discovered:
        # Even with no discovery, show the entrance so the player has
        # *something* to look at on first entry.
        discovered = {entrance}
    current = floor_state.get("current")

    positions = layout_graph(rooms, entrance)
    visible = known_rooms(rooms, discovered)
    visible_positions = {n: positions[n] for n in visible if n in positions}
    if not visible_positions:
        return ""

    xs = [p[0] for p in visible_positions.values()]
    ys = [p[1] for p in visible_positions.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    grid_w = (max_x - min_x) * 2 + 1
    grid_h = (max_y - min_y) * 2 + 1
    grid: list[list[str]] = [[" "] * grid_w for _ in range(grid_h)]

    def cell_xy(node: str) -> tuple[int, int]:
        x, y = visible_positions[node]
        return (x - min_x) * 2, (y - min_y) * 2

    # Place rooms.
    for node in visible_positions:
        gx, gy = cell_xy(node)
        if node == current:
            glyph = GLYPH_CURRENT
        elif node in discovered:
            glyph = GLYPH_VISITED
        else:
            glyph = GLYPH_KNOWN
        grid[gy][gx] = glyph

    # Place connectors between adjacent visible rooms.
    drawn_edges: set[tuple[str, str]] = set()
    for node in visible_positions:
        x, y = visible_positions[node]
        for exit_id in rooms.get(node, {}).get("exits", []) or []:
            if exit_id not in visible_positions:
                continue
            edge = tuple(sorted([node, exit_id]))
            if edge in drawn_edges:
                continue
            ex, ey = visible_positions[exit_id]
            dx, dy = ex - x, ey - y
            # Only draw connectors for adjacent (1-cell-apart) rooms; layout
            # may otherwise produce unconnected pairs — skip those gracefully.
            if abs(dx) + abs(dy) != 1:
                continue
            gx = (x - min_x) * 2 + dx
            gy = (y - min_y) * 2 + dy
            grid[gy][gx] = GLYPH_H_CONN if dy == 0 else GLYPH_V_CONN
            drawn_edges.add(edge)

    body = "\n".join("".join(row).rstrip() for row in grid)
    if not body.strip():
        return ""
    return f"```\n{body}\n```"


# ---------------------------------------------------------------------------
# Legend (for embed footer or help text).
# ---------------------------------------------------------------------------


def map_legend() -> str:
    """Short one-liner explaining the glyphs."""
    return (
        f"{GLYPH_CURRENT} you  "
        f"{GLYPH_VISITED} cleared  "
        f"{GLYPH_KNOWN} unexplored"
    )
