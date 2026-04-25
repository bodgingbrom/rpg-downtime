"""Tests for dungeon/llm.py — system prompt assembly, prompt caching shape,
and graceful fallback when the API is unavailable.

We don't hit the real Anthropic API in CI. Calls that exercise the
remote endpoint are mocked via ``unittest.mock.patch`` on the
``_get_client`` factory — that's the same seam the fishing module's
tests use.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from dungeon import llm as dungeon_llm


# ---------------------------------------------------------------------------
# Fixtures: a small dungeon with a populated background block.
# ---------------------------------------------------------------------------


def _dungeon():
    return {
        "id": "test_dungeon",
        "name": "The Test Dungeon",
        "background": {
            "tone": "Sparse and observational. Restrained.",
            "pitch": "A test fixture pretending to be a dungeon.",
            "lore": "Things test things. Tests test back.",
            "dm_hooks": [
                "A faint hum in the walls",
                "Lichen that responds to light",
            ],
            "style_notes": "Avoid bombast.",
        },
    }


# ---------------------------------------------------------------------------
# is_available — happy paths and failure modes.
# ---------------------------------------------------------------------------


def test_is_available_returns_false_with_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(dungeon_llm, "_client", None)
    assert dungeon_llm.is_available() is False


def test_is_available_returns_true_when_client_present(monkeypatch):
    """A non-None _client makes is_available True regardless of env."""
    monkeypatch.setattr(dungeon_llm, "_client", object())
    assert dungeon_llm.is_available() is True


# ---------------------------------------------------------------------------
# System prompt assembly.
# ---------------------------------------------------------------------------


def test_system_prompt_includes_dungeon_name_and_tone():
    prompt = dungeon_llm._build_system_prompt(_dungeon())
    assert "The Test Dungeon" in prompt
    assert "Sparse and observational" in prompt
    assert "Things test things" in prompt
    assert "Avoid bombast" in prompt
    # DM hooks rendered as a bullet list.
    assert "- A faint hum in the walls" in prompt
    assert "- Lichen that responds to light" in prompt


def test_system_prompt_handles_missing_background():
    prompt = dungeon_llm._build_system_prompt({"name": "Bare Dungeon"})
    assert "Bare Dungeon" in prompt
    # Falls back to placeholders rather than crashing.
    assert "(unspecified" in prompt or "(no" in prompt


def test_system_blocks_uses_ephemeral_cache_by_default(monkeypatch):
    monkeypatch.delenv("DUNGEON_LLM_NO_CACHE", raising=False)
    blocks = dungeon_llm._system_blocks(_dungeon())
    assert isinstance(blocks, list) and len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    # The cached text is the rendered system prompt.
    assert "The Test Dungeon" in blocks[0]["text"]


def test_system_blocks_disables_cache_when_env_set(monkeypatch):
    monkeypatch.setenv("DUNGEON_LLM_NO_CACHE", "1")
    blocks = dungeon_llm._system_blocks(_dungeon())
    assert isinstance(blocks, list) and len(blocks) == 1
    # No cache_control key when caching is disabled.
    assert "cache_control" not in blocks[0]


# ---------------------------------------------------------------------------
# Reward formatting (for the LLM user prompt).
# ---------------------------------------------------------------------------


def test_format_rewards_includes_gold_and_items():
    text = dungeon_llm._format_rewards([
        {"type": "gold", "amount": 12},
        {"type": "item", "item_id": "health_potion"},
    ])
    assert "12 gold" in text
    assert "health_potion" in text


def test_format_rewards_handles_empty_table():
    assert "nothing" in dungeon_llm._format_rewards([]).lower()


def test_format_rewards_includes_narration():
    text = dungeon_llm._format_rewards([
        {"type": "narrate", "text": "A whisper from the walls."},
    ])
    assert "whisper" in text.lower()


# ---------------------------------------------------------------------------
# Feature listing (only passive + visible features make it into the prompt).
# ---------------------------------------------------------------------------


def test_features_for_prompt_only_includes_visible_and_passive():
    text = dungeon_llm._features_for_prompt([
        {"id": "a", "name": "stone bier", "visibility": "visible"},
        {"id": "b", "name": "loose stone", "visibility": "concealed"},
        {"id": "c", "name": "false bottom", "visibility": "secret"},
        {"id": "d", "name": "corpse", "visibility": "passive"},
    ])
    assert "stone bier" in text
    assert "corpse" in text
    # Concealed and secret features are NOT mentioned to the LLM —
    # surfacing them would tip off the player to hidden content.
    assert "loose stone" not in text
    assert "false bottom" not in text


def test_features_for_prompt_handles_empty():
    assert "(none)" in dungeon_llm._features_for_prompt([])
    assert "(none)" in dungeon_llm._features_for_prompt(None)


# ---------------------------------------------------------------------------
# Async narration: returns None when no client; mocks the call when present.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrate_room_intro_returns_none_without_client(monkeypatch):
    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: None)
    out = await dungeon_llm.narrate_room_intro(
        dungeon_data=_dungeon(),
        room_description="A bare alcove.",
        ambient_pool=["A draft."],
        features=[{"id": "x", "name": "thing", "visibility": "visible"}],
    )
    assert out is None


@pytest.mark.asyncio
async def test_narrate_search_outcome_returns_none_without_client(monkeypatch):
    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: None)
    out = await dungeon_llm.narrate_search_outcome(
        dungeon_data=_dungeon(),
        feature_name="chest",
        feature_flavor=None,
        rewards=[{"type": "gold", "amount": 5}],
    )
    assert out is None


@pytest.mark.asyncio
async def test_narrate_room_intro_returns_text_on_success(monkeypatch):
    """Mock the Anthropic client to return canned text; verify wiring."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Cold flagstones. A draft.")]
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: mock_client)

    out = await dungeon_llm.narrate_room_intro(
        dungeon_data=_dungeon(),
        room_description="A bare alcove.",
        ambient_pool=None,
        features=None,
    )
    assert out == "Cold flagstones. A draft."
    # Verify the client was called with the structured cached system prompt.
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert isinstance(call_kwargs["system"], list)
    assert call_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Model name routed through env var.
    assert call_kwargs["model"]


@pytest.mark.asyncio
async def test_narrate_room_intro_returns_none_on_exception(monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API down")
    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: mock_client)

    out = await dungeon_llm.narrate_room_intro(
        dungeon_data=_dungeon(),
        room_description="A bare alcove.",
        ambient_pool=None,
        features=None,
    )
    assert out is None


@pytest.mark.asyncio
async def test_narrate_search_outcome_strips_wrapper_quotes(monkeypatch):
    """If the model wraps its response in quotes, we strip them."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='"You find a coin."')]
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr(dungeon_llm, "_get_client", lambda: mock_client)

    out = await dungeon_llm.narrate_search_outcome(
        dungeon_data=_dungeon(),
        feature_name="chest",
        feature_flavor="A rusted lock.",
        rewards=[{"type": "gold", "amount": 5}],
    )
    assert out == "You find a coin."
