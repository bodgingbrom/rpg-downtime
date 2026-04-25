"""LLM-driven narration for v2 dungeon rooms and search outcomes.

Follows the lazy-singleton pattern from ``fishing/llm.py``. The LLM is
**enhancement**, not infrastructure — every call returns either a
generated string or ``None``, and callers MUST have authored fallback
prose that plays when the LLM is unavailable.

## Scope

Per ``DUNGEON_OVERHAUL_DESIGN.md`` §6:

- **Allowed**: atmospheric paint over authored facts, reactive narration
  of authored outcomes, sensory elaboration of authored items.
- **Disallowed**: inventing interactable things, deciding outcomes,
  inventing rewards. The LLM never picks what's in the chest — it only
  describes what was rolled.

## Caching

The dungeon's system prompt (tone + lore + dm_hooks + style_notes) is
identical across every call within a delve, so it's wrapped in a
``cache_control: ephemeral`` block. After the first call in a delve,
subsequent calls hit the prompt cache — same prefix, much cheaper.

## Backends

This module abstracts over the LLM call so future migrations (local
LLM, OpenRouter, etc.) only need to swap ``_call_llm``. All public
functions return ``str | None`` regardless of backend.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("discord_bot")

_client = None


def _get_client():
    """Return the Anthropic client, or None if unavailable.

    Mirrors ``fishing/llm.py`` exactly so the bot's LLM surface is
    consistent. Missing API key or missing SDK both yield None — callers
    fall back to authored prose.
    """
    global _client
    if _client is None:
        try:
            import anthropic

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning(
                    "ANTHROPIC_API_KEY not set — dungeon LLM features disabled"
                )
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning(
                "anthropic package not installed — dungeon LLM features disabled"
            )
            return None
    return _client


def is_available() -> bool:
    """Return True if the LLM client can be used."""
    return _get_client() is not None


# Model — narration is short and atmospheric; a small/fast model is the right
# fit. Override with DUNGEON_LLM_MODEL.
DUNGEON_MODEL = os.getenv("DUNGEON_LLM_MODEL", "claude-haiku-4-5")


# ---------------------------------------------------------------------------
# System prompt — built once per dungeon and prompt-cached.
# ---------------------------------------------------------------------------


_SYSTEM_TEMPLATE = """\
You are the narrating voice of a Discord dungeon-crawler called Monster Mash. Your \
job is ATMOSPHERIC PROSE around facts the engine has already decided. You do NOT \
control outcomes. You do NOT invent things the player can interact with.

## Dungeon

Name: {name}

Tone: {tone}

Lore:
{lore}

DM hooks (motifs to weave in occasionally — never all at once):
{dm_hooks}

Style notes:
{style_notes}

## Hard rules

- You may add sensory and aesthetic detail to authored facts. You may NOT invent \
objects, exits, monsters, or anything the player could attempt to click on.
- If you mention a feature with an authored name, the noun must remain recognizably \
that noun. Adjectives and elaboration are fine.
- Anything atmospheric — smells, sounds, drafts, feelings — is yours to add freely.
- Anything physical and interactable — chests, levers, tables — must come from the \
"AUTHORED FEATURES" list. If the list is empty, do not mention any.
- 2-4 short lines. Never more than ~80 words. Second person, present tense.
- No emoji. No quotes. No meta. No mention of dice, stats, hit points, or game \
mechanics.
- Match the dungeon's tone above. Do not break it for a joke.
"""


def _build_system_prompt(dungeon_data: dict[str, Any]) -> str:
    """Render the per-dungeon system prompt from the dungeon's background block."""
    bg = dungeon_data.get("background") or {}
    hooks = bg.get("dm_hooks") or []
    if isinstance(hooks, list):
        hooks_text = "\n".join(f"- {h}" for h in hooks) or "(none)"
    else:
        hooks_text = str(hooks)
    return _SYSTEM_TEMPLATE.format(
        name=dungeon_data.get("name", "Unnamed Dungeon"),
        tone=bg.get("tone") or "(unspecified — pick a sensible voice)",
        lore=(bg.get("lore") or "(no lore provided)").strip(),
        dm_hooks=hooks_text,
        style_notes=(bg.get("style_notes") or "(no style notes provided)").strip(),
    )


def _system_blocks(dungeon_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the system message structured for prompt caching.

    The system text is a single content block tagged with
    ``cache_control: ephemeral``, so subsequent calls within the cache
    window (~5 minutes) reuse the cached prefix at a fraction of the
    normal cost. Tuning knob if needed: env var DUNGEON_LLM_NO_CACHE=1
    drops back to a plain string for debugging.
    """
    text = _build_system_prompt(dungeon_data)
    if os.getenv("DUNGEON_LLM_NO_CACHE"):
        return [{"type": "text", "text": text}]
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ---------------------------------------------------------------------------
# Room intro narration.
# ---------------------------------------------------------------------------


def _features_for_prompt(features: list[dict[str, Any]] | None) -> str:
    """Format the authored interactable features for the LLM.

    Only ``passive`` and ``visible`` features are surfaced — concealed
    and secret features are by definition not yet known to the player.
    """
    if not features:
        return "(none)"
    names: list[str] = []
    for f in features:
        if f.get("visibility") in {"passive", "visible"}:
            n = f.get("name") or f.get("id")
            if n:
                names.append(f"- {n}")
    return "\n".join(names) or "(none)"


async def narrate_room_intro(
    dungeon_data: dict[str, Any],
    room_description: str,
    ambient_pool: list[str] | None,
    features: list[dict[str, Any]] | None,
) -> str | None:
    """Return atmospheric prose for entering a room. None on failure.

    The output is meant to *replace* the picked authored description —
    it weaves the description with whatever ambient flavor and feature
    references make sense for the dungeon's tone. Caller is expected to
    persist the returned string per-room so re-renders are stable.
    """
    client = _get_client()
    if client is None:
        return None

    ambient_text = "\n".join(f"- {a}" for a in (ambient_pool or [])) or "(none)"
    feature_text = _features_for_prompt(features)
    user_prompt = (
        f"AUTHORED ROOM DESCRIPTION:\n{room_description}\n\n"
        f"AMBIENT POOL (you may pick 0-2 to weave in, or invent equivalent):\n{ambient_text}\n\n"
        f"AUTHORED FEATURES (interactable; mention naturally if it fits, do not invent new ones):\n{feature_text}\n\n"
        "Write a 2-4 line room-entry passage. Keep the authored description's facts "
        "intact — the room and its features are exactly what the engine says they are. "
        "Add atmosphere, not new content."
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=DUNGEON_MODEL,
            max_tokens=200,
            system=_system_blocks(dungeon_data),
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text or None
    except Exception:
        logger.exception("Failed to narrate dungeon room intro")
        return None


# ---------------------------------------------------------------------------
# Search outcome narration.
# ---------------------------------------------------------------------------


def _format_rewards(rewards: list[dict[str, Any]]) -> str:
    """Human-readable summary of what the engine rolled, for the LLM."""
    if not rewards:
        return "(nothing of value)"
    parts: list[str] = []
    for r in rewards:
        rtype = r.get("type")
        if rtype == "gold":
            parts.append(f"- {r.get('amount', 0)} gold")
        elif rtype == "item":
            parts.append(f"- a {r.get('item_id', 'thing')}")
        elif rtype == "narrate":
            parts.append(f"- (flavor) {r.get('text', '')}")
    return "\n".join(parts) or "(nothing of value)"


async def narrate_search_outcome(
    dungeon_data: dict[str, Any],
    feature_name: str,
    feature_flavor: str | None,
    rewards: list[dict[str, Any]],
) -> str | None:
    """Generate 1-2 lines describing what the player finds. None on failure.

    The engine has already decided WHAT was found (rewards list); the
    LLM only describes HOW the finding feels. If ``feature_flavor`` is
    authored, the LLM should respect / build on it.
    """
    client = _get_client()
    if client is None:
        return None

    flavor_block = (
        f"AUTHORED FLAVOR (use this voice; do not contradict):\n{feature_flavor}\n"
        if feature_flavor
        else ""
    )
    user_prompt = (
        f"FEATURE BEING SEARCHED: {feature_name}\n\n"
        f"{flavor_block}"
        f"REWARDS THE ENGINE ROLLED (describe these, do not invent others):\n"
        f"{_format_rewards(rewards)}\n\n"
        "Write 1-2 short lines narrating what the player finds (or fails to find). "
        "Mention the gold or item naturally — do not list them mechanically. "
        "Keep the dungeon's tone."
    )

    try:
        response = await asyncio.to_thread(
            client.messages.create,
            model=DUNGEON_MODEL,
            max_tokens=160,
            system=_system_blocks(dungeon_data),
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text or None
    except Exception:
        logger.exception("Failed to narrate dungeon search outcome")
        return None


__all__ = [
    "is_available",
    "narrate_room_intro",
    "narrate_search_outcome",
]
