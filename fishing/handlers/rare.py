"""Rare-rarity bite: LLM writes 2 of 3 haiku lines, player completes the third."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

import discord

from .. import llm
from .. import repositories as fish_repo

logger = logging.getLogger("discord_bot")

# Phase 3 (haiku completion) timeout in seconds.
RARE_HAIKU_TIMEOUT = 40


class HaikuModal(discord.ui.Modal):
    """Modal where the player writes one missing line of a haiku.

    The missing line can be any of the three positions; the label adapts to
    the expected syllable count.
    """

    def __init__(self, parent_view: "HaikuView", syllables: int, position_label: str):
        super().__init__(title="Complete the haiku")
        self._view = parent_view
        self.line_input = discord.ui.TextInput(
            label=f"Your {position_label} line (roughly {syllables} syllables)",
            placeholder="a few honest words",
            max_length=100,
            required=True,
            style=discord.TextStyle.short,
        )
        self.add_item(self.line_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.line_input.value or "").strip()
        # If the player includes multiple lines, keep only the first one
        first_line = raw.splitlines()[0].strip() if raw else ""
        self._view.submitted_line = first_line
        self._view.submitted = True
        await interaction.response.defer()
        self._view.stop()


class HaikuView(discord.ui.View):
    """A button that opens the haiku fill-in modal for a rare bite.

    ``missing_line`` (1, 2, or 3) is the position the player is filling.
    """

    def __init__(
        self,
        user_id: int,
        missing_line: int,
        timeout: float = RARE_HAIKU_TIMEOUT,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.missing_line = missing_line
        self.submitted_line: str | None = None
        self.submitted = False

    @discord.ui.button(label="✍️ Complete the haiku", style=discord.ButtonStyle.primary)
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
        syllables = 7 if self.missing_line == 2 else 5
        position_label = {
            1: "opening",
            2: "middle",
            3: "closing",
        }.get(self.missing_line, "")
        await interaction.response.send_modal(
            HaikuModal(self, syllables=syllables, position_label=position_label)
        )


async def handle_rare(
    runner,
    fs,
    catch: dict[str, Any],
    location_data: dict[str, Any],
) -> bool:
    """Rare haiku: LLM writes opening 5-7 lines, player closes with 5.

    PASS → catch the fish AND save the haiku to the player's log.
    FAIL or timeout → fish escapes, bait burned, haiku not saved.
    """
    channel = runner._get_post_target(fs)
    if channel is None:
        return False

    loc_name = location_data.get("name", fs.location_name)

    # Generate a full three-line haiku, then blank one line at random
    full = await llm.generate_full_haiku(
        catch["name"], catch.get("rarity", "rare"), loc_name
    )
    if full is None:
        logger.warning("Full haiku generation returned None; escaping bite")
        try:
            await channel.send(
                f"<@{fs.user_id}> — a rare catch slips by before the "
                f"words can form. (LLM unavailable.)"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        return False

    full_line_1, full_line_2, full_line_3 = full
    missing_line = random.randint(1, 3)

    # Build the display (with the blank in the right slot) and a
    # placeholder version used for logging
    BLANK = "_______________"

    def _display_lines(player_line: str | None) -> tuple[str, str, str]:
        """Return (line_1, line_2, line_3) with `player_line` inserted at
        the missing slot. If `player_line` is None, the blank is shown."""
        filler = player_line if player_line is not None else BLANK
        if missing_line == 1:
            return filler, full_line_2, full_line_3
        if missing_line == 2:
            return full_line_1, filler, full_line_3
        return full_line_1, full_line_2, filler

    prompt_display_l1, prompt_display_l2, prompt_display_l3 = _display_lines(None)
    prompt_log_text = f"{prompt_display_l1}\n{prompt_display_l2}\n{prompt_display_l3}"

    position_word = {1: "opening", 2: "middle", 3: "closing"}[missing_line]
    target_syllables = 7 if missing_line == 2 else 5

    prompt = discord.Embed(
        title=f"✨ A rare stir at {loc_name}...",
        description=(
            f"<@{fs.user_id}>\n\n"
            f"*{prompt_display_l1}*\n"
            f"*{prompt_display_l2}*\n"
            f"*{prompt_display_l3}*\n\n"
            f"Fill in the **{position_word}** {target_syllables}-syllable "
            f"line in **{RARE_HAIKU_TIMEOUT}s**."
        ),
        color=0x9B59B6,
    )
    view = HaikuView(user_id=fs.user_id, missing_line=missing_line)
    try:
        message = await channel.send(embed=prompt, view=view)
    except (discord.Forbidden, discord.HTTPException):
        return False

    timed_out = await view.wait()

    if timed_out or not view.submitted or not view.submitted_line:
        escape_embed = discord.Embed(
            title="🌊 The rare one slips away...",
            description=(
                f"*{prompt_display_l1}*\n"
                f"*{prompt_display_l2}*\n"
                f"*{prompt_display_l3}*\n\n"
                f"*(unfinished)*"
            ),
            color=0x95A5A6,
        )
        try:
            await message.edit(embed=escape_embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await runner._log_event(
            fs, "rare", catch["name"],
            prompt_text=prompt_log_text, player_response="",
            outcome="timeout",
        )
        return False

    player_line = view.submitted_line
    final_l1, final_l2, final_l3 = _display_lines(player_line)

    # Judge the full assembled haiku (player's line is in the right slot)
    verdict = await llm.judge_haiku(final_l1, final_l2, final_l3)
    if verdict is None:
        # Fail closed when the judge can't be reached
        verdict = False

    if verdict:
        # Save the haiku to the player's log in canonical 1-2-3 order
        now = datetime.now(timezone.utc)
        try:
            async with runner.bot.scheduler.sessionmaker() as db:
                await fish_repo.save_haiku(
                    db,
                    user_id=fs.user_id,
                    guild_id=fs.guild_id,
                    location_name=fs.location_name,
                    fish_species=catch["name"],
                    line_1=final_l1,
                    line_2=final_l2,
                    line_3=final_l3,
                    created_at=now,
                )
        except Exception:
            logger.exception("Failed to save haiku")

        details: list[str] = []
        if catch.get("length"):
            details.append(f"{catch['length']}in")
        details.append(f"{catch['value']} coins")
        angler = runner._resolve_display_name(fs)
        result_embed = discord.Embed(
            title=f"🐟 {angler} caught a {catch['name']}!",
            description=(
                f"*{final_l1}*\n"
                f"*{final_l2}*\n"
                f"*{final_l3}*\n\n"
                f"**{catch['name']}** • {' • '.join(details)}\n"
                f"*Added to your haiku log — `/fish haiku mine`*"
            ),
            color=0x9B59B6,
        )
        try:
            await message.edit(embed=result_embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            pass
        await runner._log_event(
            fs, "rare", catch["name"],
            prompt_text=prompt_log_text, player_response=player_line,
            outcome="caught",
        )
        return True

    # FAIL
    fail_embed = discord.Embed(
        title="🌊 The rare one slips away...",
        description=(
            f"*{final_l1}*\n"
            f"*{final_l2}*\n"
            f"*{final_l3}*\n\n"
            f"*The rhythm wasn't quite right.*"
        ),
        color=0x95A5A6,
    )
    try:
        await message.edit(embed=fail_embed, view=None)
    except (discord.Forbidden, discord.HTTPException):
        pass
    await runner._log_event(
        fs, "rare", catch["name"],
        prompt_text=prompt_log_text, player_response=player_line,
        outcome="escaped",
    )
    return False
