"""Race definitions and racial passive lookup.

All numeric tuning lives here so it's easy to adjust without touching
game-specific logic files.  Each game calls ``get_racial_modifier()``
with a namespaced key to read the value for a player's race.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Valid race IDs (order = display order in /race choose)
# ---------------------------------------------------------------------------

RACE_IDS: list[str] = ["human", "dwarf", "elf", "halfling", "orc"]

# ---------------------------------------------------------------------------
# Racial modifier lookup
# ---------------------------------------------------------------------------

RACIAL_MODIFIERS: dict[str, dict[str, Any]] = {
    # -- Human: The Generalist --
    "human": {
        # Signature: +15% XP everywhere
        "global.xp_multiplier": 1.15,
        # Fishing: +5% weight toward rare+ fish
        "fishing.rare_weight_bonus": 0.05,
        # Brewing: +10% payout
        "brewing.payout_multiplier": 1.10,
        # Racing: daily reward tier +1
        "racing.daily_tier_bonus": 1,
    },

    # -- Dwarf: The Enduring Crafter --
    "dwarf": {
        # Signature: cheat death once per run
        "dungeon.stoneblood": True,
        # Rest shrines heal 40%
        "dungeon.rest_heal_fraction": 0.40,
        # Flaw: flee DC 14, trap saves -1
        "dungeon.flee_dc": 14,
        "dungeon.trap_save_bonus": -1,
        # Brewing: explosion threshold +15
        "brewing.explosion_threshold_bonus": 15,
        # Racing: training cost -20%
        "racing.training_cost_multiplier": 0.80,
    },

    # -- Elf: The Precise --
    "elf": {
        # Signature: 10% double catch
        "fishing.double_catch_chance": 0.10,
        # Dungeon: +2 trap saves
        "dungeon.trap_save_bonus": 2,
        # Dungeon: crit on 19-20
        "dungeon.crit_threshold": 19,
        # Flaw: HP = CON * 1.75
        "dungeon.hp_multiplier": 1.75,
        # Brewing: +15% potency
        "brewing.potency_multiplier": 1.15,
        # Racing: mood floor 2
        "racing.mood_floor": 2,
    },

    # -- Halfling: The Lucky --
    "halfling": {
        # Signature: treasure rolls twice, +5% loot chance
        "dungeon.treasure_double_roll": True,
        "dungeon.loot_chance_bonus": 5,
        # Flaw: weapon bonus -1
        "dungeon.weapon_bonus_penalty": 1,
        # Dungeon: flee DC 10
        "dungeon.flee_dc": 10,
        # Fishing: 15% bait save
        "fishing.bait_save_chance": 0.15,
        # Brewing: ingredient prices -15%
        "brewing.ingredient_price_multiplier": 0.85,
        # Racing: bet payout +15%
        "racing.bet_payout_multiplier": 1.15,
    },

    # -- Orc: The Berserker --
    "orc": {
        # Signature: advantage on damage dice below 50% HP
        "dungeon.bloodrage": True,
        # HP = CON * 2.25
        "dungeon.hp_multiplier": 2.25,
        # Flaw: explosion threshold -15
        "brewing.explosion_threshold_bonus": -15,
        # Fishing: cast time -10%
        "fishing.cast_time_multiplier": 0.90,
        # Racing: injury chance halved
        "racing.injury_chance_multiplier": 0.50,
        # Daily: +5 flat gold
        "economy.daily_gold_bonus": 5,
    },
}


def get_racial_modifier(race: str, key: str, default: Any = None) -> Any:
    """Return a racial modifier value, or *default* if not set."""
    return RACIAL_MODIFIERS.get(race, {}).get(key, default)


# ---------------------------------------------------------------------------
# Race change cost
# ---------------------------------------------------------------------------

_RACE_CHANGE_COSTS = [250, 500, 1_000, 2_000, 5_000]


def get_race_change_cost(changes_so_far: int) -> int:
    """Return the gold cost for the next race change.

    Escalating: 250 → 500 → 1000 → 2000 → 5000 (cap).
    """
    idx = min(changes_so_far, len(_RACE_CHANGE_COSTS) - 1)
    return _RACE_CHANGE_COSTS[idx]
