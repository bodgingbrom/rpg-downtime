from __future__ import annotations

from typing import List

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

from derby import logic, models
from derby import repositories as repo


class BetSelect(discord.ui.Select):
    def __init__(self, view: "BetView", racers: List[models.Racer]):
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in racers]
        super().__init__(
            placeholder="Pick a racer", min_values=1, max_values=1, options=options
        )
        self.view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.value = int(self.values[0])
        await interaction.response.defer()
        self.view.stop()


class BetView(discord.ui.View):
    def __init__(self, racers: List[models.Racer]):
        super().__init__(timeout=30)
        self.value: int | None = None
        self.add_item(BetSelect(self, racers))


class WatchView(discord.ui.View):
    def __init__(self, log: List[str]):
        super().__init__(timeout=120)
        self.log = log
        self.index = 0

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.index >= len(self.log):
            button.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
            return
        await interaction.response.send_message(self.log[self.index], ephemeral=True)
        self.index += 1
        if self.index >= len(self.log):
            button.label = "Done"
            button.style = discord.ButtonStyle.grey
            await interaction.message.edit(view=self)
            self.stop()


class Derby(commands.Cog, name="derby"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="race", description="Race commands")
    async def race(
        self, context: Context
    ) -> None:  # pragma: no cover - command dispatch
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @race.command(name="next", description="Show the next scheduled race")
    async def race_next(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            result = await session.execute(
                select(models.Race)
                .where(models.Race.finished.is_(False))
                .order_by(models.Race.id)
            )
            race = result.scalars().first()
        if race is None:
            await context.send("No races scheduled.", ephemeral=True)
        else:
            await context.send(f"Next race ID: {race.id}")

    @race.command(name="upcoming", description="Show upcoming race odds")
    async def race_upcoming(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race_result = await session.execute(
                select(models.Race)
                .where(models.Race.finished.is_(False))
                .order_by(models.Race.id)
            )
            race = race_result.scalars().first()
            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
        if race is None or not racers:
            await context.send("No upcoming race.", ephemeral=True)
            return

        odds = logic.calculate_odds(racers, [], 0.1)
        embed = discord.Embed(title="Upcoming Race")
        embed.add_field(name="Race ID", value=str(race.id), inline=False)
        for racer in racers:
            embed.add_field(
                name=racer.name,
                value=f"{odds.get(racer.id, 0):.1f}x",
                inline=False,
            )
        await context.send(embed=embed)

    @race.command(name="bet", description="Bet on the next race")
    @app_commands.describe(amount="Amount to bet")
    async def race_bet(self, context: Context, amount: int) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race_result = await session.execute(
                select(models.Race)
                .where(models.Race.finished.is_(False))
                .order_by(models.Race.id)
            )
            race = race_result.scalars().first()
            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
        if race is None or not racers:
            await context.send("No race available.", ephemeral=True)
            return
        view = BetView(racers)
        embed = discord.Embed(title="Place your bet", description="Select a racer")
        await context.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await context.send("Bet cancelled.", ephemeral=True)
            return
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await repo.get_wallet(session, context.author.id)
            if wallet is None:
                wallet = await repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    balance=self.bot.settings.default_wallet,
                )
            if wallet.balance < amount:
                await context.send("Insufficient balance.", ephemeral=True)
                return
            wallet.balance -= amount
            await session.commit()
            await repo.create_bet(
                session,
                race_id=race.id,
                user_id=context.author.id,
                racer_id=view.value,
                amount=amount,
            )
        await context.send(f"Bet placed on racer {view.value} for {amount} coins")

    @race.command(name="watch", description="Watch the next race")
    async def race_watch(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race_result = await session.execute(
                select(models.Race)
                .where(models.Race.finished.is_(False))
                .order_by(models.Race.id)
            )
            race = race_result.scalars().first()
            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
        if race is None or not racers:
            await context.send("No race to watch.", ephemeral=True)
            return
        placements, log = logic.simulate_race({"racers": racers}, seed=race.id)
        view = WatchView(log)
        embed = discord.Embed(
            title="Race Commentary", description="Click next to see events"
        )
        await context.send(embed=embed, view=view)
        await view.wait()
        results = "\n".join(f"{i+1}. Racer {rid}" for i, rid in enumerate(placements))
        await context.send(f"Race finished!\n{results}")

    @commands.hybrid_group(name="derby", description="Derby admin commands")
    @commands.has_guild_permissions(manage_guild=True)
    async def derby_group(
        self, context: Context
    ) -> None:  # pragma: no cover - command dispatch
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @derby_group.command(name="add_racer", description="Add a new racer")
    @app_commands.describe(name="Racer name", owner="Owner")
    async def add_racer(self, context: Context, name: str, owner: discord.User) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.create_racer(session, name=name, owner_id=owner.id)
        await context.send(f"Racer {racer.name} added with id {racer.id}")

    @derby_group.command(name="edit_racer", description="Edit a racer name")
    @app_commands.describe(racer_id="Racer id", name="New name")
    async def edit_racer(self, context: Context, racer_id: int, name: str) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.update_racer(session, racer_id, name=name)
        if racer is None:
            await context.send("Racer not found", ephemeral=True)
        else:
            await context.send(f"Racer {racer.id} renamed to {racer.name}")

    @derby_group.command(name="start_race", description="Start a new race now")
    async def start_race(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race = await repo.create_race(session, guild_id=context.guild.id)
        await context.send(f"Race {race.id} created")

    @derby_group.command(name="cancel_race", description="Cancel the next race")
    async def cancel_race(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            result = await session.execute(
                select(models.Race)
                .where(models.Race.finished.is_(False))
                .order_by(models.Race.id)
            )
            race = result.scalars().first()
            if race is None:
                await context.send("No race to cancel", ephemeral=True)
                return
            await repo.delete_race(session, race.id)
        await context.send(f"Race {race.id} cancelled")


async def setup(bot) -> None:
    await bot.add_cog(Derby(bot))
