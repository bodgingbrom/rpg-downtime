"""V2 dungeon exploration engine — procedural floor graphs, tick system, room loop.

This is the engine for the RPG-lite overhaul. See
``dungeon/data/DUNGEON_OVERHAUL_DESIGN.md`` for the full design spec.

## Overview

A v2 dungeon is recognized by the presence of a ``room_pool`` field on
any of its floors. v2 floors describe rooms as a *pool* with *anchors*
that are placed at fixed positions; the floor graph is procedurally
assembled from these at floor entry.

Floor exploration state lives in ``DungeonRun.floor_state_json``,
separate from per-encounter combat state. The state machine:

    enter floor → init floor_state_json from pool/anchors
                → render entry room
                → loop:
                    read action button click
                    apply tick (advance danger)
                    resolve action
                    if combat: hand off to combat system
                                  on return: continue loop
                    if move: change current room, render new entry
                    else: re-render current room with action result

## Action grammar (PR 1)

PR 1 ships three actions; PR 3 adds Investigate + features:

- **Look Around** — once per room. Tick cost 1. PR 1 narrates a flavor
  line; PR 3 will surface concealed features.
- **Listen** — repeatable, tick cost 1. Surfaces danger tells.
- **Move on \<exit\>** — free (0 ticks). Transitions to a connected room.

## Tick / danger model

A floor-level tension counter advances with each tick. When the counter
crosses ``wandering_threshold`` (default 6 for the placeholder), the
*next* tick triggers a wandering encounter pulled from the floor's
``wandering_pool``. After a wandering encounter resolves, tension
resets. Tells (low-grade narration) are emitted as tension climbs:

    tension < 3:    no tells
    tension 3..4:   "Something stirs in the distance."
    tension 5+:     "The sound is closer now. You can almost place it."
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# v1 / v2 detection.
# ---------------------------------------------------------------------------


def is_v2_dungeon(dungeon_data: dict[str, Any]) -> bool:
    """A dungeon is v2 if any floor has a ``room_pool`` field."""
    for floor in dungeon_data.get("floors", []) or []:
        if floor.get("room_pool"):
            return True
    return False


def floor_is_v2(floor_data: dict[str, Any]) -> bool:
    return bool(floor_data.get("room_pool"))


# ---------------------------------------------------------------------------
# Floor graph generation.
# ---------------------------------------------------------------------------


@dataclass
class GraphRoom:
    id: str           # graph node id (e.g. "r0")
    room_def_id: str  # which room from the pool this is
    exits: list[str]  # graph node ids


def generate_floor_graph(
    floor_data: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """Build a procedural floor graph from the floor's room_pool and anchors.

    Returns a graph dict ready to embed in floor_state_json::

        {
            "rooms": {
                "r0": {"room_def_id": "dev_alcove", "exits": ["r1"]},
                "r1": {"room_def_id": "dev_corridor", "exits": ["r0", "r2"]},
                "r2": {"room_def_id": "dev_endroom", "exits": ["r1"]},
            },
            "entrance": "r0",
            "boss": "r2",
        }

    PR 1 implementation: linear chain. Picks anchors first (entrance, boss),
    fills the rest from the pool (without replacement, weighted), connects
    sequentially. Branches and complex topologies land in later PRs but the
    return shape is forward-compatible.
    """
    layout = floor_data.get("layout") or {}
    rpr = layout.get("rooms_per_run", [3, 3])
    if isinstance(rpr, list) and len(rpr) == 2:
        room_count = rng.randint(int(rpr[0]), int(rpr[1]))
    else:
        room_count = int(rpr)

    pool = list(floor_data.get("room_pool") or [])
    anchors = list(floor_data.get("anchors") or [])
    by_id = {r["id"]: r for r in pool}

    # Find entrance and boss anchors; everything else fills the middle.
    entrance_id: str | None = None
    boss_id: str | None = None
    for a in anchors:
        pos = a.get("position")
        if pos == "entrance":
            entrance_id = a["room_id"]
        elif pos == "boss":
            boss_id = a["room_id"]
    if entrance_id is None and pool:
        entrance_id = pool[0]["id"]
    if boss_id is None and pool:
        boss_id = pool[-1]["id"]

    # Sample non-anchor rooms by weight, without replacement, until we have
    # enough to fill room_count - 2 (entrance + boss are already chosen).
    # If the pool is too small, we just use as many as we have.
    middle_target = max(0, room_count - 2)
    candidates = [r for r in pool if r["id"] != entrance_id and r["id"] != boss_id]
    middle_picks: list[str] = []
    remaining = list(candidates)
    while remaining and len(middle_picks) < middle_target:
        weights = [int(r.get("weight", 100)) for r in remaining]
        picked = rng.choices(remaining, weights=weights, k=1)[0]
        middle_picks.append(picked["id"])
        remaining.remove(picked)

    # Assemble linear chain: entrance → middle... → boss.
    sequence: list[str] = []
    if entrance_id is not None:
        sequence.append(entrance_id)
    sequence.extend(middle_picks)
    if boss_id is not None and (not sequence or sequence[-1] != boss_id):
        sequence.append(boss_id)

    # Build graph nodes with exits. Each room gets a graph id rN.
    graph_rooms: dict[str, dict[str, Any]] = {}
    for i, room_def_id in enumerate(sequence):
        node_id = f"r{i}"
        exits: list[str] = []
        if i > 0:
            exits.append(f"r{i - 1}")
        if i < len(sequence) - 1:
            exits.append(f"r{i + 1}")
        graph_rooms[node_id] = {"room_def_id": room_def_id, "exits": exits}

    return {
        "rooms": graph_rooms,
        "entrance": "r0" if sequence else None,
        "boss": f"r{len(sequence) - 1}" if sequence else None,
    }


# ---------------------------------------------------------------------------
# Variant pick on room visit.
# ---------------------------------------------------------------------------


def pick_room_variant(room_def: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    """If the room defines variants, pick one weighted-random; else None.

    Variants override room fields when the room is rendered. PR 1 doesn't
    exercise variant content but the picker is wired now so the schema
    stays stable for PR 3+.
    """
    variants = room_def.get("variants") or []
    if not variants:
        return None
    weights = [int(v.get("weight", 100)) for v in variants]
    if all(w <= 0 for w in weights):
        return None
    return rng.choices(variants, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Floor state initialization.
# ---------------------------------------------------------------------------


WANDERING_THRESHOLD_DEFAULT = 6  # ticks before wandering encounter is armed


def initial_floor_state(
    floor_data: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    """Build the starting floor_state_json payload for a fresh floor entry."""
    graph = generate_floor_graph(floor_data, rng)
    entrance = graph.get("entrance")

    # Pre-roll variants and ambush state for every room in the graph so the
    # whole floor is deterministic from a seed.
    pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
    room_states: dict[str, dict[str, Any]] = {}
    for node_id, node in graph["rooms"].items():
        room_def = pool_by_id.get(node["room_def_id"], {})
        variant = pick_room_variant(room_def, rng)
        # Merge variant ambush override if present.
        ambush_def = (variant or {}).get("ambush") or room_def.get("ambush") or {}
        # Description picked once per room visit, but stored at first entry
        # so re-renders are stable.
        descs = (variant or {}).get("description_pool") or room_def.get("description_pool") or []
        picked_desc = rng.choice(descs) if descs else None
        room_states[node_id] = {
            "visited": False,
            "looked_around": False,
            "searched": [],          # feature ids investigated (PR 3+)
            "found": [],             # found feature ids (PR 3+)
            "variant_key": (variant or {}).get("key"),
            "description": picked_desc,
            "ambush_armed": bool(ambush_def.get("armed", False)),
            "ambush_resolved": False,
            "encounter_resolved": False,  # set after combat completes in this room
        }

    state: dict[str, Any] = {
        "graph": graph,
        "current": entrance,
        "discovered": [entrance] if entrance else [],
        "room_states": room_states,
        "tension": 0,
        "wandering_threshold": int(
            floor_data.get("wandering_threshold", WANDERING_THRESHOLD_DEFAULT)
        ),
        "pending_combat": None,
    }
    if entrance:
        room_states[entrance]["visited"] = True
    return state


# ---------------------------------------------------------------------------
# Tick / danger.
# ---------------------------------------------------------------------------


def _danger_tell(tension: int) -> str | None:
    if tension >= 5:
        return "_The sound is closer now — you can almost place it._"
    if tension >= 3:
        return "_Something stirs in the distance._"
    return None


def advance_tick(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
    *,
    cost: int = 1,
) -> tuple[list[str], str | None]:
    """Advance the tension counter by ``cost`` ticks.

    Returns ``(narrative_lines, wandering_monster_id_or_none)``. If a
    wandering encounter triggers, the caller must initiate combat with
    that monster id. Tension resets after a wandering trigger.
    """
    if cost <= 0:
        return [], None
    state["tension"] = int(state.get("tension", 0)) + cost
    threshold = int(state.get("wandering_threshold", WANDERING_THRESHOLD_DEFAULT))

    narrative: list[str] = []
    if state["tension"] >= threshold:
        # Wandering encounter armed — pull from the floor's wandering pool.
        pool = floor_data.get("wandering_pool") or []
        if not pool:
            # No wandering pool defined — relieve tension and emit nothing.
            state["tension"] = 0
            return narrative, None
        # Each entry can be a string (monster_id) or a dict {monster_id, weight}.
        normalized: list[tuple[str, int]] = []
        for entry in pool:
            if isinstance(entry, str):
                normalized.append((entry, 100))
            elif isinstance(entry, dict):
                mid = entry.get("monster_id")
                if mid:
                    normalized.append((mid, int(entry.get("weight", 100))))
        if not normalized:
            state["tension"] = 0
            return narrative, None
        ids = [n[0] for n in normalized]
        weights = [n[1] for n in normalized]
        chosen = rng.choices(ids, weights=weights, k=1)[0]
        state["tension"] = 0
        return narrative, chosen

    tell = _danger_tell(state["tension"])
    if tell:
        narrative.append(tell)
    return narrative, None


# ---------------------------------------------------------------------------
# Action resolution.
# ---------------------------------------------------------------------------


@dataclass
class ActionResult:
    """Outcome of a player action.

    ``narrative`` lines are appended to the room's display.
    ``next_step`` is one of:
      - ``"explore"`` — re-render the current room
      - ``"combat"`` — combat should start; combat will pull monster from
        ``state["pending_combat"]``
      - ``"transition"`` — current room changed; re-render new room
      - ``"floor_complete"`` — boss defeated, floor done
    """
    narrative: list[str]
    next_step: str  # explore | combat | transition | floor_complete


def take_look_around(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
) -> ActionResult:
    """Player looks around. PR 1: narrates a placeholder line.

    Tick cost 1. May trigger wandering encounter. If the room has an armed
    ambush, lookin around can also trigger it (the player draws attention).
    PR 1 keeps it simple: no ambush trigger from Look (that's a PR 3
    decision when authored content has variety).
    """
    cur = state.get("current")
    if cur is None:
        return ActionResult([], "explore")
    rs = state["room_states"].setdefault(cur, {})

    narrative: list[str] = []
    if rs.get("looked_around"):
        narrative.append("_You've already looked around. Nothing new catches your eye._")
        # Still costs a tick.
        tells, monster_id = advance_tick(state, floor_data, rng, cost=1)
        narrative.extend(tells)
        if monster_id:
            state["pending_combat"] = {"monster_id": monster_id, "kind": "wandering"}
            return ActionResult(narrative, "combat")
        return ActionResult(narrative, "explore")

    rs["looked_around"] = True
    # PR 1 placeholder: no concealed features to surface yet. Just narrate.
    narrative.append("_You scan the room slowly. Nothing of obvious interest._")

    tells, monster_id = advance_tick(state, floor_data, rng, cost=1)
    narrative.extend(tells)
    if monster_id:
        state["pending_combat"] = {"monster_id": monster_id, "kind": "wandering"}
        return ActionResult(narrative, "combat")
    return ActionResult(narrative, "explore")


def take_listen(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
) -> ActionResult:
    """Player listens. Repeatable. Tick cost 1.

    Surfaces a more direct danger tell than the passive ones.
    """
    narrative: list[str] = []
    tension = int(state.get("tension", 0))
    threshold = int(state.get("wandering_threshold", WANDERING_THRESHOLD_DEFAULT))

    if tension <= 0:
        narrative.append("_You hold still. Silence, mostly. Something dripping, somewhere far._")
    elif tension < threshold // 2:
        narrative.append("_You listen. Faint movement, but distant. Nothing close._")
    elif tension < threshold:
        narrative.append("_You listen. Something is moving — closer than before, and not alone._")
    else:
        narrative.append("_You listen. **Footsteps. Close.**_")

    tells, monster_id = advance_tick(state, floor_data, rng, cost=1)
    narrative.extend(t for t in tells if t not in narrative)
    if monster_id:
        state["pending_combat"] = {"monster_id": monster_id, "kind": "wandering"}
        return ActionResult(narrative, "combat")
    return ActionResult(narrative, "explore")


def take_move_on(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
    *,
    target_node: str,
) -> ActionResult:
    """Player moves to a connected room. Free (0 ticks).

    If the destination has an armed ambush, it fires on entry — pending
    combat is set with the ambush monster. Otherwise, returns 'transition'
    and the caller renders the new room.
    """
    graph = state.get("graph") or {}
    rooms = graph.get("rooms") or {}
    cur = state.get("current")
    if cur is None or target_node not in rooms:
        return ActionResult(["_You can't go that way._"], "explore")
    if cur is not None and target_node not in rooms.get(cur, {}).get("exits", []):
        return ActionResult(["_You can't go that way._"], "explore")

    state["current"] = target_node
    if target_node not in state.get("discovered", []):
        state.setdefault("discovered", []).append(target_node)

    rs = state["room_states"].setdefault(target_node, {})
    first_visit = not rs.get("visited", False)
    rs["visited"] = True

    narrative: list[str] = []
    # Boss room: combat starts on entry if the floor declares a boss
    # monster id and combat hasn't already been resolved here.
    boss_node = graph.get("boss")
    if target_node == boss_node and not rs.get("encounter_resolved"):
        boss_def = floor_data.get("boss") or {}
        if boss_def.get("id"):
            state["pending_combat"] = {"monster_id": boss_def["id"], "kind": "boss"}
            return ActionResult(narrative, "combat")

    # Ambush in the destination room fires on first action — entering the
    # room counts as the first action for ambush purposes.
    if first_visit and rs.get("ambush_armed") and not rs.get("ambush_resolved"):
        # Look up the ambush creature from the room def or its picked variant.
        pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
        room_def = pool_by_id.get(rooms[target_node]["room_def_id"], {})
        ambush_def = room_def.get("ambush") or {}
        # Variant override
        if rs.get("variant_key"):
            for v in (room_def.get("variants") or []):
                if v.get("key") == rs["variant_key"]:
                    ambush_def = v.get("ambush") or ambush_def
                    break
        monster_id = ambush_def.get("monster_id") or ambush_def.get("creature")
        if monster_id:
            state["pending_combat"] = {"monster_id": monster_id, "kind": "ambush"}
            rs["ambush_resolved"] = True
            return ActionResult(narrative, "combat")

    return ActionResult(narrative, "transition")


# ---------------------------------------------------------------------------
# Rendering helpers — text the cog uses to build embeds.
# ---------------------------------------------------------------------------


def render_room_intro(
    state: dict[str, Any],
    floor_data: dict[str, Any],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Return ``(description, ambient_lines, exit_buttons)`` for the current room.

    ``exit_buttons`` is a list of ``{"node_id": "rN", "label": "north"}`` dicts.
    The cog wires them up as Discord buttons.
    """
    cur = state.get("current")
    graph = state.get("graph") or {}
    rooms = graph.get("rooms") or {}
    if cur is None or cur not in rooms:
        return "(no current room)", [], []
    pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
    room_def = pool_by_id.get(rooms[cur]["room_def_id"], {})
    rs = state["room_states"].get(cur, {})

    description = rs.get("description") or room_def.get("description") or "(empty room)"
    ambient_pool = room_def.get("ambient_pool") or []
    # PR 1: don't auto-include ambient lines in the intro — the LLM will
    # choose them in PR 4. For now, leave them out so authored prose is
    # the whole experience.
    ambient_lines: list[str] = []

    exit_buttons: list[dict[str, Any]] = []
    for exit_node in rooms[cur].get("exits", []):
        # Label: simple ordinal for PR 1. PR 3 introduces explicit labels.
        idx = len(exit_buttons) + 1
        label = f"Move on (path {idx})"
        exit_buttons.append({"node_id": exit_node, "label": label})

    return description, ambient_lines, exit_buttons


def available_exploration_actions(
    state: dict[str, Any],
    floor_data: dict[str, Any],
) -> list[str]:
    """Return the action ids currently surfaced as buttons.

    PR 1: Look Around (if not already done in this room), Listen, Move on
    (per exit), Investigate (none yet — PR 3).
    """
    cur = state.get("current")
    rs = state["room_states"].get(cur, {}) if cur else {}
    actions = []
    if not rs.get("looked_around"):
        actions.append("look_around")
    actions.append("listen")
    return actions


# ---------------------------------------------------------------------------
# Persistence helpers — JSON safe load / dump.
# ---------------------------------------------------------------------------


def load_floor_state(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def dump_floor_state(state: dict[str, Any]) -> str:
    return json.dumps(state)
