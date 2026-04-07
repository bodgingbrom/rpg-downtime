"""Pure game logic for Potion Panic brewing mechanics."""

from __future__ import annotations

import random
from typing import Sequence

from .models import DangerousTriple, Ingredient

# ---------------------------------------------------------------------------
# Flavor text pools
# ---------------------------------------------------------------------------

FLAVOR_SAFE = [
    "The cauldron simmers peacefully. A pleasant aroma fills the air.",
    "The mixture settles into a smooth, even consistency.",
    "A faint shimmer plays across the surface. All seems well.",
]

FLAVOR_UNEASY = [
    "A thin wisp of acrid smoke curls upward.",
    "The liquid darkens slightly at the edges.",
    "You hear a faint hiss from somewhere deep in the mixture.",
]

FLAVOR_CONCERNING = [
    "The cauldron shudders. Bubbles rise aggressively.",
    "The mixture changes color rapidly before settling on something unsettling.",
    "A sharp smell hits your nose. This is getting volatile.",
]

FLAVOR_DANGEROUS = [
    "The cauldron is ROILING. The liquid is climbing the sides.",
    "Something deep in the mixture is *ticking*.",
    "The air around the cauldron shimmers with heat. One wrong move...",
    "Cracks of light appear in the brew. It's barely holding together.",
]

EXPLOSION_ELEMENTS = ["flames", "acid", "shadow", "light", "crystalline shrapnel"]

EXPLOSION_TEXTS = [
    "**BOOM.** The cauldron erupts in a spectacular shower of {element}. Your brew — and your ingredients — are gone.",
    "A deafening crack. The mixture tears itself apart. You're left standing in the smoke with nothing.",
    "The cauldron shatters. A shockwave of {element} knocks you back. Everything is lost.",
]

CASHOUT_LOW = "You bottle a murky, underwhelming concoction. It'll sell, barely."
CASHOUT_MEDIUM = "The potion glows with genuine potency. A solid brew."
CASHOUT_HIGH = "The bottled potion hums with power. This is masterwork-grade alchemy."
CASHOUT_LEGENDARY = "The potion radiates energy so intense it illuminates the room. Alchemists would kill for this."

# ---------------------------------------------------------------------------
# Instability color mapping
# ---------------------------------------------------------------------------

COLOR_SAFE = 0x2ECC71
COLOR_UNEASY = 0xF1C40F
COLOR_CONCERNING = 0xE67E22
COLOR_DANGEROUS = 0xE74C3C
COLOR_EXPLODED = 0x1A1A1A
COLOR_CASHOUT = 0xF1C40F

# ---------------------------------------------------------------------------
# Payout tiers: (max_potency, multiplier)
# ---------------------------------------------------------------------------

PAYOUT_TIERS = [
    (10, 0.5),
    (30, 1.0),
    (60, 1.5),
    (100, 2.5),
    (150, 4.0),
    (200, 6.0),
]
PAYOUT_LEGENDARY_MULTIPLIER = 8.0


# ---------------------------------------------------------------------------
# Core calculations
# ---------------------------------------------------------------------------


def calculate_potency(
    new_ingredient: Ingredient,
    cauldron_ingredients: Sequence[Ingredient],
    base_potency: int = 10,
    min_no_match: int = 2,
) -> int:
    """Calculate potency gained from adding an ingredient to the cauldron."""
    new_tags = (new_ingredient.tag_1, new_ingredient.tag_2)
    total_matches = 0
    for tag in new_tags:
        for existing in cauldron_ingredients:
            if tag in (existing.tag_1, existing.tag_2):
                total_matches += 1
    if total_matches == 0:
        return min_no_match
    return base_potency * total_matches


def calculate_instability(
    all_tags: set[str],
    dangerous_triples: Sequence[DangerousTriple],
) -> int:
    """Calculate total instability from all dangerous triples present."""
    total = 0
    for triple in dangerous_triples:
        if {triple.tag_1, triple.tag_2, triple.tag_3} <= all_tags:
            total += triple.instability_value
    return total


def check_explosion(instability: int, threshold: int) -> bool:
    """Return True if the brew explodes."""
    return instability >= threshold


def calculate_payout(potency: int) -> int:
    """Calculate coin payout for a given potency using the tiered curve."""
    for max_potency, multiplier in PAYOUT_TIERS:
        if potency <= max_potency:
            return round(potency * multiplier)
    return round(potency * PAYOUT_LEGENDARY_MULTIPLIER)


# ---------------------------------------------------------------------------
# Flavor text and color helpers
# ---------------------------------------------------------------------------


def get_instability_color(instability: int) -> int:
    """Return embed color hex based on instability level."""
    if instability == 0:
        return COLOR_SAFE
    if instability <= 30:
        return COLOR_UNEASY
    if instability <= 60:
        return COLOR_CONCERNING
    return COLOR_DANGEROUS


def get_flavor_text(instability: int) -> str:
    """Return a random flavor string for the current instability tier."""
    if instability == 0:
        return random.choice(FLAVOR_SAFE)
    if instability <= 30:
        return random.choice(FLAVOR_UNEASY)
    if instability <= 60:
        return random.choice(FLAVOR_CONCERNING)
    return random.choice(FLAVOR_DANGEROUS)


def get_explosion_text() -> str:
    """Return a random explosion description."""
    text = random.choice(EXPLOSION_TEXTS)
    element = random.choice(EXPLOSION_ELEMENTS)
    return text.format(element=element)


def get_cashout_text(potency: int) -> str:
    """Return cashout flavor text based on potency tier."""
    if potency < 30:
        return CASHOUT_LOW
    if potency <= 100:
        return CASHOUT_MEDIUM
    if potency <= 200:
        return CASHOUT_HIGH
    return CASHOUT_LEGENDARY


def collect_cauldron_tags(ingredients: Sequence[Ingredient]) -> set[str]:
    """Collect all unique tags from a sequence of ingredients."""
    tags: set[str] = set()
    for ing in ingredients:
        tags.add(ing.tag_1)
        tags.add(ing.tag_2)
    return tags
