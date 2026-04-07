"""Date-seeded daily shop rotation for Potion Panic."""

from __future__ import annotations

import random

from .models import Ingredient


def get_daily_shop(date_str: str, all_ingredients: list[Ingredient]) -> list[Ingredient]:
    """Return today's shop ingredients: 4-5 uncommon + 1 rare.

    Uses a date-seeded RNG so the shop is deterministic for a given day.
    Free ingredients are always available and not included here.
    """
    rng = random.Random(f"potion-shop-{date_str}")

    uncommon = [i for i in all_ingredients if i.rarity == "uncommon"]
    rare = [i for i in all_ingredients if i.rarity == "rare"]

    num_uncommon = rng.choice([4, 5])
    shop: list[Ingredient] = rng.sample(uncommon, num_uncommon)
    shop += rng.sample(rare, 1)

    return shop
