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
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from . import logic as fish_logic
from . import repositories as fish_repo
from .handlers import (
    handle_common,
    handle_legendary,
    handle_rare,
    handle_uncommon,
)

logger = logging.getLogger("discord_bot")


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
                success = await handle_common(self, fs, catch, location_data)
            elif rarity == "uncommon":
                success = await handle_uncommon(self, fs, catch, location_data)
            elif rarity == "rare":
                success = await handle_rare(self, fs, catch, location_data)
            elif rarity == "legendary":
                success = await handle_legendary(self, fs, catch, location_data)
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
