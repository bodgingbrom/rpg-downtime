from __future__ import annotations

import json
import random

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

import checks
from config import resolve_guild_setting
from derby import commentary, logic, models
from derby import repositories as repo
from economy import repositories as wallet_repo


_stat_band = logic.stat_band
_mood_label = logic.mood_label

TEMPERAMENT_CHOICES = [
    app_commands.Choice(name=t, value=t) for t in logic.TEMPERAMENTS
]


async def racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete callback that suggests racers in the next race."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    active = interaction.client.scheduler.active_races
    guild_id = interaction.guild_id or 0
    async with sessionmaker() as session:
        race_result = await session.execute(
            select(models.Race)
            .where(
                models.Race.guild_id == guild_id,
                models.Race.finished.is_(False),
            )
            .order_by(models.Race.id)
        )
        races = race_result.scalars().all()
        race = next((r for r in races if r.id not in active), None)
        if race is None:
            return []
        racers = await repo.get_race_participants(session, race.id)
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            choices.append(app_commands.Choice(name=f"{r.name} (#{r.id})", value=r.id))
        if len(choices) >= 25:
            break
    return choices


async def unowned_racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete showing unowned racers with prices."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    settings = interaction.client.settings
    async with sessionmaker() as session:
        racers = await repo.get_unowned_guild_racers(session, guild_id)
        gs = await repo.get_guild_settings(session, guild_id)
    base = resolve_guild_setting(gs, settings, "racer_buy_base")
    mult = resolve_guild_setting(gs, settings, "racer_buy_multiplier")
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            price = logic.calculate_buy_price(r, base, mult)
            choices.append(
                app_commands.Choice(
                    name=f"{r.name} - {price} coins", value=r.id
                )
            )
        if len(choices) >= 25:
            break
    return choices


async def owned_racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete showing the user's owned racers."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    async with sessionmaker() as session:
        racers = await repo.get_owned_racers(
            session, interaction.user.id, guild_id
        )
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            choices.append(
                app_commands.Choice(name=f"{r.name} (#{r.id})", value=r.id)
            )
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
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            race_result = await session.execute(
                select(models.Race)
                .where(
                    models.Race.guild_id == guild_id,
                    models.Race.finished.is_(False),
                )
                .order_by(models.Race.id)
            )
            races = race_result.scalars().all()
            active = self.bot.scheduler.active_races
            race = next((r for r in races if r.id not in active), None)
            if race is None:
                await context.send("No upcoming race.", ephemeral=True)
                return
            racers = await repo.get_race_participants(session, race.id)
        if not racers:
            await context.send("No upcoming race.", ephemeral=True)
            return

        odds = logic.calculate_odds(racers, [], 0.1)
        embed = discord.Embed(title="Upcoming Race")
        embed.add_field(name="Race ID", value=str(race.id), inline=False)

        # Show next race time using Discord timestamp (auto-localizes)
        task = getattr(self.bot.scheduler, "task", None)
        next_iter = getattr(task, "next_iteration", None) if task else None
        if next_iter is not None:
            ts = int(next_iter.timestamp())
            embed.add_field(
                name="Scheduled",
                value=f"<t:{ts}:F> (<t:{ts}:R>)",
                inline=False,
            )

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
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            race_result = await session.execute(
                select(models.Race)
                .where(
                    models.Race.guild_id == guild_id,
                    models.Race.finished.is_(False),
                )
                .order_by(models.Race.id)
            )
            races = race_result.scalars().all()
            # Skip races already being run by the scheduler so the bet
            # lands on the same race that force-start would pick.
            active = self.bot.scheduler.active_races
            race = next((r for r in races if r.id not in active), None)
            if race is None:
                await context.send("No race available.", ephemeral=True)
                return
            racers = await repo.get_race_participants(session, race.id)
        if not racers:
            await context.send("No race available.", ephemeral=True)
            return
        if racer not in [r.id for r in racers]:
            await context.send(
                "That racer isn't in the next race.", ephemeral=True
            )
            return
        racer_name = next((r.name for r in racers if r.id == racer), f"Racer {racer}")
        odds = logic.calculate_odds(racers, [], 0.1)
        multiplier = odds.get(racer, 0)
        payout = int(amount * multiplier)
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(
                session, context.author.id, guild_id
            )
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
                    payout_multiplier=multiplier,
                )
            else:
                await repo.update_bet(
                    session,
                    existing_bet.id,
                    racer_id=racer,
                    amount=amount,
                    payout_multiplier=multiplier,
                )
        if old_name is not None:
            await context.send(
                f"**Race {race.id}** \u2014 Bet changed from **{old_name}** "
                f"({old_amount} coins refunded) to **{racer_name}** for "
                f"{amount} coins ({multiplier:.1f}x odds \u2014 win pays {payout})"
            )
        else:
            await context.send(
                f"**Race {race.id}** \u2014 Bet placed on **{racer_name}** for "
                f"{amount} coins ({multiplier:.1f}x odds \u2014 win pays {payout})"
            )

    @race.command(name="history", description="Show recent race results")
    @app_commands.describe(count="Number of races to display")
    async def race_history(self, context: Context, count: int = 5) -> None:
        await context.defer()
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
        await context.defer()
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
        if racer_obj is None:
            await context.send("Racer not found", ephemeral=True)
            return
        phase = logic.career_phase(racer_obj)
        eff = logic.effective_stats(racer_obj)
        embed = discord.Embed(title=racer_obj.name)
        embed.add_field(name="Speed", value=_stat_band(eff["speed"]), inline=True)
        embed.add_field(
            name="Cornering", value=_stat_band(eff["cornering"]), inline=True
        )
        embed.add_field(
            name="Stamina", value=_stat_band(eff["stamina"]), inline=True
        )
        embed.add_field(name="Temperament", value=racer_obj.temperament, inline=True)
        embed.add_field(name="Mood", value=_mood_label(racer_obj.mood), inline=True)
        embed.add_field(
            name="Career",
            value=f"Race {racer_obj.races_completed}/{racer_obj.career_length} ({phase})",
            inline=True,
        )
        injury_text = "None"
        if racer_obj.injuries:
            injury_text = f"{racer_obj.injuries} ({racer_obj.injury_races_remaining} races remaining)"
        embed.add_field(name="Injuries", value=injury_text, inline=False)
        await context.send(embed=embed)

    @commands.hybrid_group(name="derby", description="Derby admin commands")
    @commands.has_guild_permissions(manage_guild=True)
    async def derby_group(
        self, context: Context
    ) -> None:  # pragma: no cover - command dispatch
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    # -- Map commands --------------------------------------------------

    @derby_group.group(name="map", description="Race map commands")
    async def map_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @map_group.command(name="list", description="List available race maps")
    async def map_list(self, context: Context) -> None:
        await context.defer()
        maps = logic.load_all_maps()
        if not maps:
            await context.send("No maps available.", ephemeral=True)
            return
        embed = discord.Embed(title="Available Race Maps")
        for m in maps:
            embed.add_field(
                name=f"{m.name} ({m.theme})",
                value=f"{len(m.segments)} segments \u2014 {m.description}"
                if m.description
                else f"{len(m.segments)} segments",
                inline=False,
            )
        await context.send(embed=embed)

    @map_group.command(name="view", description="View a race map's segments")
    @app_commands.describe(name="Map name")
    async def map_view(self, context: Context, name: str) -> None:
        await context.defer()
        maps = logic.load_all_maps()
        race_map = next((m for m in maps if m.name.lower() == name.lower()), None)
        if race_map is None:
            await context.send("Map not found.", ephemeral=True)
            return
        layout = " \u2192 ".join(
            f"[{s.type.capitalize()}]" for s in race_map.segments
        )
        embed = discord.Embed(
            title=race_map.name,
            description=f"**Theme:** {race_map.theme}\n{race_map.description}\n\n{layout}",
        )
        for i, s in enumerate(race_map.segments, 1):
            embed.add_field(
                name=f"{i}. {s.type.capitalize()} (distance {s.distance})",
                value=s.description or "\u200b",
                inline=False,
            )
        await context.send(embed=embed)

    @map_view.autocomplete("name")
    async def map_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        maps = logic.load_all_maps()
        choices = []
        current_lower = current.lower()
        for m in maps:
            if current_lower in m.name.lower():
                choices.append(app_commands.Choice(name=m.name, value=m.name))
            if len(choices) >= 25:
                break
        return choices

    # -- Racer commands -------------------------------------------------

    @derby_group.command(name="add_racer", description="Add a new racer")
    @checks.has_role("Race Admin")
    @app_commands.describe(
        name="Racer name (leave blank for a random name)",
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
        owner: discord.User,
        name: str | None = None,
        random_stats: bool = False,
        speed: app_commands.Range[int, 0, 31] | None = None,
        cornering: app_commands.Range[int, 0, 31] | None = None,
        stamina: app_commands.Range[int, 0, 31] | None = None,
        temperament: str | None = None,
    ) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        if name is None:
            async with self.bot.scheduler.sessionmaker() as session:
                result = await session.execute(
                    select(models.Racer.name).where(
                        models.Racer.guild_id == guild_id,
                        models.Racer.retired.is_(False),
                    )
                )
                taken = {row[0] for row in result.all()}
            name = logic.pick_name(taken)
            if name is None:
                await context.send(
                    "All default names are taken! Please provide a name.",
                    ephemeral=True,
                )
                return
        career_length = random.randint(25, 40)
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
                session,
                name=name,
                owner_id=owner.id,
                guild_id=guild_id,
                career_length=career_length,
                peak_end=int(career_length * 0.6),
                **stats,
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

    @derby_group.command(
        name="start_schedule",
        description="Start the automatic race schedule",
    )
    @checks.has_role("Race Admin")
    async def start_schedule(self, context: Context) -> None:
        await context.defer()
        scheduler = self.bot.scheduler
        if scheduler.task and scheduler.task.is_running():
            await context.send("Race schedule is already running.", ephemeral=True)
            return
        await scheduler.start()
        await context.send(
            "Race schedule started! Races will run at the configured times."
        )

    @derby_group.command(
        name="stop_schedule",
        description="Stop the automatic race schedule",
    )
    @checks.has_role("Race Admin")
    async def stop_schedule(self, context: Context) -> None:
        await context.defer()
        scheduler = self.bot.scheduler
        if not scheduler.task or not scheduler.task.is_running():
            await context.send("Race schedule is not running.", ephemeral=True)
            return
        scheduler.task.cancel()
        await context.send("Race schedule stopped.")

    @derby_group.command(name="cancel_race", description="Cancel the next race")
    @checks.has_role("Race Admin")
    async def cancel_race(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            result = await session.execute(
                select(models.Race)
                .where(
                    models.Race.guild_id == guild_id,
                    models.Race.finished.is_(False),
                )
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
        await context.defer()
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await context.send("Racer not found", ephemeral=True)
                return
            await repo.delete_racer(session, racer)
        await context.send(f"Racer **{racer_obj.name}** (#{racer}) deleted")

    @racer_group.command(name="injure", description="Injure a racer (2d4 races recovery)")
    @app_commands.describe(racer="Racer to injure", description="Injury description")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def racer_injure(
        self, context: Context, racer: int, description: str = "Injured"
    ) -> None:
        await context.defer()
        recovery = random.randint(1, 4) + random.randint(1, 4)  # 2d4
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await context.send("Racer not found", ephemeral=True)
                return
            await repo.update_racer(
                session,
                racer,
                injuries=description,
                injury_races_remaining=recovery,
            )
        embed = discord.Embed(
            title=f"\U0001f915 {racer_obj.name} Injured!",
            description=f"**{description}**\nOut for **{recovery} races** (2d4)",
            color=0xE02B2B,
        )
        await context.send(embed=embed)

    @racer_group.command(name="heal", description="Heal a racer immediately")
    @app_commands.describe(racer="Racer to heal")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def racer_heal(self, context: Context, racer: int) -> None:
        await context.defer()
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await context.send("Racer not found", ephemeral=True)
                return
            if not racer_obj.injuries:
                await context.send(
                    f"**{racer_obj.name}** is not injured.", ephemeral=True
                )
                return
            await repo.update_racer(
                session, racer, injuries="", injury_races_remaining=0
            )
        embed = discord.Embed(
            title=f"\U0001f489 {racer_obj.name} Healed!",
            description=f"**{racer_obj.name}** has been healed and is ready to race!",
            color=0x2ECC71,
        )
        await context.send(embed=embed)

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
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            if race_id is None:
                result = await session.execute(
                    select(models.Race)
                    .where(
                        models.Race.guild_id == guild_id,
                        models.Race.finished.is_(False),
                    )
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
            if race.id in self.bot.scheduler.active_races:
                await context.send(
                    f"Race {race.id} is already in progress!", ephemeral=True
                )
                return
            self.bot.scheduler.active_races.add(race.id)
            participants = await repo.get_race_participants(session, race.id)
            if not participants:
                # Legacy race without entries — fall back to guild racers
                participants = await repo.get_guild_racers(session, guild_id)
            if len(participants) < 2:
                self.bot.scheduler.active_races.discard(race.id)
                await context.send("Not enough racers available", ephemeral=True)
                return
            race_map = logic.pick_map()
            result = logic.simulate_race(
                {"racers": participants}, seed=race.id, race_map=race_map
            )
            winner_id = result.placements[0] if result.placements else None
            await repo.update_race(
                session, race.id, finished=True, winner_id=winner_id
            )
            if winner_id is not None:
                await logic.resolve_payouts(
                    session, race.id, winner_id, guild_id=guild_id
                )
            await logic.apply_mood_drift(
                session, result.placements, participants
            )
            new_injuries = logic.check_injury_risk(result)
            await logic.apply_injuries(session, new_injuries, participants)
            for r in participants:
                r.races_completed += 1
                if r.races_completed >= r.career_length:
                    await repo.update_racer(session, r.id, retired=True)
                    cl = random.randint(25, 40)
                    await repo.create_racer(
                        session,
                        name=f"{r.name} II",
                        owner_id=r.owner_id,
                        guild_id=guild_id,
                        speed=random.randint(0, 31),
                        cornering=random.randint(0, 31),
                        stamina=random.randint(0, 31),
                        temperament=random.choice(list(logic.TEMPERAMENTS)),
                        career_length=cl,
                        peak_end=int(cl * 0.6),
                    )
            await session.commit()

        names = result.racer_names
        channel = context.channel

        # --- Pre-race build-up while LLM generates commentary ---
        lineup = ", ".join(
            f"**{names.get(rid, f'Racer {rid}')}**" for rid in result.placements
        )
        track_info = f" on **{result.map_name}**" if result.map_name else ""
        ready_embed = discord.Embed(
            title=f"\U0001f3c7 Race {race.id} — Racers Getting Ready!",
            description=(
                f"The racers are lining up{track_info}!\n\n"
                f"Lineup: {lineup}\n\n"
                f"*The race is about to begin...*"
            ),
            color=0xFFAA00,
        )
        if race_map and race_map.segments:
            layout = " \u2192 ".join(
                f"[{s.type.capitalize()}]" for s in race_map.segments
            )
            ready_embed.add_field(
                name="Track Layout", value=layout, inline=False
            )
        await context.send(embed=ready_embed)

        # Generate LLM commentary (runs during the "getting ready" moment)
        log = await commentary.generate_commentary(result)
        if log is None:
            log = commentary.build_template_commentary(result)

        # --- Stream commentary ---
        try:
            await self.bot.scheduler._stream_commentary(
                race.id, context.guild.id, log
            )

            # --- Post results ---
            await self.bot.scheduler._post_results(
                context.guild.id, result.placements, names
            )

            # --- Announce injuries ---
            if new_injuries:
                await self.bot.scheduler._announce_injuries(
                    context.guild.id, new_injuries, names
                )
        finally:
            self.bot.scheduler.active_races.discard(race.id)

    @derby_group.group(name="debug", description="Debug commands")
    async def debug_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @debug_group.command(name="race", description="Dump race data")
    @app_commands.describe(race_id="Race id")
    async def debug_race(self, context: Context, race_id: int) -> None:
        await context.defer()
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


    # -- Guild settings commands --------------------------------------------

    SETTING_KEYS = [
        "default_wallet",
        "retirement_threshold",
        "bet_window",
        "countdown_total",
        "max_racers_per_race",
        "commentary_delay",
        "channel_name",
        "racer_buy_base",
        "racer_buy_multiplier",
        "racer_sell_fraction",
        "max_racers_per_owner",
        "min_pool_size",
    ]

    @derby_group.group(name="settings", description="Per-guild setting overrides")
    async def settings_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await self._show_settings(context)

    async def _show_settings(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
        embed = discord.Embed(title="Guild Settings")
        for key in self.SETTING_KEYS:
            guild_val = getattr(gs, key, None) if gs else None
            global_val = getattr(self.bot.settings, key)
            if guild_val is not None:
                embed.add_field(
                    name=key,
                    value=f"**{guild_val}** (override)",
                    inline=False,
                )
            else:
                embed.add_field(
                    name=key,
                    value=f"{global_val} (default)",
                    inline=False,
                )
        await context.send(embed=embed, ephemeral=True)

    @settings_group.command(name="set", description="Override a setting for this server")
    @checks.has_role("Race Admin")
    @app_commands.describe(key="Setting name", value="New value (use 'reset' to clear)")
    async def settings_set(self, context: Context, key: str, value: str) -> None:
        await context.defer()
        if key not in self.SETTING_KEYS:
            await context.send(
                f"Unknown setting `{key}`. Valid: {', '.join(self.SETTING_KEYS)}",
                ephemeral=True,
            )
            return
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            if gs is None:
                gs = await repo.create_guild_settings(
                    session, guild_id=guild_id
                )

            if value.lower() == "reset":
                await repo.update_guild_settings(
                    session, guild_id, **{key: None}
                )
                global_val = getattr(self.bot.settings, key)
                await context.send(
                    f"`{key}` reset to global default: **{global_val}**",
                    ephemeral=True,
                )
                return

            # Parse value to the correct type
            try:
                if key == "channel_name":
                    parsed: str | int | float = value
                elif key in ("commentary_delay", "racer_sell_fraction"):
                    parsed = float(value)
                else:
                    parsed = int(value)
            except ValueError:
                await context.send(
                    f"Invalid value for `{key}`: expected a number.",
                    ephemeral=True,
                )
                return

            await repo.update_guild_settings(
                session, guild_id, **{key: parsed}
            )
        await context.send(
            f"`{key}` set to **{parsed}** for this server.", ephemeral=True
        )

    @settings_set.autocomplete("key")
    async def settings_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current_lower = current.lower()
        return [
            app_commands.Choice(name=k, value=k)
            for k in self.SETTING_KEYS
            if current_lower in k.lower()
        ][:25]


class Stable(commands.Cog, name="stable"):
    """Player-facing racer ownership commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    def _resolve(self, key: str, gs) -> int | float | str:
        return resolve_guild_setting(gs, self.bot.settings, key)

    @commands.hybrid_group(name="stable", description="Your racing stable")
    async def stable(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await self._show_stable(context)

    async def _show_stable(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racers = await repo.get_owned_racers(
                session, context.author.id, guild_id
            )
        if not racers:
            await context.send(
                "You don't own any racers yet! Use `/stable browse` to see "
                "what's available, then `/stable buy` to purchase one.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title=f"{context.author.display_name}'s Stable")
        for r in racers:
            phase = logic.career_phase(r)
            eff = logic.effective_stats(r)
            injury = f" | Injured: {r.injuries} ({r.injury_races_remaining}r)" if r.injuries else ""
            embed.add_field(
                name=f"{r.name} (#{r.id})",
                value=(
                    f"Spd {_stat_band(eff['speed'])} / "
                    f"Cor {_stat_band(eff['cornering'])} / "
                    f"Sta {_stat_band(eff['stamina'])}\n"
                    f"{r.temperament} | {_mood_label(r.mood)} | {phase}{injury}"
                ),
                inline=False,
            )
        await context.send(embed=embed)

    @stable.command(name="browse", description="Browse racers available for purchase")
    async def stable_browse(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racers = await repo.get_unowned_guild_racers(session, guild_id)
            gs = await repo.get_guild_settings(session, guild_id)
        base = self._resolve("racer_buy_base", gs)
        mult = self._resolve("racer_buy_multiplier", gs)
        if not racers:
            await context.send("No racers available for purchase right now.", ephemeral=True)
            return
        embed = discord.Embed(title="Racers For Sale")
        for r in racers[:25]:  # Discord embed limit
            price = logic.calculate_buy_price(r, base, mult)
            eff = logic.effective_stats(r)
            phase = logic.career_phase(r)
            embed.add_field(
                name=f"{r.name} — {price} coins",
                value=(
                    f"Spd {_stat_band(eff['speed'])} / "
                    f"Cor {_stat_band(eff['cornering'])} / "
                    f"Sta {_stat_band(eff['stamina'])}\n"
                    f"{r.temperament} | {_mood_label(r.mood)} | {phase}"
                ),
                inline=False,
            )
        if len(racers) > 25:
            embed.set_footer(text=f"Showing 25 of {len(racers)} available racers")
        await context.send(embed=embed)

    @stable.command(name="buy", description="Buy an unowned racer")
    @app_commands.describe(racer="Racer to purchase")
    @app_commands.autocomplete(racer=unowned_racer_autocomplete)
    async def stable_buy(self, context: Context, racer: int) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return
            if racer_obj.owner_id != 0:
                await context.send("That racer is already owned!", ephemeral=True)
                return

            gs = await repo.get_guild_settings(session, guild_id)
            base = self._resolve("racer_buy_base", gs)
            mult = self._resolve("racer_buy_multiplier", gs)
            max_owned = self._resolve("max_racers_per_owner", gs)
            price = logic.calculate_buy_price(racer_obj, base, mult)

            # Check ownership limit
            owned = await repo.get_owned_racers(
                session, context.author.id, guild_id
            )
            if len(owned) >= max_owned:
                await context.send(
                    f"You already own {len(owned)} racers (max {max_owned}). "
                    f"Sell one first with `/stable sell`.",
                    ephemeral=True,
                )
                return

            # Check/create wallet
            wallet = await wallet_repo.get_wallet(
                session, context.author.id, guild_id
            )
            if wallet is None:
                default_bal = self._resolve("default_wallet", gs)
                wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )
            if wallet.balance < price:
                await context.send(
                    f"Not enough coins! **{racer_obj.name}** costs "
                    f"**{price}** but you only have **{wallet.balance}**.",
                    ephemeral=True,
                )
                return

            # Re-verify ownership hasn't changed (race condition guard)
            refreshed = await repo.get_racer(session, racer)
            if refreshed is None or refreshed.owner_id != 0:
                await context.send("That racer was just purchased by someone else!", ephemeral=True)
                return

            wallet.balance -= price
            await repo.update_racer(session, racer, owner_id=context.author.id)
            await session.commit()

        embed = discord.Embed(
            title=f"Purchased {racer_obj.name}!",
            description=f"You bought **{racer_obj.name}** for **{price} coins**.",
            color=0x2ECC71,
        )
        eff = logic.effective_stats(racer_obj)
        embed.add_field(name="Speed", value=_stat_band(eff["speed"]), inline=True)
        embed.add_field(name="Cornering", value=_stat_band(eff["cornering"]), inline=True)
        embed.add_field(name="Stamina", value=_stat_band(eff["stamina"]), inline=True)
        embed.add_field(name="Temperament", value=racer_obj.temperament, inline=True)
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        await context.send(embed=embed)

    @stable.command(name="sell", description="Sell one of your racers back to the pool")
    @app_commands.describe(racer="Racer to sell")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_sell(self, context: Context, racer: int) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return
            if racer_obj.owner_id != context.author.id:
                await context.send("You don't own that racer!", ephemeral=True)
                return

            # Block selling if racer is in an unfinished race
            in_race = (
                await session.execute(
                    select(models.RaceEntry.id)
                    .join(models.Race, models.RaceEntry.race_id == models.Race.id)
                    .where(
                        models.RaceEntry.racer_id == racer,
                        models.Race.finished.is_(False),
                    )
                )
            ).scalars().first()
            if in_race is not None:
                await context.send(
                    f"**{racer_obj.name}** is entered in an upcoming race and can't be sold right now.",
                    ephemeral=True,
                )
                return

            gs = await repo.get_guild_settings(session, guild_id)
            base = self._resolve("racer_buy_base", gs)
            mult = self._resolve("racer_buy_multiplier", gs)
            frac = self._resolve("racer_sell_fraction", gs)
            sell_price = logic.calculate_sell_price(racer_obj, base, mult, frac)

            wallet = await wallet_repo.get_wallet(
                session, context.author.id, guild_id
            )
            if wallet is None:
                default_bal = self._resolve("default_wallet", gs)
                wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=context.author.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )
            wallet.balance += sell_price
            await repo.update_racer(session, racer, owner_id=0)
            await session.commit()

        await context.send(
            f"Sold **{racer_obj.name}** for **{sell_price} coins**. "
            f"Balance: **{wallet.balance}**."
        )

    @stable.command(name="rename", description="Rename one of your racers")
    @app_commands.describe(racer="Racer to rename", new_name="New name (max 32 characters)")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_rename(
        self, context: Context, racer: int, new_name: str
    ) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        new_name = new_name.strip()
        if not new_name or len(new_name) > 32:
            await context.send(
                "Name must be 1-32 characters.", ephemeral=True
            )
            return
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return
            if racer_obj.owner_id != context.author.id:
                await context.send("You don't own that racer!", ephemeral=True)
                return

            # Check name uniqueness among non-retired racers in guild
            existing = await session.execute(
                select(models.Racer.id).where(
                    models.Racer.guild_id == guild_id,
                    models.Racer.retired.is_(False),
                    models.Racer.name == new_name,
                )
            )
            if existing.scalars().first() is not None:
                await context.send(
                    f"A racer named **{new_name}** already exists in this guild.",
                    ephemeral=True,
                )
                return

            old_name = racer_obj.name
            await repo.update_racer(session, racer, name=new_name)

        await context.send(
            f"Renamed **{old_name}** to **{new_name}**."
        )


async def setup(bot) -> None:
    await bot.add_cog(Derby(bot))
    await bot.add_cog(Stable(bot))
