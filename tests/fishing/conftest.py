"""Shared fixtures for fishing tests.

Provides:
  - sessionmaker          in-memory sqlite, all fishing tables created
  - sample_location       a hand-crafted location dict matching the YAML schema
  - sample_rod            a basic rod dict matching the rods.yaml schema
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db_base import Base
import fishing.models  # noqa: F401 — register fishing tables on Base


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path):
    """Per-test in-memory sqlite sessionmaker with the schema applied."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sm


@pytest.fixture
def sample_location() -> dict[str, Any]:
    """A small location dict modeled after fishing/locations/calm_pond.yaml.

    Three fish (one per rarity tier we care about), one trash entry, one
    legendary. ``required_bait`` and ``preferred_bait`` are exercised so
    weight-filter tests can target them.
    """
    return {
        "name": "Test Pond",
        "description": "Synthetic location for unit tests.",
        "theme": "pond",
        "skill_level": 1,
        "base_cast_time": 900,
        "fish": [
            {
                "name": "Bluegill",
                "rarity": "common",
                "value_range": [1, 4],
                "length_range": [4, 10],
                "preferred_bait": "worm",
                "weight": 40,
            },
            {
                "name": "Silverscale Trout",
                "rarity": "uncommon",
                "value_range": [5, 10],
                "length_range": [10, 20],
                "preferred_bait": "insect",
                "weight": 15,
            },
            {
                "name": "Glasswing Pike",
                "rarity": "rare",
                "value_range": [20, 40],
                "length_range": [25, 40],
                "required_bait": "shiny_lure",
                "weight": 5,
            },
            {
                "name": "Old One",
                "rarity": "legendary",
                "value_range": [200, 500],
                "length_range": [60, 100],
                "weight": 1,
            },
        ],
        "trash": [
            {"name": "Old Boot", "value": 0, "weight": 10},
        ],
    }


@pytest.fixture
def sample_rod() -> dict[str, Any]:
    """A basic rod dict matching the rods.yaml schema."""
    return {
        "id": "basic",
        "name": "Basic Rod",
        "cost": 0,
        "tier": 0,
        "cast_reduction": 0.0,
        "trash_multiplier": 1.0,
        "rare_boost": 0.0,
    }
