"""LLM-powered NPC trainer generation using Claude Haiku."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random

logger = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Anthropic client (lazy-loaded, shared with descriptions.py pattern)
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
                logger.warning("ANTHROPIC_API_KEY not set — NPC generation disabled")
                return None
            _client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            logger.warning("anthropic package not installed — NPC generation disabled")
            return None
    return _client


MODEL = os.getenv("NPC_MODEL", "claude-haiku-4-5")

# ---------------------------------------------------------------------------
# Rank stat ranges (total of speed + cornering + stamina)
# ---------------------------------------------------------------------------

RANK_STAT_RANGES: dict[str, tuple[int, int]] = {
    "D": (5, 23),
    "C": (24, 46),
    "B": (47, 65),
    "A": (66, 80),
    "S": (81, 93),
}

# The 5 NPC archetype slots with rank bands
NPC_SLOTS: list[dict[str, str]] = [
    {"rank_min": "D", "rank_max": "C", "archetype": "scrappy newcomer / bumbling hobbyist"},
    {"rank_min": "C", "rank_max": "B", "archetype": "calculating strategist / serious competitor"},
    {"rank_min": "B", "rank_max": "A", "archetype": "flamboyant showman / cocky gambler"},
    {"rank_min": "A", "rank_max": "S", "archetype": "grizzled veteran / stoic old-timer"},
    {"rank_min": "S", "rank_max": "S", "archetype": "mysterious champion / enigmatic legend"},
]

TEMPERAMENTS = ["Agile", "Reckless", "Tactical", "Burly", "Steady", "Sharpshift", "Quirky"]


# ---------------------------------------------------------------------------
# NPC generation
# ---------------------------------------------------------------------------


def _build_npc_prompt(racer_flavor: str) -> str:
    """Build the prompt for generating 5 NPCs themed to a guild's flavor."""
    slot_descriptions = []
    for i, slot in enumerate(NPC_SLOTS, 1):
        slot_descriptions.append(
            f"  NPC {i}: Ranks {slot['rank_min']}-{slot['rank_max']}, "
            f"personality archetype: {slot['archetype']}"
        )

    return f"""You are creating NPC rival trainers for a Discord racing game.
The server's creature theme is: "{racer_flavor}"

Generate exactly 5 NPC trainers. Each trainer has a unique personality and owns 2 racing creatures.

NPC slots:
{chr(10).join(slot_descriptions)}

For EACH NPC, provide:
- name: A memorable character name that fits the theme (2-3 words max)
- emoji: A single emoji that represents their personality
- personality: A short archetype label (2-4 words)
- personality_desc: 1-2 sentences describing their character, mannerisms, and how they talk
- catchphrase: A short signature line they'd say (under 10 words)
- racer1_name: Name for their first racer (in the rank_min tier). Should match the NPC's personality/style.
- racer2_name: Name for their second racer (in the rank_max tier). Should match the NPC's personality/style.

RULES:
- Names should feel like they belong in the same world as "{racer_flavor}"
- Each NPC should have a DISTINCT personality — no two should feel similar
- Racer names should reflect the NPC's personality (e.g., a cocky NPC might name racers "Double Down" and "House Money")
- Racer names should be 1-3 words, under 32 characters
- Keep it fun and memorable — these are characters players will develop rivalries with

Return ONLY valid JSON — an array of 5 objects with the exact keys listed above. No markdown formatting."""


async def generate_guild_npcs(racer_flavor: str) -> list[dict] | None:
    """Generate 5 themed NPCs for a guild via LLM.

    Returns a list of dicts with NPC data, or None on failure.
    Each dict has keys: name, emoji, personality, personality_desc,
    catchphrase, rank_min, rank_max, racer1_name, racer2_name.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = _build_npc_prompt(racer_flavor)

    def _call():
        return client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

    try:
        response = await asyncio.to_thread(_call)
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        npcs_raw = json.loads(text)
        if not isinstance(npcs_raw, list) or len(npcs_raw) != 5:
            logger.warning("NPC generation returned %d NPCs, expected 5", len(npcs_raw) if isinstance(npcs_raw, list) else 0)
            return None

        # Merge with slot rank data
        result = []
        for i, npc_data in enumerate(npcs_raw):
            slot = NPC_SLOTS[i]
            result.append({
                "name": str(npc_data.get("name", f"Trainer {i + 1}")),
                "emoji": str(npc_data.get("emoji", "")),
                "personality": str(npc_data.get("personality", "Unknown")),
                "personality_desc": str(npc_data.get("personality_desc", "")),
                "catchphrase": str(npc_data.get("catchphrase", "")),
                "rank_min": slot["rank_min"],
                "rank_max": slot["rank_max"],
                "racer1_name": str(npc_data.get("racer1_name", f"NPC Racer {i * 2 + 1}")),
                "racer2_name": str(npc_data.get("racer2_name", f"NPC Racer {i * 2 + 2}")),
            })
        return result
    except Exception:
        logger.exception("NPC generation failed")
        return None


def generate_racer_stats_for_rank(rank: str) -> dict[str, int]:
    """Generate random stats (speed, cornering, stamina) within a rank's range."""
    low, high = RANK_STAT_RANGES.get(rank, (5, 23))
    total = random.randint(low, high)
    # Distribute total across 3 stats with some variance
    base = total // 3
    remainder = total - base * 3
    stats = [base, base, base]
    # Add remainder randomly
    for _ in range(remainder):
        stats[random.randint(0, 2)] += 1
    # Add variance: shift points between stats (preserves total)
    for _ in range(min(5, base)):
        src, dst = random.sample(range(3), 2)
        shift = random.randint(0, min(3, stats[src]))
        stats[src] -= shift
        stats[dst] += shift
    # Clamp individual stats to 0-31, then fix total if clamping changed it
    stats = [max(0, min(31, s)) for s in stats]
    actual_total = sum(stats)
    # If clamping reduced the total, redistribute the deficit
    while actual_total < low:
        idx = random.randint(0, 2)
        if stats[idx] < 31:
            stats[idx] += 1
            actual_total += 1
    return {
        "speed": stats[0],
        "cornering": stats[1],
        "stamina": stats[2],
    }


# ---------------------------------------------------------------------------
# Quip generation
# ---------------------------------------------------------------------------


def _build_quip_prompt(
    npc_name: str,
    personality_desc: str,
    racer_flavor: str,
    quip_type: str,
    count: int,
    existing_quips: list[str] | None = None,
) -> str:
    """Build the prompt for generating win or loss quips."""
    context = (
        f"NPC Trainer: {npc_name}\n"
        f"Personality: {personality_desc}\n"
        f"Racing creature theme: {racer_flavor}\n\n"
    )

    if quip_type == "win":
        instruction = (
            f"Generate exactly {count} unique WIN quips — things this trainer would say "
            "when their racer WINS a race. They should be celebratory, boastful, or smug "
            "in a way that matches their personality."
        )
    else:
        instruction = (
            f"Generate exactly {count} unique LOSS quips — things this trainer would say "
            "when their racer finishes LAST. They should be frustrated, dismissive, or "
            "making excuses in a way that matches their personality."
        )

    existing_note = ""
    if existing_quips:
        existing_note = (
            "\n\nThese quips have already been used — do NOT repeat similar themes:\n"
            + "\n".join(f"- {q}" for q in existing_quips)
        )

    return (
        f"{context}{instruction}\n\n"
        "RULES:\n"
        "- Each quip is 1-2 short sentences\n"
        "- Stay in character — match the personality\n"
        "- Vary the themes (don't just rephrase the same idea)\n"
        "- Keep them fun and suitable for a Discord game channel\n"
        f"{existing_note}\n\n"
        "Return ONLY valid JSON — an array of strings. No markdown formatting."
    )


async def generate_npc_quips(
    npc_name: str,
    personality_desc: str,
    racer_flavor: str,
    quip_type: str,
    count: int = 20,
    existing_quips: list[str] | None = None,
) -> list[str] | None:
    """Generate win or loss quips for an NPC via LLM.

    Returns a list of quip strings, or None on failure.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = _build_quip_prompt(
        npc_name, personality_desc, racer_flavor, quip_type, count, existing_quips
    )

    def _call():
        return client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

    try:
        response = await asyncio.to_thread(_call)
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        quips = json.loads(text)
        if not isinstance(quips, list):
            return None
        return [str(q) for q in quips if q]
    except Exception:
        logger.exception("Quip generation failed for %s", npc_name)
        return None


async def generate_npc_racer_name(
    npc_name: str,
    personality_desc: str,
    racer_flavor: str,
    taken_names: set[str],
) -> str | None:
    """Generate a single themed racer name for an NPC's replacement racer."""
    client = _get_client()
    if client is None:
        return None

    taken_list = ", ".join(sorted(taken_names)[:20]) if taken_names else "none"
    prompt = (
        f"NPC Trainer: {npc_name}\n"
        f"Personality: {personality_desc}\n"
        f"Racing creature theme: {racer_flavor}\n\n"
        f"Generate exactly 1 new racer name for this trainer's new racing creature. "
        f"It should match the trainer's personality and naming style.\n"
        f"Names already taken (do NOT reuse): {taken_list}\n\n"
        "Return ONLY the name — no quotes, no formatting, just the name (1-3 words, under 32 chars)."
    )

    def _call():
        return client.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )

    try:
        response = await asyncio.to_thread(_call)
        name = response.content[0].text.strip().strip('"').strip("'")
        if name and len(name) <= 32 and name not in taken_names:
            return name
        return None
    except Exception:
        logger.exception("Racer name generation failed for NPC %s", npc_name)
        return None
