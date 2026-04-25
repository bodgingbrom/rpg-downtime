"""Uncommon-rarity bite: atmospheric vibe passage + one-word LLM judgement.

PASS → catch the fish. FAIL or timeout → fish escapes, bait burned.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import discord

from .. import llm

logger = logging.getLogger("discord_bot")

# From bite to modal submission.
UNCOMMON_VIBE_TIMEOUT = 25


class VibeCheckModal(discord.ui.Modal, title="What's the vibe?"):
    """Modal for submitting a one-word vibe response to an uncommon bite."""

    word_input = discord.ui.TextInput(
        label="One word captures this bite",
        placeholder="patient, hungry, sharp, lonely, hollow...",
        max_length=30,
        required=True,
        style=discord.TextStyle.short,
    )

    def __init__(self, parent_view: "VibeCheckView"):
        super().__init__()
        self._view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.word_input.value or "").strip()
        # Take the first whitespace-separated token, lowercase it
        first = raw.split()[0].lower() if raw else ""
        self._view.word = first
        self._view.submitted = True
        await interaction.response.defer()  # acknowledge silently
        self._view.stop()


class VibeCheckView(discord.ui.View):
    """A Respond button that opens the vibe-check modal for an uncommon bite."""

    def __init__(self, user_id: int, timeout: float = UNCOMMON_VIBE_TIMEOUT):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.word: str | None = None
        self.submitted = False

    @discord.ui.button(label="Respond", style=discord.ButtonStyle.primary)
    async def respond(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your line!", ephemeral=True
            )
            return
        # Sending the modal IS the response to the interaction
        await interaction.response.send_modal(VibeCheckModal(self))


async def handle_uncommon(
    runner,
    fs,
    catch: dict[str, Any],
    location_data: dict[str, Any],
) -> bool:
    """Uncommon vibe check: atmospheric passage + one-word LLM judge.

    PASS → catch the fish. FAIL or timeout → fish escapes, bait burned.
    """
    channel = runner._get_post_target(fs)
    if channel is None:
        # No channel to prompt in — conservative: escape
        return False

    loc_name = location_data.get("name", fs.location_name)

    # Roll a target mood from the fish's mood_pool (if defined in YAML).
    # Falls back to None for species without a mood_pool — the LLM then
    # free-writes whatever mood fits the fish, legacy-style.
    fish_defs = location_data.get("fish", [])
    fish_def = next(
        (f for f in fish_defs if f.get("name") == catch["name"]), None
    )
    mood_pool = (fish_def or {}).get("mood_pool") or []
    target_mood = random.choice(mood_pool) if mood_pool else None

    # Generate the atmospheric passage up front, seeded with the rolled mood
    passage = await llm.generate_vibe_passage(
        catch["name"], catch.get("rarity", "uncommon"), loc_name,
        mood=target_mood,
    )
    # Composed log text so /reports fishing-uncommon shows the rolled mood
    log_prompt = (
        f"[mood: {target_mood}]\n{passage}" if target_mood else (passage or "")
    )
    if passage is None:
        # LLM unavailable mid-session — escape, safer than auto-pass
        logger.warning("Vibe passage generation returned None; escaping bite")
        try:
            await channel.send(
                f"<@{fs.user_id}> — a bigger bite slips away before you "
                f"can read it. (LLM unavailable.)"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return False

    # Post the passage with a Respond button
    prompt = discord.Embed(
        title=f"🎣 Something bigger at {loc_name}...",
        description=(
            f"<@{fs.user_id}>\n\n*{passage}*\n\n"
            f"Respond in **{UNCOMMON_VIBE_TIMEOUT}s** with a single word "
            f"that captures the vibe."
        ),
        color=0x3498DB,
    )
    view = VibeCheckView(user_id=fs.user_id)
    try:
        message = await channel.send(embed=prompt, view=view)
    except (discord.Forbidden, discord.HTTPException):
        return False

    timed_out = await view.wait()

    if timed_out or not view.submitted or not view.word:
        # Timeout or empty — escape
        escape_embed = discord.Embed(
            title=f"🌊 The fish slips away...",
            description=f"*{passage}*\n\nYou hesitated too long.",
            color=0x95A5A6,
        )
        try:
            await message.edit(embed=escape_embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await runner._log_event(
            fs, "uncommon", catch["name"],
            prompt_text=log_prompt, player_response="",
            outcome="timeout",
        )
        return False

    # Judge the word against the rolled target mood
    verdict = await llm.judge_vibe(
        passage, view.word, target_mood=target_mood,
    )
    if verdict is None:
        # LLM glitched — be fair and treat as pass? User said fail-closed.
        # Going with fail-closed for consistency.
        verdict = False

    if verdict:
        result_lines = [f"**{catch['name']}**"]
        details: list[str] = []
        if catch.get("length"):
            details.append(f"{catch['length']}in")
        details.append(f"{catch['value']} coins")
        result_lines.append(" • ".join(details))
        result_lines.append(
            f"\n*{passage}*\n\nTheir word: **{view.word}** — the water agrees."
        )
        angler = runner._resolve_display_name(fs)
        result_embed = discord.Embed(
            title=f"🐟 {angler} caught a {catch['name']}!",
            description="\n".join(result_lines),
            color=0x2ECC71,
        )
        try:
            await message.edit(embed=result_embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await runner._log_event(
            fs, "uncommon", catch["name"],
            prompt_text=log_prompt, player_response=view.word,
            outcome="caught",
        )
        return True

    # FAIL
    fail_embed = discord.Embed(
        title=f"🌊 The fish slips away...",
        description=(
            f"*{passage}*\n\n"
            f"Your word: **{view.word}** — not quite the right feel."
        ),
        color=0x95A5A6,
    )
    try:
        await message.edit(embed=fail_embed, view=None)
    except (discord.Forbidden, discord.HTTPException):
        pass
    await runner._log_event(
        fs, "uncommon", catch["name"],
        prompt_text=log_prompt, player_response=view.word,
        outcome="escaped",
    )
    return False
