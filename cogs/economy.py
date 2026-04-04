from __future__ import annotations

import discord
from discord.ext import commands
from discord.ext.commands import Context

from config import resolve_guild_setting
from derby import repositories as repo
from economy import repositories as wallet_repo


class Economy(commands.Cog, name="economy"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="wallet", description="Show your wallet balance")
    async def wallet(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(
                session, context.author.id, guild_id
            )
            was_new = wallet is None
            if wallet is None:
                gs = await repo.get_guild_settings(session, guild_id)
                default_bal = resolve_guild_setting(
                    gs, self.bot.settings, "default_wallet"
                )
                wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )
        if was_new:
            embed = discord.Embed(
                title="Welcome!",
                description=(
                    f"You've been given **{wallet.balance} coins** to start.\n"
                    f"Use `/race upcoming` to see the next race and `/race bet` to wager!"
                ),
            )
            await context.send(embed=embed)
        else:
            await context.send(f"Your balance is {wallet.balance} coins")


async def setup(bot) -> None:
    await bot.add_cog(Economy(bot))
