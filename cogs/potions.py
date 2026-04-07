from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from brewing import potions as brew_potions
from brewing import repositories as brew_repo


async def _potion_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete showing player's owned potions."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild.id if interaction.guild else 0
    user_id = interaction.user.id

    async with sessionmaker() as session:
        potions = await brew_repo.get_player_potions(session, user_id, guild_id)

    current_lower = current.lower()
    choices = []
    for p in potions:
        if current_lower in p.potion_name.lower():
            choices.append(
                app_commands.Choice(name=p.potion_name, value=str(p.id))
            )
        if len(choices) >= 25:
            break
    return choices


class Potions(commands.Cog, name="potions"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    potion = commands.hybrid_group(
        name="potion", description="Potion Panic potion commands"
    )

    @potion.command(name="list", description="View your potion inventory")
    async def potion_list(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            potions = await brew_repo.get_player_potions(session, user_id, guild_id)

        if not potions:
            await context.send(
                "You don't have any potions. Brew with 100+ potency to create one!",
                ephemeral=True,
            )
            return

        # Group by potion type
        grouped: dict[str, list] = {}
        for p in potions:
            grouped.setdefault(p.potion_type, []).append(p)

        embed = discord.Embed(
            title="\U0001f9ea Potion Inventory",
            color=0x9B59B6,
        )

        for ptype, plist in grouped.items():
            desc = brew_potions.POTION_DESCRIPTIONS.get(ptype, "")
            names = [p.potion_name for p in plist]
            # Count duplicates
            name_counts: dict[str, int] = {}
            for n in names:
                name_counts[n] = name_counts.get(n, 0) + 1
            display = []
            for name, count in name_counts.items():
                if count > 1:
                    display.append(f"{name} x{count}")
                else:
                    display.append(name)

            embed.add_field(
                name=", ".join(display),
                value=desc,
                inline=False,
            )

        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Potions(bot))
