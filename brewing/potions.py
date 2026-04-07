"""Potion creation logic — dominant tag calculation and potion determination."""

from __future__ import annotations

import random
from typing import Sequence

from .models import Ingredient

# ---------------------------------------------------------------------------
# Tag → Potion type mapping
# ---------------------------------------------------------------------------

TAG_TO_POTION: dict[str, str] = {
    "Thermal": "swiftness",
    "Volatile": "dexterity",
    "Calcified": "giants_strength",
    "Stabilizing": "clarity",
    "Resonant": "harmony",
    "Celestial": "fertility",
    "Spectral": "longevity",
    "Corrosive": "stripping",
    "Verdant": "healing",
    "Mutagenic": "mutation",
    "Abyssal": "fortification",
    "Luminous": "foresight",  # 200+ overridden to "revelation"
}

# Minimum potency to create any potion
POTION_MIN_POTENCY = 100

# Luminous threshold for the secret Revelation potion
REVELATION_POTENCY = 200

# ---------------------------------------------------------------------------
# Tier naming for non-numeric potions
# ---------------------------------------------------------------------------

_TIER_NAMES_3 = {1: "Minor", 2: "", 3: "Superior"}
_TIER_NAMES_5 = {1: "Minor", 2: "Lesser", 3: "", 4: "Greater", 5: "Superior"}
_TIER_NAMES_6 = {
    1: "Minor",
    2: "Lesser",
    3: "",
    4: "Greater",
    5: "Superior",
    6: "Supreme",
}


def _tiered_name(base: str, tier: int, tier_names: dict[int, str]) -> str:
    """Build a display name like 'Minor Potion of Healing'."""
    prefix = tier_names.get(tier, "")
    if prefix:
        return f"{prefix} Potion of {base}"
    return f"Potion of {base}"


# ---------------------------------------------------------------------------
# Dominant tag calculation
# ---------------------------------------------------------------------------


def calculate_dominant_tag(ingredients: Sequence[Ingredient]) -> str | None:
    """Determine the dominant tag from an ordered sequence of brew ingredients.

    The first ingredient's tags are weighted 3x, the second's 2x, and all
    subsequent ingredients 1x. Highest weighted total wins; alphabetical
    tiebreaker ensures determinism.
    """
    if not ingredients:
        return None

    tag_weights: dict[str, int] = {}
    for idx, ing in enumerate(ingredients):
        weight = 3 if idx == 0 else (2 if idx == 1 else 1)
        for tag in (ing.tag_1, ing.tag_2):
            tag_weights[tag] = tag_weights.get(tag, 0) + weight

    # Sort by weight descending, then alphabetically for tiebreaker
    dominant = sorted(tag_weights.keys(), key=lambda t: (-tag_weights[t], t))[0]
    return dominant


# ---------------------------------------------------------------------------
# Potion determination
# ---------------------------------------------------------------------------


def determine_potion(
    dominant_tag: str, potency: int
) -> tuple[str, int, str] | None:
    """Determine which potion is created from the dominant tag and potency.

    Returns ``(potion_type, effect_value, display_name)`` or ``None`` if
    potency is below the minimum threshold.
    """
    if potency < POTION_MIN_POTENCY:
        return None

    potion_type = TAG_TO_POTION.get(dominant_tag)
    if potion_type is None:
        return None

    # Luminous special case: 200+ creates Revelation instead of Foresight
    if dominant_tag == "Luminous" and potency >= REVELATION_POTENCY:
        return ("revelation", 0, "Potion of Revelation")

    p = potency  # shorthand

    if potion_type == "swiftness":
        val = 1 + (p - 100) // 10
        return (potion_type, val, f"Potion of Swiftness +{val}")

    if potion_type == "dexterity":
        val = 1 + (p - 100) // 10
        return (potion_type, val, f"Potion of Dexterity +{val}")

    if potion_type == "giants_strength":
        val = 1 + (p - 100) // 10
        return (potion_type, val, f"Potion of Giant's Strength +{val}")

    if potion_type == "clarity":
        val = 1 + (p - 100) // 30
        return (potion_type, val, f"Potion of Clarity +{val}")

    if potion_type == "harmony":
        val = 1 + (p - 100) // 20
        return (potion_type, val, f"Potion of Harmony +{val}")

    if potion_type == "fertility":
        if p >= 200:
            val = 3
        elif p >= 150:
            val = 2
        else:
            val = 1
        return (potion_type, val, _tiered_name("Fertility", {1: 1, 2: 2, 3: 3}[val], _TIER_NAMES_3))

    if potion_type == "longevity":
        val = 2 + (p - 100) // 20
        return (potion_type, val, f"Potion of Longevity +{val}")

    if potion_type == "stripping":
        val = min(6, 2 + (p - 100) // 25)
        tier = val - 1  # 2→1, 3→2, 4→3, 5→4, 6→5
        return (potion_type, val, _tiered_name("Stripping", tier, _TIER_NAMES_5))

    if potion_type == "healing":
        val = min(6, 1 + (p - 100) // 20)
        return (potion_type, val, _tiered_name("Healing", val, _TIER_NAMES_6))

    if potion_type == "mutation":
        val = min(15, ((p - 100) // 15) * 3)
        tier = val // 3 + 1  # 0→1, 3→2, 6→3, 9→4, 12→5, 15→6
        return (potion_type, val, _tiered_name("Mutation", tier, _TIER_NAMES_6))

    if potion_type == "fortification":
        val = 70 + round((p - 100) * 0.6)
        val = min(130, val)
        # Map to 5 tiers based on ranges
        if val < 82:
            tier = 1
        elif val < 94:
            tier = 2
        elif val < 106:
            tier = 3
        elif val < 118:
            tier = 4
        else:
            tier = 5
        return (potion_type, val, _tiered_name("Fortification", tier, _TIER_NAMES_5))

    if potion_type == "foresight":
        return (potion_type, 0, "Potion of Foresight")

    return None


# ---------------------------------------------------------------------------
# Potion description helper (for /potion list)
# ---------------------------------------------------------------------------

POTION_DESCRIPTIONS: dict[str, str] = {
    "swiftness": "Temporarily boosts a racer's Speed for 1 race.",
    "dexterity": "Temporarily boosts a racer's Cornering for 1 race.",
    "giants_strength": "Temporarily boosts a racer's Stamina for 1 race.",
    "clarity": "Temporarily boosts a racer's Mood for 1 race.",
    "harmony": "Temporarily boosts all three stats for 1 race.",
    "fertility": "Restores breeding slot(s) to a female racer.",
    "longevity": "Extends a racer's career length and peak period.",
    "stripping": "Reroll a racer's temperament with choices to pick from.",
    "healing": "Reduces a racer's injury recovery time.",
    "mutation": "Randomly reshuffles one of a racer's stats.",
    "fortification": "Raises the minimum explosion threshold of your next brew.",
    "foresight": "Reveals the explosion threshold when you start your next brew.",
    "revelation": "Permanently reveals the hidden tags of one ingredient.",
}

# All 7 temperaments available in Downtime Derby
ALL_TEMPERAMENTS = [
    "Agile", "Reckless", "Tactical", "Burly", "Steady", "Sharpshift", "Quirky",
]

# Potion types that target a racer
RACER_POTIONS = {
    "swiftness", "dexterity", "giants_strength", "clarity", "harmony",
    "fertility", "longevity", "stripping", "healing", "mutation",
}

# Potion types that target an ingredient (revelation)
INGREDIENT_POTIONS = {"revelation"}

# Potion types with no target (brew effects)
NO_TARGET_POTIONS = {"fortification", "foresight"}

# Stat buff potion types → buff_type for RacerBuff
STAT_BUFF_MAP: dict[str, str] = {
    "swiftness": "speed",
    "dexterity": "cornering",
    "giants_strength": "stamina",
    "clarity": "mood",
    "harmony": "all_stats",
}


# ---------------------------------------------------------------------------
# Effect application functions
# ---------------------------------------------------------------------------


def generate_stripping_choices(
    current_temperament: str, num_choices: int, seed: int
) -> list[str]:
    """Generate random temperament choices for a Stripping potion.

    Returns ``num_choices`` temperaments excluding the current one.
    """
    available = [t for t in ALL_TEMPERAMENTS if t != current_temperament]
    rng = random.Random(seed)
    num_choices = min(num_choices, len(available))
    return rng.sample(available, num_choices)


def apply_mutation(
    speed: int, cornering: int, stamina: int, floor_value: int, seed: int
) -> tuple[str, int, int]:
    """Pick a random stat and set it to a random value in [floor_value, 31].

    Returns ``(stat_name, old_value, new_value)``.
    """
    rng = random.Random(seed)
    stats = {"speed": speed, "cornering": cornering, "stamina": stamina}
    stat_name = rng.choice(list(stats.keys()))
    old_value = stats[stat_name]
    new_value = rng.randint(floor_value, 31)
    return (stat_name, old_value, new_value)
