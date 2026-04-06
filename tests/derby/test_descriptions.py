"""Tests for LLM-powered racer description generation."""

import types
from unittest.mock import MagicMock, patch

import pytest

from derby import descriptions


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the lazy-loaded client between tests."""
    descriptions._client = None
    yield
    descriptions._client = None


def _mock_response(text: str):
    """Build a mock Anthropic response."""
    content_block = types.SimpleNamespace(text=text)
    return types.SimpleNamespace(content=[content_block])


@pytest.mark.asyncio
async def test_generate_description_returns_string():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(
        "A sleek, midnight-blue lizard with iridescent scales and powerful haunches. "
        "Its long, whip-like tail is tipped with a crackling energy node, and a jagged "
        "scar runs across its left flank."
    )
    descriptions._client = mock_client

    result = await descriptions.generate_description(
        name="Thunderhoof",
        speed=25,
        cornering=15,
        stamina=20,
        temperament="Bold",
        gender="M",
        flavor="cyberpunk racing lizards",
    )

    assert result is not None
    assert "midnight-blue" in result
    assert mock_client.messages.create.called


@pytest.mark.asyncio
async def test_generate_description_respects_flavor():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response("A sturdy warhorse.")
    descriptions._client = mock_client

    await descriptions.generate_description(
        name="Blaze",
        speed=10,
        cornering=10,
        stamina=10,
        temperament="Quirky",
        gender="F",
        flavor="enchanted warhorses",
    )

    # Check that flavor appears in the system prompt
    call_kwargs = mock_client.messages.create.call_args
    system_prompt = call_kwargs.kwargs.get("system") or call_kwargs[1].get("system")
    assert "enchanted warhorses" in system_prompt


@pytest.mark.asyncio
async def test_generate_description_foal_blending():
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response(
        "A compact young lizard with its sire's cobalt scales and its dam's "
        "distinctive amber eyes."
    )
    descriptions._client = mock_client

    result = await descriptions.generate_description(
        name="Baby",
        speed=15,
        cornering=15,
        stamina=15,
        temperament="Bold",
        gender="F",
        flavor="cyberpunk racing lizards",
        sire_desc="A massive cobalt-scaled lizard with chrome implants.",
        dam_desc="A lithe amber-eyed lizard with bioluminescent markings.",
    )

    assert result is not None

    # Check parent descriptions appear in system prompt
    call_kwargs = mock_client.messages.create.call_args
    system_prompt = call_kwargs.kwargs.get("system") or call_kwargs[1].get("system")
    assert "cobalt-scaled" in system_prompt
    assert "amber-eyed" in system_prompt
    assert "foal" in system_prompt.lower()


@pytest.mark.asyncio
async def test_generate_description_failure_returns_none():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API error")
    descriptions._client = mock_client

    result = await descriptions.generate_description(
        name="Crasher",
        speed=10,
        cornering=10,
        stamina=10,
        temperament="Quirky",
        gender="M",
        flavor="horses",
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_description_no_api_key():
    """Without an API key or client, generation returns None."""
    descriptions._client = None

    with patch.dict("os.environ", {}, clear=True):
        with patch("derby.descriptions._get_client", return_value=None):
            result = await descriptions.generate_description(
                name="NoKey",
                speed=10,
                cornering=10,
                stamina=10,
                temperament="Quirky",
                gender="M",
                flavor="horses",
            )

    assert result is None
