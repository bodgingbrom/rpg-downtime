"""LLM-powered race commentary using Claude Haiku."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .logic import RaceResult

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
                logger.warning("ANTHROPIC_API_KEY not set — LLM commentary disabled")
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — LLM commentary disabled")
            return None
    return _client


MODEL = "claude-haiku-4-20250414"
MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_prompt(result: RaceResult) -> str:
    """Build the system + user prompt from race results."""
    lines: list[str] = []

    lines.append(f"Track: {result.map_name or 'Unknown Track'}")
    lines.append(f"Racers: {', '.join(result.racer_names.values())}")
    lines.append("")

    for seg in result.segments:
        lines.append(
            f"Segment {seg.position}: {seg.segment_description or seg.segment_type}"
            f" ({seg.segment_type})"
        )
        standings_str = ", ".join(
            f"{result.racer_names.get(rid, f'Racer {rid}')} ({cum:.1f}pts)"
            for rid, _seg_score, cum in seg.standings
        )
        lines.append(f"  Standings: {standings_str}")
        if seg.events:
            for event in seg.events:
                lines.append(f"  Event: {event}")
        lines.append("")

    # Final placements
    lines.append("Final placements:")
    for i, rid in enumerate(result.placements, 1):
        lines.append(f"  {i}. {result.racer_names.get(rid, f'Racer {rid}')}")

    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are an enthusiastic fantasy horse-racing commentator for a Discord bot game "
    "called Downtime Derby. You narrate races in a dramatic, entertaining style with "
    "short punchy sentences. Use the race data provided to write segment-by-segment "
    "commentary.\n\n"
    "Rules:\n"
    "- Write one paragraph per segment (2-3 sentences each)\n"
    "- Separate each segment paragraph with a blank line\n"
    "- End with a final paragraph announcing the winner\n"
    "- Reference racer names, events (overtakes, stumbles, surges), and track features\n"
    "- Keep total output under 800 words\n"
    "- Do NOT use headers, bullet points, or markdown formatting\n"
    "- Write in present tense as if calling the race live"
)


# ---------------------------------------------------------------------------
# Main generation function
# ---------------------------------------------------------------------------


async def generate_commentary(result: RaceResult) -> list[str] | None:
    """Generate LLM commentary for a race result.

    Returns a list of commentary strings (one per segment + finale), or
    ``None`` if the LLM is unavailable so the caller can fall back to
    template commentary.
    """
    client = _get_client()
    if client is None:
        return None

    if not result.segments:
        return None

    user_prompt = _build_prompt(result)

    try:
        # Use sync client in a thread to avoid blocking the event loop
        import asyncio

        response = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = response.content[0].text

        # Split into paragraphs — each becomes a commentary message
        paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]

        if not paragraphs:
            logger.warning("LLM returned empty commentary")
            return None

        logger.info(
            "LLM commentary generated",
            extra={
                "segments": len(result.segments),
                "paragraphs": len(paragraphs),
                "model": MODEL,
            },
        )
        return paragraphs

    except Exception:
        logger.exception("Failed to generate LLM commentary")
        return None


# ---------------------------------------------------------------------------
# Template fallback (extracted from scheduler for reuse)
# ---------------------------------------------------------------------------


def build_template_commentary(result: RaceResult) -> list[str]:
    """Build simple template-based commentary from a RaceResult.

    Used as a fallback when LLM commentary is unavailable.
    """
    log: list[str] = []
    for seg in result.segments:
        header = f"**{seg.segment_description or seg.segment_type.capitalize()}**"
        log.append(header)
        for event in seg.events:
            log.append(event)
        top = seg.standings[:3]
        standing_lines = ", ".join(
            f"{result.racer_names.get(rid, f'Racer {rid}')}"
            for rid, _, _ in top
        )
        log.append(f"Standings: {standing_lines}")
    return log
