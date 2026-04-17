"""Racer appearance rolling and inheritance.

Loads a YAML pool of visual attributes (coloring, build, unique features,
eyes, origin) and provides helpers to roll a fresh appearance for new
racers and inherit attributes from parents when breeding.

The design philosophy: the LLM is great at phrasing but bad at being
consistently weird, so we pick the distinguishing traits here and hand
them to the LLM as structured input. Server owners edit the YAML to
retune the tone of their guild's racers.
"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

logger = logging.getLogger("discord_bot")


# ---------------------------------------------------------------------------
# YAML loader (cached)
# ---------------------------------------------------------------------------

_APPEARANCE_DIR = os.path.dirname(__file__)
_appearance_pool: dict[str, Any] | None = None


def _load_appearance_pool() -> dict[str, Any]:
    """Load and cache the appearance YAML file."""
    global _appearance_pool
    if _appearance_pool is not None:
        return _appearance_pool

    import yaml

    path = os.path.join(_APPEARANCE_DIR, "racer_appearance.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            _appearance_pool = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("racer_appearance.yaml not found — appearance disabled")
        _appearance_pool = {}
    return _appearance_pool


def reload_appearance_pool() -> None:
    """Force reload of the YAML file — used by tests."""
    global _appearance_pool
    _appearance_pool = None


# ---------------------------------------------------------------------------
# Rolling
# ---------------------------------------------------------------------------

# All fields, in a stable order. Origin is last and is never inherited.
APPEARANCE_FIELDS = (
    "color",
    "variation",
    "build",
    "feature_primary",
    "feature_secondary",
    "eyes",
    "origin",
)

# Fields that can be inherited from parents (origin is a life event, not genetic)
HERITABLE_FIELDS = (
    "color",
    "variation",
    "build",
    "feature_primary",
    "feature_secondary",
    "eyes",
)


def roll_appearance(rng: random.Random | None = None) -> dict[str, str]:
    """Roll a fresh set of appearance attributes.

    Returns a dict with keys: color, variation, build, feature_primary,
    feature_secondary, eyes, origin. If the YAML is missing/malformed or
    any unexpected error occurs, returns an empty dict (caller should
    treat as "appearance disabled for this racer").
    """
    rng = rng or random
    pool = _load_appearance_pool()
    if not pool:
        return {}

    try:
        result: dict[str, str] = {}

        # Color is nested: pick a color, then pick one of its variations
        colors = pool.get("colors") or []
        if colors:
            color_entry = rng.choice(colors)
            # Defensive: handle patched-random in tests, malformed YAML, etc.
            if isinstance(color_entry, dict):
                result["color"] = color_entry.get("name", "")
                variations = color_entry.get("variations") or [""]
                result["variation"] = rng.choice(variations)
            else:
                return {}

        # Flat lists
        for field, key in (
            ("build", "builds"),
            ("feature_primary", "features_primary"),
            ("feature_secondary", "features_secondary"),
            ("eyes", "eyes"),
            ("origin", "origins"),
        ):
            items = pool.get(key) or []
            if items:
                choice = rng.choice(items)
                if isinstance(choice, str):
                    result[field] = choice
                else:
                    return {}

        return result
    except Exception:
        logger.exception("Failed to roll appearance")
        return {}


def inherit_appearance(
    sire: dict[str, str] | None,
    dam: dict[str, str] | None,
    rng: random.Random | None = None,
    inherit_chance: float = 0.45,
) -> dict[str, str]:
    """Produce a foal's appearance from two parents.

    For each heritable field: ``inherit_chance`` chance from sire, same
    from dam, remainder fresh-rolled. Origin is **always** fresh-rolled
    — origin is a life event, not a genetic trait.

    If either parent is ``None`` or empty (legacy racer without
    structured appearance), that parent contributes nothing and fresh
    rolls fill the gap.
    """
    rng = rng or random
    sire = sire or {}
    dam = dam or {}

    # Start with a fresh roll — we'll overwrite heritable fields
    foal = roll_appearance(rng)
    if not foal:
        return {}

    for field in HERITABLE_FIELDS:
        roll = rng.random()
        if roll < inherit_chance and sire.get(field):
            foal[field] = sire[field]
        elif roll < inherit_chance * 2 and dam.get(field):
            foal[field] = dam[field]
        # else keep the fresh roll that's already there

    # If color was inherited but variation wasn't, pick any variation
    # from the inherited color's list so they stay coherent.
    if foal.get("color") and foal.get("variation"):
        pool = _load_appearance_pool()
        colors = pool.get("colors") or []
        color_entry = next(
            (c for c in colors if c.get("name") == foal["color"]),
            None,
        )
        if color_entry:
            valid_variations = color_entry.get("variations") or []
            if valid_variations and foal["variation"] not in valid_variations:
                foal["variation"] = rng.choice(valid_variations)

    return foal


# ---------------------------------------------------------------------------
# Serialization helpers (for storing on the Racer model as JSON text)
# ---------------------------------------------------------------------------


def serialize(appearance: dict[str, str]) -> str:
    """Convert an appearance dict to JSON text for DB storage."""
    return json.dumps(appearance, ensure_ascii=False)


def deserialize(text: str | None) -> dict[str, str]:
    """Parse a JSON appearance blob from the DB. Empty/invalid → {}."""
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        logger.warning("Failed to parse appearance JSON: %r", text[:80])
    return {}


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def format_appearance_for_prompt(appearance: dict[str, str]) -> str:
    """Format rolled attributes as a bullet block for the LLM prompt."""
    if not appearance:
        return ""

    lines = []
    color = appearance.get("color", "")
    variation = appearance.get("variation", "")
    if color:
        if variation and variation != "solid":
            lines.append(f"- Coloring: {color}, {variation}")
        else:
            lines.append(f"- Coloring: {color}")
    if appearance.get("build"):
        lines.append(f"- Build: {appearance['build']}")
    if appearance.get("eyes"):
        lines.append(f"- Eyes: {appearance['eyes']}")
    if appearance.get("feature_primary"):
        lines.append(f"- Distinguishing feature: {appearance['feature_primary']}")
    if appearance.get("feature_secondary"):
        lines.append(f"- Distinguishing feature: {appearance['feature_secondary']}")
    if appearance.get("origin"):
        lines.append(f"- Origin: {appearance['origin']}")

    return "\n".join(lines)


def format_appearance_for_display(appearance: dict[str, str]) -> str:
    """Format rolled attributes for display in /stable view.

    Returns a short multi-line string suitable for a Discord embed field.
    """
    if not appearance:
        return ""

    parts = []
    color = appearance.get("color", "")
    variation = appearance.get("variation", "")
    if color:
        if variation and variation != "solid":
            parts.append(f"**Coloring:** {color}, {variation}")
        else:
            parts.append(f"**Coloring:** {color}")
    if appearance.get("build"):
        parts.append(f"**Build:** {appearance['build']}")
    if appearance.get("eyes"):
        parts.append(f"**Eyes:** {appearance['eyes']}")
    if appearance.get("feature_primary"):
        parts.append(f"\u2022 {appearance['feature_primary']}")
    if appearance.get("feature_secondary"):
        parts.append(f"\u2022 {appearance['feature_secondary']}")
    if appearance.get("origin"):
        parts.append(f"*{appearance['origin']}*")

    return "\n".join(parts)
