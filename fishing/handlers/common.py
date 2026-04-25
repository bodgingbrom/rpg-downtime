"""Common-rarity bite: reel-in button → optional whisper → always catches."""

from __future__ import annotations

import logging
from typing import Any

import discord

from .. import llm

logger = logging.getLogger("discord_bot")

# Timeout in seconds. Long window — no fail state, the player just misses
# the whisper flavor text if they don't click.
COMMON_REEL_TIMEOUT = 300


class ReelInView(discord.ui.View):
    """A button the player clicks to reel in a common bite.

    Timeout = no click. For commons this just means they miss the whisper;
    they still catch the fish.
    """

    def __init__(self, user_id: int, timeout: float = COMMON_REEL_TIMEOUT):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.clicked = False

    @discord.ui.button(label="🎣 Reel it in", style=discord.ButtonStyle.primary)
    async def reel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your line!", ephemeral=True
            )
            return
        self.clicked = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


async def handle_common(
    runner,
    fs,
    catch: dict[str, Any],
    location_data: dict[str, Any],
) -> bool:
    """Common whisper: click → LLM whisper → always catches.

    Timeout just means they miss the flavor text; they still catch.
    """
    channel = runner._get_post_target(fs)
    if channel is None:
        return True

    loc_name = location_data.get("name", fs.location_name)
    prompt = discord.Embed(
        title=f"🎣 Something bites at {loc_name}...",
        description=(
            f"<@{fs.user_id}> — your line twitches. "
            f"Reel it in to see what you caught."
        ),
        color=0x3498DB,
    )
    view = ReelInView(user_id=fs.user_id)
    try:
        message = await channel.send(embed=prompt, view=view)
    except (discord.Forbidden, discord.HTTPException):
        return True

    timed_out = await view.wait()

    # Generate the whisper (may be None if LLM unavailable)
    whisper = None
    if view.clicked:
        try:
            whisper = await llm.generate_whisper(
                catch["name"], catch.get("rarity", "common"), loc_name
            )
        except Exception:
            logger.exception("Whisper generation failed")

    # Build result embed
    result_lines = [f"**{catch['name']}**"]
    details: list[str] = []
    if catch.get("length"):
        details.append(f"{catch['length']}in")
    details.append(f"{catch['value']} coins")
    result_lines.append(" • ".join(details))
    if whisper:
        result_lines.append(f"\n*{catch['name']} whispers:* \"{whisper}\"")
    elif timed_out or not view.clicked:
        result_lines.append("\n*(The fish slipped away from your attention...but you still pulled it in.)*")

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

    return True
