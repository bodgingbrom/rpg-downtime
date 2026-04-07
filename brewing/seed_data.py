"""Seed data for Potion Panic ingredients and dangerous triples."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DangerousTriple, Ingredient

# ---------------------------------------------------------------------------
# Ingredients — 6 free, 15 uncommon, 7 rare
# ---------------------------------------------------------------------------

INGREDIENTS: list[dict] = [
    # Free ingredients (always available, cost 0)
    {
        "name": "Ember Salt",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Thermal",
        "tag_2": "Volatile",
        "flavor_text": "Warm orange crystals that crackle faintly",
    },
    {
        "name": "Moonpetal",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Luminous",
        "tag_2": "Celestial",
        "flavor_text": "A pale flower that glows in dim light",
    },
    {
        "name": "Wraith Moss",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Spectral",
        "tag_2": "Verdant",
        "flavor_text": "Translucent moss harvested from old graves",
    },
    {
        "name": "Iron Root",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Calcified",
        "tag_2": "Stabilizing",
        "flavor_text": "Dense, petrified root that smells of metal",
    },
    {
        "name": "Gloomcap",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Abyssal",
        "tag_2": "Mutagenic",
        "flavor_text": "A dark mushroom with shifting patterns",
    },
    {
        "name": "Brimstone Dust",
        "rarity": "free",
        "base_cost": 0,
        "tag_1": "Thermal",
        "tag_2": "Corrosive",
        "flavor_text": "Acrid yellow powder, stings the nostrils",
    },
    # Uncommon ingredients (shop rotation, 10-20 coins)
    {
        "name": "Singing Quartz",
        "rarity": "uncommon",
        "base_cost": 12,
        "tag_1": "Resonant",
        "tag_2": "Calcified",
        "flavor_text": "A crystal shard that hums when touched",
    },
    {
        "name": "Voidbloom",
        "rarity": "uncommon",
        "base_cost": 15,
        "tag_1": "Abyssal",
        "tag_2": "Verdant",
        "flavor_text": "A flower that blooms only in total darkness",
    },
    {
        "name": "Ashenworm Silk",
        "rarity": "uncommon",
        "base_cost": 10,
        "tag_1": "Thermal",
        "tag_2": "Stabilizing",
        "flavor_text": "Fireproof thread from ashenworm cocoons",
    },
    {
        "name": "Ghostlight Oil",
        "rarity": "uncommon",
        "base_cost": 15,
        "tag_1": "Spectral",
        "tag_2": "Luminous",
        "flavor_text": "Faintly glowing oil that passes through fingers",
    },
    {
        "name": "Rot Blossom",
        "rarity": "uncommon",
        "base_cost": 12,
        "tag_1": "Corrosive",
        "tag_2": "Verdant",
        "flavor_text": "A pungent flower in perpetual decomposition",
    },
    {
        "name": "Starite Shard",
        "rarity": "uncommon",
        "base_cost": 18,
        "tag_1": "Celestial",
        "tag_2": "Resonant",
        "flavor_text": "Fragment of a fallen star, vibrates at dawn",
    },
    {
        "name": "Flickerstone",
        "rarity": "uncommon",
        "base_cost": 14,
        "tag_1": "Volatile",
        "tag_2": "Luminous",
        "flavor_text": "A stone that strobes with erratic light",
    },
    {
        "name": "Marshglow Lichen",
        "rarity": "uncommon",
        "base_cost": 10,
        "tag_1": "Verdant",
        "tag_2": "Luminous",
        "flavor_text": "Bioluminescent growth from deep swamps",
    },
    {
        "name": "Echo Bone",
        "rarity": "uncommon",
        "base_cost": 16,
        "tag_1": "Resonant",
        "tag_2": "Spectral",
        "flavor_text": "Skeletal fragment that repeats nearby sounds",
    },
    {
        "name": "Nullite Powder",
        "rarity": "uncommon",
        "base_cost": 15,
        "tag_1": "Stabilizing",
        "tag_2": "Abyssal",
        "flavor_text": "Dull gray powder that absorbs nearby energy",
    },
    {
        "name": "Tremor Grub",
        "rarity": "uncommon",
        "base_cost": 18,
        "tag_1": "Mutagenic",
        "tag_2": "Resonant",
        "flavor_text": "A larva that vibrates constantly",
    },
    {
        "name": "Duskfen Mud",
        "rarity": "uncommon",
        "base_cost": 12,
        "tag_1": "Abyssal",
        "tag_2": "Calcified",
        "flavor_text": "Dense black clay from lightless swamps",
    },
    {
        "name": "Scorchcap Spore",
        "rarity": "uncommon",
        "base_cost": 20,
        "tag_1": "Thermal",
        "tag_2": "Mutagenic",
        "flavor_text": "Spores that ignite briefly when released",
    },
    {
        "name": "Prism Beetle Shell",
        "rarity": "uncommon",
        "base_cost": 14,
        "tag_1": "Luminous",
        "tag_2": "Calcified",
        "flavor_text": "Iridescent shell fragments",
    },
    {
        "name": "Coilweed",
        "rarity": "uncommon",
        "base_cost": 10,
        "tag_1": "Stabilizing",
        "tag_2": "Verdant",
        "flavor_text": "A tightly spiraled aquatic plant",
    },
    # Rare ingredients (shop rotation, 30-50 coins)
    {
        "name": "Wyrm's Tear",
        "rarity": "rare",
        "base_cost": 40,
        "tag_1": "Volatile",
        "tag_2": "Celestial",
        "flavor_text": "A pearlescent droplet that burns cold",
    },
    {
        "name": "Hollow King's Sigh",
        "rarity": "rare",
        "base_cost": 35,
        "tag_1": "Spectral",
        "tag_2": "Abyssal",
        "flavor_text": "Bottled breath of a long-dead monarch",
    },
    {
        "name": "Titan Marrow",
        "rarity": "rare",
        "base_cost": 45,
        "tag_1": "Calcified",
        "tag_2": "Mutagenic",
        "flavor_text": "Bone paste from something impossibly large",
    },
    {
        "name": "Phoenix Cinder",
        "rarity": "rare",
        "base_cost": 50,
        "tag_1": "Thermal",
        "tag_2": "Celestial",
        "flavor_text": "A coal that will never fully extinguish",
    },
    {
        "name": "Leviathan Ink",
        "rarity": "rare",
        "base_cost": 40,
        "tag_1": "Corrosive",
        "tag_2": "Abyssal",
        "flavor_text": "Pitch-black liquid that dissolves lesser containers",
    },
    {
        "name": "Harmonic Amber",
        "rarity": "rare",
        "base_cost": 35,
        "tag_1": "Resonant",
        "tag_2": "Stabilizing",
        "flavor_text": "Ancient resin that sings a single perfect note",
    },
    {
        "name": "Chaosbloom Pollen",
        "rarity": "rare",
        "base_cost": 45,
        "tag_1": "Mutagenic",
        "tag_2": "Volatile",
        "flavor_text": "Pollen that changes color every few seconds",
    },
]

# ---------------------------------------------------------------------------
# Dangerous Triples — tag combinations that cause instability
# ---------------------------------------------------------------------------

DANGEROUS_TRIPLES: list[dict] = [
    {
        "tag_1": "Volatile",
        "tag_2": "Thermal",
        "tag_3": "Corrosive",
        "instability_value": 50,
    },
    {
        "tag_1": "Mutagenic",
        "tag_2": "Volatile",
        "tag_3": "Celestial",
        "instability_value": 50,
    },
    {
        "tag_1": "Abyssal",
        "tag_2": "Luminous",
        "tag_3": "Volatile",
        "instability_value": 50,
    },
    {
        "tag_1": "Corrosive",
        "tag_2": "Spectral",
        "tag_3": "Mutagenic",
        "instability_value": 50,
    },
    {
        "tag_1": "Thermal",
        "tag_2": "Abyssal",
        "tag_3": "Resonant",
        "instability_value": 50,
    },
]


async def seed_if_empty(session: AsyncSession) -> None:
    """Insert seed data for ingredients and dangerous triples if tables are empty."""
    ingredient_count = (
        await session.execute(select(func.count()).select_from(Ingredient))
    ).scalar_one()
    if ingredient_count == 0:
        session.add_all([Ingredient(**row) for row in INGREDIENTS])

    triple_count = (
        await session.execute(select(func.count()).select_from(DangerousTriple))
    ).scalar_one()
    if triple_count == 0:
        session.add_all([DangerousTriple(**row) for row in DANGEROUS_TRIPLES])

    await session.commit()
