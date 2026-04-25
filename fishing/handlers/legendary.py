"""Legendary-rarity bite: multi-turn LLM dialogue with a unique character.

Each location has at most one active legendary. Memory persists across
encounters; if the player convinces the fish (CONVINCED verdict within 3
rounds), it retires and a new one spawns to take its place.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import discord

from .. import llm
from .. import repositories as fish_repo

logger = logging.getLogger("discord_bot")

# Phase 4 (legendary turn) timeout in seconds.
LEGENDARY_TURN_TIMEOUT = 60


class LegendaryResponseModal(discord.ui.Modal, title="Speak to the fish"):
    """Modal where the player responds to a legendary's question/challenge."""

    response_input = discord.ui.TextInput(
        label="Your response",
        placeholder="Speak honestly. The fish is listening.",
        max_length=500,
        required=True,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, parent_view: "LegendaryResponseView"):
        super().__init__()
        self._view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.response_input.value or "").strip()
        self._view.response = raw
        self._view.submitted = True
        await interaction.response.defer()
        self._view.stop()


class LegendaryResponseView(discord.ui.View):
    """A button opens the legendary-response modal for one dialogue turn."""

    def __init__(self, user_id: int, timeout: float = LEGENDARY_TURN_TIMEOUT):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.response: str | None = None
        self.submitted = False

    @discord.ui.button(label="💬 Respond", style=discord.ButtonStyle.primary)
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
        await interaction.response.send_modal(LegendaryResponseModal(self))


async def handle_legendary(
    runner,
    fs,
    catch: dict[str, Any],
    location_data: dict[str, Any],
) -> bool:
    """Legendary encounter: multi-turn LLM dialogue with a unique character.

    Each location has at most one active legendary. If none exists, one
    is generated now. The fish remembers prior encounters with this player
    and is aware of recent encounters with others. Player must convince
    it within 3 rounds. On catch, the legendary retires and a new one is
    generated to take its place.

    Outcomes returned:
    - True + legendary caught + new one generated
    - False (UNCONVINCED or timeout or LLM unavailable) → fish stays, bait burned
    """
    channel = runner._get_post_target(fs)
    if channel is None:
        return False

    loc_name = location_data.get("name", fs.location_name)

    # --- Load or create the active legendary -------------------------
    async with runner.bot.scheduler.sessionmaker() as db:
        legendary = await fish_repo.get_active_legendary(
            db, fs.guild_id, fs.location_name
        )
        if legendary is None:
            # Generate a new one
            gen = await llm.generate_legendary(catch["name"], loc_name)
            if gen is None:
                try:
                    await channel.send(
                        f"<@{fs.user_id}> — a legendary shape circles once "
                        f"and vanishes. (LLM unavailable.)"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return False
            new_name, new_personality = gen
            legendary = await fish_repo.create_legendary(
                db,
                guild_id=fs.guild_id,
                location_name=fs.location_name,
                species_name=catch["name"],
                name=new_name,
                personality=new_personality,
                created_at=datetime.now(timezone.utc),
            )

        # Load memories
        past_encounters = await fish_repo.get_player_encounter_history(
            db, legendary.id, fs.user_id, limit=5
        )
        other_encounters = await fish_repo.get_recent_legendary_encounters(
            db, legendary.id, exclude_user_id=fs.user_id, limit=5
        )

        legendary_id = legendary.id
        legendary_name = legendary.name
        legendary_personality = legendary.personality

    # Resolve player display name for LLM context
    player_name = runner._resolve_display_name(fs)

    past_summaries = [e.dialogue_summary for e in past_encounters]
    other_summaries: list[tuple[str, str]] = []
    for e in other_encounters:
        other_display = f"<user:{e.user_id}>"
        try:
            guild = runner.bot.get_guild(fs.guild_id)
            m = guild.get_member(e.user_id) if guild else None
            if m is not None:
                other_display = m.display_name
        except Exception:
            pass
        other_summaries.append((other_display, e.dialogue_summary))

    # --- Dialogue loop (max 3 rounds) -------------------------------
    transcript: list[tuple[str, str]] = []  # (speaker, line)
    max_rounds = 3
    final_verdict = "UNCONVINCED"  # default if we bail out

    for round_num in range(1, max_rounds + 1):
        # Fish speaks
        fish_line = await llm.generate_legendary_line(
            legendary_name=legendary_name,
            personality=legendary_personality,
            player_name=player_name,
            past_with_player=past_summaries,
            recent_with_others=other_summaries,
            transcript=transcript,
            is_opening=(round_num == 1),
        )
        if fish_line is None:
            # LLM unavailable mid-encounter
            try:
                await channel.send(
                    f"<@{fs.user_id}> — the legendary flickers out of "
                    f"focus and is gone. (LLM unavailable.)"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            final_verdict = "UNCONVINCED"
            break

        transcript.append(("fish", fish_line))

        # Post the fish's message + Respond button
        title = (
            f"🐉 A legendary appears at {loc_name}..."
            if round_num == 1
            else f"🐉 {legendary_name} presses on..."
        )
        prompt_embed = discord.Embed(
            title=title,
            description=(
                f"<@{fs.user_id}>\n\n"
                f"**{legendary_name}**:\n*{fish_line}*\n\n"
                f"Respond within **{LEGENDARY_TURN_TIMEOUT}s** "
                f"(round {round_num} of {max_rounds})."
            ),
            color=0xF1C40F,
        )
        view = LegendaryResponseView(user_id=fs.user_id)
        try:
            message = await channel.send(embed=prompt_embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            final_verdict = "UNCONVINCED"
            break

        timed_out = await view.wait()

        if timed_out or not view.submitted or not view.response:
            escape_embed = discord.Embed(
                title=f"🌊 {legendary_name} turns away...",
                description=(
                    f"**{legendary_name}**:\n*{fish_line}*\n\n"
                    f"*(You hesitated. The legendary slips beyond reach.)*"
                ),
                color=0x95A5A6,
            )
            try:
                await message.edit(embed=escape_embed, view=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
            final_verdict = "UNCONVINCED"
            break

        player_response = view.response
        transcript.append(("player", player_response))

        # Judge
        verdict = await llm.judge_legendary_response(
            legendary_name=legendary_name,
            personality=legendary_personality,
            transcript=transcript,
            player_response=player_response,
        )
        if verdict is None:
            verdict = "UNCONVINCED"
        final_verdict = verdict

        # Render the turn result
        if verdict == "CONVINCED":
            final_embed = discord.Embed(
                title=f"🏆 {legendary_name} yields!",
                description=(
                    f"**{legendary_name}**:\n*{fish_line}*\n\n"
                    f"**{player_name}**:\n{player_response}\n\n"
                    f"*{legendary_name} has been caught. Its story ends here.*"
                ),
                color=0xF1C40F,
            )
            try:
                await message.edit(embed=final_embed, view=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
            break
        elif verdict == "UNCONVINCED":
            gone_embed = discord.Embed(
                title=f"🌊 {legendary_name} leaves...",
                description=(
                    f"**{legendary_name}**:\n*{fish_line}*\n\n"
                    f"**{player_name}**:\n{player_response}\n\n"
                    f"*The legendary has heard enough. It will remember.*"
                ),
                color=0x95A5A6,
            )
            try:
                await message.edit(embed=gone_embed, view=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
            break
        else:  # ALMOST
            is_last_round = round_num == max_rounds
            if is_last_round:
                # On the final round, ALMOST becomes UNCONVINCED — no more chances
                final_verdict = "UNCONVINCED"
                final_embed = discord.Embed(
                    title=f"🌊 {legendary_name} slips away...",
                    description=(
                        f"**{legendary_name}**:\n*{fish_line}*\n\n"
                        f"**{player_name}**:\n{player_response}\n\n"
                        f"*So close. The legendary disappears into the deep, "
                        f"not yet swayed.*"
                    ),
                    color=0x95A5A6,
                )
                try:
                    await message.edit(embed=final_embed, view=None)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                break
            almost_embed = discord.Embed(
                title=f"💬 {legendary_name} listens...",
                description=(
                    f"**{legendary_name}**:\n*{fish_line}*\n\n"
                    f"**{player_name}**:\n{player_response}\n\n"
                    f"*Something stirs. The fish waits for more.*"
                ),
                color=0xE67E22,
            )
            try:
                await message.edit(embed=almost_embed, view=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
            # fall through to next round

    # --- Record the encounter summary -------------------------------
    outcome = (
        "caught" if final_verdict == "CONVINCED"
        else "unconvinced" if final_verdict == "UNCONVINCED"
        else "escaped"
    )

    summary = await llm.summarize_encounter(
        legendary_name=legendary_name,
        personality=legendary_personality,
        transcript=transcript,
        outcome=outcome,
        player_name=player_name,
    )
    if summary is None:
        # Fallback summary if LLM fails
        last_player = next(
            (l for s, l in reversed(transcript) if s == "player"), ""
        )
        short = last_player[:60] + "..." if len(last_player) > 60 else last_player
        summary = f"Spoke briefly with {player_name} ({outcome})."
        if short:
            summary = f"Heard {player_name} say \"{short}\" and {outcome}."

    now = datetime.now(timezone.utc)

    async with runner.bot.scheduler.sessionmaker() as db:
        # Save the encounter
        try:
            await fish_repo.save_encounter(
                db,
                legendary_id=legendary_id,
                user_id=fs.user_id,
                outcome=outcome,
                dialogue_summary=summary,
                created_at=now,
            )
        except Exception:
            logger.exception("Failed to save legendary encounter")

        # If caught, retire this legendary and generate a replacement
        if outcome == "caught":
            try:
                await fish_repo.mark_legendary_caught(
                    db, legendary_id=legendary_id,
                    caught_by=fs.user_id, caught_at=now,
                )
            except Exception:
                logger.exception("Failed to mark legendary caught")

            # Generate a new legendary in its place — best effort, don't block
            try:
                gen = await llm.generate_legendary(catch["name"], loc_name)
                if gen is not None:
                    new_name, new_personality = gen
                    await fish_repo.create_legendary(
                        db,
                        guild_id=fs.guild_id,
                        location_name=fs.location_name,
                        species_name=catch["name"],
                        name=new_name,
                        personality=new_personality,
                        created_at=now,
                    )
            except Exception:
                logger.exception("Failed to generate replacement legendary")

    # Public flavor announcement on catch — post in the thread (record)
    # AND cross-post to the main channel (showoff). Legendary catches
    # are rare enough to justify the cross-post.
    if outcome == "caught":
        announcement = (
            f"👑 <@{fs.user_id}> has landed **{legendary_name}** at "
            f"**{loc_name}**! A new legend has already begun to circle..."
        )
        try:
            await channel.send(announcement)
        except (discord.Forbidden, discord.HTTPException):
            pass
        # Cross-post to the parent channel if we're in a thread
        if getattr(fs, "thread_id", None):
            parent = runner.bot.get_channel(fs.channel_id)
            if parent is not None:
                try:
                    await parent.send(announcement)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    return outcome == "caught"
