"""Unit tests for derby.flavor_names module."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from derby import flavor_names
from derby.logic import pick_name


@pytest.fixture(autouse=True)
def reset_client():
    """Reset the module-level client before each test."""
    flavor_names._client = None
    yield
    flavor_names._client = None


def test_load_flavor_names_missing_file():
    """Returns empty list when file doesn't exist."""
    names = flavor_names.load_flavor_names(999999)
    assert names == []


def test_save_and_load_flavor_names(tmp_path, monkeypatch):
    """Round-trip save and load."""
    monkeypatch.setattr(flavor_names, "_FLAVOR_DIR", str(tmp_path))
    test_names = ["Thunderscale", "Neon Fang", "Pixel Dust"]
    flavor_names.save_flavor_names(42, test_names)

    loaded = flavor_names.load_flavor_names(42)
    assert loaded == test_names


def test_delete_flavor_names(tmp_path, monkeypatch):
    """Delete removes the file."""
    monkeypatch.setattr(flavor_names, "_FLAVOR_DIR", str(tmp_path))
    flavor_names.save_flavor_names(42, ["Test"])
    assert os.path.exists(flavor_names.flavor_names_path(42))

    flavor_names.delete_flavor_names(42)
    assert not os.path.exists(flavor_names.flavor_names_path(42))


def test_delete_flavor_names_no_file(tmp_path, monkeypatch):
    """Delete is a no-op when file doesn't exist."""
    monkeypatch.setattr(flavor_names, "_FLAVOR_DIR", str(tmp_path))
    flavor_names.delete_flavor_names(42)  # should not raise


@pytest.mark.asyncio
async def test_generate_flavor_names_no_client():
    """Returns None when no API key is set."""
    with patch.dict(os.environ, {}, clear=True):
        flavor_names._client = None
        result = await flavor_names.generate_flavor_names("cyberpunk lizards")
    assert result is None


@pytest.mark.asyncio
async def test_generate_flavor_names_success():
    """Parses LLM response into a list of names."""
    fake_names = "\n".join([f"Name{i}" for i in range(100)])
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=fake_names)]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    flavor_names._client = mock_client

    result = await flavor_names.generate_flavor_names("cyberpunk lizards")
    assert result is not None
    assert len(result) == 100
    assert result[0] == "Name0"


@pytest.mark.asyncio
async def test_generate_flavor_names_filters_long_names():
    """Names longer than 32 chars are filtered out."""
    names = ["Short"] * 50 + ["A" * 33] * 50 + ["Also Short"] * 10
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="\n".join(names))]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    flavor_names._client = mock_client

    result = await flavor_names.generate_flavor_names("test theme")
    assert result is not None
    assert all(len(n) <= 32 for n in result)
    assert len(result) == 60  # 50 + 10, the 50 long ones filtered


@pytest.mark.asyncio
async def test_generate_flavor_names_too_few():
    """Returns None when LLM returns fewer than 10 valid names."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Only\nThree\nNames")]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    flavor_names._client = mock_client

    result = await flavor_names.generate_flavor_names("test")
    assert result is None


@pytest.mark.asyncio
async def test_generate_flavor_names_exception():
    """Returns None on LLM exception."""
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API error")
    flavor_names._client = mock_client

    result = await flavor_names.generate_flavor_names("test")
    assert result is None


def test_pick_name_includes_flavor_names(tmp_path, monkeypatch):
    """pick_name should draw from both base and flavor name pools."""
    monkeypatch.setattr(flavor_names, "_FLAVOR_DIR", str(tmp_path))
    flavor_names.save_flavor_names(42, ["UniqueFlavorName"])

    # All base names taken
    from derby.logic import _load_names
    base = set(_load_names())

    # Should find the flavor name since all base names are "taken"
    result = pick_name(base, guild_id=42)
    assert result == "UniqueFlavorName"


def test_pick_name_no_flavor_file():
    """pick_name works without a flavor file (just base names)."""
    result = pick_name(set(), guild_id=0)
    assert result is not None  # should pick from base names
