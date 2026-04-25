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
from dataclasses import dataclass, field
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


KNOWN_VISIBILITIES: set[str] = {"passive", "visible", "concealed", "secret"}
KNOWN_CONTENT_TYPES: set[str] = {"gold", "item", "narrate", "lore_fragment", "corpse_recovery"}


def validate_room(room_def: dict[str, Any], path: str = "") -> list[str]:
    """Return human-readable errors for a v2 room definition. Empty = valid.

    PR 3 schema covers ``features``, ``ambush``, ``description_pool``,
    and ``ambient_pool``. Variant overrides go through this validator
    too (the variants list is checked separately).
    """
    errors: list[str] = []
    features = room_def.get("features")
    if features is not None:
        if not isinstance(features, list):
            errors.append(f"{path}features must be a list")
        else:
            seen_ids: set[str] = set()
            for i, feat in enumerate(features):
                if not isinstance(feat, dict):
                    errors.append(f"{path}features[{i}] must be a dict")
                    continue
                fid = feat.get("id")
                if not fid or not isinstance(fid, str):
                    errors.append(f"{path}features[{i}].id missing or not a string")
                elif fid in seen_ids:
                    errors.append(f"{path}features[{i}].id '{fid}' duplicated")
                else:
                    seen_ids.add(fid)
                vis = feat.get("visibility", "visible")
                if vis not in KNOWN_VISIBILITIES:
                    errors.append(
                        f"{path}features[{i}].visibility '{vis}' not in {sorted(KNOWN_VISIBILITIES)}"
                    )
                if vis == "concealed" and "perception_dc" not in feat:
                    errors.append(
                        f"{path}features[{i}] visibility=concealed requires perception_dc"
                    )
                if vis == "secret" and not feat.get("revealed_by"):
                    errors.append(
                        f"{path}features[{i}] visibility=secret requires revealed_by"
                    )
                # Validate content table.
                content = feat.get("content")
                if content is not None:
                    if not isinstance(content, list):
                        errors.append(f"{path}features[{i}].content must be a list")
                    else:
                        for j, c in enumerate(content):
                            if not isinstance(c, dict):
                                errors.append(
                                    f"{path}features[{i}].content[{j}] must be a dict"
                                )
                                continue
                            ctype = c.get("type")
                            if ctype not in KNOWN_CONTENT_TYPES:
                                errors.append(
                                    f"{path}features[{i}].content[{j}].type "
                                    f"'{ctype}' not in {sorted(KNOWN_CONTENT_TYPES)}"
                                )
                            elif ctype == "lore_fragment":
                                fid = c.get("fragment_id")
                                if not isinstance(fid, int):
                                    errors.append(
                                        f"{path}features[{i}].content[{j}].fragment_id "
                                        f"must be an int"
                                    )
            # Cross-check secret revealed_by points to a real feature.
            for i, feat in enumerate(features):
                if not isinstance(feat, dict):
                    continue
                if feat.get("visibility") == "secret":
                    rby = feat.get("revealed_by")
                    if rby and rby not in seen_ids:
                        errors.append(
                            f"{path}features[{i}].revealed_by '{rby}' not found in this room's features"
                        )

    # Ambient pool — list of strings.
    ambient = room_def.get("ambient_pool")
    if ambient is not None and not (
        isinstance(ambient, list) and all(isinstance(s, str) for s in ambient)
    ):
        errors.append(f"{path}ambient_pool must be a list of strings")

    # description_pool — list of strings.
    descs = room_def.get("description_pool")
    if descs is not None and not (
        isinstance(descs, list) and all(isinstance(s, str) for s in descs)
    ):
        errors.append(f"{path}description_pool must be a list of strings")

    # Variants — recurse on overrides.
    variants = room_def.get("variants")
    if variants is not None:
        if not isinstance(variants, list):
            errors.append(f"{path}variants must be a list")
        else:
            for i, v in enumerate(variants):
                if not isinstance(v, dict):
                    errors.append(f"{path}variants[{i}] must be a dict")
                    continue
                if "key" not in v:
                    errors.append(f"{path}variants[{i}] missing 'key'")
                # If variant declares features_add, validate as a sub-room.
                if "features_add" in v:
                    errors.extend(
                        validate_room(
                            {"features": v["features_add"]},
                            path=f"{path}variants[{i}].",
                        )
                    )
    return errors


def validate_dungeon_meta(dungeon_data: dict[str, Any], path: str = "") -> list[str]:
    """Validate the dungeon-level lore_fragments + legendary_reward fields.

    These are PR 5 additions; both are optional. ``lore_fragments`` is a
    list of ``{id: int, text: str}`` with unique ids. ``legendary_reward``
    is a dict with at least ``item_id``; the player unlocks it after
    collecting all fragments (so it's expected to coexist with a non-empty
    fragments list, but the validator doesn't enforce that pairing).

    Cross-check: every ``lore_fragment`` content reference in any room
    feature must point at a fragment id that exists in the top-level list.
    """
    errors: list[str] = []
    fragments = dungeon_data.get("lore_fragments")
    fragment_ids: set[int] = set()
    if fragments is not None:
        if not isinstance(fragments, list):
            errors.append(f"{path}lore_fragments must be a list")
        else:
            for i, frag in enumerate(fragments):
                if not isinstance(frag, dict):
                    errors.append(f"{path}lore_fragments[{i}] must be a dict")
                    continue
                fid = frag.get("id")
                text = frag.get("text")
                if not isinstance(fid, int):
                    errors.append(f"{path}lore_fragments[{i}].id must be an int")
                elif fid in fragment_ids:
                    errors.append(f"{path}lore_fragments[{i}].id {fid} duplicated")
                else:
                    fragment_ids.add(fid)
                if not isinstance(text, str) or not text.strip():
                    errors.append(f"{path}lore_fragments[{i}].text must be a non-empty string")

    legendary = dungeon_data.get("legendary_reward")
    if legendary is not None:
        if not isinstance(legendary, dict):
            errors.append(f"{path}legendary_reward must be a dict")
        elif not isinstance(legendary.get("item_id"), str) or not legendary["item_id"]:
            errors.append(f"{path}legendary_reward.item_id must be a non-empty string")

    # Cross-check: lore_fragment content references in features.
    for f_idx, floor in enumerate(dungeon_data.get("floors", []) or []):
        for r_idx, room in enumerate(floor.get("room_pool") or []):
            for feat_idx, feat in enumerate(room.get("features") or []):
                for c_idx, content in enumerate(feat.get("content") or []):
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") != "lore_fragment":
                        continue
                    fid = content.get("fragment_id")
                    if isinstance(fid, int) and fragment_ids and fid not in fragment_ids:
                        errors.append(
                            f"{path}floor[{f_idx}].room_pool[{r_idx}].features[{feat_idx}]"
                            f".content[{c_idx}].fragment_id {fid} not in top-level "
                            f"lore_fragments"
                        )
    return errors


def seed_corpse_in_floor(
    state: dict[str, Any],
    rng: random.Random,
    *,
    loot: list[dict[str, Any]],
) -> str | None:
    """Pick a random non-boss room in the floor graph and seed the corpse.

    Mutates ``state`` in place by setting ``state["corpse"] = {room_node, loot}``.
    Returns the chosen room node id, or None if no eligible room exists.
    Caller is responsible for committing the new floor_state.
    """
    graph = state.get("graph") or {}
    rooms = graph.get("rooms") or {}
    boss = graph.get("boss")
    eligible = [n for n in rooms.keys() if n != boss]
    if not eligible:
        return None
    chosen = rng.choice(eligible)
    state["corpse"] = {
        "room_node": chosen,
        "loot": list(loot or []),
        "recovered": False,
    }
    return chosen


def find_floor_monster(floor_data: dict[str, Any], monster_id: str | None) -> dict[str, Any] | None:
    """Look up a monster definition on a floor by id.

    Searches the floor's ``boss`` field first, then ``monsters`` list.
    Returns None if the id isn't found. Used by v2 combat paths to resolve
    the active monster from ``run.monster_id`` (which is populated from
    floor_state's pending_combat).
    """
    if not monster_id:
        return None
    boss_def = floor_data.get("boss") or {}
    if boss_def.get("id") == monster_id:
        return boss_def
    for m in floor_data.get("monsters") or []:
        if m.get("id") == monster_id:
            return m
    return None


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
            # PR 3 feature tracking:
            "searched": [],            # feature ids the player has Investigated
            "revealed_concealed": [],  # concealed feature ids surfaced via Look Around
            "revealed_secrets": [],    # secret feature ids unlocked via revealed_by
            "found_log": [],           # human-readable log of what was found in this room
            # Variant + flavor (PR 1+):
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

    ``rewards`` is a list of reward descriptors the caller applies to the
    run (gold to add, items to drop into found_items, etc.). Rewards
    aren't applied by the explore module so the module stays free of
    persistence coupling.
    """
    narrative: list[str]
    next_step: str  # explore | combat | transition | floor_complete
    rewards: list[dict[str, Any]] = field(default_factory=list)


def _room_def_for_current(state: dict[str, Any], floor_data: dict[str, Any]) -> dict[str, Any]:
    """Look up the room_pool definition for the player's current room."""
    cur = state.get("current")
    graph = state.get("graph") or {}
    rooms = graph.get("rooms") or {}
    if cur is None or cur not in rooms:
        return {}
    pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
    return pool_by_id.get(rooms[cur]["room_def_id"], {})


def _features_in_room(state: dict[str, Any], floor_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the feature list for the current room, applying variant overrides.

    Variants can declare ``features_add`` (extends the base list) or
    ``features`` (replaces it). Both are honored here.

    If a corpse has been seeded into the current room (``state["corpse"]``
    with ``room_node`` matching the current room), a synthetic
    ``your_corpse`` feature is appended so the player can investigate
    and recover their lost loot.
    """
    room_def = _room_def_for_current(state, floor_data)
    cur = state.get("current")
    rs = state["room_states"].get(cur, {}) if cur else {}
    variant_key = rs.get("variant_key")
    base = list(room_def.get("features") or [])

    # Apply variant overrides.
    if variant_key:
        for v in (room_def.get("variants") or []):
            if v.get("key") != variant_key:
                continue
            if "features" in v:
                base = list(v["features"])
            elif "features_add" in v:
                base = base + list(v["features_add"])
            break

    # Inject synthetic corpse feature if one is seeded in this room.
    corpse = state.get("corpse")
    if corpse and corpse.get("room_node") == cur and not corpse.get("recovered"):
        base = list(base) + [{
            "id": "your_corpse",
            "name": "your previous self",
            "visibility": "visible",
            "investigate_label": "Loot the body",
            "noise": 1,
            "flavor_success": (
                "_You find a body slumped against the wall — your own gear, "
                "by the look of it. Whatever you can carry, you take._"
            ),
            "flavor_empty": (
                "_The body is yours, all right. The pockets are already empty._"
            ),
            "content": [
                {
                    "type": "corpse_recovery",
                    "loot": list(corpse.get("loot") or []),
                    "chance": 1.0,
                },
            ],
        }]

    return base


def take_look_around(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
    *,
    perception_modifier: int = 0,
) -> ActionResult:
    """Player looks around. Once-per-room. Tick cost 1.

    Reveals **concealed** features via a perception check
    (1d20 + ``perception_modifier`` >= the feature's ``perception_dc``).
    Visible features are always already surfaced; secret features only
    appear after Investigating their ``revealed_by`` parent.

    May trigger a wandering encounter from the tick.
    """
    cur = state.get("current")
    if cur is None:
        return ActionResult([], "explore")
    rs = state["room_states"].setdefault(cur, {})

    narrative: list[str] = []
    already = bool(rs.get("looked_around"))
    rs["looked_around"] = True

    if already:
        narrative.append("_You've already swept the room. Nothing new catches your eye._")
    else:
        features = _features_in_room(state, floor_data)
        revealed = list(rs.get("revealed_concealed") or [])
        new_reveals: list[str] = []
        for feat in features:
            if feat.get("visibility") != "concealed":
                continue
            fid = feat.get("id")
            if not fid or fid in revealed:
                continue
            roll = rng.randint(1, 20) + int(perception_modifier)
            dc = int(feat.get("perception_dc", 12))
            if roll >= dc:
                revealed.append(fid)
                new_reveals.append(fid)
                hint = feat.get("look_hint") or feat.get("name") or fid
                narrative.append(f"_You spot **{hint}**._")
        rs["revealed_concealed"] = revealed
        if not new_reveals:
            # No concealed features found (or none authored). Narrate a flat line.
            narrative.append(
                "_You scan the room slowly. Nothing else catches your eye._"
            )

    tells, monster_id = advance_tick(state, floor_data, rng, cost=1)
    narrative.extend(tells)
    if monster_id:
        state["pending_combat"] = {"monster_id": monster_id, "kind": "wandering"}
        return ActionResult(narrative, "combat")
    return ActionResult(narrative, "explore")


def take_investigate(
    state: dict[str, Any],
    floor_data: dict[str, Any],
    rng: random.Random,
    *,
    feature_id: str,
) -> ActionResult:
    """Player Investigates a specific feature in the current room.

    Tick cost is per-feature (``noise``, default 2). May trigger a
    wandering encounter mid-search; per the design spec, the action is
    *cancelled* in that case — the feature is NOT marked searched, the
    player can re-attempt after combat.

    On success, rolls the feature's ``content`` table:
    - ``{type: gold, amount: [min, max], chance: float}`` — adds gold
    - ``{type: item, item_id: str, chance: float}`` — drops a consumable
    - ``{type: narrate, text: str}`` — pure flavor

    Marks the feature as searched. Reveals any ``secret`` features whose
    ``revealed_by`` matches this feature.
    """
    cur = state.get("current")
    if cur is None:
        return ActionResult([], "explore")
    rs = state["room_states"].setdefault(cur, {})

    features = _features_in_room(state, floor_data)
    feature = next((f for f in features if f.get("id") == feature_id), None)
    if feature is None:
        return ActionResult(["_You can't find anything like that to inspect._"], "explore")

    if feature_id in (rs.get("searched") or []):
        return ActionResult(["_You've already searched that._"], "explore")

    # Tick before content roll. If a wandering encounter triggers, the
    # action is cancelled — the player has to retry.
    cost = int(feature.get("noise", 2))
    tells, monster_id = advance_tick(state, floor_data, rng, cost=cost)
    if monster_id:
        state["pending_combat"] = {"monster_id": monster_id, "kind": "wandering"}
        return ActionResult(tells, "combat")

    # Mark searched.
    rs.setdefault("searched", []).append(feature_id)

    # Reveal any secret features unlocked by this one.
    secret_reveals: list[str] = []
    for f in features:
        if f.get("visibility") != "secret":
            continue
        if f.get("revealed_by") != feature_id:
            continue
        sid = f.get("id")
        if sid and sid not in (rs.get("revealed_secrets") or []):
            rs.setdefault("revealed_secrets", []).append(sid)
            secret_reveals.append(f.get("name") or sid)

    # Roll content.
    narrative: list[str] = []
    rewards: list[dict[str, Any]] = []
    success_flavor = feature.get("flavor_success")
    if success_flavor:
        narrative.append(success_flavor)

    for content in (feature.get("content") or []):
        chance = float(content.get("chance", 1.0))
        if chance < 1.0 and rng.random() > chance:
            continue
        ctype = content.get("type", "")
        if ctype == "gold":
            amt_range = content.get("amount", [1, 1])
            if isinstance(amt_range, list) and len(amt_range) == 2:
                amt = rng.randint(int(amt_range[0]), int(amt_range[1]))
            else:
                amt = int(amt_range)
            if amt > 0:
                rewards.append({"type": "gold", "amount": amt})
                narrative.append(f"You find **{amt}g**.")
        elif ctype == "item":
            item_id = content.get("item_id")
            if item_id:
                rewards.append({"type": "item", "item_id": item_id})
                narrative.append(f"You find a **{item_id}**.")
        elif ctype == "narrate":
            text = content.get("text") or ""
            if text:
                narrative.append(text)
        elif ctype == "lore_fragment":
            fid = content.get("fragment_id")
            if isinstance(fid, int):
                rewards.append({"type": "lore_fragment", "fragment_id": fid})
                # Narrative is intentionally generic here — the cog awards the
                # fragment (DB write) and produces the personalized
                # "you found fragment N" line so it can also report
                # already-collected vs newly-collected.
        elif ctype == "corpse_recovery":
            # Synthetic content emitted by an injected corpse feature. The
            # actual loot lives in content["loot"] (gold / item entries).
            for inner in (content.get("loot") or []):
                inner_type = inner.get("type")
                if inner_type == "gold":
                    amt_range = inner.get("amount", [1, 1])
                    if isinstance(amt_range, list) and len(amt_range) == 2:
                        amt = rng.randint(int(amt_range[0]), int(amt_range[1]))
                    else:
                        amt = int(amt_range)
                    if amt > 0:
                        rewards.append({"type": "gold", "amount": amt})
                elif inner_type == "item":
                    item_id = inner.get("item_id")
                    if item_id:
                        rewards.append({"type": "item", "item_id": item_id})
                elif inner_type == "gear":
                    gear_id = inner.get("gear_id")
                    if gear_id:
                        rewards.append({"type": "gear", "gear_id": gear_id})
            rewards.append({"type": "corpse_recovered"})  # signal for caller

    # If nothing rolled and no flavor, fall back to the empty-flavor.
    if not narrative:
        narrative.append(
            feature.get("flavor_empty")
            or "_You search carefully. Nothing of value._"
        )

    # Surface any secret reveals at the end so the player notices.
    for s in secret_reveals:
        narrative.append(f"_You spot a hidden **{s}** you hadn't seen before._")

    rs.setdefault("found_log", []).extend(
        [r for r in rewards if r.get("type") in {"gold", "item"}]
    )

    narrative.extend(tells)
    return ActionResult(narrative, "explore", rewards=rewards)


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
    # PR 1: don't auto-include ambient lines in the intro — the LLM will
    # choose them in PR 4. For now, leave them out so authored prose is
    # the whole experience.
    ambient_lines: list[str] = []

    exit_buttons: list[dict[str, Any]] = []
    for i, exit_node in enumerate(rooms[cur].get("exits", [])):
        # PR 3 honors explicit per-exit labels when authored on the room.
        # Looks like: room_def["exits"] = [{id, label}] OR a list of node-id strings.
        # The graph rooms list is just node ids; we map by index here when
        # the room_def supplies labels.
        label = f"Move on (path {i + 1})"
        room_exits = room_def.get("exits") or []
        if i < len(room_exits) and isinstance(room_exits[i], dict):
            authored_label = room_exits[i].get("label")
            if authored_label:
                label = f"Move on ({authored_label})"
        exit_buttons.append({"node_id": exit_node, "label": label})

    return description, ambient_lines, exit_buttons


def visible_feature_buttons(
    state: dict[str, Any],
    floor_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the list of ``Investigate <feature>`` buttons to render.

    A feature is investigable if:
      - visibility == 'visible' (always surfaced), OR
      - visibility == 'concealed' AND its id is in revealed_concealed, OR
      - visibility == 'secret' AND its id is in revealed_secrets

    AND the player hasn't already searched it (one-Investigate-per-feature).

    Returns dicts: ``{"feature_id": ..., "label": "Investigate the X"}``.
    """
    cur = state.get("current")
    rs = state["room_states"].get(cur, {}) if cur else {}
    revealed_concealed = set(rs.get("revealed_concealed") or [])
    revealed_secrets = set(rs.get("revealed_secrets") or [])
    searched = set(rs.get("searched") or [])

    out: list[dict[str, Any]] = []
    for feat in _features_in_room(state, floor_data):
        fid = feat.get("id")
        if not fid or fid in searched:
            continue
        vis = feat.get("visibility", "visible")
        if vis == "passive":
            continue  # passive features are flavor only — not interactable
        if vis == "concealed" and fid not in revealed_concealed:
            continue
        if vis == "secret" and fid not in revealed_secrets:
            continue
        if vis not in {"visible", "concealed", "secret"}:
            continue
        label = feat.get("investigate_label") or f"Investigate the {feat.get('name', fid)}"
        out.append({"feature_id": fid, "label": label})
    return out


def available_exploration_actions(
    state: dict[str, Any],
    floor_data: dict[str, Any],
) -> list[str]:
    """Return the action ids currently surfaced as buttons.

    Look Around is once-per-room. Listen is repeatable. Investigate
    buttons are emitted by :func:`visible_feature_buttons` separately.
    Move-on buttons come from :func:`render_room_intro`'s exits list.
    """
    cur = state.get("current")
    rs = state["room_states"].get(cur, {}) if cur else {}
    actions = []
    if not rs.get("looked_around"):
        actions.append("look_around")
    actions.append("listen")
    if visible_feature_buttons(state, floor_data):
        actions.append("investigate")
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
