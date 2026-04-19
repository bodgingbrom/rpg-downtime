"""Active fishing mode — per-session asyncio task runner and event dispatch.

Each active session has its own long-running asyncio task that sleeps until
the next bite, then posts an interactive prompt and waits for the player's
response. On success, awards coins/XP/log updates; on failure, the fish
escapes and the bait is burned anyway.

The scheduler tick (derby/scheduler.py `_fishing_tick`) only processes
AFK-mode sessions — active sessions are skipped via the `mode` filter on
`get_all_due_sessions`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from . import llm
from . import logic as fish_logic
from . import repositories as fish_repo

logger = logging.getLogger("discord_bot")


# Timeouts per event type (seconds)
COMMON_REEL_TIMEOUT = 300     # commons: long window, no fail state — just miss the flavor
UNCOMMON_VIBE_TIMEOUT = 25    # from bite to modal submission
RARE_HAIKU_TIMEOUT = 40       # phase 3
LEGENDARY_TURN_TIMEOUT = 60   # phase 4


# ---------------------------------------------------------------------------
# Reel-in button view for common catches
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Active session runner
# ---------------------------------------------------------------------------


class ActiveFishingRunner:
    """Owns the asyncio tasks for all in-flight active sessions.

    Instantiated once on the bot and shared across cogs / startup hooks.
    """

    def __init__(self, bot):
        self.bot = bot
        self._tasks: dict[int, asyncio.Task] = {}

    # --- lifecycle --------------------------------------------------------

    def start_session(self, session_id: int) -> None:
        """Spawn a task that drives a session. No-op if one already exists."""
        if session_id in self._tasks and not self._tasks[session_id].done():
            return
        task = asyncio.create_task(self._run(session_id))
        self._tasks[session_id] = task

    def stop_session(self, session_id: int) -> None:
        """Cancel the task for a session (e.g. when /fish stop is used)."""
        task = self._tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def is_running(self, session_id: int) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done())

    # --- main loop --------------------------------------------------------

    async def _run(self, session_id: int) -> None:
        try:
            while True:
                async with self.bot.scheduler.sessionmaker() as db:
                    fs = await fish_repo.get_session_by_id(db, session_id)

                if fs is None or not fs.active or fs.bait_remaining <= 0:
                    return

                # Sleep until the next bite
                now = datetime.now(timezone.utc)
                next_ts = fs.next_catch_at
                if next_ts.tzinfo is None:
                    next_ts = next_ts.replace(tzinfo=timezone.utc)
                delay = (next_ts - now).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)

                # Re-load — session may have been stopped while sleeping
                async with self.bot.scheduler.sessionmaker() as db:
                    fs = await fish_repo.get_session_by_id(db, session_id)
                if fs is None or not fs.active:
                    return

                await self._process_bite(session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Active fishing runner crashed for session %s", session_id
            )
        finally:
            self._tasks.pop(session_id, None)

    # --- bite processing --------------------------------------------------

    async def _process_bite(self, session_id: int) -> None:
        """Roll a catch, dispatch an event, award or burn bait."""
        async with self.bot.scheduler.sessionmaker() as db:
            fs = await fish_repo.get_session_by_id(db, session_id)
            if fs is None or not fs.active:
                return

            # Load race for racial modifiers
            try:
                from rpg import repositories as rpg_repo
                from rpg.logic import get_racial_modifier
                profile = await rpg_repo.get_or_create_profile(
                    db, fs.user_id, fs.guild_id
                )
                race = profile.race
            except Exception:
                race = None

                def get_racial_modifier(_r, _k, default):  # type: ignore[misc]
                    return default

            locations = fish_logic.load_locations()
            location_data = locations.get(fs.location_name)
            if location_data is None:
                # Location deleted — refund bait, end session
                if fs.bait_remaining > 0:
                    await fish_repo.add_bait(
                        db, fs.user_id, fs.guild_id,
                        fs.bait_type, fs.bait_remaining,
                    )
                await fish_repo.end_session(db, fs.id)
                return

            rod_data = fish_logic.get_rod(fs.rod_id)
            rare_bonus = get_racial_modifier(race, "fishing.rare_weight_bonus", 0.0)

            # Roll a catch — active mode has no trash
            catch = fish_logic.select_catch(
                location_data, rod_data, fs.bait_type,
                rare_weight_bonus=rare_bonus,
                include_trash=False,
            )

        # Dispatch to event handler by rarity — released the DB session while
        # awaiting user interaction to avoid holding a transaction open
        rarity = (catch.get("rarity") or "common").lower()
        try:
            if rarity == "common":
                success = await self._handle_common(fs, catch, location_data)
            elif rarity == "uncommon":
                success = await self._handle_uncommon(fs, catch, location_data)
            elif rarity == "rare":
                success = await self._handle_rare(fs, catch, location_data)
            elif rarity == "legendary":
                success = await self._handle_legendary(fs, catch, location_data)
            else:
                success = True
        except Exception:
            logger.exception("Active event handler failed")
            success = False

        # Commit the outcome
        async with self.bot.scheduler.sessionmaker() as db:
            fs = await fish_repo.get_session_by_id(db, session_id)
            if fs is None or not fs.active:
                return
            await self._finalize_bite(
                db, fs, catch, location_data, rod_data, race, success,
            )

    # --- helpers ---------------------------------------------------------

    def _resolve_display_name(self, fs) -> str:
        """Return the angler's display name for embed text.

        Prefers the snapshotted name stored on the session (set at
        ``/fish active`` time from ``context.author.display_name``), then
        falls back to the member cache, then to ``"Angler"`` as a
        last resort. Embed titles don't render raw ``<@id>`` mentions,
        so we always need a resolved string.
        """
        snapshot = getattr(fs, "angler_name", None)
        if snapshot:
            return snapshot
        try:
            guild = self.bot.get_guild(fs.guild_id)
            member = guild.get_member(fs.user_id) if guild else None
            if member is not None:
                return member.display_name
        except Exception:
            pass
        return "Angler"

    def _get_post_target(self, fs):
        """Return the channel/thread the session should post to.

        Active sessions have a dedicated thread; AFK sessions post in the
        original channel. Falls back to ``channel_id`` if the thread is
        gone (deleted, archived past the bot's view, etc.).
        """
        thread_id = getattr(fs, "thread_id", None)
        if thread_id:
            target = self.bot.get_channel(thread_id)
            if target is not None:
                return target
        return self.bot.get_channel(fs.channel_id)

    # --- event handlers ---------------------------------------------------

    async def _handle_common(
        self,
        fs,
        catch: dict[str, Any],
        location_data: dict[str, Any],
    ) -> bool:
        """Common whisper: click → LLM whisper → always catches.

        Timeout just means they miss the flavor text; they still catch.
        """
        channel = self._get_post_target(fs)
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

        angler = self._resolve_display_name(fs)
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

    async def _log_event(
        self,
        fs,
        rarity: str,
        fish_species: str,
        prompt_text: str,
        player_response: str,
        outcome: str,
    ) -> None:
        """Persist an active-mode event to ActiveFishingEventLog.

        Best-effort — logging failures are swallowed so they never break a
        catch. Used by uncommon + rare handlers. Legendaries go through
        ``save_encounter`` instead.
        """
        try:
            async with self.bot.scheduler.sessionmaker() as db:
                await fish_repo.log_active_event(
                    db,
                    user_id=fs.user_id,
                    guild_id=fs.guild_id,
                    rarity=rarity,
                    location_name=fs.location_name,
                    fish_species=fish_species,
                    prompt_text=prompt_text,
                    player_response=player_response,
                    outcome=outcome,
                    created_at=datetime.now(timezone.utc),
                )
        except Exception:
            logger.exception("Failed to log active fishing event")

    async def _handle_uncommon(
        self,
        fs,
        catch: dict[str, Any],
        location_data: dict[str, Any],
    ) -> bool:
        """Uncommon vibe check: atmospheric passage + one-word LLM judge.

        PASS → catch the fish. FAIL or timeout → fish escapes, bait burned.
        """
        channel = self._get_post_target(fs)
        if channel is None:
            # No channel to prompt in — conservative: escape
            return False

        loc_name = location_data.get("name", fs.location_name)

        # Generate the atmospheric passage up front
        passage = await llm.generate_vibe_passage(
            catch["name"], catch.get("rarity", "uncommon"), loc_name
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
            await self._log_event(
                fs, "uncommon", catch["name"],
                prompt_text=passage, player_response="",
                outcome="timeout",
            )
            return False

        # Judge the word
        verdict = await llm.judge_vibe(passage, view.word)
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
            angler = self._resolve_display_name(fs)
            result_embed = discord.Embed(
                title=f"🐟 {angler} caught a {catch['name']}!",
                description="\n".join(result_lines),
                color=0x2ECC71,
            )
            try:
                await message.edit(embed=result_embed, view=None)
            except (discord.Forbidden, discord.HTTPException):
                pass
            await self._log_event(
                fs, "uncommon", catch["name"],
                prompt_text=passage, player_response=view.word,
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
        await self._log_event(
            fs, "uncommon", catch["name"],
            prompt_text=passage, player_response=view.word,
            outcome="escaped",
        )
        return False

    async def _handle_rare(
        self,
        fs,
        catch: dict[str, Any],
        location_data: dict[str, Any],
    ) -> bool:
        """Rare haiku: LLM writes opening 5-7 lines, player closes with 5.

        PASS → catch the fish AND save the haiku to the player's log.
        FAIL or timeout → fish escapes, bait burned, haiku not saved.
        """
        channel = self._get_post_target(fs)
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
            await self._log_event(
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
                async with self.bot.scheduler.sessionmaker() as db:
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
            angler = self._resolve_display_name(fs)
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
            await self._log_event(
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
        await self._log_event(
            fs, "rare", catch["name"],
            prompt_text=prompt_log_text, player_response=player_line,
            outcome="escaped",
        )
        return False

    async def _handle_legendary(
        self,
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
        channel = self._get_post_target(fs)
        if channel is None:
            return False

        loc_name = location_data.get("name", fs.location_name)

        # --- Load or create the active legendary -------------------------
        async with self.bot.scheduler.sessionmaker() as db:
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
        player_name = self._resolve_display_name(fs)

        past_summaries = [e.dialogue_summary for e in past_encounters]
        other_summaries: list[tuple[str, str]] = []
        for e in other_encounters:
            other_display = f"<user:{e.user_id}>"
            try:
                guild = self.bot.get_guild(fs.guild_id)
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

        async with self.bot.scheduler.sessionmaker() as db:
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
                parent = self.bot.get_channel(fs.channel_id)
                if parent is not None:
                    try:
                        await parent.send(announcement)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        return outcome == "caught"

    async def _handle_placeholder(
        self,
        fs,
        catch: dict[str, Any],
        location_data: dict[str, Any],
    ) -> bool:
        """Auto-success handler for rarities not yet implemented.

        Posts a plain "you caught it" message and returns True. Replaced
        in Phases 2-4 with the real mechanics.
        """
        channel = self._get_post_target(fs)
        if channel is not None:
            rarity = catch.get("rarity", "common")
            try:
                embed = discord.Embed(
                    title=f"🐟 {catch['name']} ({rarity})",
                    description=(
                        f"<@{fs.user_id}> pulled in a **{catch['name']}** "
                        f"for **{catch['value']}** coins!"
                    ),
                    color=0x2ECC71,
                )
                await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return True

    # --- catch finalization ----------------------------------------------

    async def _finalize_bite(
        self,
        db,
        fs,
        catch: dict[str, Any],
        location_data: dict[str, Any],
        rod_data: dict[str, Any],
        race,
        success: bool,
    ) -> None:
        """Commit the outcome: award coins/XP/log on success, burn bait either way."""
        from economy import repositories as wallet_repo

        try:
            from rpg.logic import get_racial_modifier
        except Exception:
            def get_racial_modifier(_r, _k, default):  # type: ignore[misc]
                return default

        now = datetime.now(timezone.utc)
        new_remaining = fs.bait_remaining - 1
        update_kwargs: dict[str, Any] = {"bait_remaining": new_remaining}
        trophy_just_earned = False
        new_level = fish_logic.get_level(0)
        old_level = new_level

        if success:
            # Award coins
            if catch["value"] > 0:
                wallet = await wallet_repo.get_wallet(db, fs.user_id, fs.guild_id)
                if wallet is None:
                    wallet = await wallet_repo.create_wallet(
                        db, user_id=fs.user_id, guild_id=fs.guild_id,
                    )
                wallet.balance += catch["value"]

            # Award XP (with racial multiplier)
            xp_gained = fish_logic.calculate_catch_xp(catch, location_data)
            xp_mult = get_racial_modifier(race, "global.xp_multiplier", 1.0)
            xp_gained = int(xp_gained * xp_mult)
            _, old_level, new_level = await fish_repo.add_xp(
                db, fs.user_id, fs.guild_id, xp_gained
            )

            # Log the catch and check trophy
            if not catch.get("is_trash"):
                fish_catch = await fish_repo.upsert_fish_catch(
                    db, fs.user_id, fs.guild_id,
                    catch["name"], fs.location_name,
                    catch.get("rarity", "common"),
                    catch.get("length") or 0,
                    catch["value"], now,
                )
                if fish_catch.catch_count == 1:
                    caught = await fish_repo.get_caught_species_at_location(
                        db, fs.user_id, fs.guild_id, fs.location_name
                    )
                    trophy_just_earned = fish_logic.has_location_trophy(
                        caught, location_data
                    )

            # Daily summary
            date_str = now.strftime("%Y-%m-%d")
            await fish_repo.upsert_daily_summary(
                db, fs.user_id, fs.guild_id, date_str, catch
            )

            update_kwargs["total_fish"] = fs.total_fish + 1
            update_kwargs["total_coins"] = fs.total_coins + catch["value"]
            update_kwargs["last_catch_name"] = catch["name"]
            update_kwargs["last_catch_value"] = catch["value"]
            update_kwargs["last_catch_length"] = catch.get("length")

        # Schedule next bite (30-90s base, with reductions)
        caught_species = await fish_repo.get_caught_species_at_location(
            db, fs.user_id, fs.guild_id, fs.location_name
        )
        has_trophy = fish_logic.has_location_trophy(caught_species, location_data)
        skill_reduction = fish_logic.get_skill_cast_reduction(
            new_level, location_data.get("skill_level", 1)
        )
        trophy_reduction = (
            fish_logic.TROPHY_CAST_REDUCTION if has_trophy else 0.0
        )
        cast_mult = get_racial_modifier(race, "fishing.cast_time_multiplier", 1.0)
        next_catch = now + timedelta(
            seconds=fish_logic.calculate_active_cast_time(
                rod_data, fs.bait_type,
                skill_reduction=skill_reduction,
                trophy_reduction=trophy_reduction,
                cast_multiplier=cast_mult,
            )
        )
        update_kwargs["next_catch_at"] = next_catch

        if new_remaining <= 0:
            update_kwargs["active"] = False

        await fish_repo.update_session(db, fs.id, **update_kwargs)

        # Announcements
        channel = self._get_post_target(fs)
        if channel is not None:
            try:
                if success and new_level > old_level:
                    await channel.send(
                        f"\u2B50 <@{fs.user_id}> reached "
                        f"**Fishing Level {new_level}**!"
                    )
                if trophy_just_earned:
                    loc_name = location_data.get("name", fs.location_name)
                    await channel.send(
                        f"\U0001f3c6 <@{fs.user_id}> completed the "
                        f"**{loc_name}** collection! Trophy earned!"
                    )
                if new_remaining <= 0:
                    await channel.send(
                        f"🎣 <@{fs.user_id}>'s active fishing session "
                        f"has ended — out of bait."
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass


# ---------------------------------------------------------------------------
# Startup cleanup
# ---------------------------------------------------------------------------


async def cleanup_orphaned_sessions(bot) -> int:
    """On bot startup, end any active-mode sessions whose task is gone.

    The bot restart killed all asyncio tasks, so any leftover active-mode
    sessions are orphaned. Refund their bait and mark them inactive.

    Returns the number of sessions cleaned up.
    """
    count = 0
    async with bot.scheduler.sessionmaker() as db:
        orphans = await fish_repo.get_orphaned_active_sessions(db)
        for fs in orphans:
            if fs.bait_remaining > 0:
                await fish_repo.add_bait(
                    db, fs.user_id, fs.guild_id,
                    fs.bait_type, fs.bait_remaining,
                )
            await fish_repo.end_session(db, fs.id)
            count += 1
    if count:
        logger.info("Cleaned up %d orphaned active fishing sessions", count)
    return count
