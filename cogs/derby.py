from __future__ import annotations

import json
import random

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

import checks
from derby import logic, models
from derby import repositories as repo


_stat_band = logic.stat_band
_mood_label = logic.mood_label

TEMPERAMENT_CHOICES = [
    app_commands.Choice(name=t, value=t) for t in logic.TEMPERAMENTS
]


async def racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete callback that suggests active racers by name."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    async with sessionmaker() as session:
        result = await session.execute(
            select(models.Racer).where(models.Racer.retired.is_(False))
        )
        racers = result.scalars().all()
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            choices.append(app_commands.Choice(name=f"{r.name} (#{r.id})", value=r.id))
        if len(choices) >= 25:
            break
    return choices


class Derby(commands.Cog, name="derby"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="race", description="Race commands")
    async def race(
        self, context: Context
    ) -> None:  # pragma: no cover - command dispatch
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

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
        for r in racers:
            mult = odds.get(r.id, 0)
            embed.add_field(
                name=f"{r.name} (#{r.id})",
                value=f"{mult:.1f}x \u2014 bet 100, win {int(100 * mult)}",
                inline=False,
            )
        embed.set_footer(text="Use /race bet to place your bet!")
        await context.send(embed=embed)

    @race.command(name="bet", description="Bet on the next race")
    @app_commands.describe(racer="Racer to bet on", amount="Amount to bet")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def race_bet(self, context: Context, racer: int, amount: int) -> None:
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
        if racer not in [r.id for r in racers]:
            await context.send("Racer not found.", ephemeral=True)
            return
        racer_name = next((r.name for r in racers if r.id == racer), f"Racer {racer}")
        odds = logic.calculate_odds(racers, [], 0.1)
        multiplier = odds.get(racer, 0)
        payout = int(amount * multiplier)
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await repo.get_wallet(session, context.author.id)
            if wallet is None:
                wallet = await repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    balance=self.bot.settings.default_wallet,
                )
            bet_result = await session.execute(
                select(models.Bet)
                .where(models.Bet.race_id == race.id)
                .where(models.Bet.user_id == context.author.id)
            )
            existing_bet = bet_result.scalars().first()
            old_name = None
            old_amount = 0
            if existing_bet is not None:
                old_name = next(
                    (r.name for r in racers if r.id == existing_bet.racer_id),
                    f"Racer {existing_bet.racer_id}",
                )
                old_amount = existing_bet.amount
                wallet.balance += existing_bet.amount
            if wallet.balance < amount:
                await session.commit()
                await context.send("Insufficient balance.", ephemeral=True)
                return
            wallet.balance -= amount
            await session.commit()
            if existing_bet is None:
                await repo.create_bet(
                    session,
                    race_id=race.id,
                    user_id=context.author.id,
                    racer_id=racer,
                    amount=amount,
                )
            else:
                await repo.update_bet(
                    session, existing_bet.id, racer_id=racer, amount=amount
                )
        if old_name is not None:
            await context.send(
                f"Bet changed from **{old_name}** ({old_amount} coins refunded) "
                f"to **{racer_name}** for {amount} coins "
                f"({multiplier:.1f}x odds \u2014 win pays {payout})"
            )
        else:
            await context.send(
                f"Bet placed on **{racer_name}** for {amount} coins "
                f"({multiplier:.1f}x odds \u2014 win pays {payout})"
            )

    @race.command(name="history", description="Show recent race results")
    @app_commands.describe(count="Number of races to display")
    async def race_history(self, context: Context, count: int = 5) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            records = await repo.get_race_history(session, context.guild.id, count)
            racer_names: dict[int, str] = {}
            for _race, winner_id, _payout in records:
                if winner_id is not None and winner_id not in racer_names:
                    racer = await repo.get_racer(session, winner_id)
                    racer_names[winner_id] = (
                        racer.name if racer else f"Racer {winner_id}"
                    )
        if not records:
            await context.send("No finished races.", ephemeral=True)
            return

        embed = discord.Embed(title="Recent Races")
        for race_obj, winner_id, payout in records:
            winner = (
                racer_names.get(winner_id, f"Racer {winner_id}")
                if winner_id is not None
                else "N/A"
            )
            embed.add_field(
                name=f"Race {race_obj.id}",
                value=f"Winner: {winner}\nPayouts: {payout}",
                inline=False,
            )
        await context.send(embed=embed)

    @race.command(name="info", description="Show racer info")
    @app_commands.describe(racer="Racer to inspect")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def race_info(self, context: Context, racer: int) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
        if racer_obj is None:
            await context.send("Racer not found", ephemeral=True)
            return
        embed = discord.Embed(title=racer_obj.name)
        embed.add_field(name="Speed", value=_stat_band(racer_obj.speed), inline=True)
        embed.add_field(
            name="Cornering", value=_stat_band(racer_obj.cornering), inline=True
        )
        embed.add_field(
            name="Stamina", value=_stat_band(racer_obj.stamina), inline=True
        )
        embed.add_field(name="Temperament", value=racer_obj.temperament, inline=True)
        embed.add_field(name="Mood", value=_mood_label(racer_obj.mood), inline=True)
        embed.add_field(
            name="Injuries", value=racer_obj.injuries or "None", inline=False
        )
        await context.send(embed=embed)

    @commands.hybrid_command(name="wallet", description="Show your wallet balance")
    async def wallet(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await repo.get_wallet(session, context.author.id)
            was_new = wallet is None
            if wallet is None:
                wallet = await repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    balance=self.bot.settings.default_wallet,
                )
        if was_new:
            embed = discord.Embed(
                title="Welcome to Downtime Derby!",
                description=(
                    f"You've been given **{wallet.balance} coins** to start.\n"
                    f"Use `/race upcoming` to see the next race and `/race bet` to wager!"
                ),
            )
            await context.send(embed=embed)
        else:
            await context.send(f"Your balance is {wallet.balance} coins")

    @commands.hybrid_group(name="derby", description="Derby admin commands")
    @commands.has_guild_permissions(manage_guild=True)
    async def derby_group(
        self, context: Context
    ) -> None:  # pragma: no cover - command dispatch
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @derby_group.command(name="add_racer", description="Add a new racer")
    @checks.has_role("Race Admin")
    @app_commands.describe(
        name="Racer name",
        owner="Owner",
        random_stats="Generate random stats",
        speed="Speed stat",
        cornering="Cornering stat",
        stamina="Stamina stat",
        temperament="Temperament",
    )
    @app_commands.choices(temperament=TEMPERAMENT_CHOICES)
    async def add_racer(
        self,
        context: Context,
        name: str,
        owner: discord.User,
        random_stats: bool = False,
        speed: app_commands.Range[int, 0, 31] | None = None,
        cornering: app_commands.Range[int, 0, 31] | None = None,
        stamina: app_commands.Range[int, 0, 31] | None = None,
        temperament: str | None = None,
    ) -> None:
        if random_stats:
            stats = {
                "speed": random.randint(0, 31),
                "cornering": random.randint(0, 31),
                "stamina": random.randint(0, 31),
                "temperament": random.choice(list(logic.TEMPERAMENTS)),
            }
        else:
            stats = {
                "speed": speed or 0,
                "cornering": cornering or 0,
                "stamina": stamina or 0,
                "temperament": temperament or "Quirky",
            }
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.create_racer(
                session, name=name, owner_id=owner.id, **stats
            )
        embed = discord.Embed(title=f"New Racer: {racer.name} (#{racer.id})")
        embed.add_field(
            name="Owner",
            value=getattr(owner, "mention", str(owner.id)),
            inline=False,
        )
        embed.add_field(
            name="Speed", value=_stat_band(racer.speed), inline=True
        )
        embed.add_field(
            name="Cornering", value=_stat_band(racer.cornering), inline=True
        )
        embed.add_field(
            name="Stamina", value=_stat_band(racer.stamina), inline=True
        )
        embed.add_field(
            name="Temperament", value=racer.temperament, inline=True
        )
        if random_stats:
            embed.set_footer(text="Stats randomly generated")
        await context.send(embed=embed)

    @derby_group.command(name="edit_racer", description="Edit a racer")
    @checks.has_role("Race Admin")
    @app_commands.describe(
        racer="Racer to edit",
        name="New name",
        speed="Speed stat",
        cornering="Cornering stat",
        stamina="Stamina stat",
        temperament="Temperament",
    )
    @app_commands.autocomplete(racer=racer_autocomplete)
    @app_commands.choices(temperament=TEMPERAMENT_CHOICES)
    async def edit_racer(
        self,
        context: Context,
        racer: int,
        name: str | None = None,
        speed: app_commands.Range[int, 0, 31] | None = None,
        cornering: app_commands.Range[int, 0, 31] | None = None,
        stamina: app_commands.Range[int, 0, 31] | None = None,
        temperament: str | None = None,
    ) -> None:
        updates: dict[str, int | str] = {}
        if name is not None:
            updates["name"] = name
        if speed is not None:
            updates["speed"] = speed
        if cornering is not None:
            updates["cornering"] = cornering
        if stamina is not None:
            updates["stamina"] = stamina
        if temperament is not None:
            updates["temperament"] = temperament
        if not updates:
            await context.send("No updates provided", ephemeral=True)
            return
        async with self.bot.scheduler.sessionmaker() as session:
            old = await repo.get_racer(session, racer)
            if old is None:
                await context.send("Racer not found", ephemeral=True)
                return
            old_values = {k: getattr(old, k) for k in updates}
            updated = await repo.update_racer(session, racer, **updates)
        embed = discord.Embed(title=f"Racer Updated: {updated.name}")
        for key, new_val in updates.items():
            old_val = old_values[key]
            if key in ("speed", "cornering", "stamina"):
                embed.add_field(
                    name=key.capitalize(),
                    value=f"{_stat_band(old_val)} \u2192 {_stat_band(new_val)}",
                    inline=True,
                )
            else:
                embed.add_field(
                    name=key.capitalize(),
                    value=f"{old_val} \u2192 {new_val}",
                    inline=True,
                )
        await context.send(embed=embed)

    @derby_group.command(name="start_race", description="Start a new race now")
    @checks.has_role("Race Admin")
    async def start_race(self, context: Context) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race = await repo.create_race(session, guild_id=context.guild.id)
            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
        embed = discord.Embed(
            title=f"Race {race.id} Created",
            description=f"{len(racers)} active racers in the pool",
        )
        if racers:
            odds = logic.calculate_odds(racers, [], 0.1)
            for r in racers:
                mult = odds.get(r.id, 0)
                embed.add_field(
                    name=f"{r.name} (#{r.id})",
                    value=f"{mult:.1f}x",
                    inline=True,
                )
        await context.send(embed=embed)

    @derby_group.command(name="cancel_race", description="Cancel the next race")
    @checks.has_role("Race Admin")
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

    @derby_group.group(name="racer", description="Racer admin commands")
    async def racer_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @racer_group.command(name="delete", description="Delete a racer")
    @app_commands.describe(racer="Racer to delete")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def racer_delete(self, context: Context, racer: int) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await context.send("Racer not found", ephemeral=True)
                return
            await repo.delete_racer(session, racer)
        await context.send(f"Racer **{racer_obj.name}** (#{racer}) deleted")

    @derby_group.group(name="race", description="Race admin commands")
    async def race_admin(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @race_admin.command(
        name="force-start", description="Simulate a pending race immediately"
    )
    @app_commands.describe(race_id="Race id (defaults to next pending)")
    async def race_force_start(
        self, context: Context, race_id: int | None = None
    ) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            if race_id is None:
                result = await session.execute(
                    select(models.Race)
                    .where(models.Race.finished.is_(False))
                    .order_by(models.Race.id)
                )
                race = result.scalars().first()
            else:
                race = await repo.get_race(session, race_id)
                if race is not None and race.finished:
                    race = None
            if race is None:
                await context.send("No pending race found", ephemeral=True)
                return
            racers_result = await session.execute(
                select(models.Racer).where(models.Racer.retired.is_(False))
            )
            racers = racers_result.scalars().all()
            if not racers:
                await context.send("No racers available", ephemeral=True)
                return
            participants = random.sample(racers, min(8, len(racers)))
            placements, _log = logic.simulate_race(
                {"racers": participants}, seed=race.id
            )
            winner_id = placements[0] if placements else None
            await repo.update_race(
                session, race.id, finished=True, winner_id=winner_id
            )
            if winner_id is not None:
                await logic.resolve_payouts(session, race.id, winner_id)
            threshold = self.bot.settings.retirement_threshold
            for r in participants:
                if random.randint(1, 100) >= threshold:
                    await repo.update_racer(session, r.id, retired=True)
                    await repo.create_racer(
                        session,
                        name=f"{r.name} II",
                        owner_id=r.owner_id,
                        speed=int(r.speed * random.uniform(0.5, 0.75)),
                        cornering=int(r.cornering * random.uniform(0.5, 0.75)),
                        stamina=int(r.stamina * random.uniform(0.5, 0.75)),
                        temperament=r.temperament,
                    )
            await session.commit()
        names = {r.id: r.name for r in participants}
        results = "\n".join(
            f"{i+1}. {names.get(rid, f'Racer {rid}')}" for i, rid in enumerate(placements)
        )
        await context.send(f"Race {race.id} finished!\n{results}")

    @derby_group.group(name="debug", description="Debug commands")
    async def debug_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @debug_group.command(name="race", description="Dump race data")
    @app_commands.describe(race_id="Race id")
    async def debug_race(self, context: Context, race_id: int) -> None:
        async with self.bot.scheduler.sessionmaker() as session:
            race = await repo.get_race(session, race_id)
            if race is None:
                await context.send("Race not found", ephemeral=True)
                return
            bets = (
                (
                    await session.execute(
                        select(models.Bet).where(models.Bet.race_id == race_id)
                    )
                )
                .scalars()
                .all()
            )
            racer_ids = {b.racer_id for b in bets}
            if racer_ids:
                racers = (
                    (
                        await session.execute(
                            select(models.Racer).where(models.Racer.id.in_(racer_ids))
                        )
                    )
                    .scalars()
                    .all()
                )
            else:
                racers = []
            data = {
                "race": {c.key: getattr(race, c.key) for c in race.__table__.columns},
                "bets": [
                    {c.key: getattr(b, c.key) for c in b.__table__.columns}
                    for b in bets
                ],
                "participants": [
                    {c.key: getattr(r, c.key) for c in r.__table__.columns}
                    for r in racers
                ],
            }
        await context.send(
            f"```json\n{json.dumps(data, default=str, indent=2)}\n```",
            ephemeral=True,
        )


async def setup(bot) -> None:
    await bot.add_cog(Derby(bot))
