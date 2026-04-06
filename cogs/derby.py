from __future__ import annotations

import json
import random
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

import checks
from config import resolve_guild_setting
from derby import commentary, descriptions, logic, models
from derby import repositories as repo
from economy import repositories as wallet_repo


_stat_band = logic.stat_band
_mood_label = logic.mood_label
_gender = logic.GENDER_LABELS.get

MOOD_EMOJIS = {1: "\U0001f621", 2: "\U0001f61f", 3: "\U0001f610", 4: "\U0001f642", 5: "\U0001f604"}

TEMPERAMENT_CHOICES = [
    app_commands.Choice(name=t, value=t) for t in logic.TEMPERAMENTS
]

STAT_CHOICES = [
    app_commands.Choice(name="Speed", value="speed"),
    app_commands.Choice(name="Cornering", value="cornering"),
    app_commands.Choice(name="Stamina", value="stamina"),
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
    fem_mult = resolve_guild_setting(gs, settings, "female_buy_multiplier")
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            price = logic.calculate_buy_price(r, base, mult, fem_mult)
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


async def guild_racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete showing all racers in the guild (for admin commands)."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    async with sessionmaker() as session:
        racers = await repo.get_guild_racers(
            session, guild_id, eligible_only=False
        )
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            label = f"{r.name} (#{r.id})"
            if r.owner_id:
                label += f" \u2014 owner:{r.owner_id}"
            choices.append(app_commands.Choice(name=label[:100], value=r.id))
        if len(choices) >= 25:
            break
    return choices


async def viewable_racer_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete for /stable view — user's racers first, then others."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    user_id = interaction.user.id
    guild = interaction.guild
    async with sessionmaker() as session:
        racers = await repo.get_guild_racers(
            session, guild_id, eligible_only=False
        )
    # Split into owned vs others, filter by search
    current_lower = current.lower()
    owned = []
    others = []
    for r in racers:
        if current_lower and current_lower not in r.name.lower():
            continue
        # Build a friendly label
        if r.owner_id == user_id:
            label = f"\u2b50 {r.name} (#{r.id})"
            owned.append(app_commands.Choice(name=label[:100], value=r.id))
        else:
            if r.owner_id and r.owner_id != 0 and guild:
                member = guild.get_member(r.owner_id)
                owner_tag = member.display_name if member else "Owned"
            elif r.owner_id and r.owner_id != 0:
                owner_tag = "Owned"
            else:
                owner_tag = "Unowned"
            label = f"{r.name} (#{r.id}) \u2014 {owner_tag}"
            others.append(app_commands.Choice(name=label[:100], value=r.id))
    return (owned + others)[:25]


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

    BET_TYPE_LABELS = {
        "win": "Win",
        "place": "Place",
        "exacta": "Exacta",
        "trifecta": "Trifecta",
        "superfecta": "Superfecta",
    }

    async def _find_next_race(self, guild_id: int):
        """Return (race, racers) for the next bettable race, or (None, [])."""
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
                return None, []
            racers = await repo.get_race_participants(session, race.id)
        return race, racers

    FREE_BET_AMOUNT = 10

    async def _place_bet(
        self,
        context: Context,
        bet_type: str,
        picks: list[int],
        amount: int,
    ) -> None:
        """Shared logic for all bet commands."""
        if amount < 0:
            await context.send("Bet amount must be positive.", ephemeral=True)
            return

        guild_id = context.guild.id if context.guild else 0
        race, racers = await self._find_next_race(guild_id)
        if race is None or not racers:
            await context.send("No race available.", ephemeral=True)
            return

        # Superfecta requires exactly 6 racers in the field
        if bet_type == "superfecta" and len(racers) < 6:
            await context.send(
                "Superfecta requires exactly 6 racers in the field.",
                ephemeral=True,
            )
            return

        racer_ids_in_race = [r.id for r in racers]

        # Validate all picks are in the race
        for pick in picks:
            if pick not in racer_ids_in_race:
                await context.send(
                    "That racer isn't in the next race.", ephemeral=True
                )
                return

        # Validate no duplicate picks
        if len(picks) != len(set(picks)):
            await context.send(
                "Each racer can only appear once in your picks.", ephemeral=True
            )
            return

        multiplier = logic.calculate_bet_odds(racers, None, 0.1, bet_type, picks)
        racer_ids_json = json.dumps(picks)
        primary_racer_id = picks[0]
        pick_names = [
            next((r.name for r in racers if r.id == p), f"Racer {p}")
            for p in picks
        ]
        label = self.BET_TYPE_LABELS.get(bet_type, bet_type)

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

            # --- Free bet handling ---
            is_free = False
            if amount == 0:
                if wallet.balance > 0:
                    await context.send(
                        "You can only place a free bet when your balance is 0.",
                        ephemeral=True,
                    )
                    return
                # One free bet per race (any type)
                free_check = await session.execute(
                    select(models.Bet).where(
                        models.Bet.race_id == race.id,
                        models.Bet.user_id == context.author.id,
                        models.Bet.is_free.is_(True),
                    )
                )
                if free_check.scalars().first() is not None:
                    await context.send(
                        "You already have a free bet on this race.",
                        ephemeral=True,
                    )
                    return
                is_free = True
                amount = self.FREE_BET_AMOUNT

            payout = int(amount * multiplier)

            # Check for existing bet of the SAME type
            bet_result = await session.execute(
                select(models.Bet)
                .where(
                    models.Bet.race_id == race.id,
                    models.Bet.user_id == context.author.id,
                    models.Bet.bet_type == bet_type,
                )
            )
            existing_bet = bet_result.scalars().first()
            old_amount = 0
            if existing_bet is not None:
                old_amount = existing_bet.amount
                # Only refund to wallet if the old bet was paid (not free)
                if not existing_bet.is_free:
                    wallet.balance += existing_bet.amount
            if not is_free and wallet.balance < amount:
                await session.commit()
                await context.send("Insufficient balance.", ephemeral=True)
                return
            if not is_free:
                wallet.balance -= amount
            await session.commit()
            if existing_bet is None:
                await repo.create_bet(
                    session,
                    race_id=race.id,
                    user_id=context.author.id,
                    racer_id=primary_racer_id,
                    amount=amount,
                    payout_multiplier=multiplier,
                    bet_type=bet_type,
                    racer_ids=racer_ids_json,
                    is_free=is_free,
                )
            else:
                await repo.update_bet(
                    session,
                    existing_bet.id,
                    racer_id=primary_racer_id,
                    amount=amount,
                    payout_multiplier=multiplier,
                    racer_ids=racer_ids_json,
                    is_free=is_free,
                )

        # Build pick description
        if bet_type in ("win", "place"):
            pick_desc = f"**{pick_names[0]}**"
        else:
            pick_desc = " \u2192 ".join(f"**{n}**" for n in pick_names)

        free_tag = " (Free House Bet)" if is_free else ""
        if is_free:
            await context.send(
                f"\U0001f3b0 **{label}**{free_tag} \u2014 Race {race.id}\n"
                f"The house backs you on {pick_desc} for {amount} coins "
                f"({multiplier:.1f}x \u2014 win pays {payout})"
            )
        elif old_amount > 0:
            await context.send(
                f"\U0001f3b0 **{label}** \u2014 Race {race.id}\n"
                f"Bet changed ({old_amount} coins refunded) to {pick_desc} "
                f"for {amount} coins ({multiplier:.1f}x \u2014 win pays {payout})"
            )
        else:
            await context.send(
                f"\U0001f3b0 **{label}** \u2014 Race {race.id}\n"
                f"Bet placed on {pick_desc} for {amount} coins "
                f"({multiplier:.1f}x \u2014 win pays {payout})"
            )

    @race.command(name="bet-win", description="Bet on a racer to win (1st place)")
    @app_commands.describe(racer="Racer to bet on", amount="Amount to bet")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def race_bet_win(self, context: Context, racer: int, amount: int) -> None:
        await context.defer()
        await self._place_bet(context, "win", [racer], amount)

    @race.command(name="bet-place", description="Bet on a racer to place (1st or 2nd)")
    @app_commands.describe(racer="Racer to bet on", amount="Amount to bet")
    @app_commands.autocomplete(racer=racer_autocomplete)
    async def race_bet_place(self, context: Context, racer: int, amount: int) -> None:
        await context.defer()
        await self._place_bet(context, "place", [racer], amount)

    @race.command(name="bet-exacta", description="Bet on exact 1st and 2nd place")
    @app_commands.describe(
        first="Racer to finish 1st", second="Racer to finish 2nd",
        amount="Amount to bet",
    )
    @app_commands.autocomplete(first=racer_autocomplete, second=racer_autocomplete)
    async def race_bet_exacta(
        self, context: Context, first: int, second: int, amount: int
    ) -> None:
        await context.defer()
        await self._place_bet(context, "exacta", [first, second], amount)

    @race.command(name="bet-trifecta", description="Bet on exact 1st, 2nd, and 3rd place")
    @app_commands.describe(
        first="Racer to finish 1st", second="Racer to finish 2nd",
        third="Racer to finish 3rd", amount="Amount to bet",
    )
    @app_commands.autocomplete(
        first=racer_autocomplete, second=racer_autocomplete,
        third=racer_autocomplete,
    )
    async def race_bet_trifecta(
        self, context: Context, first: int, second: int, third: int, amount: int
    ) -> None:
        await context.defer()
        await self._place_bet(context, "trifecta", [first, second, third], amount)

    @race.command(
        name="bet-superfecta",
        description="Bet on the exact finish order of all 6 racers",
    )
    @app_commands.describe(
        first="Racer to finish 1st", second="Racer to finish 2nd",
        third="Racer to finish 3rd", fourth="Racer to finish 4th",
        fifth="Racer to finish 5th", sixth="Racer to finish 6th",
        amount="Amount to bet",
    )
    @app_commands.autocomplete(
        first=racer_autocomplete, second=racer_autocomplete,
        third=racer_autocomplete, fourth=racer_autocomplete,
        fifth=racer_autocomplete, sixth=racer_autocomplete,
    )
    async def race_bet_superfecta(
        self, context: Context,
        first: int, second: int, third: int,
        fourth: int, fifth: int, sixth: int,
        amount: int,
    ) -> None:
        await context.defer()
        await self._place_bet(
            context, "superfecta",
            [first, second, third, fourth, fifth, sixth], amount,
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
        gender = _gender(getattr(racer_obj, "gender", "M"), "")
        embed = discord.Embed(title=f"{gender} {racer_obj.name}")
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
            name="Rank",
            value=logic.rank_label(getattr(racer_obj, "rank", None)),
            inline=True,
        )
        embed.add_field(
            name="Career",
            value=f"Race {racer_obj.races_completed}/{racer_obj.career_length} ({phase})",
            inline=True,
        )
        injury_text = "None"
        if racer_obj.injuries:
            injury_text = f"{racer_obj.injuries} ({racer_obj.injury_races_remaining} races remaining)"
        embed.add_field(name="Injuries", value=injury_text, inline=False)

        # Accolades
        t_wins = getattr(racer_obj, "tournament_wins", 0) or 0
        t_places = getattr(racer_obj, "tournament_placements", 0) or 0
        if t_wins > 0 or t_places > 0:
            accolade_parts = []
            if t_wins > 0:
                accolade_parts.append(f"\U0001f3c6 {t_wins} Tournament Win{'s' if t_wins != 1 else ''}")
            if t_places > 0:
                accolade_parts.append(f"\U0001f948 {t_places} Placement{'s' if t_places != 1 else ''}")
            embed.add_field(
                name="Accolades",
                value=" | ".join(accolade_parts),
                inline=False,
            )

        # Lineage info
        sire_id = getattr(racer_obj, "sire_id", None)
        dam_id = getattr(racer_obj, "dam_id", None)
        if sire_id or dam_id:
            async with self.bot.scheduler.sessionmaker() as session:
                sire = await repo.get_racer(session, sire_id) if sire_id else None
                dam = await repo.get_racer(session, dam_id) if dam_id else None
            sire_name = sire.name if sire else "Unknown"
            dam_name = dam.name if dam else "Unknown"
            embed.add_field(
                name="Lineage",
                value=f"Sire: {sire_name} | Dam: {dam_name}",
                inline=False,
            )

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

    # -- Economy commands -----------------------------------------------

    @derby_group.command(
        name="give-coins", description="Give or remove coins from a player"
    )
    @checks.has_role("Race Admin")
    @app_commands.describe(
        user="Player to give coins to",
        amount="Amount of coins (negative to remove)",
    )
    async def give_coins(
        self, context: Context, user: discord.User, amount: int
    ) -> None:
        await context.defer()
        if amount == 0:
            await context.send("Amount must not be zero.", ephemeral=True)
            return
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(session, user.id, guild_id)
            if wallet is None:
                gs = await repo.get_guild_settings(session, guild_id)
                default_bal = resolve_guild_setting(
                    gs, self.bot.settings, "default_wallet"
                )
                wallet = await wallet_repo.create_wallet(
                    session,
                    user_id=user.id,
                    guild_id=guild_id,
                    balance=default_bal,
                )
            new_balance = wallet.balance + amount
            if new_balance < 0:
                await context.send(
                    f"Cannot remove {abs(amount)} coins \u2014 "
                    f"{user.mention} only has {wallet.balance}.",
                    ephemeral=True,
                )
                return
            wallet.balance = new_balance
            await session.commit()
        action = "Gave" if amount > 0 else "Removed"
        await context.send(
            f"{action} **{abs(amount)}** coins "
            f"{'to' if amount > 0 else 'from'} {user.mention}. "
            f"New balance: **{new_balance}**."
        )

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
        rank = logic.calculate_rank(
            stats.get("speed", 0), stats.get("cornering", 0), stats.get("stamina", 0)
        )
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.create_racer(
                session,
                name=name,
                owner_id=owner.id,
                guild_id=guild_id,
                career_length=career_length,
                peak_end=int(career_length * 0.6),
                rank=rank,
                **stats,
            )
            # Generate description if flavor is set
            gs = await repo.get_guild_settings(session, guild_id)
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            if flavor:
                desc = await descriptions.generate_description(
                    name=racer.name,
                    speed=racer.speed,
                    cornering=racer.cornering,
                    stamina=racer.stamina,
                    temperament=racer.temperament,
                    gender=racer.gender,
                    flavor=flavor,
                )
                if desc:
                    racer.description = desc
                    await session.commit()
                    await session.refresh(racer)
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
        owner="New owner (mention or 0 for unowned)",
        speed="Speed stat",
        cornering="Cornering stat",
        stamina="Stamina stat",
        temperament="Temperament",
    )
    @app_commands.autocomplete(racer=guild_racer_autocomplete)
    @app_commands.choices(temperament=TEMPERAMENT_CHOICES)
    async def edit_racer(
        self,
        context: Context,
        racer: int,
        name: str | None = None,
        owner: discord.Member | None = None,
        speed: app_commands.Range[int, 0, 31] | None = None,
        cornering: app_commands.Range[int, 0, 31] | None = None,
        stamina: app_commands.Range[int, 0, 31] | None = None,
        temperament: str | None = None,
    ) -> None:
        updates: dict[str, int | str] = {}
        if name is not None:
            updates["name"] = name
        if owner is not None:
            updates["owner_id"] = owner.id
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
            # Recalculate rank if any stat changed
            if any(k in updates for k in ("speed", "cornering", "stamina")):
                rank_change = logic.recalculate_rank(updated)
                if rank_change:
                    await session.commit()
            else:
                rank_change = None
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
        if rank_change:
            embed.add_field(
                name="Rank Changed",
                value=f"Now **{logic.rank_label(rank_change)}**",
                inline=False,
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
            placements_json = json.dumps(result.placements)
            await repo.update_race(
                session, race.id, finished=True, winner_id=winner_id,
                placements=placements_json,
            )
            await logic.resolve_payouts(
                session, race.id, result.placements, guild_id=guild_id
            )
            gs = await repo.get_guild_settings(session, guild_id)
            prize_list = logic.parse_placement_prizes(
                resolve_guild_setting(gs, self.bot.settings, "placement_prizes")
            )
            placement_awards = await logic.resolve_placement_prizes(
                session, result.placements, participants,
                guild_id=guild_id, prize_list=prize_list,
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

        # Ensure the next scheduled race is queued so the timer loop
        # doesn't stall after a force-start consumes the pending race.
        await self.bot.scheduler._create_next_race(guild_id)

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
        "placement_prizes",
        "training_base",
        "training_multiplier",
        "rest_cost",
        "feed_cost",
        "stable_upgrade_costs",
        "female_buy_multiplier",
        "retired_sell_penalty",
        "foal_sell_penalty",
        "min_training_to_race",
        "breeding_fee",
        "breeding_cooldown",
        "min_races_to_breed",
        "max_foals_per_female",
        "racer_flavor",
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
                display = global_val if global_val is not None else "not set"
                embed.add_field(
                    name=key,
                    value=f"{display} (default)",
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
                if key in ("channel_name", "placement_prizes", "stable_upgrade_costs", "racer_flavor"):
                    parsed: str | int | float = value
                elif key in (
                    "commentary_delay",
                    "racer_sell_fraction",
                    "female_buy_multiplier",
                    "retired_sell_penalty",
                    "foal_sell_penalty",
                ):
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
            gs = await repo.get_guild_settings(session, guild_id)
        if not racers:
            await context.send(
                "You don't own any racers yet! Use `/stable browse` to see "
                "what's available, then `/stable buy` to purchase one.",
                ephemeral=True,
            )
            return
        min_train = self._resolve("min_training_to_race", gs)
        embed = discord.Embed(title=f"{context.author.display_name}'s Stable")
        for r in racers:
            phase = logic.career_phase(r)
            eff = logic.effective_stats(r)
            gender = _gender(getattr(r, "gender", "M"), "")
            injury = f" | Injured: {r.injuries} ({r.injury_races_remaining}r)" if r.injuries else ""
            # Show training progress for foals (bred racers)
            tc = r.training_count or 0
            training = ""
            if r.sire_id is not None and tc < min_train:
                training = f" | Training: {tc}/{min_train} \U0001f3cb"
            rank = logic.rank_label(getattr(r, "rank", None))
            t_wins = getattr(r, "tournament_wins", 0) or 0
            trophy = f" \U0001f3c6{t_wins}" if t_wins > 0 else ""
            embed.add_field(
                name=f"{gender} {r.name} (#{r.id}) [{rank}]{trophy}",
                value=(
                    f"Spd {_stat_band(eff['speed'])} / "
                    f"Cor {_stat_band(eff['cornering'])} / "
                    f"Sta {_stat_band(eff['stamina'])}\n"
                    f"{r.temperament} | {_mood_label(r.mood)} | {phase}{injury}{training}"
                ),
                inline=False,
            )
        await context.send(embed=embed)

    @stable.command(name="view", description="View a racer's full profile")
    @app_commands.describe(racer="Racer to view")
    @app_commands.autocomplete(racer=viewable_racer_autocomplete)
    async def stable_view(self, context: Context, racer: int) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return

            gs = await repo.get_guild_settings(session, guild_id)

            # Look up lineage names
            sire_name = "Unknown"
            dam_name = "Unknown"
            if racer_obj.sire_id:
                sire = await repo.get_racer(session, racer_obj.sire_id)
                if sire:
                    sire_name = sire.name
            if racer_obj.dam_id:
                dam = await repo.get_racer(session, racer_obj.dam_id)
                if dam:
                    dam_name = dam.name

            # Look up owner name
            if racer_obj.owner_id and racer_obj.owner_id != 0:
                member = context.guild.get_member(racer_obj.owner_id) if context.guild else None
                owner_name = member.display_name if member else f"User #{racer_obj.owner_id}"
            else:
                owner_name = "Unowned"

            # Lazy-generate description if missing and flavor is set
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            if racer_obj.description is None and flavor:
                desc = await descriptions.generate_description(
                    name=racer_obj.name,
                    speed=racer_obj.speed,
                    cornering=racer_obj.cornering,
                    stamina=racer_obj.stamina,
                    temperament=racer_obj.temperament,
                    gender=racer_obj.gender,
                    flavor=flavor,
                )
                if desc:
                    racer_obj.description = desc
                    await session.commit()
                    await session.refresh(racer_obj)

        # Build embed
        gender_emoji = logic.GENDER_LABELS.get(racer_obj.gender, "")
        if racer_obj.retired:
            color = 0xF1C40F  # gold
        elif racer_obj.injuries:
            color = 0xE74C3C  # red
        else:
            color = 0x2ECC71  # green

        embed = discord.Embed(
            title=f"{racer_obj.name}  |  {gender_emoji} {racer_obj.gender}",
            color=color,
        )

        eff = logic.effective_stats(racer_obj)
        embed.add_field(
            name="Stats",
            value=(
                f"Speed {eff['speed']} ({_stat_band(eff['speed'])}) / "
                f"Cornering {eff['cornering']} ({_stat_band(eff['cornering'])}) / "
                f"Stamina {eff['stamina']} ({_stat_band(eff['stamina'])})"
            ),
            inline=False,
        )
        embed.add_field(name="Temperament", value=racer_obj.temperament, inline=True)

        mood_emoji = MOOD_EMOJIS.get(racer_obj.mood, "")
        embed.add_field(
            name="Mood",
            value=f"{mood_emoji} {_mood_label(racer_obj.mood)} ({racer_obj.mood}/5)",
            inline=True,
        )

        phase = logic.career_phase(racer_obj)
        embed.add_field(
            name="Career",
            value=f"{racer_obj.races_completed}/{racer_obj.career_length} races | {phase}",
            inline=True,
        )

        rank = logic.rank_label(getattr(racer_obj, "rank", None))
        embed.add_field(name="Rank", value=rank, inline=True)

        # Lineage
        if racer_obj.sire_id or racer_obj.dam_id:
            embed.add_field(
                name="Lineage",
                value=f"Sire: {sire_name} | Dam: {dam_name}",
                inline=False,
            )

        # Foals
        max_foals = self._resolve("max_foals_per_female", gs)
        foal_val = str(racer_obj.foal_count)
        if racer_obj.gender == "F":
            foal_val += f"/{max_foals}"
        embed.add_field(name="Foals", value=foal_val, inline=True)

        # Tournament record
        t_wins = getattr(racer_obj, "tournament_wins", 0) or 0
        t_place = getattr(racer_obj, "tournament_placements", 0) or 0
        if t_wins or t_place:
            embed.add_field(
                name="Tournament Record",
                value=f"{t_wins}W / {t_place} top-3",
                inline=True,
            )

        embed.add_field(
            name="Training",
            value=f"{racer_obj.training_count or 0} sessions",
            inline=True,
        )

        # Injury
        if racer_obj.injuries:
            embed.add_field(
                name="Injury",
                value=f"\u26a0\ufe0f {racer_obj.injuries} ({racer_obj.injury_races_remaining} races left)",
                inline=False,
            )

        # Breed cooldown
        if racer_obj.breed_cooldown and racer_obj.breed_cooldown > 0:
            embed.add_field(
                name="Breed Cooldown",
                value=f"{racer_obj.breed_cooldown} races",
                inline=True,
            )

        # Description
        if racer_obj.description:
            desc = racer_obj.description
        elif flavor:
            desc = "No description yet."
        else:
            desc = "Set a racer flavor with `/derby settings set racer_flavor <text>` to generate descriptions."
        embed.add_field(name="Description", value=desc, inline=False)

        embed.set_footer(text=f"ID: {racer_obj.id} | Owner: {owner_name}")
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
        fem_mult = self._resolve("female_buy_multiplier", gs)
        if not racers:
            await context.send("No racers available for purchase right now.", ephemeral=True)
            return
        embed = discord.Embed(title="Racers For Sale")
        for r in racers[:25]:  # Discord embed limit
            price = logic.calculate_buy_price(r, base, mult, fem_mult)
            eff = logic.effective_stats(r)
            phase = logic.career_phase(r)
            gender = _gender(getattr(r, "gender", "M"), "")
            rank = logic.rank_label(getattr(r, "rank", None))
            embed.add_field(
                name=f"{gender} {r.name} [{rank}] — {price} coins",
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
            fem_mult = self._resolve("female_buy_multiplier", gs)
            price = logic.calculate_buy_price(racer_obj, base, mult, fem_mult)

            # Check ownership limit (retired racers count toward slots)
            stable = await repo.get_stable_racers(
                session, context.author.id, guild_id
            )
            base_slots = self._resolve("max_racers_per_owner", gs)
            pd = await repo.get_player_data(
                session, context.author.id, guild_id
            )
            extra = pd.extra_slots if pd else 0
            upgrade_costs = logic.parse_stable_upgrade_costs(
                self._resolve("stable_upgrade_costs", gs)
            )
            max_slots = min(base_slots + extra, base_slots + len(upgrade_costs))
            if len(stable) >= max_slots:
                await context.send(
                    f"Your stable is full ({len(stable)}/{max_slots}). "
                    f"Sell a racer or `/stable upgrade` for more slots.",
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
            fem_mult = self._resolve("female_buy_multiplier", gs)
            ret_pen = self._resolve("retired_sell_penalty", gs)
            foal_pen = self._resolve("foal_sell_penalty", gs)
            t_bonus = logic.calculate_tournament_sell_bonus(racer_obj)
            sell_price = logic.calculate_sell_price(
                racer_obj, base, mult, frac,
                female_multiplier=fem_mult,
                retired_penalty=ret_pen,
                foal_penalty=foal_pen,
                tournament_bonus=t_bonus,
            )

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

    @stable.command(name="train", description="Train a racer to improve a stat")
    @app_commands.describe(racer="Racer to train", stat="Stat to improve")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    @app_commands.choices(stat=STAT_CHOICES)
    async def stable_train(
        self, context: Context, racer: int, stat: app_commands.Choice[str]
    ) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        stat_name = stat.value if isinstance(stat, app_commands.Choice) else stat

        if stat_name not in logic.TRAINABLE_STATS:
            await context.send(
                "Invalid stat. Choose speed, cornering, or stamina.",
                ephemeral=True,
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
            if racer_obj.retired:
                await context.send("This racer is retired.", ephemeral=True)
                return

            current_value = getattr(racer_obj, stat_name)
            if current_value >= logic.MAX_STAT:
                await context.send(
                    f"**{racer_obj.name}**'s {stat_name} is already at maximum "
                    f"({logic.MAX_STAT}).",
                    ephemeral=True,
                )
                return

            # Resolve training cost settings
            gs = await repo.get_guild_settings(session, guild_id)
            training_base = self._resolve("training_base", gs)
            training_mult = self._resolve("training_multiplier", gs)
            cost = logic.calculate_training_cost(
                current_value, training_base, training_mult
            )

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
            if wallet.balance < cost:
                await context.send(
                    f"Training costs **{cost} coins** but you only have "
                    f"**{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            # Deduct cost and reduce mood
            wallet.balance -= cost
            old_mood = racer_obj.mood
            new_mood = max(1, old_mood - 1)
            racer_obj.mood = new_mood

            # Roll for failure
            fail_chance = logic.training_failure_chance(
                old_mood, racer_obj.injury_races_remaining > 0
            )
            failed = random.random() < fail_chance

            if not failed:
                new_value = current_value + 1
                await repo.update_racer(session, racer, **{stat_name: new_value})
                racer_obj.training_count = (racer_obj.training_count or 0) + 1
                # Recalculate rank after stat change
                setattr(racer_obj, stat_name, new_value)
                rank_change = logic.recalculate_rank(racer_obj)
            else:
                new_value = current_value
                rank_change = None

            await session.commit()

        # Build response embed
        if failed:
            embed = discord.Embed(
                title=f"Training Failed: {racer_obj.name}",
                description=(
                    f"The training session didn't stick. "
                    f"**{cost} coins** spent but {stat_name} unchanged."
                ),
                color=0xE74C3C,
            )
        else:
            embed = discord.Embed(
                title=f"Training Complete: {racer_obj.name}",
                color=0x2ECC71,
            )
            embed.add_field(
                name=stat_name.capitalize(),
                value=(
                    f"{_stat_band(current_value)} \u2192 {_stat_band(new_value)} "
                    f"({current_value} \u2192 {new_value})"
                ),
                inline=True,
            )

        embed.add_field(name="Cost", value=f"{cost} coins", inline=True)
        if old_mood != new_mood:
            embed.add_field(
                name="Mood",
                value=f"{_mood_label(old_mood)} \u2192 {_mood_label(new_mood)}",
                inline=True,
            )
        if rank_change:
            embed.add_field(
                name="\u2b06\ufe0f Rank Up!",
                value=f"Promoted to **{logic.rank_label(rank_change)}**",
                inline=False,
            )
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        if fail_chance > 0:
            pct = int(fail_chance * 100)
            embed.description = (embed.description or "") + f"\n*Failure chance was {pct}%*"
        await context.send(embed=embed)

    @stable.command(name="rest", description="Rest a racer to improve their mood (+1)")
    @app_commands.describe(racer="Your racer to rest")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_rest(self, context: Context, racer: int) -> None:
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
            if racer_obj.retired:
                await context.send("This racer is retired.", ephemeral=True)
                return

            new_mood, error = logic.apply_rest(racer_obj.mood)
            if error:
                await context.send(error, ephemeral=True)
                return

            gs = await repo.get_guild_settings(session, guild_id)
            cost = self._resolve("rest_cost", gs)

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
            if wallet.balance < cost:
                await context.send(
                    f"Resting costs **{cost} coins** but you only have "
                    f"**{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            old_mood = racer_obj.mood
            wallet.balance -= cost
            racer_obj.mood = new_mood
            await session.commit()

        embed = discord.Embed(
            title=f"{racer_obj.name} Takes a Rest",
            description=(
                f"{racer_obj.name} relaxes in the stable and feels better."
            ),
            color=0x3498DB,
        )
        embed.add_field(
            name="Mood",
            value=f"{_mood_label(old_mood)} \u2192 {_mood_label(new_mood)}",
            inline=True,
        )
        embed.add_field(name="Cost", value=f"{cost} coins", inline=True)
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        await context.send(embed=embed)

    @stable.command(name="feed", description="Feed a racer premium oats to boost mood (+2)")
    @app_commands.describe(racer="Your racer to feed")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_feed(self, context: Context, racer: int) -> None:
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
            if racer_obj.retired:
                await context.send("This racer is retired.", ephemeral=True)
                return

            new_mood, error = logic.apply_feed(racer_obj.mood)
            if error:
                await context.send(error, ephemeral=True)
                return

            gs = await repo.get_guild_settings(session, guild_id)
            cost = self._resolve("feed_cost", gs)

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
            if wallet.balance < cost:
                await context.send(
                    f"Feeding costs **{cost} coins** but you only have "
                    f"**{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            old_mood = racer_obj.mood
            wallet.balance -= cost
            racer_obj.mood = new_mood
            await session.commit()

        embed = discord.Embed(
            title=f"{racer_obj.name} Enjoys a Feast",
            description=(
                f"{racer_obj.name} devours a bucket of premium oats and perks right up."
            ),
            color=0xF39C12,
        )
        embed.add_field(
            name="Mood",
            value=f"{_mood_label(old_mood)} \u2192 {_mood_label(new_mood)}",
            inline=True,
        )
        embed.add_field(name="Cost", value=f"{cost} coins", inline=True)
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        await context.send(embed=embed)


    @stable.command(name="upgrade", description="Upgrade your stable to hold more racers")
    async def stable_upgrade(self, context: Context) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0

        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            base_slots = self._resolve("max_racers_per_owner", gs)
            cost_string = self._resolve("stable_upgrade_costs", gs)
            upgrade_costs = logic.parse_stable_upgrade_costs(cost_string)

            pd = await repo.get_player_data(
                session, context.author.id, guild_id
            )
            extra = pd.extra_slots if pd else 0
            max_extra = len(upgrade_costs)
            current_slots = base_slots + extra
            max_slots = base_slots + max_extra

            if extra >= max_extra:
                await context.send(
                    f"Your stable is fully upgraded! "
                    f"({current_slots}/{max_slots} slots)",
                    ephemeral=True,
                )
                return

            cost = logic.get_next_upgrade_cost(extra, upgrade_costs)
            if cost is None:
                await context.send("No upgrades available.", ephemeral=True)
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
            if wallet.balance < cost:
                await context.send(
                    f"Upgrading costs **{cost} coins** but you only have "
                    f"**{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            wallet.balance -= cost
            if pd is None:
                pd = await repo.create_player_data(
                    session,
                    user_id=context.author.id,
                    guild_id=guild_id,
                    extra_slots=1,
                )
            else:
                pd.extra_slots += 1
            await session.commit()

        new_slots = base_slots + pd.extra_slots
        embed = discord.Embed(
            title="Stable Upgraded!",
            description=(
                f"Your stable now holds **{new_slots}** racers "
                f"(was {new_slots - 1})."
            ),
            color=0x9B59B6,
        )
        embed.add_field(name="Cost", value=f"{cost} coins", inline=True)
        remaining = max_extra - pd.extra_slots
        if remaining > 0:
            next_cost = logic.get_next_upgrade_cost(pd.extra_slots, upgrade_costs)
            embed.add_field(
                name="Next Upgrade",
                value=f"{next_cost} coins ({remaining} remaining)",
                inline=True,
            )
        else:
            embed.add_field(name="Status", value="Fully upgraded!", inline=True)
        embed.set_footer(text=f"Balance: {wallet.balance} coins")
        await context.send(embed=embed)


    @stable.command(name="breed", description="Breed two of your racers to produce a foal")
    @app_commands.describe(male="Male racer (sire)", female="Female racer (dam)")
    @app_commands.autocomplete(male=owned_racer_autocomplete, female=owned_racer_autocomplete)
    async def stable_breed(self, context: Context, male: int, female: int) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0

        if male == female:
            await context.send("You must select two different racers.", ephemeral=True)
            return

        async with self.bot.scheduler.sessionmaker() as session:
            sire = await repo.get_racer(session, male)
            dam = await repo.get_racer(session, female)
            if sire is None or sire.guild_id != guild_id:
                await context.send("Male racer not found.", ephemeral=True)
                return
            if dam is None or dam.guild_id != guild_id:
                await context.send("Female racer not found.", ephemeral=True)
                return

            gs = await repo.get_guild_settings(session, guild_id)
            fee = self._resolve("breeding_fee", gs)
            cooldown = self._resolve("breeding_cooldown", gs)
            min_races = self._resolve("min_races_to_breed", gs)
            max_foals = self._resolve("max_foals_per_female", gs)

            # Stable slot check
            stable = await repo.get_stable_racers(
                session, context.author.id, guild_id
            )
            base_slots = self._resolve("max_racers_per_owner", gs)
            pd = await repo.get_player_data(
                session, context.author.id, guild_id
            )
            extra = pd.extra_slots if pd else 0
            upgrade_costs = logic.parse_stable_upgrade_costs(
                self._resolve("stable_upgrade_costs", gs)
            )
            max_slots = min(base_slots + extra, base_slots + len(upgrade_costs))

            # Validate
            error = logic.validate_breeding(
                sire, dam, context.author.id, len(stable), max_slots,
                min_races=min_races, max_foals=max_foals,
            )
            if error:
                await context.send(error, ephemeral=True)
                return

            # Check wallet
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
            if wallet.balance < fee:
                await context.send(
                    f"Breeding costs **{fee} coins** but you only have "
                    f"**{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            # Breed!
            kwargs = logic.breed_racer(sire, dam, guild_id)
            foal = await repo.create_racer(session, **kwargs)

            # Generate foal description if both parents have descriptions and flavor is set
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            if flavor and sire.description and dam.description:
                foal_desc = await descriptions.generate_description(
                    name=foal.name,
                    speed=foal.speed,
                    cornering=foal.cornering,
                    stamina=foal.stamina,
                    temperament=foal.temperament,
                    gender=foal.gender,
                    flavor=flavor,
                    sire_desc=sire.description,
                    dam_desc=dam.description,
                )
                if foal_desc:
                    foal.description = foal_desc

            wallet.balance -= fee
            sire.breed_cooldown = cooldown
            dam.breed_cooldown = cooldown
            dam.foal_count = (dam.foal_count or 0) + 1
            await session.commit()

        min_train = self._resolve("min_training_to_race", gs)
        gender = _gender(foal.gender, "")
        embed = discord.Embed(
            title=f"\U0001f423 New Foal: {foal.name}",
            description=(
                f"**{sire.name}** \u2642 \u00d7 **{dam.name}** \u2640 "
                f"produced a foal!"
            ),
            color=0xE91E63,
        )
        embed.add_field(name="Gender", value=f"{gender} {foal.gender}", inline=True)
        embed.add_field(name="Temperament", value=foal.temperament, inline=True)
        embed.add_field(
            name="Career",
            value=f"{foal.career_length} races (peak until {foal.peak_end})",
            inline=True,
        )
        embed.add_field(
            name="Speed", value=f"{_stat_band(foal.speed)} ({foal.speed})", inline=True
        )
        embed.add_field(
            name="Cornering", value=f"{_stat_band(foal.cornering)} ({foal.cornering})", inline=True
        )
        embed.add_field(
            name="Stamina", value=f"{_stat_band(foal.stamina)} ({foal.stamina})", inline=True
        )
        embed.add_field(
            name="Training",
            value=f"0/{min_train} sessions before racing",
            inline=False,
        )
        embed.set_footer(
            text=f"Breeding fee: {fee} coins | Balance: {wallet.balance} coins"
        )
        await context.send(embed=embed)


class Tournament(commands.Cog, name="tournament_cog"):
    """Tournament registration and listing commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    tournament = app_commands.Group(
        name="tournament", description="Tournament commands"
    )

    @tournament.command(name="register", description="Register a racer for the next tournament")
    @app_commands.describe(racer="The racer to register")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def tournament_register(
        self, interaction: discord.Interaction, racer: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id or 0
        user_id = interaction.user.id

        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await interaction.followup.send("Racer not found.", ephemeral=True)
                return

            if racer_obj.owner_id != user_id:
                await interaction.followup.send(
                    "You don't own that racer.", ephemeral=True
                )
                return

            if racer_obj.retired:
                await interaction.followup.send(
                    "Retired racers can't compete in tournaments.", ephemeral=True
                )
                return

            rank = racer_obj.rank
            if rank is None:
                rank = logic.assign_rank_if_needed(racer_obj)
                await session.commit()

            if rank is None:
                await interaction.followup.send(
                    "This racer has no rank assigned.", ephemeral=True
                )
                return

            # Find or create pending tournament for this guild+rank
            tournament = await repo.get_pending_tournament(session, guild_id, rank)
            if tournament is None:
                tournament = await repo.create_tournament(
                    session, guild_id=guild_id, rank=rank
                )

            # Check for duplicate registration in this bracket
            existing = await repo.get_player_tournament_entry(
                session, tournament.id, user_id
            )
            if existing is not None:
                await interaction.followup.send(
                    f"You already have a racer registered in the {rank}-Rank tournament.",
                    ephemeral=True,
                )
                return

            await repo.create_tournament_entry(
                session,
                tournament_id=tournament.id,
                racer_id=racer_obj.id,
                owner_id=user_id,
                is_pool_filler=False,
            )

        # Find next scheduled time for this rank
        next_time = _next_tournament_time(rank)
        time_str = f"<t:{int(next_time.timestamp())}:R>" if next_time else "soon"

        await interaction.followup.send(
            f"✅ **{racer_obj.name}** registered for the **{rank}-Rank** tournament! "
            f"Next run: {time_str}",
            ephemeral=True,
        )

    @tournament.command(name="cancel", description="Cancel a tournament registration")
    @app_commands.describe(racer="The racer to unregister")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def tournament_cancel(
        self, interaction: discord.Interaction, racer: int
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id or 0
        user_id = interaction.user.id

        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None:
                await interaction.followup.send("Racer not found.", ephemeral=True)
                return

            rank = racer_obj.rank
            if rank is None:
                await interaction.followup.send(
                    "This racer has no rank.", ephemeral=True
                )
                return

            tournament = await repo.get_pending_tournament(session, guild_id, rank)
            if tournament is None:
                await interaction.followup.send(
                    "No pending tournament for this rank.", ephemeral=True
                )
                return

            entry = await repo.get_player_tournament_entry(
                session, tournament.id, user_id
            )
            if entry is None or entry.racer_id != racer_obj.id:
                await interaction.followup.send(
                    "That racer isn't registered in this tournament.", ephemeral=True
                )
                return

            await session.delete(entry)
            await session.commit()

        await interaction.followup.send(
            f"❌ **{racer_obj.name}** has been withdrawn from the **{rank}-Rank** tournament.",
            ephemeral=True,
        )

    @tournament.command(name="list", description="Show pending tournaments")
    async def tournament_list(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild_id = interaction.guild_id or 0

        lines = []
        async with self.bot.scheduler.sessionmaker() as session:
            for rank in ["S", "A", "B", "C", "D"]:
                tournament = await repo.get_pending_tournament(session, guild_id, rank)
                if tournament is None:
                    continue
                entries = await repo.get_tournament_entries(session, tournament.id)
                player_entries = [e for e in entries if not e.is_pool_filler]
                if not player_entries:
                    continue

                racer_names = []
                for entry in player_entries:
                    racer = await session.get(models.Racer, entry.racer_id)
                    name = racer.name if racer else f"Racer {entry.racer_id}"
                    racer_names.append(f"**{name}** (<@{entry.owner_id}>)")

                next_time = _next_tournament_time(rank)
                time_str = f"<t:{int(next_time.timestamp())}:R>" if next_time else "TBD"

                lines.append(
                    f"**{rank}-Rank** — {len(player_entries)} registered | Next: {time_str}\n"
                    + "\n".join(f"  • {n}" for n in racer_names)
                )

        if not lines:
            await interaction.followup.send(
                "No tournaments with registered players. "
                "Use `/tournament register` to enter one!"
            )
            return

        embed = discord.Embed(
            title="🏟️ Pending Tournaments",
            description="\n\n".join(lines),
            color=0x9B59B6,
        )
        await interaction.followup.send(embed=embed)


def _next_tournament_time(rank: str) -> datetime | None:
    """Return the next UTC datetime when a tournament of this rank fires."""
    from datetime import timedelta, timezone
    from derby.scheduler import TOURNAMENT_SCHEDULE

    now = datetime.now(timezone.utc)
    matches = [(wd, h, m) for wd, h, m, r in TOURNAMENT_SCHEDULE if r == rank]
    if not matches:
        return None

    candidates = []
    for wd, h, m in matches:
        days_ahead = (wd - now.weekday()) % 7
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        candidates.append(candidate)

    return min(candidates)


async def setup(bot) -> None:
    await bot.add_cog(Derby(bot))
    await bot.add_cog(Stable(bot))
    await bot.add_cog(Tournament(bot))
