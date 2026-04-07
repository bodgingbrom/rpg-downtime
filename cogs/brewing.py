from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from brewing import models as brew_models
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


async def setup(bot) -> None:
    await bot.add_cog(Brewing(bot))
