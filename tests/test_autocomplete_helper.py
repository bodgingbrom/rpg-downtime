"""Unit tests for cogs/_autocomplete.py — the filter_choices helper."""

from __future__ import annotations

import types

import pytest

from cogs._autocomplete import CHOICE_LIMIT, LABEL_LIMIT, filter_choices


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def test_returns_empty_when_no_matches():
    items = [_ns(name="Apple"), _ns(name="Banana")]
    out = filter_choices(items, "zzz", label=lambda i: i.name, value=lambda i: i.name)
    assert out == []


def test_case_insensitive_substring_match():
    items = [_ns(name="Apple"), _ns(name="apricot"), _ns(name="Banana")]
    out = filter_choices(items, "AP", label=lambda i: i.name, value=lambda i: i.name)
    names = [c.name for c in out]
    assert "Apple" in names
    assert "apricot" in names
    assert "Banana" not in names


def test_caps_at_limit():
    items = [_ns(name=f"item-{n}") for n in range(50)]
    out = filter_choices(items, "item", label=lambda i: i.name, value=lambda i: i.name)
    assert len(out) == CHOICE_LIMIT


def test_custom_limit():
    items = [_ns(name=f"item-{n}") for n in range(50)]
    out = filter_choices(
        items, "item", label=lambda i: i.name, value=lambda i: i.name, limit=5,
    )
    assert len(out) == 5


def test_label_truncated_to_discord_max():
    long_name = "x" * 200
    items = [_ns(name=long_name)]
    out = filter_choices(items, "x", label=lambda i: i.name, value=lambda i: i.name)
    assert len(out) == 1
    assert len(out[0].name) == LABEL_LIMIT


def test_match_callback_uses_separate_haystack():
    items = [
        _ns(name="Pretty Name", key="ugly_key"),
        _ns(name="Other", key="zebra"),
    ]
    out = filter_choices(
        items,
        "ugly",
        label=lambda i: i.name,
        value=lambda i: i.key,
        match=lambda i: i.key,
    )
    assert [c.value for c in out] == ["ugly_key"]


def test_value_can_be_int_or_str():
    items = [_ns(name="A", id=1), _ns(name="B", id=2)]
    int_choices = filter_choices(
        items, "", label=lambda i: i.name, value=lambda i: i.id,
    )
    assert [c.value for c in int_choices] == [1, 2]

    str_choices = filter_choices(
        items, "", label=lambda i: i.name, value=lambda i: str(i.id),
    )
    assert [c.value for c in str_choices] == ["1", "2"]


def test_works_with_dict_items():
    data = {"key1": {"name": "First"}, "key2": {"name": "Second"}}
    out = filter_choices(
        data.items(),
        "First",
        label=lambda kv: kv[1]["name"],
        value=lambda kv: kv[0],
    )
    assert len(out) == 1
    assert out[0].name == "First"
    assert out[0].value == "key1"


def test_empty_current_returns_all_up_to_limit():
    items = [_ns(name=f"x{n}") for n in range(10)]
    out = filter_choices(items, "", label=lambda i: i.name, value=lambda i: i.name)
    assert len(out) == 10
