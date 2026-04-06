"""LLM-powered flavor-specific racer name generation using Claude Sonnet."""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Anthropic client (lazy-loaded, shared with descriptions.py)
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
                logger.warning("ANTHROPIC_API_KEY not set — flavor name generation disabled")
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — flavor name generation disabled")
            return None
    return _client


MODEL = os.getenv("FLAVOR_NAMES_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = 4096

_FLAVOR_DIR = os.path.dirname(__file__)


def flavor_names_path(guild_id: int) -> str:
    """Return the file path for a guild's flavor names."""
    return os.path.join(_FLAVOR_DIR, f"flavor_names_{guild_id}.txt")


def load_flavor_names(guild_id: int) -> list[str]:
    """Load flavor names for a guild, returning an empty list if none exist."""
    path = flavor_names_path(guild_id)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def delete_flavor_names(guild_id: int) -> None:
    """Delete a guild's flavor names file if it exists."""
    path = flavor_names_path(guild_id)
    if os.path.exists(path):
        os.remove(path)


def save_flavor_names(guild_id: int, names: list[str]) -> None:
    """Write flavor names to a guild's file."""
    path = flavor_names_path(guild_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(names) + "\n")


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


async def generate_flavor_names(flavor: str) -> list[str] | None:
    """Generate 100 themed racer names via LLM.

    Returns a list of name strings, or ``None`` if the LLM is unavailable
    or generation fails.
    """
    client = _get_client()
    if client is None:
        return None

    system_prompt = (
        "You are a creative naming assistant for a Discord racing game. "
        "Generate exactly 100 unique racer names that fit the theme described. "
        "Include a good mix of:\n"
        "- Serious/cool names that fit the theme\n"
        "- Punny names (wordplay on the theme)\n"
        "- Funny/witty names\n"
        "- Weird/quirky names\n"
        "- Pop culture references twisted to fit the theme\n\n"
        "Names should be 1-3 words, max 32 characters each. "
        "Return ONLY the names, one per line, no numbering or formatting."
    )

    user_prompt = f"Theme: {flavor}"

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
            logger.warning("LLM returned empty name list")
            return None

        # Parse names: one per line, strip whitespace, skip blanks
        names = [line.strip() for line in text.splitlines() if line.strip()]

        # Filter out any that are too long
        names = [n for n in names if len(n) <= 32]

        if len(names) < 10:
            logger.warning(
                "LLM returned too few names (%d), discarding", len(names)
            )
            return None

        logger.info(
            "Generated %d flavor names for theme: %s",
            len(names),
            flavor,
        )
        return names

    except Exception:
        logger.exception("Failed to generate flavor names")
        return None
