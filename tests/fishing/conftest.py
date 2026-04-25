"""Shared fixtures for fishing tests.

Provides:
  - sessionmaker          in-memory sqlite, all fishing tables created
  - sample_location       a hand-crafted location dict matching the YAML schema
  - sample_rod            a basic rod dict matching the rods.yaml schema
  - mock_runner           an ActiveFishingRunner wired to a stub bot
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db_base import Base
import fishing.models  # noqa: F401 — register fishing tables on Base
from fishing.active import ActiveFishingRunner


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


@pytest.fixture
def mock_runner(sessionmaker):
    """A real ActiveFishingRunner backed by a stub bot.

    The bot exposes ``scheduler.sessionmaker`` (real, in-memory) so
    handler DB writes hit a real DB. ``get_channel`` and ``get_guild``
    return ``None`` by default — handlers that need a posting target
    should patch ``runner.bot.get_channel`` to return a mock channel
    (use ``make_mock_channel`` below).

    Tests own the LLM mocks: patch the relevant ``fishing.handlers.<rarity>.llm.*``
    function with ``unittest.mock.patch``.
    """
    bot = SimpleNamespace(
        scheduler=SimpleNamespace(sessionmaker=sessionmaker),
        get_channel=lambda _id: None,
        get_guild=lambda _id: None,
    )
    return ActiveFishingRunner(bot)


@pytest.fixture
def dummy_fs():
    """A read-only stub of a FishingSession suitable for handler tests.

    Real DB sessions live in test_repositories.py — handler tests only
    need ``fs.user_id``/``guild_id``/``location_name``/``thread_id``/
    ``channel_id``/``angler_name``.
    """
    return SimpleNamespace(
        id=1,
        user_id=42,
        guild_id=100,
        location_name="calm_pond",
        thread_id=None,
        channel_id=999,
        angler_name="TestAngler",
        bait_remaining=5,
        bait_type="worm",
        active=True,
        mode="active",
    )


def make_mock_channel():
    """A Discord channel/thread stub that satisfies ``await channel.send(...)``.

    Returns a MagicMock channel where ``send`` is an AsyncMock returning
    a message stub whose ``edit`` is also async. Use in tests that need
    handlers to make it past ``_get_post_target``:

        runner.bot.get_channel = lambda _: make_mock_channel()
    """
    message = MagicMock()
    message.edit = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=message)
    return channel
