"""Racer ability rolling, inheritance, and in-race trigger evaluation.

Loads the ability pool from ``racer_abilities.yaml`` and provides:
- ``roll_abilities`` — assign a (signature, quirk) pair to a newly created
  racer based on their highest stat and temperament
- ``inherit_abilities`` — pick one ability from a parent's pool for foals,
  roll the other fresh
- ``evaluate`` — given a racer's abilities and the current segment context,
  return the procs that fire this segment
- color-emoji palette assignment for race visualization

The YAML is the source of truth for ability content; ability keys are what
gets stored on the Racer model, so editing an ability's effect/commentary
in the YAML automatically applies to all racers with that ability key.
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("discord_bot")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ability:
    key: str
    name: str
    summary: str
    trigger: dict
    effect: dict
    commentary: str
    temperament_weight: dict = field(default_factory=dict)


@dataclass
class SegmentContext:
    """Everything an ability trigger needs to evaluate itself.

    Position is as-of the START of this segment (the racer's position
    heading INTO the segment). Event flags that reference "this segment"
    are based on the noise roll that just happened. Flags that reference
    "last segment" are carried forward from the previous segment's
    outcome. This avoids circular-dependency issues where an ability
    needs to know a result that won't exist until after the ability fires.
    """

    racer_id: int
    segment_index: int  # 0-based
    total_segments: int
    segment_type: str  # straight, corner, climb, descent, hazard
    position: int  # 1-based position heading INTO this segment
    field_size: int
    # Event flags for this segment (known at trigger-time because noise
    # roll has already been computed)
    is_stumbling: bool  # noise_mult < 0.65 this segment
    surged: bool  # noise_mult > 1.35 this segment
    # Event flags carried from last segment (reactive triggers)
    gained_position_last_segment: bool
    lost_position_last_segment: bool
    stumbled_last_segment: bool
    # Rival / field context
    rival_ranks: list[str]  # ranks of other racers in the field
    own_rank: str | None
    sibling_ids_in_field: set[int]
    is_offspring_of_winner: bool
    # Ability state across the race
    once_per_race_fired: set[str]  # ability keys that already fired this race

    @property
    def segment_phase(self) -> str:
        """Return 'opening' | 'mid' | 'final_stretch' based on segment_index."""
        if self.total_segments <= 1:
            return "final_stretch"
        if self.segment_index == 0:
            return "opening"
        if self.segment_index >= self.total_segments - 1:
            return "final_stretch"
        return "mid"

    @property
    def position_band(self) -> str:
        """Return 'leading' | 'mid_pack' | 'trailing' based on position/field_size."""
        if self.field_size <= 0:
            return "mid_pack"
        third = max(1, self.field_size // 3)
        if self.position <= third:
            return "leading"
        if self.position > self.field_size - third:
            return "trailing"
        return "mid_pack"


@dataclass
class AbilityProc:
    """A fired ability: the ability, the commentary, and its resolved effect."""

    ability: Ability
    commentary_rendered: str  # with {name} already substituted
    effect: dict  # the ability's effect dict, passed through for applier


# ---------------------------------------------------------------------------
# YAML loader (cached)
# ---------------------------------------------------------------------------

_ABILITIES_DIR = os.path.dirname(__file__)
_ability_pool: dict[str, Any] | None = None  # raw parsed YAML
_abilities_by_key: dict[str, Ability] | None = None  # flat key→Ability


def _parse_abilities(raw: dict[str, Any]) -> dict[str, Ability]:
    """Flatten all pools into a single key→Ability lookup."""
    result: dict[str, Ability] = {}
    for pool_key in ("speed_pool", "cornering_pool", "stamina_pool", "quirk_pool"):
        for entry in raw.get(pool_key) or []:
            key = entry.get("key")
            if not key:
                continue
            result[key] = Ability(
                key=key,
                name=entry.get("name", key),
                summary=entry.get("summary", ""),
                trigger=entry.get("trigger") or {},
                effect=entry.get("effect") or {},
                commentary=entry.get("commentary", ""),
                temperament_weight=entry.get("temperament_weight") or {},
            )
    return result


def _load_ability_pool() -> dict[str, Any]:
    """Load and cache the ability YAML file."""
    global _ability_pool, _abilities_by_key
    if _ability_pool is not None:
        return _ability_pool

    import yaml

    path = os.path.join(_ABILITIES_DIR, "racer_abilities.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            _ability_pool = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("racer_abilities.yaml not found — abilities disabled")
        _ability_pool = {}
    _abilities_by_key = _parse_abilities(_ability_pool)
    return _ability_pool


def load_abilities() -> dict[str, Ability]:
    """Return the full key→Ability lookup."""
    _load_ability_pool()
    return _abilities_by_key or {}


def reload_abilities() -> None:
    """Force reload of the YAML — used by tests."""
    global _ability_pool, _abilities_by_key
    _ability_pool = None
    _abilities_by_key = None


# ---------------------------------------------------------------------------
# Rolling
# ---------------------------------------------------------------------------


def _highest_stat_pool(racer) -> str:
    """Return the pool key matching the racer's highest stat.

    On ties, pick uniformly at random among tied stats.
    """
    stats = {
        "speed_pool": getattr(racer, "speed", 0) or 0,
        "cornering_pool": getattr(racer, "cornering", 0) or 0,
        "stamina_pool": getattr(racer, "stamina", 0) or 0,
    }
    top = max(stats.values())
    candidates = [k for k, v in stats.items() if v == top]
    return random.choice(candidates)


def _weighted_choice(
    items: list[Ability], weights: list[float], rng: random.Random,
) -> Ability:
    """Random-weighted choice. Falls back to uniform if all weights are zero."""
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
    r = rng.uniform(0, total)
    cumulative = 0.0
    for item, w in zip(items, weights):
        cumulative += w
        if r <= cumulative:
            return item
    return items[-1]


def roll_abilities(
    racer, rng: random.Random | None = None,
) -> tuple[str, str]:
    """Return ``(signature_key, quirk_key)`` for a new racer.

    Signature is drawn from the pool matching the racer's highest stat.
    Quirk is drawn from the quirk pool, weighted by temperament.
    Returns ``("", "")`` if abilities are unavailable (YAML missing).
    """
    rng = rng or random
    pool = _load_ability_pool()
    if not pool:
        return "", ""

    try:
        # Signature: pick from the matching stat pool
        stat_pool_key = _highest_stat_pool(racer)
        stat_pool = pool.get(stat_pool_key) or []
        if not stat_pool:
            return "", ""
        signature_entry = rng.choice(stat_pool)
        signature_key = signature_entry.get("key", "")

        # Quirk: pick from quirk_pool, temperament-weighted
        quirk_entries = pool.get("quirk_pool") or []
        if not quirk_entries:
            return signature_key, ""

        temperament = getattr(racer, "temperament", None)
        abilities_cache = load_abilities()
        candidates = [abilities_cache[e["key"]] for e in quirk_entries if e.get("key") in abilities_cache]

        if temperament:
            weights = [
                float(c.temperament_weight.get(temperament, 1.0))
                for c in candidates
            ]
        else:
            weights = [1.0] * len(candidates)

        quirk = _weighted_choice(candidates, weights, rng)
        return signature_key, quirk.key
    except Exception:
        logger.exception("Failed to roll abilities for racer")
        return "", ""


def inherit_abilities(
    sire_signature: str | None,
    sire_quirk: str | None,
    dam_signature: str | None,
    dam_quirk: str | None,
    foal,
    rng: random.Random | None = None,
    inherit_chance: float = 0.5,
) -> tuple[str, str]:
    """Return ``(signature_key, quirk_key)`` for a foal.

    One ability slot (signature or quirk, chosen randomly) inherits from
    a random parent; the other is rolled fresh. If the chosen parent's
    ability slot is empty (legacy racer), the foal rolls both fresh.
    """
    rng = rng or random

    # Start with a fresh roll — we'll overwrite one slot with inheritance
    fresh_sig, fresh_quirk = roll_abilities(foal, rng=rng)
    if not fresh_sig and not fresh_quirk:
        return "", ""

    # Roll for each slot: chance to inherit from sire or dam
    # Effective probabilities: inherit_chance chance from each parent,
    # remainder fresh. Matches the appearance inheritance shape.
    def _pick(parent_a: str | None, parent_b: str | None, fresh: str) -> str:
        r = rng.random()
        if r < inherit_chance and parent_a:
            return parent_a
        if r < inherit_chance * 2 and parent_b:
            return parent_b
        return fresh

    foal_sig = _pick(sire_signature, dam_signature, fresh_sig)
    foal_quirk = _pick(sire_quirk, dam_quirk, fresh_quirk)

    # Ensure no duplicate key (foal shouldn't have the same ability twice)
    if foal_sig and foal_sig == foal_quirk:
        foal_quirk = fresh_quirk if fresh_quirk != foal_sig else ""

    return foal_sig, foal_quirk


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _trigger_matches(trigger: dict, ctx: SegmentContext, rng: random.Random) -> bool:
    """Return True if this trigger's conditions all hold in the given context."""
    # Segment phase
    seg = trigger.get("segment")
    if seg and seg != "any" and seg != ctx.segment_phase:
        return False

    # Segment type
    stype = trigger.get("segment_type")
    if stype and stype != "any" and stype != ctx.segment_type:
        return False

    # Position band
    pos = trigger.get("position")
    if pos and pos != "any" and pos != ctx.position_band:
        return False

    # Event-based triggers. "gained_position" and "lost_position" reference
    # the LAST segment's outcome (they're reactive). "stumble" and "surged"
    # reference THIS segment's noise roll (which is known before triggers fire).
    event = trigger.get("event")
    if event:
        if event == "stumble" and not ctx.is_stumbling:
            return False
        if event == "surged" and not ctx.surged:
            return False
        if event in ("gained_position", "gained_position_last"):
            if not ctx.gained_position_last_segment:
                return False
        if event in ("lost_position", "lost_position_last"):
            if not ctx.lost_position_last_segment:
                return False
        if event == "post_stumble" and not ctx.stumbled_last_segment:
            return False

    # Rival conditions
    rival = trigger.get("rival_condition")
    if rival:
        if rival == "higher_rank_in_field":
            if not _has_higher_ranked(ctx):
                return False
        elif rival == "lower_rank_in_field":
            if not _has_lower_ranked(ctx):
                return False
        elif rival == "sibling_in_field":
            if not ctx.sibling_ids_in_field:
                return False
        elif rival == "offspring_of_winner":
            if not ctx.is_offspring_of_winner:
                return False
        elif rival == "lowest_ranked":
            if not _is_lowest_ranked(ctx):
                return False
        elif rival == "highest_ranked":
            if not _is_highest_ranked(ctx):
                return False

    # Field size
    min_fs = trigger.get("min_field_size")
    if min_fs and ctx.field_size < int(min_fs):
        return False
    max_fs = trigger.get("max_field_size")
    if max_fs and ctx.field_size > int(max_fs):
        return False

    # Minimum segment index (e.g. front_runner needs past segment 0)
    min_si = trigger.get("min_segment_index")
    if min_si is not None and ctx.segment_index < int(min_si):
        return False

    # Random chance gate
    chance = trigger.get("random_chance")
    if chance is not None:
        if rng.random() > float(chance):
            return False

    return True


_RANK_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}


def _rank_val(rank: str | None) -> int:
    return _RANK_ORDER.get(rank or "D", 0)


def _has_higher_ranked(ctx: SegmentContext) -> bool:
    own = _rank_val(ctx.own_rank)
    return any(_rank_val(r) > own for r in ctx.rival_ranks)


def _has_lower_ranked(ctx: SegmentContext) -> bool:
    own = _rank_val(ctx.own_rank)
    return any(_rank_val(r) < own for r in ctx.rival_ranks)


def _is_lowest_ranked(ctx: SegmentContext) -> bool:
    own = _rank_val(ctx.own_rank)
    return all(_rank_val(r) >= own for r in ctx.rival_ranks) and any(
        _rank_val(r) > own for r in ctx.rival_ranks
    )


def _is_highest_ranked(ctx: SegmentContext) -> bool:
    own = _rank_val(ctx.own_rank)
    return all(_rank_val(r) <= own for r in ctx.rival_ranks) and any(
        _rank_val(r) < own for r in ctx.rival_ranks
    )


def evaluate(
    signature_key: str | None,
    quirk_key: str | None,
    racer_name: str,
    ctx: SegmentContext,
    rng: random.Random | None = None,
) -> list[AbilityProc]:
    """Return ability procs firing this segment, in order signature → quirk."""
    rng = rng or random
    abilities = load_abilities()
    procs: list[AbilityProc] = []

    for key in (signature_key, quirk_key):
        if not key:
            continue
        ability = abilities.get(key)
        if ability is None:
            continue
        # once_per_race gate
        if ability.trigger.get("once_per_race") and key in ctx.once_per_race_fired:
            continue
        if not _trigger_matches(ability.trigger, ctx, rng):
            continue
        procs.append(
            AbilityProc(
                ability=ability,
                commentary_rendered=ability.commentary.replace("{name}", racer_name),
                effect=dict(ability.effect),  # copy so applier can mutate
            )
        )
    return procs


# ---------------------------------------------------------------------------
# Effect application helpers
# ---------------------------------------------------------------------------


def apply_score_effect(
    effect: dict, current_score: float, segment_phase: str,
) -> float:
    """Return the modified segment score after applying this effect."""
    kind = effect.get("kind")
    if kind == "score_bonus":
        return current_score + float(effect.get("value", 0))
    if kind == "segment_ramp":
        values = effect.get("values") or {}
        return current_score + float(values.get(segment_phase, 0))
    # stumble_save / rival_debuff are applied elsewhere (not to score directly)
    return current_score


def is_stumble_save(effect: dict) -> bool:
    return effect.get("kind") == "stumble_save"


# ---------------------------------------------------------------------------
# Display + commentary formatting
# ---------------------------------------------------------------------------


def display_summary(signature_key: str | None, quirk_key: str | None) -> str:
    """Return the multi-line '/stable view' Abilities field content."""
    abilities = load_abilities()
    lines = []
    for key in (signature_key, quirk_key):
        if not key:
            continue
        ab = abilities.get(key)
        if ab:
            lines.append(f"**{ab.name}** — {ab.summary}")
    return "\n".join(lines)


def format_commentary_event(
    proc: AbilityProc, color_emoji: str, racer_name: str,
) -> str:
    """Format an ability proc as an event string for the commentary system.

    The format puts the colored emoji immediately before the racer name
    (so they read as a visual pair), uses possessive phrasing to bind the
    ability to the racer, and marks the ability name with bold + quotes
    so the LLM doesn't mistake it for part of the racer's name. The ⚡
    prefix lets the LLM recognise ability procs without an `[ABILITY]`
    tag that gets awkwardly copied verbatim.
    """
    return (
        f"\u26a1 {color_emoji} **{racer_name}'s \u201c{proc.ability.name}\u201d** "
        f"activates \u2014 {proc.commentary_rendered}"
    )


# ---------------------------------------------------------------------------
# Race color palette
# ---------------------------------------------------------------------------

RACE_COLOR_PALETTE = ["🟥", "🟦", "🟩", "🟨", "🟪", "🟧", "⬛", "🟫"]


def assign_race_colors(racer_ids: list[int]) -> dict[int, str]:
    """Deterministically assign one color emoji per racer in the race.

    Order: racers are sorted by id so a given (race, racer) pair always
    gets the same color for reproducibility/tests. If the field is bigger
    than the palette, colors wrap around (shouldn't happen for normal races).
    """
    sorted_ids = sorted(racer_ids)
    return {
        rid: RACE_COLOR_PALETTE[i % len(RACE_COLOR_PALETTE)]
        for i, rid in enumerate(sorted_ids)
    }
