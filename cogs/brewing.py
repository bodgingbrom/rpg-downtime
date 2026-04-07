from __future__ import annotations

import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from brewing import logic as brew_logic
from brewing import potions as brew_potions
from brewing import repositories as brew_repo
from brewing.shop import get_daily_shop
from config import resolve_guild_setting
from derby import repositories as repo
from economy import repositories as wallet_repo


async def shop_ingredient_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for buyable ingredients (today's shop + free)."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    async with sessionmaker() as session:
        all_ingredients = await brew_repo.get_all_ingredients(session)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    shop_items = get_daily_shop(date_str, all_ingredients)
    free_items = [i for i in all_ingredients if i.rarity == "free"]
    available = shop_items + free_items

    current_lower = current.lower()
    choices = []
    for ing in available:
        if current_lower in ing.name.lower():
            label = ing.name if ing.base_cost == 0 else f"{ing.name} — {ing.base_cost} coins"
            choices.append(app_commands.Choice(name=label, value=ing.name))
        if len(choices) >= 25:
            break
    return choices


async def _brew_ingredient_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for brew add — shows free + owned ingredients."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    user_id = interaction.user.id

    async with sessionmaker() as session:
        all_ingredients = await brew_repo.get_all_ingredients(session)
        player_inv = await brew_repo.get_player_ingredients(session, user_id, guild_id)
        revealed = await brew_repo.get_revealed_ingredients(session, user_id, guild_id)

    ing_map = {i.id: i for i in all_ingredients}
    revealed_ids = {r.ingredient_id for r in revealed}
    free_items = [i for i in all_ingredients if i.rarity == "free"]

    def _tag_suffix(ing):
        if ing.id in revealed_ids:
            return f" [{ing.tag_1}/{ing.tag_2}]"
        return ""

    # Build available list: free ingredients + owned inventory
    available: list[tuple[str, str]] = []  # (label, value)
    for ing in free_items:
        available.append((f"{ing.name}{_tag_suffix(ing)}", ing.name))
    for pi in player_inv:
        ing = ing_map.get(pi.ingredient_id)
        if ing and ing.rarity != "free":
            available.append((f"{ing.name} (x{pi.quantity}){_tag_suffix(ing)}", ing.name))

    current_lower = current.lower()
    choices = []
    for label, value in available:
        if current_lower in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=value))
        if len(choices) >= 25:
            break
    return choices


async def _all_ingredient_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete showing all 28 ingredients for journal analysis."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    user_id = interaction.user.id

    async with sessionmaker() as session:
        all_ingredients = await brew_repo.get_all_ingredients(session)
        revealed = await brew_repo.get_revealed_ingredients(session, user_id, guild_id)

    revealed_ids = {r.ingredient_id for r in revealed}

    current_lower = current.lower()
    choices = []
    for ing in all_ingredients:
        if current_lower in ing.name.lower():
            label = ing.name
            if ing.id in revealed_ids:
                label += f" [{ing.tag_1}/{ing.tag_2}]"
            choices.append(app_commands.Choice(name=label[:100], value=ing.name))
        if len(choices) >= 25:
            break
    return choices


class Brewing(commands.Cog, name="brewing"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="ingredients", description="Potion Panic ingredient commands")
    async def ingredients(self, context: Context) -> None:
        if context.invoked_subcommand is not None:
            return
        # Default: show inventory
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player_inv = await brew_repo.get_player_ingredients(
                session, user_id, guild_id
            )
            if not player_inv:
                embed = discord.Embed(
                    title="Ingredient Inventory",
                    description=(
                        "Your ingredient pouch is empty.\n"
                        "Use `/ingredients shop` to browse today's stock, "
                        "or `/ingredients buy` to grab free ingredients!"
                    ),
                    color=0x3498DB,
                )
                await context.send(embed=embed)
                return

            # Look up ingredient details for display
            all_ingredients = await brew_repo.get_all_ingredients(session)
            ing_map = {i.id: i for i in all_ingredients}

        # Group by rarity
        groups: dict[str, list[str]] = {"free": [], "uncommon": [], "rare": []}
        for pi in player_inv:
            ing = ing_map.get(pi.ingredient_id)
            if ing is None:
                continue
            groups.setdefault(ing.rarity, []).append(
                f"{ing.name} x{pi.quantity}"
            )

        embed = discord.Embed(
            title="Ingredient Inventory",
            color=0x3498DB,
        )
        rarity_labels = {"free": "Common", "uncommon": "Uncommon", "rare": "Rare"}
        rarity_emojis = {"free": "\U0001f7e2", "uncommon": "\U0001f535", "rare": "\U0001f7e1"}
        for rarity in ("free", "uncommon", "rare"):
            items = groups.get(rarity, [])
            if items:
                label = f"{rarity_emojis[rarity]} {rarity_labels[rarity]}"
                embed.add_field(
                    name=label,
                    value="\n".join(items),
                    inline=False,
                )

        await context.send(embed=embed)

    @ingredients.command(name="shop", description="Browse today's ingredient shop")
    async def ingredients_shop(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        async with self.bot.scheduler.sessionmaker() as session:
            all_ingredients = await brew_repo.get_all_ingredients(session)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        shop_items = get_daily_shop(date_str, all_ingredients)
        free_items = [i for i in all_ingredients if i.rarity == "free"]

        embed = discord.Embed(
            title="\u2697\ufe0f Potion Panic — Ingredient Shop",
            description=f"Today's rotating stock ({date_str})",
            color=0xF1C40F,
        )

        # Shop items (uncommon + rare)
        uncommon_lines = []
        rare_lines = []
        for ing in shop_items:
            line = f"**{ing.name}** — {ing.base_cost} coins\n*{ing.flavor_text}*"
            if ing.rarity == "uncommon":
                uncommon_lines.append(line)
            else:
                rare_lines.append(line)

        if uncommon_lines:
            embed.add_field(
                name="\U0001f535 Uncommon",
                value="\n".join(uncommon_lines),
                inline=False,
            )
        if rare_lines:
            embed.add_field(
                name="\U0001f7e1 Rare",
                value="\n".join(rare_lines),
                inline=False,
            )

        # Free ingredients (always available)
        free_lines = [f"**{ing.name}** — Free\n*{ing.flavor_text}*" for ing in free_items]
        if free_lines:
            embed.add_field(
                name="\U0001f7e2 Always Available",
                value="\n".join(free_lines),
                inline=False,
            )

        embed.set_footer(text="Use /ingredients buy <name> to purchase")
        await context.send(embed=embed)

    @ingredients.command(name="buy", description="Buy an ingredient from the shop")
    @app_commands.describe(
        ingredient="The ingredient to buy",
        quantity="How many to buy (default 1)",
    )
    @app_commands.autocomplete(ingredient=shop_ingredient_autocomplete)
    async def ingredients_buy(
        self, context: Context, ingredient: str, quantity: int = 1
    ) -> None:
        await context.defer(ephemeral=True)
        if quantity < 1:
            await context.send("Quantity must be at least 1.", ephemeral=True)
            return

        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Look up the ingredient by name
            ing = await brew_repo.get_ingredient_by_name(session, ingredient)
            if ing is None:
                await context.send(
                    f"Unknown ingredient: **{ingredient}**. "
                    "Check `/ingredients shop` for available items.",
                    ephemeral=True,
                )
                return

            # Validate it's available (free or in today's shop)
            if ing.rarity != "free":
                all_ingredients = await brew_repo.get_all_ingredients(session)
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                shop_items = get_daily_shop(date_str, all_ingredients)
                shop_ids = {s.id for s in shop_items}
                if ing.id not in shop_ids:
                    await context.send(
                        f"**{ing.name}** is not in today's shop. "
                        "Check `/ingredients shop` for today's stock.",
                        ephemeral=True,
                    )
                    return

            total_cost = ing.base_cost * quantity

            # Free ingredients skip wallet logic
            if total_cost > 0:
                wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
                if wallet is None:
                    gs = await repo.get_guild_settings(session, guild_id)
                    default_bal = resolve_guild_setting(
                        gs, self.bot.settings, "default_wallet"
                    )
                    wallet = await wallet_repo.create_wallet(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        balance=default_bal,
                    )

                if wallet.balance < total_cost:
                    await context.send(
                        f"Not enough coins! **{ing.name}** x{quantity} costs "
                        f"**{total_cost} coins**, but you only have "
                        f"**{wallet.balance} coins**.",
                        ephemeral=True,
                    )
                    return

                wallet.balance -= total_cost

            # Add to inventory
            await brew_repo.add_player_ingredient(
                session, user_id, guild_id, ing.id, quantity
            )

        # Confirmation embed
        embed = discord.Embed(
            title="Purchase Complete!",
            color=0x2ECC71,
        )
        embed.add_field(name="Item", value=f"{ing.name} x{quantity}", inline=True)
        if total_cost > 0:
            embed.add_field(name="Cost", value=f"{total_cost} coins", inline=True)
        else:
            embed.add_field(name="Cost", value="Free", inline=True)
        embed.set_footer(text="Use /ingredients to view your inventory")
        await context.send(embed=embed)


    # ------------------------------------------------------------------
    # /brew command group
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="brew", description="Potion Panic brewing commands")
    async def brew(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send(
                "Use `/brew start`, `/brew add`, `/brew cashout`, or `/brew status`.",
                ephemeral=True,
            )

    @brew.command(name="start", description="Pay the bottle fee and begin a new brew")
    async def brew_start(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Check for existing active brew
            active = await brew_repo.get_active_brew(session, user_id, guild_id)
            if active is not None:
                await context.send(
                    "You already have an active brew! "
                    "Use `/brew add` to continue or `/brew cashout` to finish it.",
                    ephemeral=True,
                )
                return

            # Resolve bottle fee from config
            gs = await repo.get_guild_settings(session, guild_id)
            bottle_fee = resolve_guild_setting(gs, self.bot.settings, "bottle_fee")

            # Get/create wallet and check balance
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(
                    gs, self.bot.settings, "default_wallet"
                )
                wallet = await wallet_repo.create_wallet(
                    session, user_id=user_id, guild_id=guild_id, balance=default_bal,
                )

            if wallet.balance < bottle_fee:
                await context.send(
                    f"Not enough coins! The bottle fee is **{bottle_fee} coins** "
                    f"but you only have **{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            # Deduct bottle fee
            wallet.balance -= bottle_fee

            # Generate random explosion threshold
            threshold_min = resolve_guild_setting(
                gs, self.bot.settings, "explosion_threshold_min"
            )
            threshold_max = resolve_guild_setting(
                gs, self.bot.settings, "explosion_threshold_max"
            )
            threshold = random.randint(threshold_min, threshold_max)

            # Check for Fortification brew effect (raises minimum threshold)
            fort_effect = await brew_repo.get_player_brew_effect(
                session, user_id, guild_id, "fortification"
            )
            fort_applied = False
            if fort_effect is not None:
                if threshold < fort_effect.effect_value:
                    threshold = fort_effect.effect_value
                    fort_applied = True
                await brew_repo.delete_player_brew_effect(session, fort_effect.id)

            # Check for Foresight brew effect (reveals threshold)
            foresight_effect = await brew_repo.get_player_brew_effect(
                session, user_id, guild_id, "foresight"
            )
            foresight_active = foresight_effect is not None
            if foresight_effect is not None:
                await brew_repo.delete_player_brew_effect(session, foresight_effect.id)

            # Create brew session
            brew_session = await brew_repo.create_brew_session(
                session,
                user_id=user_id,
                guild_id=guild_id,
                explosion_threshold=threshold,
                bottle_cost=bottle_fee,
            )

        desc = "You light the fire and set the cauldron to a gentle simmer. Time to brew."
        if fort_applied:
            desc += "\n\n\U0001f6e1\ufe0f Your **Fortification** potion reinforces the cauldron!"
        if foresight_active:
            desc += (
                f"\n\n\U0001f52e Your **Foresight** potion reveals the explosion "
                f"threshold: **{threshold}**"
            )

        embed = discord.Embed(
            title="\u2697\ufe0f The Cauldron",
            description=desc,
            color=brew_logic.COLOR_SAFE,
        )
        embed.add_field(name="Potency", value="0", inline=True)
        embed.add_field(name="Bottle Fee", value=f"{bottle_fee} coins", inline=True)
        embed.set_footer(text="/brew add <ingredient> to begin")
        await context.send(embed=embed)

    @brew.command(name="add", description="Add an ingredient to your active brew")
    @app_commands.describe(ingredient="The ingredient to add")
    @app_commands.autocomplete(ingredient=_brew_ingredient_autocomplete)
    async def brew_add(self, context: Context, ingredient: str) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Get active brew
            brew_session = await brew_repo.get_active_brew(session, user_id, guild_id)
            if brew_session is None:
                await context.send(
                    "You don't have an active brew. Use `/brew start` first.",
                    ephemeral=True,
                )
                return

            # Resolve ingredient
            ing = await brew_repo.get_ingredient_by_name(session, ingredient)
            if ing is None:
                await context.send(
                    f"Unknown ingredient: **{ingredient}**.",
                    ephemeral=True,
                )
                return

            # Enforce one-of-each ingredient limit
            brew_ings = await brew_repo.get_brew_ingredients(session, brew_session.id)
            if any(bi.ingredient_id == ing.id for bi in brew_ings):
                await context.send(
                    f"You already have **{ing.name}** in this brew. "
                    "Each ingredient can only be added once.",
                    ephemeral=True,
                )
                return

            # Check availability: free ingredients are always available,
            # otherwise must be in player's inventory
            if ing.rarity != "free":
                player_ing = await brew_repo.get_player_ingredient(
                    session, user_id, guild_id, ing.id
                )
                if player_ing is None or player_ing.quantity < 1:
                    await context.send(
                        f"You don't have any **{ing.name}** in your inventory.",
                        ephemeral=True,
                    )
                    return
                # Consume from inventory
                await brew_repo.remove_player_ingredient(
                    session, user_id, guild_id, ing.id, 1
                )

            # Track ingredient cost
            brew_session.ingredient_cost_total += ing.base_cost

            # Resolve cauldron ingredients for potency calculation
            cauldron_ingredients: list = []
            for bi in brew_ings:
                cauldron_ing = await brew_repo.get_ingredient_by_id(
                    session, bi.ingredient_id
                )
                if cauldron_ing:
                    cauldron_ingredients.append(cauldron_ing)

            # Calculate potency gain
            gs = await repo.get_guild_settings(session, guild_id)
            base_potency = resolve_guild_setting(
                gs, self.bot.settings, "base_potency"
            )
            min_no_match = resolve_guild_setting(
                gs, self.bot.settings, "min_potency_no_match"
            )
            potency_gain = brew_logic.calculate_potency(
                ing, cauldron_ingredients, base_potency, min_no_match
            )
            brew_session.potency += potency_gain

            # Calculate instability (recalculate from scratch)
            all_cauldron = cauldron_ingredients + [ing]
            all_tags = brew_logic.collect_cauldron_tags(all_cauldron)
            triples = await brew_repo.get_all_dangerous_triples(session)
            new_instability = brew_logic.calculate_instability(all_tags, triples)
            brew_session.instability = new_instability

            # Record the addition
            add_order = len(brew_ings) + 1
            await brew_repo.add_brew_ingredient(
                session,
                brew_session_id=brew_session.id,
                ingredient_id=ing.id,
                add_order=add_order,
                potency_gained=potency_gain,
                instability_after=new_instability,
            )

            # Build ingredient name list for embed
            ingredient_names = [
                (await brew_repo.get_ingredient_by_id(session, bi.ingredient_id)).name
                for bi in brew_ings
            ]
            ingredient_names.append(ing.name)

            # Check for explosion
            if brew_logic.check_explosion(new_instability, brew_session.explosion_threshold):
                brew_session.status = "exploded"
                brew_session.completed_at = datetime.now(timezone.utc)
                await session.commit()

                total_lost = brew_session.bottle_cost + brew_session.ingredient_cost_total
                embed = discord.Embed(
                    title="\U0001f4a5 CATASTROPHIC FAILURE",
                    description=brew_logic.get_explosion_text(),
                    color=brew_logic.COLOR_EXPLODED,
                )
                embed.add_field(
                    name="Final Potency", value=str(brew_session.potency), inline=True
                )
                embed.add_field(
                    name="Coins Lost", value=f"{total_lost} coins", inline=True
                )
                embed.add_field(
                    name="Ingredients Lost",
                    value=", ".join(ingredient_names),
                    inline=False,
                )
                await context.send(embed=embed)
                return

            await session.commit()

        # Success — cauldron embed
        color = brew_logic.get_instability_color(new_instability)
        flavor = brew_logic.get_flavor_text(new_instability)

        embed = discord.Embed(
            title="\u2697\ufe0f The Cauldron",
            description=flavor,
            color=color,
        )
        embed.add_field(
            name="Potency",
            value=f"{brew_session.potency} (+{potency_gain})",
            inline=True,
        )
        embed.add_field(
            name="Ingredients Added", value=str(add_order), inline=True
        )
        embed.add_field(
            name="Ingredients",
            value=", ".join(ingredient_names),
            inline=False,
        )
        embed.set_footer(
            text="/brew add <ingredient> to continue | /brew cashout to bottle it"
        )
        await context.send(embed=embed)

    @brew.command(name="cashout", description="Bottle your brew and collect the payout")
    async def brew_cashout(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            brew_session = await brew_repo.get_active_brew(session, user_id, guild_id)
            if brew_session is None:
                await context.send(
                    "You don't have an active brew to cash out.",
                    ephemeral=True,
                )
                return

            # Calculate payout
            payout = brew_logic.calculate_payout(brew_session.potency)

            # Add payout to wallet
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None:
                gs = await repo.get_guild_settings(session, guild_id)
                default_bal = resolve_guild_setting(
                    gs, self.bot.settings, "default_wallet"
                )
                wallet = await wallet_repo.create_wallet(
                    session, user_id=user_id, guild_id=guild_id, balance=default_bal,
                )
            wallet.balance += payout

            # Rare ingredient drop at 200+ potency
            rare_drop_name = None
            gs = await repo.get_guild_settings(session, guild_id)
            rare_threshold = resolve_guild_setting(
                gs, self.bot.settings, "rare_drop_potency"
            )
            if brew_session.potency >= rare_threshold:
                rare_ingredients = await brew_repo.get_ingredients_by_rarity(
                    session, "rare"
                )
                if rare_ingredients:
                    drop = random.choice(rare_ingredients)
                    await brew_repo.add_player_ingredient(
                        session, user_id, guild_id, drop.id, 1
                    )
                    rare_drop_name = drop.name

            # Resolve brew ingredients (ordered) for potion creation + embed
            brew_ings = await brew_repo.get_brew_ingredients(session, brew_session.id)
            cauldron_ingredients = []
            ingredient_names = []
            for bi in brew_ings:
                ing_obj = await brew_repo.get_ingredient_by_id(session, bi.ingredient_id)
                if ing_obj:
                    cauldron_ingredients.append(ing_obj)
                    ingredient_names.append(ing_obj.name)

            # Create potion if potency is high enough
            potion_name = None
            if brew_session.potency >= brew_potions.POTION_MIN_POTENCY:
                dominant_tag = brew_potions.calculate_dominant_tag(
                    cauldron_ingredients
                )
                if dominant_tag:
                    result = brew_potions.determine_potion(
                        dominant_tag, brew_session.potency
                    )
                    if result:
                        p_type, p_value, p_name = result
                        await brew_repo.create_player_potion(
                            session,
                            user_id=user_id,
                            guild_id=guild_id,
                            potion_type=p_type,
                            effect_value=p_value,
                            potion_name=p_name,
                        )
                        potion_name = p_name

            # Finalize brew
            brew_session.status = "cashed_out"
            brew_session.payout = payout
            brew_session.completed_at = datetime.now(timezone.utc)

            total_cost = brew_session.bottle_cost + brew_session.ingredient_cost_total
            profit = payout - total_cost

            await session.commit()

        # Cashout embed
        cashout_text = brew_logic.get_cashout_text(brew_session.potency)
        if rare_drop_name:
            cashout_text += f"\n\nA **{rare_drop_name}** crystallizes from the residue!"
        if potion_name:
            cashout_text += f"\n\nYou also created a **{potion_name}**!"

        embed = discord.Embed(
            title="\U0001f9ea Brew Complete!",
            description=cashout_text,
            color=brew_logic.COLOR_CASHOUT,
        )
        embed.add_field(
            name="Final Potency", value=str(brew_session.potency), inline=True
        )
        embed.add_field(name="Payout", value=f"{payout} coins", inline=True)
        embed.add_field(
            name="Profit",
            value=f"{profit:+d} coins",
            inline=True,
        )
        embed.add_field(
            name="Ingredients Used",
            value=", ".join(ingredient_names) if ingredient_names else "None",
            inline=False,
        )
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        await context.send(embed=embed)

    @brew.command(name="status", description="View your current active brew")
    async def brew_status(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            brew_session = await brew_repo.get_active_brew(session, user_id, guild_id)
            if brew_session is None:
                await context.send(
                    "You don't have an active brew. Use `/brew start` to begin one.",
                    ephemeral=True,
                )
                return

            brew_ings = await brew_repo.get_brew_ingredients(session, brew_session.id)
            ingredient_names = []
            for bi in brew_ings:
                ing_obj = await brew_repo.get_ingredient_by_id(session, bi.ingredient_id)
                if ing_obj:
                    ingredient_names.append(ing_obj.name)

        color = brew_logic.get_instability_color(brew_session.instability)
        flavor = brew_logic.get_flavor_text(brew_session.instability)

        embed = discord.Embed(
            title="\u2697\ufe0f The Cauldron",
            description=flavor,
            color=color,
        )
        embed.add_field(
            name="Potency", value=str(brew_session.potency), inline=True
        )
        embed.add_field(
            name="Ingredients Added", value=str(len(brew_ings)), inline=True
        )
        if ingredient_names:
            embed.add_field(
                name="Ingredients",
                value=", ".join(ingredient_names),
                inline=False,
            )
        embed.set_footer(
            text="/brew add <ingredient> to continue | /brew cashout to bottle it"
        )
        await context.send(embed=embed)

    # ------------------------------------------------------------------
    # /brew journal
    # ------------------------------------------------------------------

    @brew.command(name="journal", description="View your brew history")
    async def brew_journal(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            brews = await brew_repo.get_brew_history(session, user_id, guild_id, 20)
            # Pre-fetch ingredient counts for each brew
            brew_ing_counts: dict[int, int] = {}
            for b in brews:
                ings = await brew_repo.get_brew_ingredients(session, b.id)
                brew_ing_counts[b.id] = len(ings)

        if not brews:
            await context.send(
                "You haven't completed any brews yet. Use `/brew start` to begin!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="\U0001f4d6 Brew Journal",
            description=f"Your last {len(brews)} brews",
            color=0x3498DB,
        )
        for b in brews:
            if b.status == "cashed_out":
                emoji = "\U0001f9ea"
                label = "Cashed Out"
                total_cost = b.bottle_cost + b.ingredient_cost_total
                profit = (b.payout or 0) - total_cost
                detail = f"Payout: {b.payout} coins ({profit:+d} profit)"
            else:
                emoji = "\U0001f4a5"
                label = "Exploded"
                total_lost = b.bottle_cost + b.ingredient_cost_total
                detail = f"Lost: {total_lost} coins"

            ing_count = brew_ing_counts.get(b.id, 0)
            embed.add_field(
                name=f"#{b.id} {emoji} {label}",
                value=f"Potency: {b.potency} | {detail} | {ing_count} ingredients",
                inline=False,
            )

        await context.send(embed=embed)

    @brew.command(name="analyze", description="Analyze your brews using a specific ingredient")
    @app_commands.describe(ingredient="The ingredient to analyze")
    @app_commands.autocomplete(ingredient=_all_ingredient_autocomplete)
    async def brew_analyze(self, context: Context, ingredient: str) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            ing = await brew_repo.get_ingredient_by_name(session, ingredient)
            if ing is None:
                await context.send(
                    f"Unknown ingredient: **{ingredient}**.",
                    ephemeral=True,
                )
                return

            brews = await brew_repo.get_brews_with_ingredient(
                session, user_id, guild_id, ing.id, 20
            )

            if not brews:
                await context.send(
                    f"You haven't used **{ing.name}** in any brews yet.",
                    ephemeral=True,
                )
                return

            # For each brew, get co-ingredients
            brew_details: list[tuple] = []
            for b in brews:
                brew_ings = await brew_repo.get_brew_ingredients(session, b.id)
                co_names = []
                for bi in brew_ings:
                    if bi.ingredient_id != ing.id:
                        co_ing = await brew_repo.get_ingredient_by_id(
                            session, bi.ingredient_id
                        )
                        if co_ing:
                            co_names.append(co_ing.name)
                brew_details.append((b, co_names))

        # Build embed
        success_count = sum(1 for b, _ in brew_details if b.status == "cashed_out")
        avg_potency = (
            sum(b.potency for b, _ in brew_details) // len(brew_details)
            if brew_details
            else 0
        )

        embed = discord.Embed(
            title=f"\U0001f50d Brew Analysis: {ing.name}",
            description=f"Your brews using {ing.name}",
            color=0x3498DB,
        )

        for b, co_names in brew_details:
            if b.status == "cashed_out":
                emoji = "\U0001f9ea"
                outcome = f"Payout: {b.payout} coins"
            else:
                emoji = "\U0001f4a5"
                total_lost = b.bottle_cost + b.ingredient_cost_total
                outcome = f"Lost: {total_lost} coins"

            co_str = ", ".join(co_names) if co_names else "None"
            embed.add_field(
                name=f"#{b.id} {emoji} Potency {b.potency}",
                value=f"Co-ingredients: {co_str} | {outcome}",
                inline=False,
            )

        embed.set_footer(
            text=(
                f"Used in {len(brew_details)} brews | "
                f"{success_count} successful | "
                f"Avg potency: {avg_potency}"
            )
        )
        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Brewing(bot))
