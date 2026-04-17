from __future__ import annotations

import random
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.ext.commands import Context

import checks
from config import resolve_guild_setting
from derby import descriptions, logic
from derby import repositories as repo
from economy import repositories as wallet_repo
from rpg import repositories as rpg_repo
from rpg.logic import get_racial_modifier


class Economy(commands.Cog, name="economy"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_any_game_channel(ctx)

    @commands.hybrid_command(name="wallet", description="Show your wallet balance")
    async def wallet(self, context: Context) -> None:
        await context.defer(ephemeral=True)
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
                    f"Use `/race upcoming` to see the next race and `/race bet-win` to wager!"
                ),
            )
            await context.send(embed=embed)
        else:
            await context.send(f"Your balance is {wallet.balance} coins")

    @commands.hybrid_command(name="daily", description="Claim your daily reward")
    async def daily(self, context: Context) -> None:
        await context.defer()
        if not context.guild:
            await context.send("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = context.guild.id
        user_id = context.author.id
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async with self.bot.scheduler.sessionmaker() as session:
            # Look up pre-generated reward for today
            reward = await repo.get_daily_reward(session, user_id, guild_id, today)

            if reward is not None and reward.claimed:
                await context.send(
                    "You already claimed today's reward! Check back tomorrow.",
                    ephemeral=True,
                )
                return

            # Generate on the spot if no pre-generated reward exists
            # (new player who joined mid-day, or daily gen hasn't run yet)
            if reward is None:
                gs = await repo.get_guild_settings(session, guild_id)
                daily_min = resolve_guild_setting(gs, self.bot.settings, "daily_min")
                daily_max = resolve_guild_setting(gs, self.bot.settings, "daily_max")
                racer_flavor = resolve_guild_setting(gs, self.bot.settings, "racer_flavor")

                # Fetch player race for modifiers
                profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)
                race = profile.race

                racers = await repo.get_owned_racers(session, user_id, guild_id)
                if racers:
                    best = max(racers, key=lambda r: logic._racer_power(r))
                    rank = best.rank or "D"

                    # Human: daily tier +1
                    tier_bonus = get_racial_modifier(race, "racing.daily_tier_bonus", 0)
                    rank_order = ["D", "C", "B", "A", "S"]
                    effective_rank = rank
                    if tier_bonus > 0:
                        idx = rank_order.index(rank) if rank in rank_order else 0
                        effective_rank = rank_order[min(idx + tier_bonus, len(rank_order) - 1)]

                    multiplier = logic.daily_rank_multiplier(effective_rank)
                    base = random.randint(daily_min, daily_max)
                    amount = base * multiplier

                    flavor_text = None
                    if racer_flavor:
                        try:
                            flavor_text = await descriptions.generate_daily_flavor(
                                best.name, rank, amount, racer_flavor,
                            )
                        except Exception:
                            pass

                    if not flavor_text:
                        loot_items = descriptions.get_random_loot(rank, 1)
                        item = loot_items[0] if loot_items else "something interesting"
                        flavor_text = (
                            f"{best.name} found {item} worth **{amount} coins** "
                            f"while out exploring!"
                        )

                    reward = await repo.create_daily_reward(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        date=today,
                        racer_id=best.id,
                        racer_name=best.name,
                        amount=amount,
                        flavor_text=flavor_text,
                    )
                else:
                    amount = random.randint(daily_min, daily_max)
                    snippet = descriptions.get_no_racer_loot()
                    flavor_text = (
                        f"You found {snippet} worth **{amount} coins**!"
                    )
                    reward = await repo.create_daily_reward(
                        session,
                        user_id=user_id,
                        guild_id=guild_id,
                        date=today,
                        amount=amount,
                        flavor_text=flavor_text,
                    )

            # Claim the reward
            reward.claimed = True

            # Orc flat gold bonus
            profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)
            race = profile.race
            gold_bonus = get_racial_modifier(race, "economy.daily_gold_bonus", 0)
            if gold_bonus > 0:
                reward.amount += gold_bonus

            await session.commit()

            # Update wallet
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None:
                gs = await repo.get_guild_settings(session, guild_id)
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=user_id, guild_id=guild_id, balance=default_bal,
                )
            wallet.balance += reward.amount
            await session.commit()

            # Build response embed
            embed = discord.Embed(
                title="Daily Reward!",
                description=reward.flavor_text,
                color=discord.Color.gold(),
            )
            if reward.racer_name:
                rank_str = logic.rank_label(
                    next((r.rank for r in await repo.get_owned_racers(session, user_id, guild_id) if r.id == reward.racer_id), "D")
                ) if reward.racer_id else ""
                embed.add_field(name="Racer", value=f"{reward.racer_name} ({rank_str})", inline=True)
            embed.add_field(name="Coins Earned", value=f"+{reward.amount}", inline=True)

            # Race reminder for players who haven't chosen yet
            if profile.chosen_at is None:
                embed.set_footer(
                    text="You haven't chosen a race yet! "
                         "Use /player choose — each race has unique passives across all games."
                )

            await context.send(embed=embed)


    @commands.hybrid_command(name="gift", description="Gift coins to another player")
    @discord.app_commands.describe(
        player="The player to gift coins to",
        amount="Number of coins to gift",
    )
    async def gift(self, context: Context, player: discord.Member, amount: int) -> None:
        await context.defer()
        if player.bot:
            await context.send("You can't gift coins to a bot!", ephemeral=True)
            return
        if player.id == context.author.id:
            await context.send("You can't gift coins to yourself!", ephemeral=True)
            return
        if amount <= 0:
            await context.send("Amount must be a positive number.", ephemeral=True)
            return

        guild_id = context.guild.id if context.guild else 0

        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")

            # Get sender wallet
            sender_wallet = await wallet_repo.get_wallet(
                session, context.author.id, guild_id
            )
            if sender_wallet is None:
                sender_wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )
            if sender_wallet.balance < amount:
                await context.send(
                    f"You only have **{sender_wallet.balance} coins** — "
                    f"can't gift **{amount}**.",
                    ephemeral=True,
                )
                return

            # Get or create recipient wallet
            recipient_wallet = await wallet_repo.get_wallet(
                session, player.id, guild_id
            )
            if recipient_wallet is None:
                recipient_wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=player.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )

            sender_wallet.balance -= amount
            recipient_wallet.balance += amount
            await session.commit()

        embed = discord.Embed(
            title="\U0001f381 Gift Sent!",
            description=(
                f"{context.author.mention} gifted **{amount} coins** "
                f"to {player.mention}!"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Your balance: {sender_wallet.balance} coins")
        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Economy(bot))
