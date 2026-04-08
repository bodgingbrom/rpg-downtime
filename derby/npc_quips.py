"""NPC quip selection and rotation logic."""

from __future__ import annotations

import json
import random


def pick_quip(
    quips: list[str], used_indices: list[int]
) -> tuple[str, list[int]]:
    """Pick a random unused quip, returning the text and updated used list.

    If all quips have been used, resets the used list and picks fresh.
    """
    if not quips:
        return "", used_indices

    available = [i for i in range(len(quips)) if i not in used_indices]
    if not available:
        # All exhausted — reset and pick from full pool
        used_indices = []
        available = list(range(len(quips)))

    idx = random.choice(available)
    used_indices = list(used_indices) + [idx]
    return quips[idx], used_indices


def should_regenerate(quips: list[str], used_indices: list[int]) -> bool:
    """Return True when >= 70% of the quip pool has been used."""
    if not quips:
        return False
    return len(used_indices) >= len(quips) * 0.7


def parse_quips(raw: str) -> list[str]:
    """Parse a JSON string of quips into a list."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def parse_used(raw: str) -> list[int]:
    """Parse a JSON string of used indices into a list."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
