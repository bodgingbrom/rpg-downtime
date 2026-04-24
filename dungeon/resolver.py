"""Modifier resolver — composes damage/defense modifiers from all sources.

This is the single place combat-time modifiers get aggregated. The
damage calc functions stay pure; the cog call sites call into here to
get composed ``Mods`` structs before invoking the math.

Sources composed:
- **Race passives** (via ``rpg.logic.get_racial_modifier``)
- **Active combat-state effects** (``state["player_effects"]`` /
  ``state["monster_effects"]``)
- **Monster ability contributions** (Scale Bar dice step, etc., which
  write into ``monster_effects`` via effect handlers)
- Future: equipped gear modifiers will plug in here as another source
  without the call sites changing.

## Stacking rules (per-key)

| Kind                 | Rule                             |
|----------------------|----------------------------------|
| Flat numeric bonuses | Sum across contributors          |
| Multipliers          | Multiply (identity 1.0)          |
| Dice size steps      | Sum, then clamp to 1d4..1d12     |
| Booleans             | OR across contributors           |
| Caps (lower better)  | Min                              |
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rpg.logic import get_racial_modifier


# ---------------------------------------------------------------------------
# Dice ladder for step-based escalation.
# ---------------------------------------------------------------------------

DICE_LADDER: list[str] = ["1d4", "1d6", "1d8", "1d10", "1d12"]


def bump_dice(base_dice: str, step: int) -> str:
    """Return ``base_dice`` bumped up ``step`` positions on the ladder.

    Unknown base dice (e.g. '2d6') are returned unchanged — the step
    system only applies to ladder dice. Negative steps go down; values
    beyond the ladder clamp to 1d4 / 1d12.
    """
    if base_dice not in DICE_LADDER:
        return base_dice
    idx = DICE_LADDER.index(base_dice)
    new_idx = max(0, min(len(DICE_LADDER) - 1, idx + step))
    return DICE_LADDER[new_idx]


# ---------------------------------------------------------------------------
# Resolved modifier structs.
# ---------------------------------------------------------------------------


@dataclass
class PlayerAttackMods:
    """Composed modifiers applied when the player attacks this turn."""
    damage_bonus: int = 0           # flat, summed
    damage_advantage: bool = False  # OR
    damage_disadvantage: bool = False  # OR — roll twice keep LOWER
    bonus_penalty: int = 0          # race (halfling); summed
    weapon_dice_step: int = 0       # summed; caller applies bump_dice


@dataclass
class MonsterAttackMods:
    """Composed modifiers applied when the monster attacks this turn."""
    attack_dice_step: int = 0       # summed
    flat_damage_bonus: int = 0      # summed
    # For future: hit chance etc.


@dataclass
class PlayerDefenseMods:
    """Composed modifiers applied to incoming damage / hit chance."""
    hit_chance_multiplier: float = 1.0  # product
    extra_damage_taken: int = 0         # flat, summed (bleed ticks counted separately)


# ---------------------------------------------------------------------------
# Resolvers — read race + combat state, produce Mods.
# ---------------------------------------------------------------------------


def resolve_player_attack_mods(
    *,
    race: str,
    run_current_hp: int,
    run_max_hp: int,
    state: dict[str, Any] | None,
) -> PlayerAttackMods:
    """Compose player-attack mods from race + active effects.

    NOTE: crit_threshold, weapon_dice, weapon_bonus, str_mod are handled
    by the call site today — see cogs/dungeon.py. This resolver adds the
    NEW composable modifiers that didn't exist before.
    """
    mods = PlayerAttackMods()

    # Race: Orc Bloodrage — advantage when below 50% HP
    if get_racial_modifier(race, "dungeon.bloodrage", False):
        if run_max_hp > 0 and run_current_hp <= run_max_hp // 2:
            mods.damage_advantage = True

    # Race: Halfling weapon bonus penalty
    mods.bonus_penalty = int(get_racial_modifier(race, "dungeon.weapon_bonus_penalty", 0))

    # Combat-state effects on the player
    if state:
        for eff in state.get("player_effects", []):
            etype = eff.get("type")
            if etype == "advantage_next_attack":
                mods.damage_advantage = True
            elif etype == "invert_next_attack":
                mods.damage_disadvantage = True
            # Note: hit_chance_reduction & bleed are defense-side, handled
            # by resolve_player_defense_mods.

    return mods


def resolve_monster_attack_mods(
    *,
    state: dict[str, Any] | None,
) -> MonsterAttackMods:
    """Compose monster-attack mods from active monster_effects."""
    mods = MonsterAttackMods()
    if not state:
        return mods
    for eff in state.get("monster_effects", []):
        etype = eff.get("type")
        if etype == "dice_step_self_active":
            mods.attack_dice_step += int(eff.get("step", 0))
        elif etype == "flat_damage_bonus_self":
            mods.flat_damage_bonus += int(eff.get("amount", 0))
    return mods


def resolve_monster_defense_bonus(state: dict[str, Any] | None) -> int:
    """Extra monster defense from active effects (e.g., Alaric's wall)."""
    if not state:
        return 0
    total = 0
    for eff in state.get("monster_effects", []):
        if eff.get("type") == "defense_bonus_self":
            total += int(eff.get("amount", 0))
    return total


def resolve_player_defense_mods(state: dict[str, Any] | None) -> PlayerDefenseMods:
    """Compose incoming-attack mods for the player."""
    mods = PlayerDefenseMods()
    if not state:
        return mods
    mult = 1.0
    for eff in state.get("player_effects", []):
        etype = eff.get("type")
        if etype == "hit_chance_reduction":
            mult *= max(0.0, 1.0 - float(eff.get("amount", 0.0)))
    mods.hit_chance_multiplier = mult
    return mods


def resolve_bleed_damage(state: dict[str, Any] | None) -> int:
    """Return bleed damage to apply to the player this turn."""
    if not state:
        return 0
    total = 0
    for eff in state.get("player_effects", []):
        if eff.get("type") == "bleed":
            total += int(eff.get("damage", 0))
    return total
