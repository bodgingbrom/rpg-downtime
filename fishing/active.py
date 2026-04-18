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
UNCOMMON_VIBE_TIMEOUT = 20    # phase 2
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
                # Phase 2 placeholder — auto-success for now
                success = await self._handle_placeholder(fs, catch, location_data)
            elif rarity == "rare":
                # Phase 3 placeholder — auto-success for now
                success = await self._handle_placeholder(fs, catch, location_data)
            elif rarity == "legendary":
                # Phase 4 placeholder — auto-success for now
                success = await self._handle_placeholder(fs, catch, location_data)
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
        channel = self.bot.get_channel(fs.channel_id)
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

        result_embed = discord.Embed(
            title=f"🐟 You caught a {catch['name']}!",
            description="\n".join(result_lines),
            color=0x2ECC71,
        )
        try:
            await message.edit(embed=result_embed, view=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

        return True

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
        channel = self.bot.get_channel(fs.channel_id)
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
        channel = self.bot.get_channel(fs.channel_id)
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
