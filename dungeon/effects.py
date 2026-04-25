"""Combat effect system — composable, data-driven abilities and status effects.

This module is the central handler registry for monster abilities, status
effects, and anything else that can modify combat. The design goal is
**reusability**: the same primitives power monster abilities (e.g. Scale
Bar's dice escalation) and player-side gear (e.g. a future magic weapon
that adds a dice step). There is one code path, parameterized by data.

## Design

Effects are declarative dicts. Each has a ``type`` key naming a handler,
plus type-specific params. Handlers live in :data:`EFFECT_HANDLERS` and
mutate the current encounter's :class:`EncounterCtx` in-place.

Example YAML::

    abilities:
      - {type: dice_step_self, step_schedule: [{turn: 3, step: 1}, {turn: 5, step: 2}]}
      - {type: random_effect_pool, every: 2, pool: [
          {type: player_next_attack_invert},
          {type: player_hit_chance_reduction, amount: 0.3, turns: 1},
        ]}

## Triggers

Triggers describe *when* an ability fires. The combat loop queries the
module-level helpers to determine which abilities should run on a given
turn.

## Status effects

Some abilities *apply* a status effect (e.g. ``player_next_attack_invert``
writes into ``state["player_effects"]``). The resolver then reads those
effects when composing damage mods. Durations decrement at end of turn.

Active-effect entries have the shape::

    {"type": "invert_next_attack", "remaining": 1, "source": "key_legend"}

Where ``remaining`` is turns-left. When 0, removed at end-of-turn.

## Adding a new effect

1. Pick a unique ``type`` string.
2. Register the handler in :data:`EFFECT_HANDLERS`.
3. Add to :data:`KNOWN_ABILITY_TYPES` so YAML validates.
4. If it produces a player/monster status effect, the resolver should
   know how to read it.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Vocabulary — pinned so bad YAML fails at load time, not in combat.
# ---------------------------------------------------------------------------

KNOWN_TRIGGERS: set[str] = {
    "on_spawn",           # fires once when the monster spawns
    "on_turn",            # every ``every`` turns (default 1)
    "on_hp_below_pct",    # first time monster HP drops below a pct threshold
    "on_hit",             # after the monster lands a hit
    "on_taken_hit",       # after the monster takes a hit
}

KNOWN_ABILITY_TYPES: set[str] = {
    # Structural / meta
    "random_effect_pool",
    "summon_add",
    "narrate",

    # Self-buffs (monster)
    "dice_step_self",
    "flat_damage_bonus_self",
    "defense_bonus_self",
    "self_heal",

    # Player-targeting status effects
    "player_next_attack_invert",
    "player_next_attack_advantage",
    "player_hit_chance_reduction",
    "bleed",

    # Special damage
    "existential_strike",
    "redraw_strike",
}


# ---------------------------------------------------------------------------
# Encounter context — passed into handlers and the resolver.
# ---------------------------------------------------------------------------


@dataclass
class EncounterCtx:
    """Mutable context representing one combat turn.

    The combat loop builds a fresh EncounterCtx at the top of each turn,
    runs handlers / resolvers against it, and persists the mutated
    ``state`` back to the run row.
    """

    state: dict[str, Any]              # combat_state_json parsed
    monster_def: dict[str, Any]        # current active monster def (post-variant)
    monster_hp: int                    # current active entity HP (for triggers)
    monster_max_hp: int
    turn: int                          # turn number (1-indexed)
    phase: int                         # phase index (0-based)
    rng: random.Random
    narrative: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State initialization — called when a monster spawns for combat.
# ---------------------------------------------------------------------------


def initial_combat_state(monster_def: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Build the starting combat_state_json payload for a new encounter.

    Picks a variant (if any) and a flavor description (if any). Initializes
    counters and effect lists. The caller is responsible for applying the
    variant's HP override to run.monster_hp / run.monster_max_hp.
    """
    state: dict[str, Any] = {
        "turn": 0,
        "phase": 0,
        "player_effects": [],
        "monster_effects": [],
        "adds": [],
        "active": "primary",
        "primary": None,
        "fired_hp_triggers": [],  # ids of on_hp_below_pct triggers that have fired
        "fired_once_abilities": [],  # for once-per-fight abilities
    }

    # Pick a variant if the monster has a variants list
    variants = monster_def.get("variants")
    if variants:
        variant = rng.choice(variants)
        state["variant"] = dict(variant)

    # Pick a description from the pool if present
    pool = monster_def.get("description_pool")
    if pool:
        state["description"] = rng.choice(pool)

    return state


# ---------------------------------------------------------------------------
# Trigger evaluation — which abilities fire this turn?
# ---------------------------------------------------------------------------


def should_trigger(
    ability: dict[str, Any],
    *,
    turn: int,
    monster_hp: int,
    monster_max_hp: int,
    state: dict[str, Any],
) -> bool:
    """Return True if ``ability`` should fire on the given turn/HP.

    Abilities can optionally be gated by phase via ``phase_min`` /
    ``phase_max`` fields. Phase 0 is baseline (before any HP threshold
    crossed); phase 1 is after the first threshold, etc. ``phase_max: 0``
    restricts the ability to baseline only (useful for summoner phase-1
    abilities that shouldn't fire in the boss's later phases).
    """
    # Phase gate — applies to every trigger type.
    current_phase = int(state.get("phase", 0)) if state else 0
    if "phase_min" in ability and current_phase < int(ability["phase_min"]):
        return False
    if "phase_max" in ability and current_phase > int(ability["phase_max"]):
        return False

    trigger = ability.get("trigger", "on_turn")
    if trigger == "on_spawn":
        return turn == 1
    if trigger == "on_turn":
        every = int(ability.get("every", 1))
        if every <= 0:
            return False
        return turn >= 1 and (turn % every == 0)
    if trigger == "on_hp_below_pct":
        pct = float(ability.get("pct", 50))
        if monster_max_hp <= 0:
            return False
        hp_pct = (monster_hp / monster_max_hp) * 100.0
        if hp_pct > pct:
            return False
        # One-shot: only fire first time
        tid = _trigger_id(ability)
        return tid not in state.get("fired_hp_triggers", [])
    # on_hit / on_taken_hit are fired by the combat loop explicitly, not
    # by should_trigger at start-of-turn.
    return False


def mark_hp_trigger_fired(ability: dict[str, Any], state: dict[str, Any]) -> None:
    """Record that a one-shot on_hp_below_pct trigger has fired."""
    fired = state.setdefault("fired_hp_triggers", [])
    tid = _trigger_id(ability)
    if tid not in fired:
        fired.append(tid)


def _trigger_id(ability: dict[str, Any]) -> str:
    """Stable id for dedup of one-shot triggers."""
    return f"{ability.get('type', '?')}:{ability.get('trigger', '?')}:{ability.get('pct', '')}"


# ---------------------------------------------------------------------------
# Handler dispatch.
# ---------------------------------------------------------------------------


EffectHandler = Callable[[EncounterCtx, dict[str, Any]], None]


def dispatch(ctx: EncounterCtx, ability: dict[str, Any]) -> None:
    """Run a single ability spec against the context."""
    atype = ability.get("type")
    handler = EFFECT_HANDLERS.get(atype)
    if handler is None:
        ctx.narrative.append(f"_(unknown effect '{atype}' skipped)_")
        return
    handler(ctx, ability)


# ---------------------------------------------------------------------------
# Effect handlers.
# ---------------------------------------------------------------------------


def _handler_narrate(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Pure flavor — append a line to the turn narrative."""
    text = params.get("text") or ""
    if text:
        ctx.narrative.append(text)


def _handler_random_effect_pool(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Pick one entry from ``pool`` and dispatch it recursively."""
    pool = params.get("pool") or []
    if not pool:
        return
    pick = ctx.rng.choice(pool)
    dispatch(ctx, pick)


def _handler_dice_step_self(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Contribute to monster-side dice step based on turn schedule.

    The schedule is a list of ``{turn, step}`` entries. We pick the
    highest ``step`` whose ``turn`` <= current turn, and apply it to the
    monster's *effective* attack dice (stored in state for the resolver).
    """
    schedule = params.get("step_schedule") or []
    active_step = 0
    for entry in schedule:
        if ctx.turn >= int(entry.get("turn", 0)):
            active_step = max(active_step, int(entry.get("step", 0)))
    # Write to monster_effects as a persistent modifier; resolver reads it.
    effs = ctx.state.setdefault("monster_effects", [])
    # Remove any prior dice_step_self contribution from this ability before
    # writing the new one so we don't stack across turns.
    effs[:] = [e for e in effs if e.get("type") != "dice_step_self_active"]
    if active_step > 0:
        effs.append({"type": "dice_step_self_active", "step": active_step, "remaining": -1})


def _handler_flat_damage_bonus_self(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Add a permanent flat damage bonus to monster attacks for this encounter."""
    amount = int(params.get("amount", 0))
    effs = ctx.state.setdefault("monster_effects", [])
    # Idempotent when fired repeatedly from the same ability — overwrite.
    source = params.get("_source_id", "flat_damage_bonus")
    effs[:] = [e for e in effs if e.get("source") != source]
    effs.append({
        "type": "flat_damage_bonus_self",
        "amount": amount,
        "remaining": -1,
        "source": source,
    })


def _handler_defense_bonus_self(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Temporary defense boost on the monster."""
    amount = int(params.get("amount", 0))
    turns = int(params.get("turns", 1))
    effs = ctx.state.setdefault("monster_effects", [])
    effs.append({
        "type": "defense_bonus_self",
        "amount": amount,
        "remaining": turns,
    })


def _handler_self_heal(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Heal the monster by ``amount`` HP (the caller applies it to run row)."""
    amount = int(params.get("amount", 0))
    if amount <= 0:
        return
    # Stash a pending heal — the combat loop applies it to run.monster_hp.
    pending = ctx.state.setdefault("_pending_self_heal", 0)
    ctx.state["_pending_self_heal"] = pending + amount
    ctx.narrative.append(f"The {ctx.monster_def.get('name', 'monster')} recovers **{amount}** HP.")


def _handler_player_next_attack_invert(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Set a single-use flag: the player's next attack rolls twice, keeps lower."""
    effs = ctx.state.setdefault("player_effects", [])
    effs.append({"type": "invert_next_attack", "remaining": 2})
    ctx.narrative.append("_Your next strike feels... reversed. The map rearranges underfoot._")


def _handler_player_next_attack_advantage(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Set a single-use flag granting the player advantage on their next attack."""
    effs = ctx.state.setdefault("player_effects", [])
    effs.append({"type": "advantage_next_attack", "remaining": 2})
    ctx.narrative.append("_A weak spot shimmers into view. Your next strike has advantage._")


def _handler_player_hit_chance_reduction(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Apply a hit-chance multiplier to the player for N turns."""
    amount = float(params.get("amount", 0.25))
    turns = int(params.get("turns", 1))
    effs = ctx.state.setdefault("player_effects", [])
    effs.append({
        "type": "hit_chance_reduction",
        "amount": amount,
        "remaining": turns + 1,  # +1 because we decrement end-of-turn
    })
    pct = int(amount * 100)
    ctx.narrative.append(f"_A fog rolls in. Your hit chance is reduced by {pct}% next turn._")


def _handler_bleed(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Apply bleed damage-over-time to the player."""
    dmg = int(params.get("damage", 1))
    turns = int(params.get("turns", 2))
    effs = ctx.state.setdefault("player_effects", [])
    effs.append({
        "type": "bleed",
        "damage": dmg,
        "remaining": turns + 1,
    })
    ctx.narrative.append(f"_You're bleeding — **{dmg}** damage over the next {turns} turns._")


def _handler_summon_add(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Spawn an add if we're under max_active. The combat loop handles
    target-swap logic based on state['active'] and state['adds'].

    The summon target is picked from (in priority order):
    1. ``add_pool`` — list of monster ids; one is chosen at random.
    2. ``add_id`` / ``monster_id`` — a single monster id.
    """
    adds = ctx.state.setdefault("adds", [])
    active = [a for a in adds if a.get("hp", 0) > 0]
    max_active = int(params.get("max_active", 1))
    if len(active) >= max_active:
        return

    pool = params.get("add_pool") or []
    if pool:
        add_id = ctx.rng.choice(pool)
    else:
        add_id = params.get("add_id") or params.get("monster_id")
    if not add_id:
        return
    # The caller must look up add_id in the dungeon's monsters and populate
    # hp/max_hp/attack_dice from the definition. Here we just record the
    # intent; combat loop does the spawn. Record an unresolved add marker.
    adds.append({
        "def_id": add_id,
        "hp": None,              # loop fills in from monster def
        "max_hp": None,
        "pending_spawn": True,
    })
    if params.get("untargetable_self"):
        ctx.state["untargetable_primary"] = True


def _handler_existential_strike(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """Mark the monster's NEXT monster_action to be an existential strike.

    The combat loop consumes this flag and deals damage using
    ``damage_dice`` instead of the normal attack_dice.
    """
    dice = params.get("damage_dice", "2d10")
    bonus = int(params.get("damage_bonus", 0))
    text = params.get("text")
    ctx.state["_pending_special_attack"] = {
        "kind": "existential_strike",
        "damage_dice": dice,
        "damage_bonus": bonus,
        "text": text or None,
    }


def _handler_redraw_strike(ctx: EncounterCtx, params: dict[str, Any]) -> None:
    """One-shot high-damage attack that partially ignores defense.

    Marks a pending special attack the combat loop consumes on the
    monster's NEXT action.
    """
    dice = params.get("damage_dice", "2d6")
    bonus = int(params.get("damage_bonus", 0))
    ignore = int(params.get("defense_ignore", 2))
    text = params.get("text")
    # Idempotent — do nothing if already armed.
    if ctx.state.get("_pending_special_attack"):
        return
    ctx.state["_pending_special_attack"] = {
        "kind": "redraw_strike",
        "damage_dice": dice,
        "damage_bonus": bonus,
        "defense_ignore": ignore,
        "text": text or None,
    }


EFFECT_HANDLERS: dict[str, EffectHandler] = {
    "narrate": _handler_narrate,
    "random_effect_pool": _handler_random_effect_pool,
    "dice_step_self": _handler_dice_step_self,
    "flat_damage_bonus_self": _handler_flat_damage_bonus_self,
    "defense_bonus_self": _handler_defense_bonus_self,
    "self_heal": _handler_self_heal,
    "player_next_attack_invert": _handler_player_next_attack_invert,
    "player_next_attack_advantage": _handler_player_next_attack_advantage,
    "player_hit_chance_reduction": _handler_player_hit_chance_reduction,
    "bleed": _handler_bleed,
    "summon_add": _handler_summon_add,
    "existential_strike": _handler_existential_strike,
    "redraw_strike": _handler_redraw_strike,
}


# ---------------------------------------------------------------------------
# Duration / end-of-turn housekeeping.
# ---------------------------------------------------------------------------


def tick_effects(state: dict[str, Any]) -> None:
    """Decrement ``remaining`` on all active effects; remove expired.

    ``remaining == -1`` means "permanent for this encounter" and is never
    decremented. Otherwise 1 is subtracted and entries at 0 are removed.

    Called ONCE per turn, at the end, after damage/outcomes are resolved.
    """
    for bucket in ("player_effects", "monster_effects"):
        effs = state.get(bucket)
        if not effs:
            continue
        new_effs = []
        for eff in effs:
            rem = eff.get("remaining", 0)
            if rem == -1:
                new_effs.append(eff)
                continue
            rem -= 1
            if rem > 0:
                eff["remaining"] = rem
                new_effs.append(eff)
            # rem == 0 → drop
        state[bucket] = new_effs


def consume_player_flag(state: dict[str, Any], flag_type: str) -> bool:
    """Remove a single-use player effect by type. Returns True if consumed."""
    effs = state.get("player_effects", [])
    for i, eff in enumerate(effs):
        if eff.get("type") == flag_type:
            effs.pop(i)
            return True
    return False


# ---------------------------------------------------------------------------
# YAML validation — called at dungeon load time.
# ---------------------------------------------------------------------------


def validate_abilities(abilities: Any, *, path: str = "") -> list[str]:
    """Return a list of human-readable error messages. Empty = valid."""
    errors: list[str] = []
    if abilities is None:
        return errors
    if not isinstance(abilities, list):
        errors.append(f"{path}abilities must be a list")
        return errors
    for i, a in enumerate(abilities):
        if not isinstance(a, dict):
            errors.append(f"{path}abilities[{i}] must be a dict")
            continue
        atype = a.get("type")
        if atype not in KNOWN_ABILITY_TYPES:
            errors.append(f"{path}abilities[{i}].type '{atype}' not in KNOWN_ABILITY_TYPES")
        trigger = a.get("trigger", "on_turn")
        if trigger not in KNOWN_TRIGGERS:
            errors.append(f"{path}abilities[{i}].trigger '{trigger}' not in KNOWN_TRIGGERS")
        # Phase gate fields must be ints if present.
        for pk in ("phase_min", "phase_max"):
            if pk in a and not isinstance(a[pk], int):
                errors.append(f"{path}abilities[{i}].{pk} must be an int")
        # summon_add: add_pool must be a list of strings if present.
        if atype == "summon_add":
            pool_ids = a.get("add_pool")
            if pool_ids is not None:
                if not isinstance(pool_ids, list) or not all(isinstance(s, str) for s in pool_ids):
                    errors.append(
                        f"{path}abilities[{i}].add_pool must be a list of strings"
                    )
        # Recurse into random_effect_pool.pool
        if atype == "random_effect_pool":
            pool = a.get("pool") or []
            for j, inner in enumerate(pool):
                if not isinstance(inner, dict):
                    errors.append(f"{path}abilities[{i}].pool[{j}] must be a dict")
                    continue
                inner_type = inner.get("type")
                if inner_type not in KNOWN_ABILITY_TYPES:
                    errors.append(
                        f"{path}abilities[{i}].pool[{j}].type '{inner_type}' not in KNOWN_ABILITY_TYPES"
                    )
    return errors


def validate_monster(monster_def: dict[str, Any], *, path: str = "") -> list[str]:
    """Validate a monster definition's new optional fields."""
    errors: list[str] = []
    errors.extend(validate_abilities(monster_def.get("abilities"), path=f"{path}"))
    # description_pool: list of strings
    pool = monster_def.get("description_pool")
    if pool is not None:
        if not isinstance(pool, list) or not all(isinstance(s, str) for s in pool):
            errors.append(f"{path}description_pool must be a list of strings")
    # variants: list of dicts, each with a 'key'
    variants = monster_def.get("variants")
    if variants is not None:
        if not isinstance(variants, list):
            errors.append(f"{path}variants must be a list")
        else:
            for i, v in enumerate(variants):
                if not isinstance(v, dict) or "key" not in v:
                    errors.append(f"{path}variants[{i}] must be a dict with 'key'")
    # phases: list of dicts with hp_below_pct
    phases = monster_def.get("phases")
    if phases is not None:
        if not isinstance(phases, list):
            errors.append(f"{path}phases must be a list")
        else:
            for i, p in enumerate(phases):
                if not isinstance(p, dict) or "hp_below_pct" not in p:
                    errors.append(f"{path}phases[{i}] must be a dict with 'hp_below_pct'")
                else:
                    # Nested abilities_add
                    errors.extend(
                        validate_abilities(p.get("abilities_add"), path=f"{path}phases[{i}].")
                    )
    return errors
