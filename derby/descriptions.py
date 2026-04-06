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
) -> str:
    """Build the system prompt for description generation."""
    prompt = (
        f"You are describing a racing creature for a Discord game. "
        f"The guild's creature theme: {flavor}.\n\n"
        "Write exactly 2-3 sentences describing this racer's physical appearance. "
        "Include at least 3 distinct physical features (build, coloring, markings, etc.). "
        "The description should subtly reflect their stats and temperament. "
        "Do NOT include the racer's name, stat numbers, or game mechanics. "
        "Prose only — no bullet points, headers, or formatting."
    )

    if sire_desc and dam_desc:
        prompt += (
            "\n\nThis is a foal born from two parents. Blend physical traits from "
            "both parents while adding 1-2 unique features of its own. The foal "
            "should clearly resemble both parents but be its own individual.\n\n"
            f"Sire's appearance: {sire_desc}\n"
            f"Dam's appearance: {dam_desc}"
        )

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
        f"Temperament: {temperament}\n"
        f"Speed: {_stat_hint(speed)} (lean/sleek builds suggest speed)\n"
        f"Cornering: {_stat_hint(cornering)} (agile/compact builds suggest cornering)\n"
        f"Stamina: {_stat_hint(stamina)} (sturdy/muscular builds suggest stamina)"
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
) -> str | None:
    """Generate an LLM description for a racer.

    Returns the description string, or ``None`` if the LLM is unavailable
    or generation fails.
    """
    client = _get_client()
    if client is None:
        return None

    system_prompt = _build_system_prompt(flavor, sire_desc, dam_desc)
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
