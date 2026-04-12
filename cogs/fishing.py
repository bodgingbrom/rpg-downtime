from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
import checks
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from config import resolve_guild_setting
from derby import repositories as derby_repo
from economy import repositories as wallet_repo
from fishing import logic as fish_logic
from fishing import repositories as fish_repo


async def location_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for fishing location names."""
    locations = fish_logic.load_locations()
    current_lower = current.lower()
    choices = []
    for key, loc in locations.items():
        if current_lower in loc["name"].lower() or current_lower in key.lower():
            choices.append(app_commands.Choice(name=loc["name"], value=key))
        if len(choices) >= 25:
            break
    return choices


class Fishing(commands.Cog, name="fishing"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx)
        # Pre-load data caches
        fish_logic.load_rods()
        fish_logic.load_locations()

    # ------------------------------------------------------------------
    # /fish  (top-level group)
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="fish", description="Lazy Lures fishing commands")
    async def fish(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send(
                "Use `/fish start`, `/fish shop`, `/fish gear`, or `/fish locations`.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /fish start <location> [bait]
    # ------------------------------------------------------------------

    @fish.command(name="start", description="Start a fishing session")
    @app_commands.describe(
        location="The fishing spot to cast your line",
        bait="Bait type to use (defaults to first available)",
    )
    @app_commands.autocomplete(location=location_autocomplete)
    @app_commands.choices(bait=[
        app_commands.Choice(name="Worm", value="worm"),
        app_commands.Choice(name="Insect", value="insect"),
        app_commands.Choice(name="Shiny Lure", value="shiny_lure"),
        app_commands.Choice(name="Premium Bait", value="premium"),
    ])
    async def fish_start(
        self,
        context: Context,
        location: str,
        bait: str | None = None,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        # Validate location
        locations = fish_logic.load_locations()
        if location not in locations:
            await context.send(
                f"Unknown location `{location}`. Use `/fish locations` to see available spots.",
                ephemeral=True,
            )
            return

        async with self.bot.scheduler.sessionmaker() as session:
            # Guard: already fishing?
            active = await fish_repo.get_active_session(session, user_id, guild_id)
            if active:
                loc = locations.get(active.location_name, {})
                loc_name = loc.get("name", active.location_name)
                await context.send(
                    f"You're already fishing at **{loc_name}**! "
                    "Use `/fish stop` to end your session first.",
                    ephemeral=True,
                )
                return

            # Ensure player record exists
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)

            # Skill level gate
            loc_data = locations[location]
            player_level = fish_logic.get_level(player.fishing_xp)
            if not fish_logic.can_fish_at_location(player_level, loc_data):
                required = loc_data.get("skill_level", 1)
                loc_display = loc_data.get("name", location)
                await context.send(
                    f"You need **Fishing Level {required}** to fish at "
                    f"**{loc_display}**! You're currently Level {player_level}.",
                    ephemeral=True,
                )
                return

            # Determine bait type
            if bait:
                bait_inv = await fish_repo.get_bait(session, user_id, guild_id, bait)
                if not bait_inv or bait_inv.quantity <= 0:
                    bait_name = fish_logic.BAIT_TYPES.get(bait, {}).get("name", bait)
                    await context.send(
                        f"You don't have any **{bait_name}**! "
                        "Buy some with `/fish buy-bait`.",
                        ephemeral=True,
                    )
                    return
                selected_bait = bait
                bait_count = bait_inv.quantity
            else:
                # Auto-select first available bait
                all_bait = await fish_repo.get_all_bait(session, user_id, guild_id)
                available = [
                    b for b in all_bait
                    if b.quantity > 0 and b.bait_type in fish_logic.BAIT_TYPES
                ]
                if not available:
                    await context.send(
                        "You have no bait! Buy some with `/fish buy-bait`.",
                        ephemeral=True,
                    )
                    return
                # Pick in priority order
                bait_order = list(fish_logic.BAIT_TYPES.keys())
                available.sort(key=lambda b: bait_order.index(b.bait_type) if b.bait_type in bait_order else 99)
                selected_bait = available[0].bait_type
                bait_count = available[0].quantity

            # Calculate first cast time (with skill + trophy bonuses)
            loc_display = loc_data.get("name", location)
            rod_data = fish_logic.get_rod(player.rod_id)
            skill_reduction = fish_logic.get_skill_cast_reduction(
                player_level, loc_data.get("skill_level", 1)
            )
            caught_species = await fish_repo.get_caught_species_at_location(
                session, user_id, guild_id, location
            )
            trophy_reduction = (
                fish_logic.TROPHY_CAST_REDUCTION
                if fish_logic.has_location_trophy(caught_species, loc_data)
                else 0.0
            )
            cast_seconds = fish_logic.calculate_cast_time(
                loc_data["base_cast_time"], rod_data, selected_bait,
                skill_reduction=skill_reduction,
                trophy_reduction=trophy_reduction,
            )

            now = datetime.now(timezone.utc)
            next_catch = now + timedelta(seconds=cast_seconds)

            # Send the session embed (public, not ephemeral)
            # Create a placeholder session object for embed building
            class _TempSession:
                pass

            temp = _TempSession()
            temp.rod_id = player.rod_id
            temp.bait_type = selected_bait
            temp.bait_remaining = bait_count
            temp.location_name = location
            temp.total_fish = 0
            temp.total_coins = 0
            temp.last_catch_name = None
            temp.last_catch_value = None
            temp.last_catch_length = None
            temp.next_catch_at = next_catch
            temp.started_at = now

            # Public announcement — simple one-liner
            await context.channel.send(
                f"\U0001F3A3 **{context.author.display_name}** has started fishing at **{loc_display}**!"
            )

            # Deduct bait from inventory — committed to this session
            consumed = await fish_repo.consume_bait(
                session, user_id, guild_id, selected_bait, bait_count,
            )
            if not consumed:
                await context.send(
                    "Something went wrong reserving your bait. Try again.",
                    ephemeral=True,
                )
                return

            # Create the session record
            await fish_repo.create_session(
                session,
                user_id=user_id,
                guild_id=guild_id,
                location_name=location,
                rod_id=player.rod_id,
                bait_type=selected_bait,
                bait_remaining=bait_count,
                channel_id=context.channel.id,
                message_id=0,
                started_at=now,
                next_catch_at=next_catch,
            )

        bait_name = fish_logic.BAIT_TYPES.get(selected_bait, {}).get("name", selected_bait)
        await context.send(
            f"Started fishing at **{loc_display}** with **{bait_name}** "
            f"({bait_count} casts). Good luck!",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /fish stop
    # ------------------------------------------------------------------

    @fish.command(name="stop", description="Stop your current fishing session")
    async def fish_stop(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            active = await fish_repo.get_active_session(session, user_id, guild_id)
            if not active:
                await context.send("You're not currently fishing.", ephemeral=True)
                return

            # Refund unused bait back to inventory
            if active.bait_remaining > 0:
                await fish_repo.add_bait(
                    session, user_id, guild_id,
                    active.bait_type, active.bait_remaining,
                )

            await fish_repo.end_session(session, active.id)

        embed = fish_logic.build_session_embed(
            active, catch=None, session_ended=True
        )
        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish status
    # ------------------------------------------------------------------

    @fish.command(name="status", description="Check your current fishing session")
    async def fish_status(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            active = await fish_repo.get_active_session(session, user_id, guild_id)

        if not active:
            await context.send("You're not currently fishing.", ephemeral=True)
            return

        embed = fish_logic.build_session_embed(active, catch=None, session_ended=False)
        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish shop
    # ------------------------------------------------------------------

    @fish.command(name="shop", description="Browse bait and rod upgrades")
    async def fish_shop(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)

        balance = wallet.balance if wallet else 0
        rod = fish_logic.get_rod(player.rod_id)
        next_rod = fish_logic.get_upgrade_path(player.rod_id)

        embed = discord.Embed(
            title="\U0001F3A3 Lazy Lures \u2014 Shop",
            color=0x2ECC71,
        )

        # Bait section
        bait_lines = []
        for bait_id, info in fish_logic.BAIT_TYPES.items():
            bait_lines.append(f"**{info['name']}** \u2014 {info['cost']} coins")
        embed.add_field(
            name="\U0001FAB1 Bait",
            value="\n".join(bait_lines),
            inline=False,
        )

        # Rod upgrade section
        if next_rod:
            rod_text = (
                f"Current: **{rod['name']}**\n"
                f"Next: **{next_rod['name']}** \u2014 {next_rod['cost']} coins\n"
                f"Use `/fish upgrade-rod` to upgrade"
            )
        else:
            rod_text = f"Current: **{rod['name']}** (max tier!)"
        embed.add_field(name="\U0001FAAD Rod", value=rod_text, inline=False)

        embed.set_footer(text=f"Your balance: {balance} coins")
        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish buy-bait <type> <quantity>
    # ------------------------------------------------------------------

    @fish.command(name="buy-bait", description="Buy bait for fishing")
    @app_commands.describe(
        type="The type of bait to buy",
        quantity="How many to buy",
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="Worm (2 coins)", value="worm"),
        app_commands.Choice(name="Insect (5 coins)", value="insect"),
        app_commands.Choice(name="Shiny Lure (12 coins)", value="shiny_lure"),
        app_commands.Choice(name="Premium Bait (20 coins)", value="premium"),
    ])
    async def fish_buy_bait(
        self,
        context: Context,
        type: str,
        quantity: int,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        if quantity < 1:
            await context.send("Quantity must be at least 1.", ephemeral=True)
            return

        bait_info = fish_logic.BAIT_TYPES.get(type)
        if not bait_info:
            await context.send("Unknown bait type.", ephemeral=True)
            return

        total_cost = bait_info["cost"] * quantity

        async with self.bot.scheduler.sessionmaker() as session:
            # Get or create wallet
            gs = await derby_repo.get_guild_settings(session, guild_id)
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=user_id, guild_id=guild_id, balance=default_bal,
                )

            if wallet.balance < total_cost:
                await context.send(
                    f"Not enough coins! You need **{total_cost}** but have **{wallet.balance}**.",
                    ephemeral=True,
                )
                return

            wallet.balance -= total_cost
            bait_row = await fish_repo.add_bait(session, user_id, guild_id, type, quantity)

        await context.send(
            f"Bought **{quantity}x {bait_info['name']}** for **{total_cost}** coins! "
            f"(You now have {bait_row.quantity})",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /fish upgrade-rod
    # ------------------------------------------------------------------

    @fish.command(name="upgrade-rod", description="Upgrade your fishing rod")
    async def fish_upgrade_rod(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)

            # Guard: fishing right now?
            active = await fish_repo.get_active_session(session, user_id, guild_id)
            if active:
                await context.send(
                    "Can't switch rods mid-session! Use `/fish stop` first.",
                    ephemeral=True,
                )
                return

            next_rod = fish_logic.get_upgrade_path(player.rod_id)
            if not next_rod:
                current = fish_logic.get_rod(player.rod_id)
                await context.send(
                    f"Your **{current['name']}** is already the best available!",
                    ephemeral=True,
                )
                return

            # Check wallet
            gs = await derby_repo.get_guild_settings(session, guild_id)
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=user_id, guild_id=guild_id, balance=default_bal,
                )

            cost = next_rod["cost"]
            if wallet.balance < cost:
                await context.send(
                    f"Not enough coins! The **{next_rod['name']}** costs "
                    f"**{cost}** coins but you have **{wallet.balance}**.",
                    ephemeral=True,
                )
                return

            wallet.balance -= cost
            await fish_repo.update_player(
                session, user_id, guild_id, rod_id=next_rod["id"]
            )
            await session.commit()

        current_rod = fish_logic.get_rod(player.rod_id)
        await context.send(
            f"Upgraded from **{current_rod['name']}** to **{next_rod['name']}**! "
            f"(-{cost} coins)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /fish locations
    # ------------------------------------------------------------------

    @fish.command(name="locations", description="View available fishing locations")
    async def fish_locations(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        locations = fish_logic.load_locations()

        if not locations:
            await context.send("No fishing locations are available.", ephemeral=True)
            return

        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)
            player_level = fish_logic.get_level(player.fishing_xp)

            # Gather trophy status for each location
            trophy_status: dict[str, bool] = {}
            for key, loc in locations.items():
                caught = await fish_repo.get_caught_species_at_location(
                    session, user_id, guild_id, key,
                )
                trophy_status[key] = fish_logic.has_location_trophy(caught, loc)

        embed = discord.Embed(
            title="\U0001F30A Lazy Lures \u2014 Locations",
            color=0x3498DB,
        )

        difficulty_emoji = {1: "\U0001F7E2", 2: "\U0001F7E1", 3: "\U0001F534"}

        for key, loc in locations.items():
            skill = loc.get("skill_level", 1)
            locked = not fish_logic.can_fish_at_location(player_level, loc)
            trophy = trophy_status.get(key, False)

            if locked:
                lock_icon = "\U0001F512"  # 🔒
            else:
                lock_icon = difficulty_emoji.get(skill, "\u26AA")

            trophy_icon = " \U0001F3C6" if trophy else ""
            cast_min = loc.get("base_cast_time", 600) // 60

            fish_list = loc.get("fish", [])
            fish_names = ", ".join(f["name"] for f in fish_list[:4])
            if len(fish_list) > 4:
                fish_names += f" +{len(fish_list) - 4} more"

            if locked:
                value = (
                    f"*Requires Fishing Level {skill}*\n"
                    f"*{loc.get('description', '')}*"
                )
            else:
                value = (
                    f"*{loc.get('description', '')}*\n"
                    f"Base cast: ~{cast_min} min | Fish: {fish_names}"
                )

            embed.add_field(
                name=f"{lock_icon} {loc['name']}{trophy_icon}",
                value=value,
                inline=False,
            )

        embed.set_footer(text=f"Your level: {player_level}")
        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish gear
    # ------------------------------------------------------------------

    @fish.command(name="gear", description="View your fishing gear and bait")
    async def fish_gear(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)
            all_bait = await fish_repo.get_all_bait(session, user_id, guild_id)

        rod = fish_logic.get_rod(player.rod_id)

        embed = discord.Embed(
            title="\U0001F3A3 Lazy Lures \u2014 Your Gear",
            color=0x2ECC71,
        )
        # Level & XP
        level = fish_logic.get_level(player.fishing_xp)
        next_info = fish_logic.get_xp_for_next_level(player.fishing_xp)
        if next_info:
            xp_needed, next_lvl = next_info
            level_text = f"Level {level} ({player.fishing_xp} XP \u2014 {xp_needed} to Lv{next_lvl})"
        else:
            level_text = f"Level {level} \u2014 MAX ({player.fishing_xp} XP)"
        embed.add_field(name="Skill", value=level_text, inline=False)

        embed.add_field(name="Rod", value=rod["name"], inline=True)
        embed.add_field(
            name="Notifications",
            value="On" if player.notify_on_catch else "Off",
            inline=True,
        )

        # Bait inventory
        bait_map = {b.bait_type: b.quantity for b in all_bait}
        bait_lines = []
        for bait_id, info in fish_logic.BAIT_TYPES.items():
            qty = bait_map.get(bait_id, 0)
            bait_lines.append(f"**{info['name']}**: {qty}")

        embed.add_field(
            name="Bait Inventory",
            value="\n".join(bait_lines) if bait_lines else "Empty!",
            inline=False,
        )

        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish notify
    # ------------------------------------------------------------------

    @fish.command(name="notify", description="Toggle catch notifications")
    async def fish_notify(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await fish_repo.get_or_create_player(session, user_id, guild_id)
            new_val = not player.notify_on_catch
            await fish_repo.update_player(
                session, user_id, guild_id, notify_on_catch=new_val
            )

        state = "on" if new_val else "off"
        await context.send(
            f"Catch notifications turned **{state}**.",
            ephemeral=True,
        )


    # ------------------------------------------------------------------
    # /fish log [location]
    # ------------------------------------------------------------------

    @fish.command(name="log", description="View your fish collection log")
    @app_commands.describe(location="Filter by location (optional)")
    @app_commands.autocomplete(location=location_autocomplete)
    async def fish_log(
        self,
        context: Context,
        location: str | None = None,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id
        locations = fish_logic.load_locations()

        if location and location not in locations:
            await context.send(
                f"Unknown location `{location}`. Use `/fish locations` to see options.",
                ephemeral=True,
            )
            return

        async with self.bot.scheduler.sessionmaker() as session:
            if location:
                catches = await fish_repo.get_fish_catches_for_location(
                    session, user_id, guild_id, location,
                )
            else:
                catches = await fish_repo.get_all_fish_catches(
                    session, user_id, guild_id,
                )

        rarity_emoji = {
            "common": "\u26AA",
            "uncommon": "\U0001F539",
            "rare": "\U0001F48E",
            "legendary": "\u2B50",
        }

        if location:
            # Show single-location view with missing species
            loc_data = locations[location]
            loc_name = loc_data.get("name", location)
            all_species = {f["name"] for f in loc_data.get("fish", [])}
            caught_names = {c.fish_name for c in catches}
            caught_map = {c.fish_name: c for c in catches}

            embed = discord.Embed(
                title=f"\U0001F4D6 Fish Log \u2014 {loc_name}",
                description=f"Discovered {len(caught_names)}/{len(all_species)} species",
                color=0x3498DB,
            )

            lines: list[str] = []
            for fish_def in loc_data.get("fish", []):
                name = fish_def["name"]
                rarity = fish_def.get("rarity", "common")
                emoji = rarity_emoji.get(rarity, "")
                if name in caught_map:
                    c = caught_map[name]
                    lines.append(
                        f"{emoji} **{name}** — {c.catch_count}× | "
                        f"Best: {c.best_length}in, {c.best_value} coins"
                    )
                else:
                    lines.append(f"{emoji} ??? *({rarity})*")

            embed.add_field(
                name="Species",
                value="\n".join(lines) if lines else "No fish at this location.",
                inline=False,
            )
        else:
            # Show overview across all locations
            embed = discord.Embed(
                title="\U0001F4D6 Fish Log \u2014 Overview",
                color=0x3498DB,
            )

            if not catches:
                embed.description = "You haven't caught any fish yet! Use `/fish start` to begin."
            else:
                # Group by location
                by_location: dict[str, list] = {}
                for c in catches:
                    by_location.setdefault(c.location_name, []).append(c)

                for loc_key, loc_data in locations.items():
                    loc_name = loc_data.get("name", loc_key)
                    total_species = len(loc_data.get("fish", []))
                    loc_catches = by_location.get(loc_key, [])
                    caught_count = len(loc_catches)
                    total_caught_fish = sum(c.catch_count for c in loc_catches)

                    if caught_count == 0:
                        embed.add_field(
                            name=loc_name,
                            value=f"0/{total_species} species discovered",
                            inline=False,
                        )
                    else:
                        embed.add_field(
                            name=loc_name,
                            value=(
                                f"{caught_count}/{total_species} species | "
                                f"{total_caught_fish} total catches"
                            ),
                            inline=False,
                        )

        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /fish trophies
    # ------------------------------------------------------------------

    @fish.command(name="trophies", description="View your location trophies")
    async def fish_trophies(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id
        locations = fish_logic.load_locations()

        embed = discord.Embed(
            title="\U0001F3C6 Lazy Lures \u2014 Trophies",
            color=0xF1C40F,
        )

        async with self.bot.scheduler.sessionmaker() as session:
            earned = 0
            total = len(locations)

            for key, loc_data in locations.items():
                loc_name = loc_data.get("name", key)
                all_species = {f["name"] for f in loc_data.get("fish", [])}
                caught = await fish_repo.get_caught_species_at_location(
                    session, user_id, guild_id, key,
                )
                has_trophy = fish_logic.has_location_trophy(caught, loc_data)
                caught_count = len(caught & all_species)
                total_species = len(all_species)

                if has_trophy:
                    earned += 1
                    icon = "\U0001F3C6"
                    bonus = f" — **{int(fish_logic.TROPHY_CAST_REDUCTION * 100)}% cast reduction**"
                else:
                    icon = "\u2B1C"
                    bonus = ""

                missing = all_species - caught
                if missing and not has_trophy:
                    missing_text = f"\nMissing: {', '.join(sorted(missing))}"
                else:
                    missing_text = ""

                embed.add_field(
                    name=f"{icon} {loc_name}",
                    value=f"{caught_count}/{total_species} species{bonus}{missing_text}",
                    inline=False,
                )

        embed.description = f"Trophies earned: **{earned}/{total}**"
        await context.send(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Fishing(bot))
