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


# ---------------------------------------------------------------------------
# Uncommon vibe check — atmospheric passage + one-word semantic judge
# ---------------------------------------------------------------------------

VIBE_PASSAGE_SYSTEM = (
    "You write tiny atmospheric bite descriptions for a Discord fishing "
    "minigame called Lazy Lures. The angler has hooked an uncommon fish, and "
    "your job is to write 1-2 short sentences evoking the *feel* of the bite. "
    "Never name the fish. Never state its mood outright with an adjective — "
    "show it through sensation. Write in second person, present tense. "
    "Concrete, sensory, evocative. Never use emoji. Never explain. Output "
    "only the passage, no quotes.\n\n"
    "Examples:\n"
    "- The line goes suddenly, impossibly still. Something is waiting below.\n"
    "- A sharp jerk, then a steady thrum, as if the water itself is humming.\n"
    "- Your rod bends low. The water has turned colder than it was a moment ago.\n"
    "- Three quick tugs, playful and impatient, like a child at a sleeve.\n"
    "- The line zigzags wildly, then pulls straight down with a quiet menace."
)


async def generate_vibe_passage(
    fish_name: str, rarity: str, location_name: str
) -> str | None:
    """Generate a 1-2 sentence atmospheric passage for an uncommon bite."""
    client = _get_client()
    if client is None:
        return None

    user_prompt = (
        f"Write the bite description for a {rarity} fish called {fish_name} "
        f"at {location_name}. Do not name the fish."
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=CHEAP_MODEL,
            max_tokens=120,
            system=VIBE_PASSAGE_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text
    except Exception:
        logger.exception("Failed to generate vibe passage")
        return None


VIBE_JUDGE_SYSTEM = (
    "You are the judge for a fishing vibe-check minigame. A short atmospheric "
    "passage describes a fish biting. The player responds with a single word "
    "meant to capture the mood. Your job: decide whether that word fits the "
    "passage's tone.\n\n"
    "Be moderately generous:\n"
    "- Accept direct matches, synonyms, and evocative adjacents\n"
    "- Accept words that capture the same emotional register (tense ≈ wary ≈ uneasy)\n"
    "- Accept sensory words that match the passage's imagery (heavy, cold, sharp)\n"
    "- Accept a close-but-imperfect fit — err toward letting the player in\n"
    "Reject only when the word clearly doesn't fit:\n"
    "- Opposite emotional register (joy for a menacing passage)\n"
    "- Totally unrelated or nonsense (random nouns, slang, proper nouns)\n"
    "- Single letters or fewer than 3 characters\n\n"
    "Respond with exactly one word — PASS or FAIL — and nothing else."
)


async def judge_vibe(passage: str, player_word: str) -> bool | None:
    """Judge whether the player's word matches the passage's mood.

    Returns True (PASS), False (FAIL), or None if the LLM is unavailable
    (callers should treat None as a fail for safety).
    """
    client = _get_client()
    if client is None:
        return None

    user_prompt = (
        f"Passage: {passage}\n\n"
        f"Player's word: {player_word}\n\n"
        "Does this word capture the mood?"
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=CHEAP_MODEL,
            max_tokens=10,
            system=VIBE_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip().upper()
        # Be lenient about extra punctuation or whitespace
        if text.startswith("PASS"):
            return True
        if text.startswith("FAIL"):
            return False
        logger.warning("Vibe judge returned unexpected text: %r", text)
        return False
    except Exception:
        logger.exception("Vibe judge call failed")
        return None


__all__ = [
    "is_available",
    "generate_whisper",
    "generate_vibe_passage",
    "judge_vibe",
]
