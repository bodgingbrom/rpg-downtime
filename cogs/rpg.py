from __future__ import annotations

import os

import discord
import yaml
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

import checks
from economy import repositories as wallet_repo
from rpg import logic as rpg_logic
from rpg import repositories as rpg_repo

# ---------------------------------------------------------------------------
# Load race display data from YAML
# ---------------------------------------------------------------------------

_RACES_YAML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "rpg", "data", "races.yaml"
)

_race_display_cache: list[dict] | None = None


def _load_race_display() -> list[dict]:
    global _race_display_cache
    if _race_display_cache is not None:
        return _race_display_cache
    with open(_RACES_YAML_PATH, "r", encoding="utf-8") as f:
        _race_display_cache = yaml.safe_load(f)
    return _race_display_cache


def _get_race_display(race_id: str) -> dict | None:
    for r in _load_race_display():
        if r["id"] == race_id:
            return r
    return None


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def _build_race_embed(race_id: str, *, is_current: bool = False) -> discord.Embed:
    """Build a detailed embed for a single race."""
    rd = _get_race_display(race_id)
    if rd is None:
        return discord.Embed(title="Unknown Race", color=discord.Color.red())

    color_map = {
        "human": 0x95A5A6,
        "dwarf": 0xE67E22,
        "elf": 0x2ECC71,
        "halfling": 0xF1C40F,
        "orc": 0xE74C3C,
    }

    title = f"{rd['emoji']} {rd['name']} — {rd['tagline']}"
    if is_current:
        title += "  (Your Race)"

    embed = discord.Embed(
        title=title,
        description=f"*{rd['quote']}*",
        color=color_map.get(race_id, 0x7F8C8D),
    )

    # Passives
    passive_lines = []
    for p in rd.get("passives", []):
        star = "\u2B50 " if p.get("signature") else ""
        passive_lines.append(f"{star}**{p['name']}** ({p['game']})\n{p['description']}")
    embed.add_field(
        name="Passives",
        value="\n".join(passive_lines) if passive_lines else "None",
        inline=False,
    )

    # Flaw
    flaw = rd.get("flaw", {})
    embed.add_field(
        name=f"\u26A0\uFE0F Flaw: {flaw.get('name', 'None')}",
        value=flaw.get("description", "No drawbacks."),
        inline=False,
    )

    return embed


def _build_race_overview_embed() -> discord.Embed:
    """Build a summary embed showing all races side by side."""
    embed = discord.Embed(
        title="Choose Your Race",
        description=(
            "Your race is a permanent choice that grants passives across "
            "**all mini-games**. Choose wisely!\n"
            "Use the buttons below to preview each race."
        ),
        color=0x7F8C8D,
    )

    for rd in _load_race_display():
        sig = next(
            (p for p in rd.get("passives", []) if p.get("signature")), None
        )
        flaw = rd.get("flaw", {})
        sig_text = f"\u2B50 *{sig['name']}*: {sig['description']}" if sig else ""
        flaw_text = f"\u26A0\uFE0F *{flaw.get('name', '')}*: {flaw.get('description', '')}"

        embed.add_field(
            name=f"{rd['emoji']} {rd['name']} — {rd['tagline']}",
            value=f"{sig_text}\n{flaw_text}",
            inline=False,
        )

    embed.set_footer(text="First pick is free. Changing later costs gold (escalating).")
    return embed


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class RaceChooseView(discord.ui.View):
    """Interactive race selection with preview and confirm."""

    def __init__(self, user_id: int, *, is_change: bool = False, change_cost: int = 0):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.is_change = is_change
        self.change_cost = change_cost
        self.selected_race: str | None = None
        self.confirmed = False

        # Build the select options
        select = discord.ui.Select(
            placeholder="Pick a race to preview...",
            options=[
                discord.SelectOption(
                    label=rd["name"],
                    value=rd["id"],
                    emoji=rd["emoji"],
                    description=rd["tagline"],
                )
                for rd in _load_race_display()
            ],
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your choice to make!", ephemeral=True
            )
            return False
        return True

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        self.selected_race = interaction.data["values"][0]
        embed = _build_race_embed(self.selected_race)

        # Remove old confirm button if present, add new one
        self.clear_items()

        # Re-add the select
        select = discord.ui.Select(
            placeholder="Pick a race to preview...",
            options=[
                discord.SelectOption(
                    label=rd["name"],
                    value=rd["id"],
                    emoji=rd["emoji"],
                    description=rd["tagline"],
                    default=(rd["id"] == self.selected_race),
                )
                for rd in _load_race_display()
            ],
        )
        select.callback = self._select_callback
        self.add_item(select)

        # Add confirm button
        if self.is_change:
            label = f"Confirm Change ({self.change_cost}g)"
        else:
            label = "Confirm Choice (Free)"
        confirm_btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.green,
            custom_id="confirm_race",
        )
        confirm_btn.callback = self._confirm_callback
        self.add_item(confirm_btn)

        cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.grey,
            custom_id="cancel_race",
        )
        cancel_btn.callback = self._cancel_callback
        self.add_item(cancel_btn)

        await interaction.response.edit_message(embed=embed, view=self)

    async def _confirm_callback(self, interaction: discord.Interaction) -> None:
        if self.selected_race is None:
            await interaction.response.send_message(
                "Pick a race first!", ephemeral=True
            )
            return
        self.confirmed = True
        self.stop()
        # Disable all items
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        embed = _build_race_embed(self.selected_race, is_current=True)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        self.confirmed = False
        self.selected_race = None
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        await interaction.response.edit_message(
            content="Race selection cancelled.", embed=None, view=self,
        )

    async def on_timeout(self) -> None:
        self.confirmed = False


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class RPG(commands.Cog, name="rpg"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_any_game_channel(ctx)

    @commands.hybrid_group(name="race", description="Manage your character race")
    async def race(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send(
                "Use `/race choose`, `/race info`, or `/race change`.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /race choose
    # ------------------------------------------------------------------

    @race.command(name="choose", description="Choose your race (first time is free)")
    async def race_choose(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)

            if profile.chosen_at is not None:
                rd = _get_race_display(profile.race)
                name = rd["name"] if rd else profile.race
                await context.send(
                    f"You're already a **{name}**! "
                    f"Use `/race change` to switch (costs gold).",
                    ephemeral=True,
                )
                return

        # Show the overview + selection view
        embed = _build_race_overview_embed()
        view = RaceChooseView(user_id)
        await context.send(embed=embed, view=view, ephemeral=True)
        timed_out = await view.wait()

        if timed_out or not view.confirmed or not view.selected_race:
            return

        # Commit the choice
        async with self.bot.scheduler.sessionmaker() as session:
            await rpg_repo.update_race(session, user_id, guild_id, view.selected_race)

        rd = _get_race_display(view.selected_race)
        name = rd["name"] if rd else view.selected_race
        await context.send(
            f"You are now a **{name}**! Your racial passives are active across all games.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /race info
    # ------------------------------------------------------------------

    @race.command(name="info", description="View race details")
    @app_commands.describe(race_name="Which race to view (defaults to your own)")
    async def race_info(
        self, context: Context, race_name: str | None = None
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        if race_name and race_name in rpg_logic.RACE_IDS:
            target_race = race_name
            is_current = False
            # Check if it's actually their race
            async with self.bot.scheduler.sessionmaker() as session:
                profile = await rpg_repo.get_or_create_profile(
                    session, user_id, guild_id
                )
                is_current = profile.race == target_race
        elif race_name:
            await context.send(
                f"Unknown race `{race_name}`. "
                f"Valid races: {', '.join(rpg_logic.RACE_IDS)}",
                ephemeral=True,
            )
            return
        else:
            async with self.bot.scheduler.sessionmaker() as session:
                profile = await rpg_repo.get_or_create_profile(
                    session, user_id, guild_id
                )
                target_race = profile.race
                is_current = True

        embed = _build_race_embed(target_race, is_current=is_current)
        await context.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /race change
    # ------------------------------------------------------------------

    @race.command(name="change", description="Change your race (costs gold)")
    @app_commands.describe(race_name="The race to change to")
    async def race_change(self, context: Context, race_name: str) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        if race_name not in rpg_logic.RACE_IDS:
            await context.send(
                f"Unknown race `{race_name}`. "
                f"Valid races: {', '.join(rpg_logic.RACE_IDS)}",
                ephemeral=True,
            )
            return

        async with self.bot.scheduler.sessionmaker() as session:
            profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)

            # If they haven't chosen yet, redirect to /race choose
            if profile.chosen_at is None:
                await context.send(
                    "You haven't chosen a race yet! Use `/race choose` (it's free).",
                    ephemeral=True,
                )
                return

            if profile.race == race_name:
                rd = _get_race_display(race_name)
                name = rd["name"] if rd else race_name
                await context.send(
                    f"You're already a **{name}**!", ephemeral=True
                )
                return

            cost = rpg_logic.get_race_change_cost(profile.race_changes)

            # Check wallet
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None or wallet.balance < cost:
                bal = wallet.balance if wallet else 0
                await context.send(
                    f"Changing race costs **{cost}g** but you only have "
                    f"**{bal}g**.",
                    ephemeral=True,
                )
                return

        # Show preview + confirm
        view = RaceChooseView(
            user_id, is_change=True, change_cost=cost
        )
        # Pre-select the requested race
        view.selected_race = race_name
        embed = _build_race_embed(race_name)
        # Manually add confirm/cancel buttons since we're skipping the select step
        confirm_btn = discord.ui.Button(
            label=f"Confirm Change ({cost}g)",
            style=discord.ButtonStyle.green,
            custom_id="confirm_race",
        )
        confirm_btn.callback = view._confirm_callback
        view.add_item(confirm_btn)

        cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.grey,
            custom_id="cancel_race",
        )
        cancel_btn.callback = view._cancel_callback
        view.add_item(cancel_btn)

        await context.send(embed=embed, view=view, ephemeral=True)
        timed_out = await view.wait()

        if timed_out or not view.confirmed or not view.selected_race:
            return

        # Deduct gold and update race
        async with self.bot.scheduler.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            if wallet is None or wallet.balance < cost:
                await context.send("Not enough gold!", ephemeral=True)
                return
            wallet.balance -= cost
            await rpg_repo.update_race(
                session, user_id, guild_id, view.selected_race, is_change=True
            )

        rd = _get_race_display(view.selected_race)
        name = rd["name"] if rd else view.selected_race
        await context.send(
            f"You are now a **{name}**! (Cost: **{cost}g**)\n"
            f"Your new racial passives are active across all games.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    @race_info.autocomplete("race_name")
    @race_change.autocomplete("race_name")
    async def race_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        choices = []
        for rd in _load_race_display():
            if current.lower() in rd["name"].lower() or current.lower() in rd["id"]:
                choices.append(
                    app_commands.Choice(name=rd["name"], value=rd["id"])
                )
        return choices[:25]


async def setup(bot) -> None:
    await bot.add_cog(RPG(bot))
