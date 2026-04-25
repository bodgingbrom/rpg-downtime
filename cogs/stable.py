from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from sqlalchemy import select

import checks
from cogs.derby import (
    MOOD_EMOJIS,
    STAT_CHOICES,
    TEMPERAMENT_CHOICES,
    _gender,
    _mood_label,
    _stat_band,
    owned_racer_autocomplete,
    unowned_racer_autocomplete,
    viewable_racer_autocomplete,
)
from config import resolve_guild_setting
from derby import abilities, appearance, descriptions, flavor_names, logic, models
from derby import repositories as repo
from economy import repositories as wallet_repo
from rpg import repositories as rpg_repo
from rpg.logic import get_racial_modifier


# ---------------------------------------------------------------------------
# Interactive Stable Management Views
# ---------------------------------------------------------------------------

STABLE_VIEW_TIMEOUT = 180  # seconds


async def _fetch_manage_data(sessionmaker, racer_id, user_id, guild_id, bot_settings):
    """Fetch all data needed for the stable manage embed in a single session."""
    async with sessionmaker() as session:
        racer = await repo.get_racer(session, racer_id)
        if racer is None:
            return None
        gs = await self.bot.scheduler.guild_settings.get(guild_id)
        wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
        if wallet is None:
            default_bal = resolve_guild_setting(gs, bot_settings, "default_wallet")
            wallet = await wallet_repo.create_wallet(
                session, user_id=user_id, guild_id=guild_id, balance=default_bal,
            )
        profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)
        eff = logic.effective_stats(racer)
        max_trains = resolve_guild_setting(gs, bot_settings, "max_trains_per_race")
        rest_cost = resolve_guild_setting(gs, bot_settings, "rest_cost")
        feed_cost = resolve_guild_setting(gs, bot_settings, "feed_cost")
        return {
            "racer": racer,
            "gs": gs,
            "wallet": wallet,
            "profile": profile,
            "eff": eff,
            "max_trains": max_trains,
            "rest_cost": rest_cost,
            "feed_cost": feed_cost,
        }


def _build_manage_embed(data, *, status_text=None):
    """Build the main stable manage embed from fetched data."""
    racer = data["racer"]
    eff = data["eff"]
    wallet = data["wallet"]
    max_trains = data["max_trains"]

    trains_used = racer.trains_since_race or 0
    trains_left = max(0, max_trains - trains_used)
    rested = getattr(racer, "rested_since_race", False)

    embed = discord.Embed(
        title=f"\U0001f40e {racer.name}",
        color=discord.Color.blue(),
    )
    rank = logic.rank_label(racer.rank)
    embed.add_field(name="Rank", value=rank, inline=True)
    embed.add_field(
        name="Mood",
        value=f"{MOOD_EMOJIS.get(racer.mood, '')} {_mood_label(racer.mood)}",
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer
    embed.add_field(name="Speed", value=f"{_stat_band(eff['speed'])} ({eff['speed']})", inline=True)
    embed.add_field(name="Cornering", value=f"{_stat_band(eff['cornering'])} ({eff['cornering']})", inline=True)
    embed.add_field(name="Stamina", value=f"{_stat_band(eff['stamina'])} ({eff['stamina']})", inline=True)
    embed.add_field(
        name="Training",
        value=f"{trains_left}/{max_trains} sessions left" + (" \u2022 Rested \u2705" if rested else ""),
        inline=True,
    )
    embed.set_footer(text=f"Balance: {wallet.balance} coins")

    if status_text:
        embed.description = status_text

    return embed


def _build_train_embed(data):
    """Build the training state embed showing stat costs and failure chance."""
    racer = data["racer"]
    gs = data["gs"]
    profile = data["profile"]
    bot_settings = data.get("bot_settings")

    base = resolve_guild_setting(gs, bot_settings, "training_base") if bot_settings else 10
    mult = resolve_guild_setting(gs, bot_settings, "training_multiplier") if bot_settings else 5

    train_cost_mult = get_racial_modifier(profile.race, "racing.training_cost_multiplier", 1.0)
    fail_chance = logic.training_failure_chance(racer.mood, racer.injury_races_remaining > 0)
    fail_pct = int(fail_chance * 100)

    lines = []
    for stat in ("speed", "cornering", "stamina"):
        val = getattr(racer, stat)
        cost = max(int(logic.calculate_training_cost(val, base, mult) * train_cost_mult), 1)
        at_max = val >= logic.MAX_STAT
        lines.append(
            f"**{stat.capitalize()}** — {_stat_band(val)} ({val})"
            + (f" — **{cost} coins**" if not at_max else " — *MAX*")
        )

    embed = discord.Embed(
        title=f"\u26a1 Train {racer.name}",
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    if fail_pct > 0:
        embed.add_field(name="Failure Chance", value=f"{fail_pct}%", inline=True)
    embed.set_footer(text=f"Balance: {data['wallet'].balance} coins")
    return embed


class StableRenameModal(discord.ui.Modal, title="Rename Racer"):
    """Modal for entering a new racer name."""

    new_name = discord.ui.TextInput(
        label="New name",
        placeholder="Enter a new name (max 32 characters)",
        required=True,
        max_length=32,
    )

    def __init__(self, bot, racer_id: int, user_id: int, guild_id: int, message):
        super().__init__()
        self.bot = bot
        self.racer_id = racer_id
        self.user_id = user_id
        self.guild_id = guild_id
        self._message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.new_name.value.strip()
        if not name or len(name) > 32:
            await interaction.response.send_message(
                "Name must be 1-32 characters.", ephemeral=True
            )
            return

        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None:
                await interaction.response.send_message("Racer not found.", ephemeral=True)
                return

            # Check uniqueness
            existing = await session.execute(
                select(models.Racer.id).where(
                    models.Racer.guild_id == self.guild_id,
                    models.Racer.retired.is_(False),
                    models.Racer.name == name,
                )
            )
            if existing.scalars().first() is not None:
                await interaction.response.send_message(
                    f"A racer named **{name}** already exists in this guild.",
                    ephemeral=True,
                )
                return

            old_name = racer.name
            await repo.update_racer(session, self.racer_id, name=name)

        # Refresh to main view with status
        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data, status_text=f"Renamed **{old_name}** to **{name}**.")
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class StableSellConfirmView(discord.ui.View):
    """Sell confirmation — confirm or cancel."""

    def __init__(self, bot, racer_id: int, user_id: int, guild_id: int, sell_price: int, racer_name: str):
        super().__init__(timeout=STABLE_VIEW_TIMEOUT)
        self.bot = bot
        self.racer_id = racer_id
        self.user_id = user_id
        self.guild_id = guild_id
        self.sell_price = sell_price
        self.racer_name = racer_name
        # discord.ui.View doesn't auto-populate this on send or edit_message;
        # initialize so on_timeout's `if self.message` check doesn't AttributeError.
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your stable!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(label="Confirm Sell \u2713", style=discord.ButtonStyle.danger, row=0)
    async def confirm_sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None or racer.owner_id != self.user_id:
                await interaction.response.send_message("Racer not found or not yours.", ephemeral=True)
                return

            # Block if in unfinished race
            in_race = (
                await session.execute(
                    select(models.RaceEntry.id)
                    .join(models.Race, models.RaceEntry.race_id == models.Race.id)
                    .where(
                        models.RaceEntry.racer_id == self.racer_id,
                        models.Race.finished.is_(False),
                    )
                )
            ).scalars().first()
            if in_race is not None:
                await interaction.response.send_message(
                    f"**{racer.name}** is entered in an upcoming race and can't be sold right now.",
                    ephemeral=True,
                )
                return

            wallet = await wallet_repo.get_wallet(session, self.user_id, self.guild_id)
            if wallet is None:
                gs = await self.bot.scheduler.guild_settings.get(self.guild_id)
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=self.user_id, guild_id=self.guild_id, balance=default_bal,
                )
            wallet.balance += self.sell_price
            pool_expiry = datetime.utcnow() + timedelta(hours=random.uniform(24, 48))
            await repo.update_racer(session, self.racer_id, owner_id=0, pool_expires_at=pool_expiry)
            await session.commit()

        embed = discord.Embed(
            title=f"Sold {self.racer_name}",
            description=f"**{self.racer_name}** was sold for **{self.sell_price} coins**.\nBalance: **{wallet.balance} coins**.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel \u2717", style=discord.ButtonStyle.secondary, row=0)
    async def cancel_sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data)
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class StableTrainView(discord.ui.View):
    """Training state — pick a stat to train."""

    def __init__(self, bot, racer_id: int, user_id: int, guild_id: int):
        super().__init__(timeout=STABLE_VIEW_TIMEOUT)
        self.bot = bot
        self.racer_id = racer_id
        self.user_id = user_id
        self.guild_id = guild_id
        # Initialized here so on_timeout's `if self.message` check won't
        # AttributeError when the view was attached via edit_message (which
        # doesn't auto-populate .message).
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your stable!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _do_train(self, interaction: discord.Interaction, stat_name: str):
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None or racer.owner_id != self.user_id:
                await interaction.response.send_message("Racer not found or not yours.", ephemeral=True)
                return
            if racer.retired:
                await interaction.response.send_message("This racer is retired.", ephemeral=True)
                return

            gs = await self.bot.scheduler.guild_settings.get(self.guild_id)
            max_trains = resolve_guild_setting(gs, self.bot.settings, "max_trains_per_race")
            if (racer.trains_since_race or 0) >= max_trains:
                await interaction.response.send_message(
                    f"**{racer.name}** has reached the training limit ({max_trains} sessions). "
                    f"Wait for the next race!",
                    ephemeral=True,
                )
                return

            current_value = getattr(racer, stat_name)
            if current_value >= logic.MAX_STAT:
                await interaction.response.send_message(
                    f"**{racer.name}**'s {stat_name} is already at maximum.",
                    ephemeral=True,
                )
                return

            training_base = resolve_guild_setting(gs, self.bot.settings, "training_base")
            training_mult = resolve_guild_setting(gs, self.bot.settings, "training_multiplier")
            cost = logic.calculate_training_cost(current_value, training_base, training_mult)

            profile = await rpg_repo.get_or_create_profile(session, self.user_id, self.guild_id)
            train_cost_mult = get_racial_modifier(profile.race, "racing.training_cost_multiplier", 1.0)
            cost = max(int(cost * train_cost_mult), 1)

            wallet = await wallet_repo.get_wallet(session, self.user_id, self.guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=self.user_id, guild_id=self.guild_id, balance=default_bal,
                )
            if wallet.balance < cost:
                await interaction.response.send_message(
                    f"Training costs **{cost} coins** but you only have **{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            # Execute training
            wallet.balance -= cost
            racer.trains_since_race = (racer.trains_since_race or 0) + 1
            old_mood = racer.mood
            mood_floor = get_racial_modifier(profile.race, "racing.mood_floor", 1)
            new_mood = max(mood_floor, old_mood - 1)
            racer.mood = new_mood

            fail_chance = logic.training_failure_chance(old_mood, racer.injury_races_remaining > 0)
            failed = random.random() < fail_chance

            if not failed:
                new_value = current_value + 1
                await repo.update_racer(session, self.racer_id, **{stat_name: new_value})
                racer.training_count = (racer.training_count or 0) + 1
                setattr(racer, stat_name, new_value)
                rank_change = logic.recalculate_rank(racer)
            else:
                new_value = current_value
                rank_change = None

            await session.commit()

        # Build status text
        if failed:
            status = (
                f"\u274c Training failed! **{cost} coins** spent but {stat_name} unchanged."
            )
        else:
            status = (
                f"\u2705 {stat_name.capitalize()}: "
                f"{_stat_band(current_value)} \u2192 {_stat_band(new_value)} "
                f"({current_value} \u2192 {new_value}) — **{cost} coins**"
            )
            if rank_change:
                status += f"\n\u2b06\ufe0f **Rank Up!** Promoted to **{logic.rank_label(rank_change)}**"

        if fail_chance > 0:
            status += f"\n*Failure chance was {int(fail_chance * 100)}%*"

        # Return to main view with status
        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data, status_text=status)
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Speed", style=discord.ButtonStyle.primary, emoji="\U0001f3c3", row=0)
    async def train_speed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_train(interaction, "speed")

    @discord.ui.button(label="Cornering", style=discord.ButtonStyle.primary, emoji="\U0001f4a8", row=0)
    async def train_cornering(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_train(interaction, "cornering")

    @discord.ui.button(label="Stamina", style=discord.ButtonStyle.primary, emoji="\U0001f4aa", row=0)
    async def train_stamina(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._do_train(interaction, "stamina")

    @discord.ui.button(label="Back \u21a9", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data)
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class StableManageView(discord.ui.View):
    """Main stable management view — train, rest, feed, sell, rename, close."""

    def __init__(self, bot, racer_id: int, user_id: int, guild_id: int):
        super().__init__(timeout=STABLE_VIEW_TIMEOUT)
        self.bot = bot
        self.racer_id = racer_id
        self.user_id = user_id
        self.guild_id = guild_id
        # Initialized here so on_timeout's `if self.message` check won't
        # AttributeError when the view was attached via edit_message (which
        # doesn't auto-populate .message).
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your stable!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message:
            try:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.description = "*Session expired.*"
                    await self.message.edit(embed=embed, view=self)
                else:
                    await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    @discord.ui.button(label="Train", style=discord.ButtonStyle.primary, emoji="\u26a1", row=0)
    async def train(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_train_embed(data)
        view = StableTrainView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Rest", style=discord.ButtonStyle.primary, emoji="\U0001f4a4", row=0)
    async def rest(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None or racer.owner_id != self.user_id:
                await interaction.response.send_message("Racer not found or not yours.", ephemeral=True)
                return
            if racer.retired:
                await interaction.response.send_message("This racer is retired.", ephemeral=True)
                return

            if getattr(racer, "rested_since_race", False):
                await interaction.response.send_message(
                    f"**{racer.name}** has already rested this cycle. Wait for the next race!",
                    ephemeral=True,
                )
                return

            new_mood, error = logic.apply_rest(racer.mood)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

            gs = await self.bot.scheduler.guild_settings.get(self.guild_id)
            cost = resolve_guild_setting(gs, self.bot.settings, "rest_cost")

            wallet = await wallet_repo.get_wallet(session, self.user_id, self.guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=self.user_id, guild_id=self.guild_id, balance=default_bal,
                )
            if wallet.balance < cost:
                await interaction.response.send_message(
                    f"Resting costs **{cost} coins** but you only have **{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            old_mood = racer.mood
            wallet.balance -= cost
            racer.mood = new_mood
            racer.rested_since_race = True
            await session.commit()

        status = (
            f"\U0001f4a4 {racer.name} takes a rest. "
            f"Mood: {_mood_label(old_mood)} \u2192 {_mood_label(new_mood)}"
            + (f" — **{cost} coins**" if cost > 0 else "")
        )

        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data, status_text=status)
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Feed", style=discord.ButtonStyle.primary, emoji="\U0001f34e", row=0)
    async def feed(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None or racer.owner_id != self.user_id:
                await interaction.response.send_message("Racer not found or not yours.", ephemeral=True)
                return
            if racer.retired:
                await interaction.response.send_message("This racer is retired.", ephemeral=True)
                return

            new_mood, error = logic.apply_feed(racer.mood)
            if error:
                await interaction.response.send_message(error, ephemeral=True)
                return

            gs = await self.bot.scheduler.guild_settings.get(self.guild_id)
            cost = resolve_guild_setting(gs, self.bot.settings, "feed_cost")

            wallet = await wallet_repo.get_wallet(session, self.user_id, self.guild_id)
            if wallet is None:
                default_bal = resolve_guild_setting(gs, self.bot.settings, "default_wallet")
                wallet = await wallet_repo.create_wallet(
                    session, user_id=self.user_id, guild_id=self.guild_id, balance=default_bal,
                )
            if wallet.balance < cost:
                await interaction.response.send_message(
                    f"Feeding costs **{cost} coins** but you only have **{wallet.balance} coins**.",
                    ephemeral=True,
                )
                return

            old_mood = racer.mood
            wallet.balance -= cost
            racer.mood = new_mood
            await session.commit()

        status = (
            f"\U0001f34e {racer.name} enjoys a feast! "
            f"Mood: {_mood_label(old_mood)} \u2192 {_mood_label(new_mood)} — **{cost} coins**"
        )

        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, self.racer_id,
            self.user_id, self.guild_id, self.bot.settings,
        )
        if data is None:
            await interaction.response.send_message("Racer not found.", ephemeral=True)
            return
        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data, status_text=status)
        view = StableManageView(self.bot, self.racer_id, self.user_id, self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Sell", style=discord.ButtonStyle.danger, emoji="\U0001f4b0", row=1)
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.bot.scheduler.sessionmaker() as session:
            racer = await repo.get_racer(session, self.racer_id)
            if racer is None or racer.owner_id != self.user_id:
                await interaction.response.send_message("Racer not found or not yours.", ephemeral=True)
                return

            # Block if in unfinished race
            in_race = (
                await session.execute(
                    select(models.RaceEntry.id)
                    .join(models.Race, models.RaceEntry.race_id == models.Race.id)
                    .where(
                        models.RaceEntry.racer_id == self.racer_id,
                        models.Race.finished.is_(False),
                    )
                )
            ).scalars().first()
            if in_race is not None:
                await interaction.response.send_message(
                    f"**{racer.name}** is entered in an upcoming race and can't be sold right now.",
                    ephemeral=True,
                )
                return

            gs = await self.bot.scheduler.guild_settings.get(self.guild_id)
            base = resolve_guild_setting(gs, self.bot.settings, "racer_buy_base")
            mult = resolve_guild_setting(gs, self.bot.settings, "racer_buy_multiplier")
            frac = resolve_guild_setting(gs, self.bot.settings, "racer_sell_fraction")
            fem_mult = resolve_guild_setting(gs, self.bot.settings, "female_buy_multiplier")
            ret_pen = resolve_guild_setting(gs, self.bot.settings, "retired_sell_penalty")
            foal_pen = resolve_guild_setting(gs, self.bot.settings, "foal_sell_penalty")
            t_bonus = logic.calculate_tournament_sell_bonus(racer)
            sell_price = logic.calculate_sell_price(
                racer, base, mult, frac,
                female_multiplier=fem_mult,
                retired_penalty=ret_pen,
                foal_penalty=foal_pen,
                tournament_bonus=t_bonus,
            )

        embed = discord.Embed(
            title=f"\U0001f4b0 Sell {racer.name}?",
            description=(
                f"Are you sure you want to sell **{racer.name}**?\n"
                f"You'll receive **{sell_price} coins**.\n\n"
                f"*This cannot be undone.*"
            ),
            color=discord.Color.red(),
        )
        view = StableSellConfirmView(
            self.bot, self.racer_id, self.user_id, self.guild_id, sell_price, racer.name,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, emoji="\u270f\ufe0f", row=1)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StableRenameModal(
            self.bot, self.racer_id, self.user_id, self.guild_id, interaction.message,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="\u274c", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.description = "*Session closed.*"
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)
        self.stop()


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
            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

    @stable.command(name="manage", description="Open an interactive panel to manage a racer")
    @app_commands.describe(racer="Racer to manage")
    @app_commands.autocomplete(racer=owned_racer_autocomplete)
    async def stable_manage(self, context: Context, racer: int) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0

        data = await _fetch_manage_data(
            self.bot.scheduler.sessionmaker, racer,
            context.author.id, guild_id, self.bot.settings,
        )
        if data is None:
            await context.send("Racer not found.", ephemeral=True)
            return
        if data["racer"].owner_id != context.author.id:
            await context.send("You don't own that racer!", ephemeral=True)
            return
        if data["racer"].retired:
            await context.send("This racer is retired.", ephemeral=True)
            return

        data["bot_settings"] = self.bot.settings
        embed = _build_manage_embed(data)
        view = StableManageView(self.bot, racer, context.author.id, guild_id)
        msg = await context.send(embed=embed, view=view)
        view.message = msg

    _RANK_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}

    @stable.command(name="report", description="Get a status report on your stable")
    async def stable_report(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            gs = await self.bot.scheduler.guild_settings.get(guild_id)
            all_racers = await repo.get_stable_racers(session, user_id, guild_id)
            if not all_racers:
                await context.send(
                    "You don't own any racers yet! Use `/stable browse` to get started.",
                    ephemeral=True,
                )
                return

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)

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
                rolled = appearance.deserialize(racer_obj.appearance)
                if not rolled:
                    rolled = appearance.roll_appearance()
                    if rolled:
                        racer_obj.appearance = appearance.serialize(rolled)
                desc = await descriptions.generate_description(
                    name=racer_obj.name,
                    speed=racer_obj.speed,
                    cornering=racer_obj.cornering,
                    stamina=racer_obj.stamina,
                    temperament=racer_obj.temperament,
                    gender=racer_obj.gender,
                    flavor=flavor,
                    appearance=rolled or None,
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

        # Appearance (structured attributes rolled at creation)
        appearance_data = appearance.deserialize(racer_obj.appearance)
        if appearance_data:
            appearance_text = appearance.format_appearance_for_display(appearance_data)
            if appearance_text:
                embed.add_field(name="Appearance", value=appearance_text, inline=False)

        # Abilities (signature + quirk)
        ability_text = abilities.display_summary(
            getattr(racer_obj, "signature_ability", None),
            getattr(racer_obj, "quirk_ability", None),
        )
        if ability_text:
            embed.add_field(name="Abilities", value=ability_text, inline=False)

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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)

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
                rolled = appearance.deserialize(racer_obj.appearance)
                if not rolled:
                    rolled = appearance.roll_appearance()
                    if rolled:
                        racer_obj.appearance = appearance.serialize(rolled)
                desc = await descriptions.generate_description(
                    name=racer_obj.name,
                    speed=racer_obj.speed,
                    cornering=racer_obj.cornering,
                    stamina=racer_obj.stamina,
                    temperament=racer_obj.temperament,
                    gender=racer_obj.gender,
                    flavor=flavor,
                    appearance=rolled or None,
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

        # Appearance (structured attributes rolled at creation)
        appearance_data = appearance.deserialize(racer_obj.appearance)
        if appearance_data:
            appearance_text = appearance.format_appearance_for_display(appearance_data)
            if appearance_text:
                embed.add_field(name="Appearance", value=appearance_text, inline=False)

        # Abilities (signature + quirk)
        ability_text = abilities.display_summary(
            getattr(racer_obj, "signature_ability", None),
            getattr(racer_obj, "quirk_ability", None),
        )
        if ability_text:
            embed.add_field(name="Abilities", value=ability_text, inline=False)

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
            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            if getattr(racer_obj, "rested_since_race", False):
                await context.send(
                    f"**{racer_obj.name}** has already rested this cycle. "
                    "Wait for the next race!",
                    ephemeral=True,
                )
                return

            new_mood, error = logic.apply_rest(racer_obj.mood)
            if error:
                await context.send(error, ephemeral=True)
                return

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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
            racer_obj.rested_since_race = True
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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
            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            gs = await self.bot.scheduler.guild_settings.get(guild_id)
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

            # Inherit appearance from parents (structured). If neither
            # parent has structured appearance, fall back to legacy prose
            # blending via sire_desc/dam_desc.
            flavor = getattr(gs, "racer_flavor", None) if gs else None
            sire_app = appearance.deserialize(sire.appearance)
            dam_app = appearance.deserialize(dam.appearance)
            has_structured_parent = bool(sire_app) or bool(dam_app)

            if has_structured_parent:
                foal_app = appearance.inherit_appearance(sire_app, dam_app)
                if foal_app:
                    foal.appearance = appearance.serialize(foal_app)
            else:
                foal_app = {}

            # Inherit abilities: one slot from one parent, the other fresh-rolled
            foal_sig, foal_quirk = abilities.inherit_abilities(
                sire.signature_ability, sire.quirk_ability,
                dam.signature_ability, dam.quirk_ability,
                foal,
            )
            if foal_sig:
                foal.signature_ability = foal_sig
            if foal_quirk:
                foal.quirk_ability = foal_quirk

            if flavor and sire.description and dam.description:
                foal_desc = await descriptions.generate_description(
                    name=foal.name,
                    speed=foal.speed,
                    cornering=foal.cornering,
                    stamina=foal.stamina,
                    temperament=foal.temperament,
                    gender=foal.gender,
                    flavor=flavor,
                    sire_desc=sire.description if not foal_app else None,
                    dam_desc=dam.description if not foal_app else None,
                    appearance=foal_app or None,
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



async def setup(bot) -> None:
    await bot.add_cog(Stable(bot))
