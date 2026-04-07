"""LLM-powered racer description generation using Claude Haiku."""

from __future__ import annotations

import asyncio
import logging
import os

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
) -> str:
    """Build the system prompt for description generation."""
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

    if sire_desc and dam_desc:
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
) -> str:
    """Build the user prompt with racer details as flavor hints."""
    # Translate stats into descriptive hints
    def _stat_hint(value: int) -> str:
        if value <= 10:
            return "low"
        if value <= 20:
            return "moderate"
        if value <= 27:
            return "high"
        return "exceptional"

    return (
        f"Racer: {name}\n"
        f"Gender: {'Male' if gender == 'M' else 'Female'}\n"
        f"Temperament: {temperament}"
    )


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
) -> str | None:
    """Generate an LLM description for a racer.

    Returns the description string, or ``None`` if the LLM is unavailable
    or generation fails.  An optional *hint* steers the output
    (e.g. "make him look like a literal ghost").
    """
    client = _get_client()
    if client is None:
        return None

    system_prompt = _build_system_prompt(flavor, sire_desc, dam_desc, hint=hint)
    user_prompt = _build_user_prompt(name, speed, cornering, stamina, temperament, gender)

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

_RANK_ITEM_HINTS = {
    "D": "worthless junk — rusty nails, tattered rags, chipped stones, old bones",
    "C": "modest finds — dull blades, copper coins, cracked potions, frayed rope",
    "B": "decent loot — polished weapons, silver trinkets, useful herbs, leather goods",
    "A": "valuable treasure — enchanted gear, gold artifacts, rare scrolls, fine gems",
    "S": "legendary prizes — magical relics, flawless gemstones, ancient spell scrolls, mythic artifacts",
}


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

    item_hint = _RANK_ITEM_HINTS.get(rank, _RANK_ITEM_HINTS["D"])

    system_prompt = (
        f"You are narrating a racing creature game themed around: {flavor}.\n"
        "Write exactly ONE short, fun sentence (under 25 words) about a racing "
        "creature finding/discovering an item while exploring between races.\n"
        f"The item should be {item_hint}.\n"
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
