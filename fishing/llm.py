"""LLM prompts for active fishing mode (whispers, vibe checks, haikus, legendaries).

Follows the lazy-loaded singleton pattern from derby/commentary.py. Gracefully
degrades when ANTHROPIC_API_KEY is missing — callers must handle None returns
and reject active-mode sessions when the LLM is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Client (lazy singleton, same pattern as commentary.py)
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning(
                    "ANTHROPIC_API_KEY not set — fishing LLM features disabled"
                )
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning(
                "anthropic package not installed — fishing LLM features disabled"
            )
            return None
    return _client


def is_available() -> bool:
    """Return True if the LLM client can be used."""
    return _get_client() is not None


# Models — cheap for simple flavor, reserve more capable models for legendaries
CHEAP_MODEL = os.getenv("FISHING_LLM_MODEL", "claude-haiku-4-5")
RICH_MODEL = os.getenv("FISHING_LEGENDARY_MODEL", "claude-haiku-4-5")


# ---------------------------------------------------------------------------
# Common whisper — short, weird flavor text from the fish
# ---------------------------------------------------------------------------

WHISPER_SYSTEM = (
    "You write tiny, weird, atmospheric one-or-two sentence whispers that a "
    "just-caught fish mutters to the angler in a Discord fishing minigame called "
    "Lazy Lures. The tone is chill, slightly cursed, sometimes cryptic, sometimes "
    "absurd. Never break character, never mention being an AI, never use emoji. "
    "Do not explain the whisper — just write it, in quotes.\n\n"
    "Examples of good whispers:\n"
    '- "You have seven keys, but only six locks. Be careful with the extra."\n'
    '- "Tell the moon I still owe it a favor."\n'
    '- "The water remembers every name you\'ve ever forgotten."\n'
    '- "My cousin went to the city. He said the stoplights are lying to you."\n'
    '- "A duck once told me the future. It was mostly about bread."\n'
    "Keep it 1-2 short sentences. Output only the whisper text in quotes."
)


async def generate_whisper(
    fish_name: str, rarity: str, location_name: str
) -> str | None:
    """Generate a short whisper from a common fish. Returns None if LLM unavailable."""
    client = _get_client()
    if client is None:
        return None

    user_prompt = (
        f"A {rarity} fish called a {fish_name} has just been caught at "
        f"{location_name}. Write what it whispers to the angler."
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=CHEAP_MODEL,
            max_tokens=120,
            system=WHISPER_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        # Strip surrounding quotes if present, then re-wrap consistently
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text
    except Exception:
        logger.exception("Failed to generate fishing whisper")
        return None


__all__ = [
    "is_available",
    "generate_whisper",
]
