"""LLM-powered racer description generation using Claude Haiku."""

from __future__ import annotations

import asyncio
import logging
import os
import random

logger = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Anthropic client (lazy-loaded so the module imports cleanly without a key)
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    """Return a shared Anthropic client, creating it on first call."""
    global _client
    if _client is None:
        try:
            import anthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("ANTHROPIC_API_KEY not set — description generation disabled")
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — description generation disabled")
            return None
    return _client


MODEL = os.getenv("DESCRIPTION_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 256


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_system_prompt(
    flavor: str,
    sire_desc: str | None = None,
    dam_desc: str | None = None,
    hint: str | None = None,
    has_appearance: bool = False,
) -> str:
    """Build the system prompt for description generation."""
    if has_appearance:
        # New path: the rolled attributes define the creature; LLM just weaves them
        prompt = (
            f"You are describing a racing creature for a Discord game. "
            f"The guild's creature theme: {flavor}.\n\n"
            "The creature's distinguishing traits are listed below. "
            "Weave them into 2-3 flowing sentences (under 60 words). "
            "Don't list the traits robotically — let the prose breathe and "
            "connect them naturally. Every listed trait must appear in the "
            "output, but you may rephrase them for flow.\n\n"
            "RULES:\n"
            "- Describe ONLY visible appearance — what someone would see looking at the creature\n"
            "- Do NOT infer abilities, personality, speed, agility, or competitiveness from appearance\n"
            "- No phrases like 'hints at', 'suggests', 'built for', 'speaks to', 'capable of'\n"
            "- No purple prose — plain, vivid, concrete descriptions\n"
            "- Do NOT include the racer's name, stat numbers, or game mechanics\n"
            "- The origin line should become a brief phrase, not a whole sentence"
        )
    else:
        # Legacy path: LLM has to invent everything
        prompt = (
            f"You are describing a racing creature for a Discord game. "
            f"The guild's creature theme: {flavor}.\n\n"
            "Write exactly 2 short sentences describing ONLY what this racer looks like. "
            "Cover exactly 3 physical features: build/size, coloring/markings, and one unique detail.\n\n"
            "RULES:\n"
            "- Describe ONLY visible appearance — what someone would see looking at the creature\n"
            "- Do NOT infer abilities, personality, speed, agility, or competitiveness from appearance\n"
            "- No phrases like 'hints at', 'suggests', 'built for', 'speaks to', 'capable of'\n"
            "- No embellishments or purple prose — plain, vivid, concrete descriptions\n"
            "- Do NOT include the racer's name, stat numbers, or game mechanics\n"
            "- Keep it under 50 words total"
        )

    if sire_desc and dam_desc and not has_appearance:
        # Parent-blending only used in the legacy path; new path uses
        # structured inheritance upstream (see derby/appearance.py).
        prompt += (
            "\n\nThis is a foal born from two parents. Blend visible traits from "
            "both parents and add one unique feature. It should resemble both "
            "parents but be distinct.\n\n"
            f"Sire's appearance: {sire_desc}\n"
            f"Dam's appearance: {dam_desc}"
        )

    if hint:
        prompt += f"\n\nAdditional direction: {hint}"

    return prompt


def _build_user_prompt(
    name: str,
    speed: int,
    cornering: int,
    stamina: int,
    temperament: str,
    gender: str,
    appearance_block: str = "",
) -> str:
    """Build the user prompt with racer details as flavor hints."""
    base = (
        f"Racer: {name}\n"
        f"Gender: {'Male' if gender == 'M' else 'Female'}\n"
        f"Temperament: {temperament}"
    )
    if appearance_block:
        base += f"\n\nTraits to include:\n{appearance_block}"
    return base


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------


async def generate_description(
    name: str,
    speed: int,
    cornering: int,
    stamina: int,
    temperament: str,
    gender: str,
    flavor: str,
    sire_desc: str | None = None,
    dam_desc: str | None = None,
    hint: str | None = None,
    appearance: dict[str, str] | None = None,
) -> str | None:
    """Generate an LLM description for a racer.

    Returns the description string, or ``None`` if the LLM is unavailable
    or generation fails.  An optional *hint* steers the output
    (e.g. "make him look like a literal ghost").

    When *appearance* is provided (the new structured-attribute system),
    the LLM's job shrinks to phrasing the given traits. When it's None,
    falls back to the legacy prompt that asks the LLM to invent features
    from scratch.
    """
    from . import appearance as appearance_module

    client = _get_client()
    if client is None:
        return None

    has_appearance = bool(appearance)
    appearance_block = (
        appearance_module.format_appearance_for_prompt(appearance)
        if has_appearance
        else ""
    )

    system_prompt = _build_system_prompt(
        flavor, sire_desc, dam_desc, hint=hint, has_appearance=has_appearance,
    )
    user_prompt = _build_user_prompt(
        name, speed, cornering, stamina, temperament, gender,
        appearance_block=appearance_block,
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()

        if not text:
            logger.warning("LLM returned empty description")
            return None

        logger.info(
            "Racer description generated",
            extra={"racer": name, "model": MODEL},
        )
        return text

    except Exception:
        logger.exception("Failed to generate racer description")
        return None


# ---------------------------------------------------------------------------
# Daily reward flavor text
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Daily loot tables (loaded once from YAML)
# ---------------------------------------------------------------------------

_LOOT_DIR = os.path.dirname(__file__)
_daily_loot: dict[str, list[str]] | None = None

_RANK_TIER_LABELS = {
    "D": "worthless junk",
    "C": "modest finds",
    "B": "decent loot",
    "A": "valuable treasure",
    "S": "legendary prizes",
}


def _load_daily_loot() -> dict[str, list[str]]:
    """Load and cache the daily loot table from YAML."""
    global _daily_loot
    if _daily_loot is not None:
        return _daily_loot

    import yaml

    loot_path = os.path.join(_LOOT_DIR, "daily_loot.yaml")
    try:
        with open(loot_path, encoding="utf-8") as f:
            _daily_loot = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("daily_loot.yaml not found — using empty loot tables")
        _daily_loot = {}
    return _daily_loot


def get_random_loot(rank: str, count: int = 1) -> list[str]:
    """Return *count* random loot items for the given rank tier."""
    loot = _load_daily_loot()
    items = loot.get(rank, loot.get("D", []))
    if not items:
        return []
    return random.sample(items, min(count, len(items)))


def get_no_racer_loot() -> str:
    """Return a random fallback flavor snippet for players with no racer."""
    loot = _load_daily_loot()
    items = loot.get("no_racer", [])
    if not items:
        return "some coins from around the track"
    return random.choice(items)


async def generate_daily_flavor(
    racer_name: str,
    rank: str,
    amount: int,
    flavor: str,
) -> str | None:
    """Generate a one-sentence daily reward flavor text using Haiku.

    Returns the text string, or ``None`` if the LLM is unavailable.
    """
    client = _get_client()
    if client is None:
        return None

    # Sample 5 items from the loot table to seed variety
    sampled = get_random_loot(rank, 5)
    tier_label = _RANK_TIER_LABELS.get(rank, "miscellaneous loot")

    if sampled:
        item_hint = f"{tier_label} like: {', '.join(sampled)}"
    else:
        item_hint = tier_label

    system_prompt = (
        f"You are narrating a racing creature game themed around: {flavor}.\n"
        "Write exactly ONE short, fun sentence (under 25 words) about a racing "
        "creature finding/discovering an item while exploring between races.\n"
        f"The item should be {item_hint}.\n"
        "Pick ONE of the suggested items (or invent something similar) — "
        "don't list multiple items.\n"
        "Match the item's value to the coin amount given. "
        "Be creative, specific, and playful. Include the racer's name. "
        "Do NOT mention coins, currency, or game mechanics."
    )

    user_prompt = f"Racer: {racer_name}, Rank: {rank}, Value: {amount} coins"

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=100,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()

        if not text:
            logger.warning("LLM returned empty daily flavor text")
            return None

        return text

    except Exception:
        logger.exception("Failed to generate daily flavor text")
        return None
