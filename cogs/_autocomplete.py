"""Shared helpers for slash-command autocomplete callbacks.

The vast majority of our autocompletes share one shape: take an iterable of
items, filter by case-insensitive substring match against some haystack
string (usually the item's name), and return up to 25 ``app_commands.Choice``
objects with a per-item label and value. This module collapses that pattern
to a single call.

Module name starts with ``_`` so ``bot.load_cogs`` skips it — it is a helper,
not a Cog.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from discord import app_commands

CHOICE_LIMIT = 25  # Discord's max number of autocomplete choices
LABEL_LIMIT = 100  # Discord's max length per choice label


def filter_choices(
    items: Iterable[Any],
    current: str,
    *,
    label: Callable[[Any], str],
    value: Callable[[Any], int | str],
    match: Callable[[Any], str] | None = None,
    limit: int = CHOICE_LIMIT,
) -> list[app_commands.Choice]:
    """Return up to ``limit`` Choices for items whose haystack matches ``current``.

    Parameters
    ----------
    items:
        Iterable of source items (e.g. ORM rows, dict entries, plain strings).
    current:
        The user's in-progress text. Matching is case-insensitive substring.
    label:
        Builds the user-facing label. Auto-truncated to 100 chars.
    value:
        Builds the value submitted when the user selects this choice.
    match:
        Builds the haystack string to match against. When ``None`` (default),
        the ``label`` output is used. Pass a separate ``match`` when items
        should be searchable by alternative text (e.g. dungeon key + name).
    limit:
        Maximum choices to return. Defaults to Discord's 25-item ceiling.
    """
    needle = current.lower()
    haystack = match if match is not None else label
    choices: list[app_commands.Choice] = []
    for item in items:
        if needle in haystack(item).lower():
            choices.append(
                app_commands.Choice(name=label(item)[:LABEL_LIMIT], value=value(item))
            )
            if len(choices) >= limit:
                break
    return choices
