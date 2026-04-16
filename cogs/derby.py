from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

import checks
from config import resolve_guild_setting
from derby import commentary, descriptions, flavor_names, logic, models
from derby import repositories as repo
from economy import repositories as wallet_repo
from rpg import repositories as rpg_repo
from rpg.logic import get_racial_modifier


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
    """Autocomplete showing the user's owned racers (including retired)."""
    sessionmaker = interaction.client.scheduler.sessionmaker
    guild_id = interaction.guild_id or 0
    async with sessionmaker() as session:
        racers = await repo.get_stable_racers(
            session, interaction.user.id, guild_id
        )
    choices = []
    current_lower = current.lower()
    for r in racers:
        if current_lower in r.name.lower():
            label = f"{r.name} (#{r.id})"
            if r.retired:
                label += " [retired]"
            choices.append(
                app_commands.Choice(name=label, value=r.id)
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


# ---------------------------------------------------------------------------
# Betting UI Components
# ---------------------------------------------------------------------------

BET_TYPE_LABELS = {
    "win": "Win",
    "place": "Place",
    "exacta": "Exacta",
    "trifecta": "Trifecta",
    "superfecta": "Superfecta",
}

BET_PICK_COUNTS = {
    "win": 1,
    "place": 1,
    "exacta": 2,
    "trifecta": 3,
    "superfecta": 6,
}

AMOUNT_PRESETS = [10, 25, 50, 100]


async def _execute_bet(
    bot,
    user_id: int,
    guild_id: int,
    race: models.Race,
    racers: list[models.Racer],
    bet_type: str,
    picks: list[int],
    amount: int,
) -> str:
    """Place a bet and return a result message string.

    This is shared by both the interactive UI and the slash commands.
    Raises ValueError with a user-facing message on validation failure.
    """
    FREE_BET_AMOUNT = 10

    if amount < 0:
        raise ValueError("Bet amount must be positive.")

    if bet_type == "superfecta" and len(racers) < 6:
        raise ValueError("Superfecta requires exactly 6 racers in the field.")

    racer_ids_in_race = [r.id for r in racers]
    for pick in picks:
        if pick not in racer_ids_in_race:
            raise ValueError("That racer isn't in the next race.")
    if len(picks) != len(set(picks)):
        raise ValueError("Each racer can only appear once in your picks.")

    multiplier = logic.calculate_bet_odds(racers, None, 0.1, bet_type, picks)
    racer_ids_json = json.dumps(picks)
    primary_racer_id = picks[0]
    pick_names = [
        next((r.name for r in racers if r.id == p), f"Racer {p}")
        for p in picks
    ]
    label = BET_TYPE_LABELS.get(bet_type, bet_type)

    async with bot.scheduler.sessionmaker() as session:
        wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
        if wallet is None:
            gs = await repo.get_guild_settings(session, guild_id)
            default_bal = resolve_guild_setting(gs, bot.settings, "default_wallet")
            wallet = await wallet_repo.create_wallet(
                session, user_id=user_id, guild_id=guild_id, balance=default_bal,
            )

        is_free = False
        if amount == 0:
            if wallet.balance > 0:
                raise ValueError("You can only place a free bet when your balance is 0.")
            free_check = await session.execute(
                select(models.Bet).where(
                    models.Bet.race_id == race.id,
                    models.Bet.user_id == user_id,
                    models.Bet.is_free.is_(True),
                )
            )
            if free_check.scalars().first() is not None:
                raise ValueError("You already have a free bet on this race.")
            is_free = True
            amount = FREE_BET_AMOUNT

        payout = int(amount * multiplier)

        bet_result = await session.execute(
            select(models.Bet).where(
                models.Bet.race_id == race.id,
                models.Bet.user_id == user_id,
                models.Bet.bet_type == bet_type,
            )
        )
        existing_bet = bet_result.scalars().first()
        old_amount = 0
        if existing_bet is not None:
            old_amount = existing_bet.amount
            if not existing_bet.is_free:
                wallet.balance += existing_bet.amount
        if not is_free and wallet.balance < amount:
            await session.commit()
            raise ValueError("Insufficient balance.")
        if not is_free:
            wallet.balance -= amount
        await session.commit()
        if existing_bet is None:
            await repo.create_bet(
                session,
                race_id=race.id,
                user_id=user_id,
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

    if bet_type in ("win", "place"):
        pick_desc = f"**{pick_names[0]}**"
    else:
        pick_desc = " \u2192 ".join(f"**{n}**" for n in pick_names)

    free_tag = " (Free House Bet)" if is_free else ""
    if is_free:
        return (
            f"\U0001f3b0 **{label}**{free_tag}\n"
            f"The house backs you on {pick_desc} for {amount} coins "
            f"({multiplier:.1f}x \u2014 win pays {payout})"
        )
    elif old_amount > 0:
        return (
            f"\U0001f3b0 **{label}**\n"
            f"Bet changed ({old_amount} coins refunded) to {pick_desc} "
            f"for {amount} coins ({multiplier:.1f}x \u2014 win pays {payout})"
        )
    else:
        return (
            f"\U0001f3b0 **{label}**\n"
            f"Bet placed on {pick_desc} for {amount} coins "
            f"({multiplier:.1f}x \u2014 win pays {payout})"
        )


# --- Quick-Bet (on race announcement) ---


class QuickBetModal(discord.ui.Modal, title="Place Bet"):
    """Modal that asks for the bet amount when quick-betting from the announcement."""

    bet_amount = discord.ui.TextInput(
        label="How much do you want to bet?",
        placeholder="Enter amount",
        required=True,
        max_length=10,
    )

    def __init__(self, bot, race, racers, racer_id: int):
        super().__init__()
        self.bot = bot
        self.race = race
        self.racers = racers
        self.racer_id = racer_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number.", ephemeral=True
            )
            return
        guild_id = interaction.guild_id or 0
        try:
            msg = await _execute_bet(
                self.bot, interaction.user.id, guild_id,
                self.race, self.racers, "win", [self.racer_id], amount,
            )
            await interaction.response.send_message(msg)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)


class QuickBetButton(discord.ui.Button):
    """A single racer button on the race announcement for quick win bets."""

    def __init__(self, bot, race, racers, racer: models.Racer, odds: float):
        label = f"{racer.name} ({odds:.1f}x)"
        super().__init__(label=label[:80], style=discord.ButtonStyle.blurple)
        self.bot = bot
        self.race = race
        self.racers = racers
        self.racer_id = racer.id

    async def callback(self, interaction: discord.Interaction) -> None:
        modal = QuickBetModal(self.bot, self.race, self.racers, self.racer_id)
        await interaction.response.send_modal(modal)


class QuickBetView(discord.ui.View):
    """Attached to the race announcement — one button per racer for quick win bets."""

    def __init__(self, bot, race, racers, odds: dict[int, float], timeout: float = 120):
        super().__init__(timeout=timeout)
        for racer in sorted(racers, key=lambda r: r.name.lower()):
            mult = odds.get(racer.id, 2.0)
            self.add_item(QuickBetButton(bot, race, racers, racer, mult))

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


# --- Full Interactive Bet Slip (/race bet) ---


class CustomAmountModal(discord.ui.Modal, title="Custom Bet Amount"):
    """Modal for entering a custom bet amount in the full bet slip."""

    bet_amount = discord.ui.TextInput(
        label="Enter your bet amount",
        placeholder="Enter amount",
        required=True,
        max_length=10,
    )

    def __init__(self, view: "BettingView"):
        super().__init__()
        self.betting_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number.", ephemeral=True
            )
            return
        if amount < 0:
            await interaction.response.send_message(
                "Amount must be positive.", ephemeral=True
            )
            return
        self.betting_view.amount = amount
        self.betting_view.state = "confirm"
        await interaction.response.edit_message(
            embed=self.betting_view.build_embed(),
            view=self.betting_view.build_view(),
        )


class BettingView(discord.ui.View):
    """Interactive multi-step betting slip.

    States: type_select → picking → amount → confirm → done
    """

    def __init__(
        self,
        bot,
        user_id: int,
        race: models.Race,
        racers: list[models.Racer],
        odds: dict[int, float],
        *,
        timeout: float = 180,
    ):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.user_id = user_id
        self.race = race
        self.racers = racers
        self.odds = odds
        self.guild_id = race.guild_id

        self.state = "type_select"
        self.bet_type: str | None = None
        self.picks: list[int] = []
        self.amount: int | None = None
        self.message: discord.Message | None = None

        self._rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This bet slip isn't yours!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            embed = discord.Embed(
                title="\U0001f3b0 Bet Slip — Expired",
                description="This bet slip has timed out.",
                color=0x95A5A6,
            )
            try:
                await self.message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    # --- Embed builder ---

    def build_embed(self) -> discord.Embed:
        if self.state == "type_select":
            desc = "Choose your bet type:"
            embed = discord.Embed(
                title="\U0001f3b0 Bet Slip",
                description=desc,
                color=0xE67E22,
            )
            # Show the field
            for r in sorted(self.racers, key=lambda r: r.name.lower()):
                mult = self.odds.get(r.id, 2.0)
                embed.add_field(
                    name=r.name, value=f"{mult:.1f}x odds", inline=True,
                )
            return embed

        label = BET_TYPE_LABELS.get(self.bet_type, self.bet_type)
        needed = BET_PICK_COUNTS.get(self.bet_type, 1)

        if self.state == "picking":
            picked_names = [
                next((r.name for r in self.racers if r.id == p), "?")
                for p in self.picks
            ]
            lines = []
            ordinals = ["1st", "2nd", "3rd", "4th", "5th", "6th"]
            for i, name in enumerate(picked_names):
                lines.append(f"{ordinals[i]}: **{name}** \u2713")
            pick_num = len(self.picks) + 1
            if needed == 1:
                prompt = "Pick the winner:" if self.bet_type == "win" else "Pick your racer:"
            else:
                prompt = f"Pick {ordinals[pick_num - 1]} place:"
            lines.append(f"\n{prompt}")

            embed = discord.Embed(
                title=f"\U0001f3b0 Bet Slip — {label}",
                description="\n".join(lines),
                color=0xE67E22,
            )
            return embed

        if self.state == "amount":
            picked_names = [
                next((r.name for r in self.racers if r.id == p), "?")
                for p in self.picks
            ]
            if self.bet_type in ("win", "place"):
                pick_desc = f"**{picked_names[0]}**"
            else:
                pick_desc = " \u2192 ".join(f"**{n}**" for n in picked_names)

            mult = logic.calculate_bet_odds(self.racers, None, 0.1, self.bet_type, self.picks)
            embed = discord.Embed(
                title=f"\U0001f3b0 Bet Slip — {label}",
                description=(
                    f"Picks: {pick_desc}\n"
                    f"Odds: **{mult:.1f}x**\n\n"
                    "Choose your bet amount:"
                ),
                color=0xE67E22,
            )
            return embed

        if self.state == "confirm":
            picked_names = [
                next((r.name for r in self.racers if r.id == p), "?")
                for p in self.picks
            ]
            if self.bet_type in ("win", "place"):
                pick_desc = f"**{picked_names[0]}**"
            else:
                pick_desc = " \u2192 ".join(f"**{n}**" for n in picked_names)

            mult = logic.calculate_bet_odds(self.racers, None, 0.1, self.bet_type, self.picks)
            payout = int(self.amount * mult)
            embed = discord.Embed(
                title=f"\U0001f3b0 Bet Slip — {label}",
                description=(
                    f"Picks: {pick_desc}\n"
                    f"Amount: **{self.amount} coins**\n"
                    f"Odds: **{mult:.1f}x** \u2014 win pays **{payout}**\n\n"
                    "Confirm your bet?"
                ),
                color=0x2ECC71,
            )
            return embed

        # done state
        return discord.Embed(
            title="\U0001f3b0 Bet Confirmed!",
            description="Your bet has been placed.",
            color=0x2ECC71,
        )

    # --- View builder ---

    def build_view(self) -> "BettingView":
        """Rebuild all children for the current state."""
        self.clear_items()
        self._rebuild()
        return self

    def _rebuild(self) -> None:
        if self.state == "type_select":
            styles = {
                "win": discord.ButtonStyle.blurple,
                "place": discord.ButtonStyle.blurple,
                "exacta": discord.ButtonStyle.grey,
                "trifecta": discord.ButtonStyle.grey,
                "superfecta": discord.ButtonStyle.grey,
            }
            for bt, lbl in BET_TYPE_LABELS.items():
                if bt == "superfecta" and len(self.racers) < 6:
                    continue
                btn = discord.ui.Button(
                    label=lbl, style=styles.get(bt, discord.ButtonStyle.grey),
                    custom_id=f"bettype_{bt}", row=0,
                )
                btn.callback = self._make_type_callback(bt)
                self.add_item(btn)
            cancel = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.danger,
                custom_id="bet_cancel", row=4,
            )
            cancel.callback = self._cancel_callback
            self.add_item(cancel)

        elif self.state == "picking":
            sorted_racers = sorted(self.racers, key=lambda r: r.name.lower())
            for i, r in enumerate(sorted_racers):
                disabled = r.id in self.picks
                style = discord.ButtonStyle.success if disabled else discord.ButtonStyle.blurple
                btn = discord.ui.Button(
                    label=r.name[:80],
                    style=style,
                    custom_id=f"pick_{r.id}",
                    disabled=disabled,
                    row=i // 3,  # 3 per row, fits 6 in rows 0-1
                )
                btn.callback = self._make_pick_callback(r.id)
                self.add_item(btn)
            back = discord.ui.Button(
                label="Back", style=discord.ButtonStyle.secondary,
                custom_id="bet_back", row=4,
            )
            back.callback = self._back_to_type
            self.add_item(back)
            cancel = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.danger,
                custom_id="bet_cancel", row=4,
            )
            cancel.callback = self._cancel_callback
            self.add_item(cancel)

        elif self.state == "amount":
            for preset in AMOUNT_PRESETS:
                btn = discord.ui.Button(
                    label=str(preset), style=discord.ButtonStyle.blurple,
                    custom_id=f"amt_{preset}", row=2,
                )
                btn.callback = self._make_amount_callback(preset)
                self.add_item(btn)
            allin = discord.ui.Button(
                label="All-In", style=discord.ButtonStyle.danger,
                custom_id="amt_allin", row=2,
            )
            allin.callback = self._allin_callback
            self.add_item(allin)
            custom = discord.ui.Button(
                label="Custom", style=discord.ButtonStyle.grey,
                custom_id="amt_custom", row=3,
            )
            custom.callback = self._custom_callback
            self.add_item(custom)
            back = discord.ui.Button(
                label="Back", style=discord.ButtonStyle.secondary,
                custom_id="bet_back", row=4,
            )
            back.callback = self._back_to_picking
            self.add_item(back)
            cancel = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.danger,
                custom_id="bet_cancel", row=4,
            )
            cancel.callback = self._cancel_callback
            self.add_item(cancel)

        elif self.state == "confirm":
            confirm = discord.ui.Button(
                label="Confirm \u2713", style=discord.ButtonStyle.success,
                custom_id="bet_confirm", row=4,
            )
            confirm.callback = self._confirm_callback
            self.add_item(confirm)
            back = discord.ui.Button(
                label="Back", style=discord.ButtonStyle.secondary,
                custom_id="bet_back", row=4,
            )
            back.callback = self._back_to_amount
            self.add_item(back)
            cancel = discord.ui.Button(
                label="Cancel", style=discord.ButtonStyle.danger,
                custom_id="bet_cancel", row=4,
            )
            cancel.callback = self._cancel_callback
            self.add_item(cancel)

    # --- Callbacks ---

    def _make_type_callback(self, bt: str):
        async def callback(interaction: discord.Interaction):
            self.bet_type = bt
            self.picks = []
            self.amount = None
            self.state = "picking"
            await interaction.response.edit_message(
                embed=self.build_embed(), view=self.build_view(),
            )
        return callback

    def _make_pick_callback(self, racer_id: int):
        async def callback(interaction: discord.Interaction):
            self.picks.append(racer_id)
            needed = BET_PICK_COUNTS.get(self.bet_type, 1)
            if len(self.picks) >= needed:
                self.state = "amount"
            await interaction.response.edit_message(
                embed=self.build_embed(), view=self.build_view(),
            )
        return callback

    def _make_amount_callback(self, amount: int):
        async def callback(interaction: discord.Interaction):
            self.amount = amount
            self.state = "confirm"
            await interaction.response.edit_message(
                embed=self.build_embed(), view=self.build_view(),
            )
        return callback

    async def _allin_callback(self, interaction: discord.Interaction):
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(
                session, self.user_id, self.guild_id
            )
            balance = wallet.balance if wallet else 0
        if balance <= 0:
            self.amount = 0  # triggers free bet path
        else:
            self.amount = balance
        self.state = "confirm"
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self.build_view(),
        )

    async def _custom_callback(self, interaction: discord.Interaction):
        modal = CustomAmountModal(self)
        await interaction.response.send_modal(modal)

    async def _confirm_callback(self, interaction: discord.Interaction):
        try:
            msg = await _execute_bet(
                self.bot, self.user_id, self.guild_id,
                self.race, self.racers, self.bet_type, self.picks, self.amount,
            )
            # Edit the ephemeral bet slip to show done state
            done_embed = discord.Embed(
                title="\U0001f3b0 Bet Confirmed!",
                description="Your bet has been placed.",
                color=0x2ECC71,
            )
            self.state = "done"
            await interaction.response.edit_message(embed=done_embed, view=None)
            # Post the bet publicly so everyone can see it
            await interaction.followup.send(msg)
            self.stop()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)

    async def _cancel_callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="\U0001f3b0 Bet Cancelled",
            description="No bet was placed.",
            color=0x95A5A6,
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    async def _back_to_type(self, interaction: discord.Interaction):
        self.state = "type_select"
        self.bet_type = None
        self.picks = []
        self.amount = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self.build_view(),
        )

    async def _back_to_picking(self, interaction: discord.Interaction):
        self.state = "picking"
        self.picks = []
        self.amount = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self.build_view(),
        )

    async def _back_to_amount(self, interaction: discord.Interaction):
        self.state = "amount"
        self.amount = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self.build_view(),
        )


class Derby(commands.Cog, name="derby"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx, "derby_channel")

    def _racer_emoji(self, gs=None) -> str:
        """Return the configured racer emoji for the guild."""
        return resolve_guild_setting(gs, self.bot.settings, "racer_emoji")

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

            # Pre-load NPC names for any NPC-owned racers
            npc_names: dict[int, str] = {}
            npc_ids = {r.npc_id for r in racers if r.npc_id}
            for npc_id in npc_ids:
                npc = await repo.get_npc(session, npc_id)
                if npc:
                    npc_names[npc_id] = f"{npc.emoji} {npc.name}".strip() if npc.emoji else npc.name

        # Load pre-picked track info
        race_map = None
        if race.map_name:
            race_map = logic.get_map_by_name(race.map_name)

        odds = logic.calculate_odds(racers, [], 0.1, race_map=race_map)
        embed = discord.Embed(title="Upcoming Race")
        embed.add_field(name="Race ID", value=str(race.id), inline=False)

        if race_map:
            layout = " \u2192 ".join(
                f"[{s.type.capitalize()}]" for s in race_map.segments
            )
            embed.add_field(
                name="Track",
                value=f"**{race_map.name}** ({race_map.theme})\n{layout}",
                inline=False,
            )

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

        guild = context.guild
        # Pre-resolve player display names for owned racers
        owner_names: dict[int, str] = {}
        player_ids = {r.owner_id for r in racers if r.owner_id and r.owner_id != 0 and not r.npc_id}
        if guild:
            for pid in player_ids:
                try:
                    member = guild.get_member(pid) or await guild.fetch_member(pid)
                    owner_names[pid] = member.display_name
                except discord.NotFound:
                    owner_names[pid] = f"Player #{pid}"

        for r in sorted(racers, key=lambda r: r.name.lower()):
            mult = odds.get(r.id, 0)
            rlabel = logic.rank_label(getattr(r, "rank", None))
            # Determine owner label
            if r.npc_id and r.npc_id in npc_names:
                owner_tag = npc_names[r.npc_id]
            elif r.owner_id and r.owner_id != 0:
                owner_tag = owner_names.get(r.owner_id, f"Player #{r.owner_id}")
            else:
                owner_tag = "Unowned"
            embed.add_field(
                name=f"{r.name} [{rlabel}] (#{r.id})",
                value=f"{mult:.1f}x \u2014 bet 100, win {int(100 * mult)}\nOwner: {owner_tag}",
                inline=False,
            )
        embed.set_footer(text="Use /race bet to place your bet!")
        await context.send(embed=embed)

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

    async def _place_bet(
        self,
        context: Context,
        bet_type: str,
        picks: list[int],
        amount: int,
    ) -> None:
        """Shared logic for all slash-command bet commands."""
        guild_id = context.guild.id if context.guild else 0
        race, racers = await self._find_next_race(guild_id)
        if race is None or not racers:
            await context.send("No race available.", ephemeral=True)
            return
        try:
            msg = await _execute_bet(
                self.bot, context.author.id, guild_id,
                race, racers, bet_type, picks, amount,
            )
            await context.send(msg)
        except ValueError as e:
            await context.send(str(e), ephemeral=True)

    @race.command(name="bet", description="Open the interactive betting slip")
    async def race_bet(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        race, racers = await self._find_next_race(guild_id)
        if race is None or not racers:
            await context.send("No race available.", ephemeral=True)
            return
        odds = logic.calculate_odds(racers, [], 0.1)
        view = BettingView(
            self.bot, context.author.id, race, racers, odds,
        )
        msg = await context.send(embed=view.build_embed(), view=view)
        view.message = msg

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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
            f"{'to' if amount > 0 else 'from'} {user.mention}."
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
        scheduler = self.bot.scheduler
        if not scheduler.task or not scheduler.task.is_running():
            await context.send("Race schedule is not running.", ephemeral=True)
            return
        scheduler.task.cancel()
        await context.send("Race schedule stopped.")

    @derby_group.command(name="cancel_race", description="Cancel the next race")
    @checks.has_role("Race Admin")
    async def cancel_race(self, context: Context) -> None:
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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

    @racer_group.command(
        name="regen-desc",
        description="Regenerate a racer's description with an optional hint",
    )
    @app_commands.describe(
        racer="Racer to regenerate description for",
        hint="Optional direction for the description (e.g. 'make him look like a ghost')",
    )
    @app_commands.autocomplete(racer=guild_racer_autocomplete)
    async def racer_regen_desc(
        self, context: Context, racer: int, hint: str | None = None,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return
            gs = await repo.get_guild_settings(session, guild_id)
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            if not flavor:
                await context.send(
                    "Set a racer flavor first with "
                    "`/derby settings set racer_flavor <text>`.",
                    ephemeral=True,
                )
                return
            desc = await descriptions.generate_description(
                name=racer_obj.name,
                speed=racer_obj.speed,
                cornering=racer_obj.cornering,
                stamina=racer_obj.stamina,
                temperament=racer_obj.temperament,
                gender=racer_obj.gender,
                flavor=flavor,
                hint=hint,
            )
            if desc is None:
                await context.send(
                    "Description generation failed — check API key.", ephemeral=True
                )
                return
            await repo.update_racer(session, racer, description=desc)
        embed = discord.Embed(
            title=f"Description Updated — {racer_obj.name}",
            description=desc,
            color=0x3498DB,
        )
        if hint:
            embed.set_footer(text=f"Hint: {hint}")
        await context.send(embed=embed)

    # ------------------------------------------------------------------
    # NPC commands
    # ------------------------------------------------------------------

    @derby_group.group(name="npc", description="NPC rival trainer commands")
    async def npc_group(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send("Specify a subcommand", ephemeral=True)

    @npc_group.command(name="list", description="List all NPC rival trainers")
    async def npc_list(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            npcs = await repo.get_guild_npcs(session, guild_id)
            if not npcs:
                await context.send(
                    "No NPC trainers yet. Set a `racer_flavor` to generate them!",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="\U0001f3ad Rival Trainers",
                color=0xE67E22,
            )
            for npc in npcs:
                racers = await repo.get_npc_racers(session, npc.id)
                racer_names = ", ".join(f"**{r.name}** ({r.rank})" for r in racers)
                if not racer_names:
                    racer_names = "*No active racers*"
                emoji = f"{npc.emoji} " if npc.emoji else ""
                embed.add_field(
                    name=f"{emoji}{npc.name} — {npc.personality}",
                    value=(
                        f"*\"{npc.catchphrase}\"*\n"
                        f"Ranks: {npc.rank_min}-{npc.rank_max} | "
                        f"Racers: {racer_names}"
                    ),
                    inline=False,
                )
            await context.send(embed=embed)

    @npc_group.command(name="info", description="Show detailed NPC info")
    @app_commands.describe(name="NPC trainer name")
    async def npc_info(self, context: Context, name: str) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            npcs = await repo.get_guild_npcs(session, guild_id)
            npc = next(
                (n for n in npcs if n.name.lower() == name.lower()),
                None,
            )
            if npc is None:
                await context.send("NPC not found.", ephemeral=True)
                return

            racers = await repo.get_npc_racers(session, npc.id)
            emoji = f"{npc.emoji} " if npc.emoji else ""
            embed = discord.Embed(
                title=f"{emoji}{npc.name}",
                description=npc.personality_desc,
                color=0xE67E22,
            )
            embed.add_field(
                name="Personality", value=npc.personality, inline=True
            )
            embed.add_field(
                name="Ranks", value=f"{npc.rank_min}-{npc.rank_max}", inline=True
            )
            embed.add_field(
                name="Catchphrase",
                value=f"*\"{npc.catchphrase}\"*" if npc.catchphrase else "*None*",
                inline=False,
            )
            for r in racers:
                total = r.speed + r.cornering + r.stamina
                embed.add_field(
                    name=f"{self._racer_emoji(gs)} {r.name} ({r.rank})",
                    value=(
                        f"SPD {r.speed} / COR {r.cornering} / STA {r.stamina} "
                        f"(total {total})\n"
                        f"Temperament: {r.temperament} | Mood: {r.mood}/5"
                    ),
                    inline=False,
                )
            if not racers:
                embed.add_field(
                    name="Racers", value="*No active racers*", inline=False
                )
            await context.send(embed=embed)

    @npc_info.autocomplete("name")
    async def npc_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_id = interaction.guild_id or 0
        async with self.bot.scheduler.sessionmaker() as session:
            npcs = await repo.get_guild_npcs(session, guild_id)
        choices = []
        for npc in npcs:
            if current.lower() in npc.name.lower():
                choices.append(
                    app_commands.Choice(name=npc.name, value=npc.name)
                )
        return choices[:25]

    @npc_group.command(
        name="regenerate",
        description="Regenerate all NPC trainers for this server (admin)",
    )
    async def npc_regenerate(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            if not flavor:
                await context.send(
                    "Set a `racer_flavor` first with "
                    "`/derby settings set racer_flavor <text>`.",
                    ephemeral=True,
                )
                return

            # Delete existing NPCs and their racers
            npcs = await repo.get_guild_npcs(session, guild_id)
            for npc in npcs:
                # Delete NPC racers
                racers = await repo.get_npc_racers(session, npc.id)
                for r in racers:
                    await repo.delete_racer(session, r.id)
                await repo.delete_npc(session, npc.id)

        await self.bot.scheduler._ensure_guild_npcs(guild_id)

        async with self.bot.scheduler.sessionmaker() as session:
            new_npcs = await repo.get_guild_npcs(session, guild_id)
        if new_npcs:
            names = ", ".join(f"**{n.name}**" for n in new_npcs)
            await context.send(
                f"Regenerated {len(new_npcs)} NPC trainers: {names}",
                ephemeral=True,
            )
        else:
            await context.send(
                "NPC generation failed — check API key and try again.",
                ephemeral=True,
            )

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
        await context.defer(ephemeral=True)
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
            if race.id in self.bot.scheduler.active_races or race.id in self.bot.scheduler.betting_races:
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
            bet_results = await logic.resolve_payouts(
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
            # Build per-racer modifier dicts from owner races
            _owner_ids = {r.id: r.owner_id for r in participants if r.owner_id}
            _mood_floors: dict[int, int] = {}
            _injury_mults: dict[int, float] = {}
            if _owner_ids:
                _seen_owners: dict[int, str] = {}
                for _rid, _oid in _owner_ids.items():
                    if _oid not in _seen_owners:
                        _prof = await rpg_repo.get_or_create_profile(session, _oid, guild_id)
                        _seen_owners[_oid] = _prof.race
                    _race = _seen_owners[_oid]
                    _mf = get_racial_modifier(_race, "racing.mood_floor", 1)
                    if _mf > 1:
                        _mood_floors[_rid] = _mf
                    _im = get_racial_modifier(_race, "racing.injury_chance_multiplier", 1.0)
                    if _im != 1.0:
                        _injury_mults[_rid] = _im
            await logic.apply_mood_drift(
                session, result.placements, participants,
                mood_floors=_mood_floors or None,
            )
            new_injuries = logic.check_injury_risk(
                result, injury_multipliers=_injury_mults or None,
            )
            await logic.apply_injuries(session, new_injuries, participants)
            stat_gains = await logic.apply_placement_stat_gains(
                session, result.placements, participants, race_map, prize_list,
            )
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

        # Reset training counters for all guild racers
        async with self.bot.scheduler.sessionmaker() as session:
            from sqlalchemy import text
            await session.execute(
                text(
                    "UPDATE racers SET trains_since_race = 0 "
                    "WHERE guild_id = :gid AND trains_since_race > 0"
                ),
                {"gid": guild_id},
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
            title=f"{self._racer_emoji(gs)} Race {race.id} — Racers Getting Ready!",
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

            # --- Announce bet results and placement prizes ---
            await self.bot.scheduler._announce_bet_results(
                context.guild.id, bet_results, names
            )
            await self.bot.scheduler._dm_payouts(bet_results, race.id, names)
            if placement_awards:
                await self.bot.scheduler._announce_placement_prizes(
                    context.guild.id, placement_awards, names,
                    stat_gains=stat_gains,
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
        "race_stat_window",
        "daily_min",
        "daily_max",
        "racer_emoji",
        "max_trains_per_race",
        "derby_channel",
        "brewing_channel",
        "fishing_channel",
        "dungeon_channel",
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
        await context.defer(ephemeral=True)
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
                if key in ("channel_name", "placement_prizes", "stable_upgrade_costs", "racer_flavor", "racer_emoji"):
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

        # Regenerate flavor names when racer_flavor changes
        if key == "racer_flavor":
            from derby import flavor_names
            flavor_names.delete_flavor_names(guild_id)
            if parsed != "reset" and parsed:
                names = await flavor_names.generate_flavor_names(str(parsed))
                if names:
                    flavor_names.save_flavor_names(guild_id, names)
                    await context.send(
                        f"`{key}` set to **{parsed}** for this server. "
                        f"Generated **{len(names)}** themed names.",
                        ephemeral=True,
                    )
                    return
                await context.send(
                    f"`{key}` set to **{parsed}** for this server. "
                    f"Flavor name generation failed — using base names only.",
                    ephemeral=True,
                )
                return

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

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx, "derby_channel")

    def _resolve(self, key: str, gs) -> int | float | str:
        return resolve_guild_setting(gs, self.bot.settings, key)

    def _racer_emoji(self, gs=None) -> str:
        """Return the configured racer emoji for the guild."""
        return self._resolve("racer_emoji", gs)

    @commands.hybrid_group(name="stable", description="Your racing stable")
    async def stable(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await self._show_stable(context)

    async def _show_stable(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racers = await repo.get_stable_racers(
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
            retired_tag = " \U0001f3d6\ufe0f Retired" if r.retired else ""
            embed.add_field(
                name=f"{gender} {r.name} (#{r.id}) [{rank}]{trophy}{retired_tag}",
                value=(
                    f"Spd {_stat_band(eff['speed'])} / "
                    f"Cor {_stat_band(eff['cornering'])} / "
                    f"Sta {_stat_band(eff['stamina'])}\n"
                    f"{r.temperament} | {_mood_label(r.mood)} | {phase}{injury}{training}"
                ),
                inline=False,
            )
        await context.send(embed=embed)

    _RANK_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}

    @stable.command(name="report", description="Get a status report on your stable")
    async def stable_report(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            all_racers = await repo.get_stable_racers(session, user_id, guild_id)
            if not all_racers:
                await context.send(
                    "You don't own any racers yet! Use `/stable browse` to get started.",
                    ephemeral=True,
                )
                return

            gs = await repo.get_guild_settings(session, guild_id)
            min_train = self._resolve("min_training_to_race", gs)

            active = [r for r in all_racers if not r.retired]
            retired = [r for r in all_racers if r.retired]

            # --- Section 1: Racer Status ---
            status_lines: list[str] = []
            for r in active:
                rank = logic.rank_label(r.rank)
                remaining = r.career_length - r.races_completed
                phase = logic.career_phase(r)

                notes: list[str] = []

                if r.injury_races_remaining > 0:
                    icon = "\U0001f534"  # red
                    injury_name = r.injuries or "injured"
                    notes.append(f"Injured: {injury_name} ({r.injury_races_remaining} races)")
                elif remaining <= 3:
                    icon = "\u26a0\ufe0f"  # warning
                    notes.append(f"Retiring Soon ({remaining} races left)")
                elif r.races_completed > r.peak_end:
                    icon = "\U0001f4c9"  # declining
                    decline = r.races_completed - r.peak_end
                    notes.append(f"Declining (-{decline})")
                elif r.sire_id is not None and (r.training_count or 0) < min_train:
                    icon = "\U0001f7e1"  # yellow
                    notes.append(f"Training: {r.training_count or 0}/{min_train}")
                else:
                    icon = "\U0001f7e2"  # green
                    notes.append(f"Ready ({phase}, {remaining} races left)")

                if r.breed_cooldown and r.breed_cooldown > 0:
                    notes.append(f"Breed cooldown: {r.breed_cooldown} races")

                mood_emoji = MOOD_EMOJIS.get(r.mood, "")
                status_lines.append(
                    f"{icon} **{r.name}** [{rank}] {mood_emoji} — {', '.join(notes)}"
                )

            if retired:
                status_lines.append(
                    f"\U0001f3d6\ufe0f {len(retired)} retired racer{'s' if len(retired) != 1 else ''}"
                )

            # --- Section 2: Tournament Eligibility ---
            tournament_lines: list[str] = []
            # Get unique ranks among active racers
            racer_ranks: dict[str, list] = {}
            for r in active:
                if r.rank:
                    racer_ranks.setdefault(r.rank, []).append(r)

            for rank in sorted(racer_ranks.keys(), key=lambda x: self._RANK_ORDER.get(x, 0)):
                tournament = await repo.get_pending_tournament(session, guild_id, rank)
                if tournament is None:
                    continue
                # Check registration
                entry = await repo.get_player_tournament_entry(
                    session, tournament.id, user_id
                )
                best_racer = racer_ranks[rank][0]
                if entry:
                    tournament_lines.append(
                        f"  {rank}-Rank: **{best_racer.name}** — \u2705 Registered"
                    )
                else:
                    injured_note = " (injured!)" if best_racer.injury_races_remaining > 0 else ""
                    tournament_lines.append(
                        f"  {rank}-Rank: **{best_racer.name}** eligible — \u274c Not registered{injured_note}"
                    )

            # --- Section 3: Summary ---
            best_rank = max(
                (r.rank for r in active if r.rank),
                key=lambda x: self._RANK_ORDER.get(x, 0),
                default="Unranked",
            )
            closest_retirement = None
            min_remaining = float("inf")
            for r in active:
                rem = r.career_length - r.races_completed
                if rem < min_remaining:
                    min_remaining = rem
                    closest_retirement = r

            summary_parts = [
                f"Active: {len(active)}",
                f"Retired: {len(retired)}",
                f"Best: {logic.rank_label(best_rank)}",
            ]
            if closest_retirement:
                rem = closest_retirement.career_length - closest_retirement.races_completed
                summary_parts.append(
                    f"Next retirement: {closest_retirement.name} ({rem} races)"
                )

        embed = discord.Embed(
            title=f"\U0001f4cb {context.author.display_name}'s Stable Report",
            color=0x3498DB,
        )
        embed.add_field(
            name=f"{self._racer_emoji(gs)} Racer Status",
            value="\n".join(status_lines) if status_lines else "No racers",
            inline=False,
        )
        if tournament_lines:
            embed.add_field(
                name="\U0001f3c6 Tournaments",
                value="\n".join(tournament_lines),
                inline=False,
            )
        embed.set_footer(text="\U0001f4ca " + " | ".join(summary_parts))
        await context.send(embed=embed)

    @stable.command(name="view", description="View a racer's full profile")
    @app_commands.describe(racer="Racer to view")
    @app_commands.autocomplete(racer=viewable_racer_autocomplete)
    async def stable_view(self, context: Context, racer: int) -> None:
        await context.defer(ephemeral=True)
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
            if racer_obj.npc_id:
                npc = await repo.get_npc(session, racer_obj.npc_id)
                if npc:
                    prefix = f"{npc.emoji} " if npc.emoji else ""
                    owner_name = f"{prefix}{npc.name}"
                else:
                    owner_name = "NPC Trainer"
            elif racer_obj.owner_id and racer_obj.owner_id != 0:
                member = None
                if context.guild:
                    try:
                        member = context.guild.get_member(racer_obj.owner_id) or await context.guild.fetch_member(racer_obj.owner_id)
                    except Exception:
                        pass
                owner_name = member.display_name if member else f"Player #{racer_obj.owner_id}"
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

        training_val = f"{racer_obj.training_count or 0} sessions"
        if not racer_obj.retired:
            train_base = self._resolve("training_base", gs)
            train_mult = self._resolve("training_multiplier", gs)
            cost_lines = []
            for stat_name in ("speed", "cornering", "stamina"):
                cur = getattr(racer_obj, stat_name)
                if cur < 31:
                    cost = logic.calculate_training_cost(cur, train_base, train_mult)
                    cost_lines.append(
                        f"{stat_name.capitalize()} {cur}\u2192{cur + 1}: {cost} coins"
                    )
            if cost_lines:
                training_val += "\n" + "\n".join(cost_lines)
        embed.add_field(
            name="Training",
            value=training_val,
            inline=False,
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

    @stable.command(name="show", description="Show off one of your racers to the channel")
    @app_commands.describe(racer="Racer to show off")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_show(self, context: Context, racer: int) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            racer_obj = await repo.get_racer(session, racer)
            if racer_obj is None or racer_obj.guild_id != guild_id:
                await context.send("Racer not found.", ephemeral=True)
                return
            if racer_obj.owner_id != context.author.id:
                await context.send("You can only show off racers you own.", ephemeral=True)
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
            color = 0xF1C40F
        elif racer_obj.injuries:
            color = 0xE74C3C
        else:
            color = 0x2ECC71

        embed = discord.Embed(
            title=f"\u2b50 {racer_obj.name}  |  {gender_emoji} {racer_obj.gender}",
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

        # Tournament record
        t_wins = getattr(racer_obj, "tournament_wins", 0) or 0
        t_place = getattr(racer_obj, "tournament_placements", 0) or 0
        if t_wins or t_place:
            embed.add_field(
                name="Tournament Record",
                value=f"{t_wins}W / {t_place} top-3",
                inline=True,
            )

        # Description
        if racer_obj.description:
            embed.add_field(name="Description", value=racer_obj.description, inline=False)

        embed.set_footer(text=f"Owned by {context.author.display_name}")
        await context.send(embed=embed)

    @stable.command(name="browse", description="Browse racers available for purchase")
    @app_commands.describe(
        rank="Filter by rank (D/C/B/A/S)",
        gender="Filter by gender",
        temperament="Filter by temperament",
        upcoming="Show only racers in the next upcoming race",
    )
    @app_commands.choices(
        rank=[app_commands.Choice(name=f"{r}-Rank", value=r) for r in ["D", "C", "B", "A", "S"]],
        gender=[
            app_commands.Choice(name="Male", value="M"),
            app_commands.Choice(name="Female", value="F"),
        ],
        temperament=TEMPERAMENT_CHOICES,
    )
    async def stable_browse(
        self,
        context: Context,
        rank: str | None = None,
        gender: str | None = None,
        temperament: str | None = None,
        upcoming: bool = False,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        async with self.bot.scheduler.sessionmaker() as session:
            gs = await repo.get_guild_settings(session, guild_id)
            if upcoming:
                # Get participants directly from the race — avoids
                # pool_expires_at filtering that would miss valid entrants.
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
                next_race = next((r for r in races if r.id not in active), None)
                if next_race is not None:
                    participants = await repo.get_race_participants(
                        session, next_race.id
                    )
                    racers = [r for r in participants if r.owner_id == 0]
                else:
                    racers = []
            else:
                racers = await repo.get_unowned_guild_racers(session, guild_id)
        # Apply filters
        if rank is not None:
            racers = [r for r in racers if getattr(r, "rank", None) == rank]
        if gender is not None:
            racers = [r for r in racers if getattr(r, "gender", "M") == gender]
        if temperament is not None:
            racers = [r for r in racers if r.temperament == temperament]
        base = self._resolve("racer_buy_base", gs)
        mult = self._resolve("racer_buy_multiplier", gs)
        fem_mult = self._resolve("female_buy_multiplier", gs)
        if not racers:
            msg = "No racers in the upcoming race are available for purchase." if upcoming else "No racers match your filters."
            await context.send(msg, ephemeral=True)
            return
        # Build title with active filters
        filters = []
        if upcoming:
            filters.append("Upcoming Race")
        if rank:
            filters.append(f"{rank}-Rank")
        if gender:
            filters.append("Male" if gender == "M" else "Female")
        if temperament:
            filters.append(temperament)
        title = "Racers For Sale"
        if filters:
            title += f" \u2014 {' '.join(filters)}"
        embed = discord.Embed(title=title)
        for r in sorted(racers, key=lambda r: r.name.lower())[:25]:  # Discord embed limit
            price = logic.calculate_buy_price(r, base, mult, fem_mult)
            eff = logic.effective_stats(r)
            phase = logic.career_phase(r)
            g = _gender(getattr(r, "gender", "M"), "")
            rlabel = logic.rank_label(getattr(r, "rank", None))
            embed.add_field(
                name=f"{g} {r.name} [{rlabel}] — {price} coins",
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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
            pool_expiry = datetime.utcnow() + timedelta(
                hours=random.uniform(24, 48)
            )
            await repo.update_racer(
                session, racer, owner_id=0, pool_expires_at=pool_expiry,
            )
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
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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

            gs = await repo.get_guild_settings(session, guild_id)
            max_trains = self._resolve("max_trains_per_race", gs)
            if (racer_obj.trains_since_race or 0) >= max_trains:
                await context.send(
                    f"**{racer_obj.name}** has reached the training limit "
                    f"({max_trains} sessions). Wait for the next race!",
                    ephemeral=True,
                )
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
            training_base = self._resolve("training_base", gs)
            training_mult = self._resolve("training_multiplier", gs)
            cost = logic.calculate_training_cost(
                current_value, training_base, training_mult
            )

            # Dwarf training cost discount (-20%)
            profile = await rpg_repo.get_or_create_profile(
                session, context.author.id, guild_id
            )
            train_mult = get_racial_modifier(profile.race, "racing.training_cost_multiplier", 1.0)
            cost = max(int(cost * train_mult), 1)

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

            # Deduct cost, reduce mood, and count training attempt
            wallet.balance -= cost
            racer_obj.trains_since_race = (racer_obj.trains_since_race or 0) + 1
            old_mood = racer_obj.mood
            # Elf mood floor: racers owned by an Elf can't drop below mood 2
            mood_floor = get_racial_modifier(profile.race, "racing.mood_floor", 1)
            new_mood = max(mood_floor, old_mood - 1)
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
        await context.defer(ephemeral=True)
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

    @stable.command(name="feed", description="Feed a racer premium oats for 30 coins to boost mood (+2)")
    @app_commands.describe(racer="Your racer to feed")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_feed(self, context: Context, racer: int) -> None:
        await context.defer(ephemeral=True)
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
        await context.defer(ephemeral=True)
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


    @stable.command(name="breed", description="Breed two of your racers for 25 coins to produce a foal")
    @app_commands.describe(male="Male racer (sire)", female="Female racer (dam)")
    @app_commands.autocomplete(male=owned_racer_autocomplete, female=owned_racer_autocomplete)
    async def stable_breed(self, context: Context, male: int, female: int) -> None:
        await context.defer(ephemeral=True)
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

            # Tiered breeding fee based on parent ranks
            fee = logic.calculate_breeding_fee(sire, dam)

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

    # -- Name submission -------------------------------------------------------

    _NAME_MAX_LEN = 32
    _NAME_PATTERN = re.compile(r"^[A-Za-z0-9 '\-]+$")

    @stable.command(
        name="suggest-name",
        description="Submit a racer name to the guild's name pool",
    )
    @app_commands.describe(name="The name to add (max 32 characters, letters/numbers/spaces/hyphens)")
    async def stable_suggest_name(self, context: Context, name: str) -> None:
        await context.defer()
        guild_id = context.guild.id if context.guild else 0

        # --- Sanitize ---
        name = name.strip()
        if not name:
            await context.send("Name cannot be empty.", ephemeral=True)
            return
        if len(name) > self._NAME_MAX_LEN:
            await context.send(
                f"Name must be {self._NAME_MAX_LEN} characters or fewer "
                f"(yours is {len(name)}).",
                ephemeral=True,
            )
            return
        if not self._NAME_PATTERN.match(name):
            await context.send(
                "Name can only contain letters, numbers, spaces, "
                "hyphens, and apostrophes.",
                ephemeral=True,
            )
            return

        # --- Duplicate check against existing pool + DB ---
        existing_flavor = flavor_names.load_flavor_names(guild_id)
        base_names = logic._load_names()
        all_pool = [n.lower() for n in existing_flavor + base_names]

        async with self.bot.scheduler.sessionmaker() as session:
            result = await session.execute(
                select(models.Racer.name).where(
                    models.Racer.guild_id == guild_id
                )
            )
            db_names = {row[0].lower() for row in result.all()}

        if name.lower() in all_pool or name.lower() in db_names:
            await context.send(
                f"**{name}** is already in the name pool or in use.",
                ephemeral=True,
            )
            return

        # --- Add to guild flavor file ---
        existing_flavor.append(name)
        flavor_names.save_flavor_names(guild_id, existing_flavor)

        await context.send(
            f"**{name}** has been added to the name pool by "
            f"**{context.author.display_name}**! "
            f"It may appear on a future racer.",
        )


class Tournament(commands.Cog, name="tournament_cog"):
    """Tournament registration and listing commands."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx, "derby_channel")

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
