"""Tests for fishing/handlers/{common,uncommon,rare,legendary}.py.

Scope is the structural / safety paths that don't require simulating a
real player interaction:

  - Channel-missing branch returns the rarity's "no UI" fallback
    (commons catch anyway; everything else escapes).
  - LLM-unavailable branch escapes (or for commons, catches without
    flavor text).
  - Legendary side effects: a new legendary row is created on first
    encounter, even when subsequent dialogue fails.

The full success path (CONVINCED dialogue, judged-passing haiku, etc.)
needs view callback simulation that's brittle; smoke-test by running
the bot.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fishing import repositories as fish_repo
from fishing.handlers import (
    handle_common,
    handle_legendary,
    handle_rare,
    handle_uncommon,
)
from tests.fishing.conftest import make_mock_channel


@pytest.fixture
def catch_common():
    return {"name": "Bluegill", "rarity": "common", "value": 3, "length": 6}


@pytest.fixture
def catch_uncommon():
    return {"name": "Silverscale Trout", "rarity": "uncommon", "value": 8, "length": 14}


@pytest.fixture
def catch_rare():
    return {"name": "Glasswing Pike", "rarity": "rare", "value": 30, "length": 32}


@pytest.fixture
def catch_legendary():
    return {"name": "Old One", "rarity": "legendary", "value": 400, "length": 80}


# ---------------------------------------------------------------------------
# Channel-missing fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_common_returns_true_when_channel_missing(
    mock_runner, dummy_fs, sample_location, catch_common,
):
    """Commons always catch — even if there's nowhere to post the prompt."""
    # mock_runner.bot.get_channel returns None by default
    success = await handle_common(mock_runner, dummy_fs, catch_common, sample_location)
    assert success is True


@pytest.mark.asyncio
async def test_handle_uncommon_returns_false_when_channel_missing(
    mock_runner, dummy_fs, sample_location, catch_uncommon,
):
    """No UI → fail-closed escape (uncommons need the vibe-check interaction)."""
    success = await handle_uncommon(mock_runner, dummy_fs, catch_uncommon, sample_location)
    assert success is False


@pytest.mark.asyncio
async def test_handle_rare_returns_false_when_channel_missing(
    mock_runner, dummy_fs, sample_location, catch_rare,
):
    success = await handle_rare(mock_runner, dummy_fs, catch_rare, sample_location)
    assert success is False


@pytest.mark.asyncio
async def test_handle_legendary_returns_false_when_channel_missing(
    mock_runner, dummy_fs, sample_location, catch_legendary,
):
    success = await handle_legendary(
        mock_runner, dummy_fs, catch_legendary, sample_location,
    )
    assert success is False


# ---------------------------------------------------------------------------
# LLM-unavailable escape paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_uncommon_escapes_when_passage_unavailable(
    mock_runner, dummy_fs, sample_location, catch_uncommon,
):
    """Vibe passage generation returning None must escape (not auto-pass)."""
    mock_runner.bot.get_channel = lambda _id: make_mock_channel()

    with patch(
        "fishing.handlers.uncommon.llm.generate_vibe_passage",
        return_value=None,
    ):
        success = await handle_uncommon(
            mock_runner, dummy_fs, catch_uncommon, sample_location,
        )
    assert success is False


@pytest.mark.asyncio
async def test_handle_rare_escapes_when_haiku_unavailable(
    mock_runner, dummy_fs, sample_location, catch_rare,
):
    """Full-haiku generation returning None must escape."""
    mock_runner.bot.get_channel = lambda _id: make_mock_channel()

    with patch(
        "fishing.handlers.rare.llm.generate_full_haiku",
        return_value=None,
    ):
        success = await handle_rare(mock_runner, dummy_fs, catch_rare, sample_location)
    assert success is False


@pytest.mark.asyncio
async def test_handle_legendary_escapes_when_no_active_and_generate_fails(
    mock_runner, dummy_fs, sample_location, catch_legendary,
):
    """No active legendary + generator returns None → escape, no row created."""
    mock_runner.bot.get_channel = lambda _id: make_mock_channel()

    with patch(
        "fishing.handlers.legendary.llm.generate_legendary",
        return_value=None,
    ):
        success = await handle_legendary(
            mock_runner, dummy_fs, catch_legendary, sample_location,
        )
    assert success is False

    # No legendary row was created
    async with mock_runner.bot.scheduler.sessionmaker() as session:
        active = await fish_repo.get_active_legendary(
            session, dummy_fs.guild_id, dummy_fs.location_name,
        )
    assert active is None


@pytest.mark.asyncio
async def test_handle_legendary_creates_legendary_then_escapes_when_line_fails(
    mock_runner, dummy_fs, sample_location, catch_legendary,
):
    """LLM available for the introduction generator, dies before the dialogue
    starts. The legendary row should still be created (so future encounters
    can find it) but the encounter returns False."""
    mock_runner.bot.get_channel = lambda _id: make_mock_channel()

    with (
        patch(
            "fishing.handlers.legendary.llm.generate_legendary",
            return_value=("Whisperfin", "A patient ancient who tests anglers."),
        ),
        patch(
            "fishing.handlers.legendary.llm.generate_legendary_line",
            return_value=None,
        ),
        patch(
            "fishing.handlers.legendary.llm.summarize_encounter",
            return_value="Spoke briefly.",
        ),
    ):
        success = await handle_legendary(
            mock_runner, dummy_fs, catch_legendary, sample_location,
        )
    assert success is False

    # Legendary row exists in DB with the generated name/personality
    async with mock_runner.bot.scheduler.sessionmaker() as session:
        active = await fish_repo.get_active_legendary(
            session, dummy_fs.guild_id, dummy_fs.location_name,
        )
    assert active is not None
    assert active.name == "Whisperfin"
    assert active.personality == "A patient ancient who tests anglers."
    assert active.species_name == "Old One"


@pytest.mark.asyncio
async def test_handle_legendary_uses_existing_active_legendary(
    mock_runner, dummy_fs, sample_location, catch_legendary,
):
    """When an active legendary already exists, generate_legendary is NOT
    called for a new one — we reuse the persistent character."""
    from datetime import datetime, timezone

    async with mock_runner.bot.scheduler.sessionmaker() as session:
        await fish_repo.create_legendary(
            session,
            guild_id=dummy_fs.guild_id, location_name=dummy_fs.location_name,
            species_name="Old One", name="ExistingFin",
            personality="Already here.",
            created_at=datetime.now(timezone.utc),
        )

    mock_runner.bot.get_channel = lambda _id: make_mock_channel()

    with (
        patch(
            "fishing.handlers.legendary.llm.generate_legendary",
        ) as gen_mock,
        patch(
            "fishing.handlers.legendary.llm.generate_legendary_line",
            return_value=None,  # bail out of the dialogue loop immediately
        ),
        patch(
            "fishing.handlers.legendary.llm.summarize_encounter",
            return_value="Heard nothing.",
        ),
    ):
        await handle_legendary(
            mock_runner, dummy_fs, catch_legendary, sample_location,
        )

    # generate_legendary should NOT have been called — there was already
    # an active one (ExistingFin)
    gen_mock.assert_not_called()
