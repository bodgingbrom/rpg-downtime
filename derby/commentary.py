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


MODEL = os.getenv("COMMENTARY_MODEL", "claude-haiku-4-5")
# ~800 words (the system-prompt target) is ~1050 tokens, so 1024 left
# zero headroom — verbose races with ability procs hit max_tokens
# mid-winner-announcement and the final paragraph came back clipped.
# 2048 gives ~1500-word headroom, plenty for any realistic race.
MAX_TOKENS = 2048


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
    "You are an enthusiastic fantasy racing commentator for a Discord bot game "
    "called Downtime Derby. You narrate races in a dramatic, entertaining style with "
    "short punchy sentences. Use the race data provided to write segment-by-segment "
    "commentary.\n\n"
    "OUTPUT FORMAT:\n"
    "- Write one paragraph per segment (2-3 sentences each)\n"
    "- Separate each segment paragraph with a blank line\n"
    "- End with a final paragraph announcing the winner\n"
    "- Write in present tense as if calling the race live\n"
    "- Keep total output under 800 words\n"
    "- You MAY use bold (**text**) for emphasis; do NOT use headers or bullets\n\n"
    "EVENTS you'll see per segment:\n"
    "- Overtakes, stumbles, surges, close battles, commanding leads\n"
    "- Mood events (d20 rolls, natural 1s/20s, confidence bursts, lost focus)\n"
    "- Ability procs — lines starting with \u26a1 (a lightning bolt)\n\n"
    "ABILITY PROCS (lines starting with \u26a1) are the most memorable beats of the "
    "race — every one you see MUST appear in your commentary for its segment. "
    "The line gives you: a colored emoji, the racer's name, the ability name in "
    "quotes, and a short description of what happens. Weave all of that into your "
    "prose naturally. Do NOT copy the \u26a1 character into your output.\n\n"
    "Phrasing rules for ability procs:\n"
    "- The colored emoji (\U0001f7e5, \U0001f7e6, etc.) ALWAYS goes immediately "
    "before the racer's name. Never before the ability name.\n"
    "- Bind the ability to the racer with possessive or activation phrasing.\n\n"
    "GOOD examples (emoji \u2192 racer \u2192 ability, connected grammatically):\n"
    "  \"\U0001f7e5 Vortex's Balanced Approach pays off through the chaos \u2014 "
    "they glide where others flail.\"\n"
    "  \"It's \U0001f7e9 Hoofing It again, and Front Runner is holding the line "
    "at the front of the pack.\"\n"
    "  \"\U0001f7e8 Warp Speed is built for this \u2014 Mudder kicks in and they "
    "eat up the muck.\"\n\n"
    "BAD examples (never do this \u2014 reads like the ability is part of the name):\n"
    "  \"\U0001f7e5 Balanced Approach Vortex glides through...\"\n"
    "  \"\U0001f7e9 Front Runner Hoofing It takes the lead...\"\n"
    "  \"\U0001f7e8 Mudder Warp Speed shows their colors...\"\n\n"
    "Mood events (d20 natural 1s/20s, confidence bursts, lost focus) should be "
    "woven in naturally \u2014 don't list every single one, just call out the most "
    "dramatic moments of each segment."
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
        stop_reason = getattr(response, "stop_reason", None)

        # Detect LLM-side truncation — the final paragraph (winner
        # announcement) is almost certainly incomplete if stop_reason
        # is "max_tokens". Loud warning so we see it in prod logs and
        # can bump MAX_TOKENS again if needed.
        if stop_reason == "max_tokens":
            logger.warning(
                "LLM commentary hit max_tokens — final paragraph likely clipped",
                extra={
                    "segments": len(result.segments),
                    "max_tokens": MAX_TOKENS,
                    "model": MODEL,
                },
            )

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
                "stop_reason": stop_reason,
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


# ---------------------------------------------------------------------------
# Live standings bar chart (rendered alongside commentary)
# ---------------------------------------------------------------------------


_BAR_FILLED = "\u2588"  # █
_BAR_EMPTY = "\u2591"   # ░


def build_standings_chart(
    standings: list[tuple[int, float, float]],
    racer_names: dict[int, str],
    color_map: dict[int, str],
    bar_width: int = 10,
) -> str:
    """Render a live-standings bar chart for one segment's standings.

    Each racer's bar length is normalized to the current **gap** between
    leader and last place — not to absolute score — so dominant leads
    look dominant and photo finishes look close. Returns a pre-formatted
    multiline string wrapped in ``` fences, ready to drop into an embed
    field value.

    ``standings`` is the ``SegmentResult.standings`` tuple list:
    ``(racer_id, seg_score, cumulative)`` sorted desc by cumulative.
    ``color_map`` maps racer_id → color emoji (from
    ``abilities.assign_race_colors``). ``bar_width`` is the number of
    cells in the bar (default 10).
    """
    if not standings:
        return ""

    leader_cum = standings[0][2]
    last_cum = standings[-1][2]
    spread = leader_cum - last_cum

    # Pad name column to the longest name in this race for monospace alignment
    max_name_len = max(
        len(racer_names.get(rid, f"Racer {rid}"))
        for rid, _, _ in standings
    )

    lines: list[str] = []
    for rid, _seg_score, cumulative in standings:
        if spread > 0:
            relative = (cumulative - last_cum) / spread
        else:
            # All racers tied (edge case, e.g. segment 0 rolls identical):
            # show everyone at a full bar rather than everyone empty.
            relative = 1.0

        filled = round(relative * bar_width)
        filled = max(0, min(bar_width, filled))
        bar = _BAR_FILLED * filled + _BAR_EMPTY * (bar_width - filled)

        color = color_map.get(rid, "")
        name = racer_names.get(rid, f"Racer {rid}").ljust(max_name_len)
        prefix = f"{color} " if color else ""
        lines.append(f"{prefix}{name}  {bar}")

    return "```\n" + "\n".join(lines) + "\n```"
