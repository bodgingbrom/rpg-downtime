from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any

import discord
import checks
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

from cogs._autocomplete import filter_choices
from dungeon import logic as dungeon_logic
from dungeon import repositories as dungeon_repo
from dungeon import effects as dungeon_effects
from dungeon import resolver as dungeon_resolver
from dungeon import explore as dungeon_explore
from dungeon import llm as dungeon_llm
from dungeon import map_render as dungeon_map
from economy import repositories as wallet_repo
from rpg import repositories as rpg_repo
from rpg.logic import get_racial_modifier

# Cross-game imports (optional — gracefully skip if not loaded)
try:
    from fishing import repositories as fishing_repo
    from fishing.logic import BAIT_TYPES as _BAIT_TYPES
    _HAS_FISHING = True
except ImportError:
    _HAS_FISHING = False
    _BAIT_TYPES = {}

try:
    from brewing import repositories as brewing_repo
    _HAS_BREWING = True
except ImportError:
    _HAS_BREWING = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Embed builders + their color palette live in dungeon/ui/embeds.py. Re-imported
# here so existing call sites (`build_combat_embed(...)`, `EMBED_COLOR_LOOT`,
# etc.) keep working without changes.
from dungeon.ui.embeds import (  # noqa: E402
    EMBED_COLOR,
    EMBED_COLOR_BOSS,
    EMBED_COLOR_COMBAT,
    EMBED_COLOR_DEATH,
    EMBED_COLOR_LOOT,
    EMBED_COLOR_RETURN,
    EMBED_COLOR_REST,
    EMBED_COLOR_TRAP,
    _build_resume_embed,
    _format_player_effects,
    _hp_bar,
    _status_line,
    build_combat_embed,
    build_combat_start_embed,
    build_death_embed,
    build_floor_complete_embed,
    build_loot_embed,
    build_rest_embed,
    build_return_embed,
    build_trap_result_embed,
)

VIEW_TIMEOUT = 600  # 10 minutes




# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------


def _get_view_for_state(run, user_id, sessionmaker, dungeon_data):
    """Return the appropriate View for the current run state."""
    state = run.state
    if state in ("combat", "boss"):
        return CombatView(run.id, user_id, sessionmaker)
    elif state == "floor_complete":
        max_floor = dungeon_logic.get_max_floor(dungeon_data)
        can_descend = run.floor < max_floor
        return FloorCompleteView(run.id, user_id, sessionmaker, can_descend)
    else:
        # exploring (or any other state) — v2 runs use ExploreView, v1 keeps
        # the existing ContinueView "continue or retreat" UX.
        floor_state = dungeon_explore.load_floor_state(
            getattr(run, "floor_state_json", None)
        )
        if floor_state and dungeon_explore.is_v2_dungeon(dungeon_data or {}):
            floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
            return _v2_render_view(run.id, user_id, sessionmaker, floor_state, floor_data)
        return ContinueView(run.id, user_id, sessionmaker)



# ---------------------------------------------------------------------------
# View classes (Button UX)
# ---------------------------------------------------------------------------


class DungeonView(discord.ui.View):
    """Base view that validates the interacting user."""

    def __init__(self, run_id: int, user_id: int, sessionmaker, *, timeout=VIEW_TIMEOUT):
        super().__init__(timeout=timeout)
        self.run_id = run_id
        self.user_id = user_id
        self.sessionmaker = sessionmaker

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your dungeon run!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True


class ContinueView(DungeonView):
    """Buttons shown between rooms: continue blind or retreat."""

    @discord.ui.button(label="Continue Deeper", style=discord.ButtonStyle.primary, emoji="\u2694\ufe0f")
    async def continue_deeper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_enter_room(interaction, self.run_id, self.user_id, self.sessionmaker)

    @discord.ui.button(label="Retreat to Town", style=discord.ButtonStyle.secondary, emoji="\U0001f3e0")
    async def retreat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_retreat(interaction, self.run_id, self.user_id, self.sessionmaker)


class CombatView(DungeonView):
    """Buttons shown during combat."""

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.danger, emoji="\u2694\ufe0f")
    async def attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_combat_action(interaction, self.run_id, self.user_id, self.sessionmaker, "attack")

    @discord.ui.button(label="Defend", style=discord.ButtonStyle.primary, emoji="\U0001f6e1\ufe0f")
    async def defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_combat_action(interaction, self.run_id, self.user_id, self.sessionmaker, "defend")

    @discord.ui.button(label="Use Item", style=discord.ButtonStyle.secondary, emoji="\U0001f9ea")
    async def use_item(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_use_item(interaction, self.run_id, self.user_id, self.sessionmaker)

    @discord.ui.button(label="Flee", style=discord.ButtonStyle.secondary, emoji="\U0001f3c3")
    async def flee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_combat_action(interaction, self.run_id, self.user_id, self.sessionmaker, "flee")


class PostRoomView(DungeonView):
    """Buttons shown after resolving any room (loot, trap result, rest, etc.)."""

    @discord.ui.button(label="Continue Deeper", style=discord.ButtonStyle.primary, emoji="\u2694\ufe0f")
    async def continue_deeper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_enter_room(interaction, self.run_id, self.user_id, self.sessionmaker)

    @discord.ui.button(label="Retreat to Town", style=discord.ButtonStyle.secondary, emoji="\U0001f3e0")
    async def retreat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_retreat(interaction, self.run_id, self.user_id, self.sessionmaker)


class FloorCompleteView(DungeonView):
    """Buttons shown after defeating a floor boss."""

    def __init__(self, run_id, user_id, sessionmaker, can_descend: bool):
        super().__init__(run_id, user_id, sessionmaker)
        if not can_descend:
            self.descend.disabled = True

    @discord.ui.button(label="Descend Deeper", style=discord.ButtonStyle.danger, emoji="\u2b07\ufe0f")
    async def descend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_descend(interaction, self.run_id, self.user_id, self.sessionmaker)

    @discord.ui.button(label="Return to Town", style=discord.ButtonStyle.success, emoji="\U0001f3e0")
    async def return_town(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _handle_retreat(interaction, self.run_id, self.user_id, self.sessionmaker)


class ExploreView(DungeonView):
    """V2 dungeon exploration buttons.

    Renders dynamic action buttons based on room state:
    - Look Around (once per room; greys out after use)
    - Listen (repeatable)
    - Investigate <feature> (one per surfaced feature; greys out after use)
    - Move on <exit> (per discovered exit)
    - Retreat to town (always available)

    Discord caps at 25 buttons (5 rows of 5). Practically PR 3 won't
    come close — even the most loaded room has ~10 buttons total.
    """

    def __init__(
        self, run_id, user_id, sessionmaker,
        *, looked_around: bool,
        exits: list[dict[str, Any]],
        investigate_buttons: list[dict[str, Any]] | None = None,
    ):
        super().__init__(run_id, user_id, sessionmaker)
        investigate_buttons = investigate_buttons or []

        # Row 0 — primary actions.
        look_btn = discord.ui.Button(
            label="Look Around",
            style=discord.ButtonStyle.primary,
            emoji="\U0001f50d",
            disabled=looked_around,
            row=0,
        )
        look_btn.callback = self._look_callback
        self.add_item(look_btn)
        listen_btn = discord.ui.Button(
            label="Listen",
            style=discord.ButtonStyle.secondary,
            emoji="\U0001f442",
            row=0,
        )
        listen_btn.callback = self._listen_callback
        self.add_item(listen_btn)
        retreat_btn = discord.ui.Button(
            label="Retreat to Town",
            style=discord.ButtonStyle.secondary,
            emoji="\U0001f3e0",
            row=0,
        )
        retreat_btn.callback = self._retreat_callback
        self.add_item(retreat_btn)

        # Rows 1-2 — Investigate buttons (up to 5 visible at a time).
        for i, fb in enumerate(investigate_buttons[:5]):
            btn = discord.ui.Button(
                label=(fb.get("label") or "Investigate")[:80],
                style=discord.ButtonStyle.primary,
                emoji="\U0001f50e",
                row=1 + (i // 5),
            )
            btn.callback = self._make_investigate_callback(fb["feature_id"])
            self.add_item(btn)

        # Row 3 — Move on per exit.
        for exit_info in exits[:5]:
            btn = discord.ui.Button(
                label=exit_info.get("label", "Move on"),
                style=discord.ButtonStyle.success,
                emoji="\U0001f6aa",
                row=3,
            )
            btn.callback = self._make_move_callback(exit_info["node_id"])
            self.add_item(btn)

    async def _look_callback(self, interaction: discord.Interaction):
        await _handle_v2_explore_action(
            interaction, self.run_id, self.user_id, self.sessionmaker,
            action="look_around",
        )

    async def _listen_callback(self, interaction: discord.Interaction):
        await _handle_v2_explore_action(
            interaction, self.run_id, self.user_id, self.sessionmaker,
            action="listen",
        )

    def _make_move_callback(self, target_node: str):
        async def _cb(interaction: discord.Interaction):
            await _handle_v2_explore_action(
                interaction, self.run_id, self.user_id, self.sessionmaker,
                action="move_on", target_node=target_node,
            )
        return _cb

    def _make_investigate_callback(self, feature_id: str):
        async def _cb(interaction: discord.Interaction):
            await _handle_v2_explore_action(
                interaction, self.run_id, self.user_id, self.sessionmaker,
                action="investigate", feature_id=feature_id,
            )
        return _cb

    async def _retreat_callback(self, interaction: discord.Interaction):
        await _handle_retreat(interaction, self.run_id, self.user_id, self.sessionmaker)


class ItemSelectView(DungeonView):
    """Select menu for choosing a consumable item during combat."""

    def __init__(self, run_id, user_id, sessionmaker, items: list[dict[str, Any]]):
        super().__init__(run_id, user_id, sessionmaker)
        options = []
        for item in items[:25]:
            item_def = dungeon_logic.get_item_by_id(item["item_id"])
            label = item_def["name"] if item_def else item["item_id"]
            desc = item_def.get("description", "")[:100] if item_def else ""
            options.append(discord.SelectOption(label=label, value=item["item_id"], description=desc))
        self.select = discord.ui.Select(placeholder="Choose an item...", options=options)
        self.select.callback = self._select_callback
        self.add_item(self.select)

    async def _select_callback(self, interaction: discord.Interaction):
        await _handle_use_item_selected(
            interaction, self.run_id, self.user_id, self.sessionmaker, self.select.values[0]
        )


class RunRecoveryView(discord.ui.View):
    """Shown when a player has a stale active run — resume or abandon."""

    def __init__(self, run_id: int, user_id: int, sessionmaker, thread_id: int | None):
        super().__init__(timeout=120)
        self.run_id = run_id
        self.user_id = user_id
        self.sessionmaker = sessionmaker
        self.thread_id = thread_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your dungeon run!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Resume Run", style=discord.ButtonStyle.primary, emoji="\u2694\ufe0f")
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.sessionmaker() as session:
            run = await dungeon_repo.get_run(session, self.run_id)
            if run is None or not run.active:
                await interaction.response.edit_message(
                    content="That run no longer exists.", embed=None, view=None,
                )
                return

            player = await dungeon_repo.get_player(session, run.user_id, run.guild_id)
            dungeon_data = dungeon_logic.get_dungeon(run.dungeon_id) or {"name": "Unknown"}

            embed = _build_resume_embed(run, player, dungeon_data)
            view = _get_view_for_state(run, self.user_id, self.sessionmaker, dungeon_data)

            # Post a fresh interactive message in the existing thread
            thread = interaction.client.get_channel(self.thread_id) if self.thread_id else None
            if thread is None and self.thread_id:
                try:
                    thread = await interaction.client.fetch_channel(self.thread_id)
                except Exception:
                    thread = None

            if thread is not None:
                msg = await thread.send(embed=embed, view=view)
                # Update stored message_id so future interactions reference this one
                run.message_id = msg.id
                await session.commit()
                await interaction.response.edit_message(
                    content=f"Resumed! Head to {thread.mention}.",
                    embed=None, view=None,
                )
            else:
                # Thread is gone — create a new one
                channel = interaction.channel
                display_name = interaction.user.display_name
                thread_name = f"Monster Mash — {display_name} — {dungeon_data['name']}"
                new_thread = await channel.create_thread(
                    name=thread_name[:100],
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=60,
                )
                msg = await new_thread.send(embed=embed, view=view)
                run.thread_id = new_thread.id
                run.message_id = msg.id
                await session.commit()
                await interaction.response.edit_message(
                    content=f"Resumed! Head to {new_thread.mention}.",
                    embed=None, view=None,
                )

    @discord.ui.button(label="Abandon Run", style=discord.ButtonStyle.danger, emoji="\U0001f480")
    async def abandon(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.sessionmaker() as session:
            run = await dungeon_repo.get_run(session, self.run_id)
            if run is None or not run.active:
                await interaction.response.edit_message(
                    content="That run no longer exists.", embed=None, view=None,
                )
                return

            player = await dungeon_repo.get_or_create_player(
                session, run.user_id, run.guild_id
            )
            dungeon_data = dungeon_logic.get_dungeon(run.dungeon_id) or {"name": "Unknown"}
            await _process_abandon(session, run, player)

        await interaction.response.edit_message(
            content=(
                f"Abandoned your run in **{dungeon_data['name']}**. "
                f"Loot lost, XP saved. You're free to start a new delve."
            ),
            embed=None, view=None,
        )


# ---------------------------------------------------------------------------
# Shop & Inventory Views (standalone, no run_id needed)
# ---------------------------------------------------------------------------

SHOP_CATEGORIES = ["weapons", "armors", "accessories", "items"]
SHOP_CATEGORY_LABELS = {
    "weapons": "Weapons",
    "armors": "Armor",
    "accessories": "Accessories",
    "items": "Consumables",
}
SHOP_CATEGORY_EMOJIS = {
    "weapons": "\u2694\ufe0f",
    "armors": "\U0001f6e1\ufe0f",
    "accessories": "\U0001f48d",
    "items": "\U0001f9ea",
}


def _build_shop_embed(
    category: str, player_level: int, balance: int, owned_gear_ids: set[str],
    equipped_ids: set[str],
) -> discord.Embed:
    """Build a shop embed for a given category."""
    label = SHOP_CATEGORY_LABELS[category]
    emoji = SHOP_CATEGORY_EMOJIS[category]
    embed = discord.Embed(
        title=f"{emoji} Monster Mash Shop — {label}",
        color=EMBED_COLOR_LOOT,
    )

    if category == "items":
        items = dungeon_logic.get_shop_items(player_level)
        if not items:
            embed.description = "Nothing for sale here."
            return embed
        lines = []
        for item in items:
            cost = item.get("cost", 0)
            desc = item.get("description", "")
            affordable = "\u2705" if balance >= cost else "\u274c"
            lines.append(f"{affordable} **{item['name']}** — {cost}g\n> {desc}")
        embed.description = "\n".join(lines)
    else:
        gear_list = dungeon_logic.get_shop_gear(category, player_level)
        if not gear_list:
            embed.description = "Nothing available at your level."
            return embed
        lines = []
        for g in gear_list:
            cost = g.get("cost", 0)
            rarity = g.get("rarity", "common")
            affordable = "\u2705" if balance >= cost else "\u274c"
            owned = " *(owned)*" if g["id"] in owned_gear_ids else ""
            equipped = " **[EQUIPPED]**" if g["id"] in equipped_ids else ""

            # Stat summary
            if "dice" in g:
                bonus = g.get("bonus", 0)
                bonus_str = f"+{bonus}" if bonus else ""
                stat = f"{g['dice']}{bonus_str}"
            elif "defense" in g:
                stat = f"+{g['defense']} DEF"
            elif "effect" in g:
                eff = g["effect"]
                stat = f"{eff.get('type', '').replace('_', ' ')}: +{eff.get('value', '')}"
            else:
                stat = ""

            lines.append(
                f"{affordable} **{g['name']}** ({rarity}) — {cost}g — {stat}{equipped}{owned}"
            )
            if g.get("description"):
                lines.append(f"> {g['description']}")
        embed.description = "\n".join(lines)

    embed.set_footer(text=f"Your gold: {balance}  |  Level {player_level}")
    return embed


class ShopView(discord.ui.View):
    """Paginated shop view with category tabs and buy button."""

    def __init__(
        self, user_id: int, sessionmaker, player_level: int, balance: int,
        owned_gear_ids: set[str], equipped_ids: set[str], category: str = "weapons",
    ):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.user_id = user_id
        self.sessionmaker = sessionmaker
        self.player_level = player_level
        self.balance = balance
        self.owned_gear_ids = owned_gear_ids
        self.equipped_ids = equipped_ids
        self.category = category

        # Add category select
        options = []
        for cat in SHOP_CATEGORIES:
            options.append(discord.SelectOption(
                label=SHOP_CATEGORY_LABELS[cat],
                value=cat,
                emoji=SHOP_CATEGORY_EMOJIS[cat],
                default=(cat == category),
            ))
        self.category_select = discord.ui.Select(
            placeholder="Browse category...", options=options, row=0,
        )
        self.category_select.callback = self._category_callback
        self.add_item(self.category_select)

        # Add buy select for current category
        self._add_buy_select()

    def _add_buy_select(self):
        """Add a buy select menu for items in the current category."""
        if self.category == "items":
            items = dungeon_logic.get_shop_items(self.player_level)
            options = []
            for item in items[:25]:
                cost = item.get("cost", 0)
                options.append(discord.SelectOption(
                    label=f"{item['name']} — {cost}g",
                    value=item["id"],
                    description=item.get("description", "")[:100],
                ))
        else:
            gear_list = dungeon_logic.get_shop_gear(self.category, self.player_level)
            options = []
            for g in gear_list[:25]:
                cost = g.get("cost", 0)
                owned = " (owned)" if g["id"] in self.owned_gear_ids or g["id"] in self.equipped_ids else ""
                options.append(discord.SelectOption(
                    label=f"{g['name']} — {cost}g{owned}",
                    value=g["id"],
                    description=g.get("description", "")[:100],
                ))

        if not options:
            return

        self.buy_select = discord.ui.Select(
            placeholder="Buy an item...", options=options, row=1,
        )
        self.buy_select.callback = self._buy_callback
        self.add_item(self.buy_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your shop!", ephemeral=True
            )
            return False
        return True

    async def _category_callback(self, interaction: discord.Interaction):
        self.category = self.category_select.values[0]
        # Rebuild the view with the new category — reload balance
        async with self.sessionmaker() as session:
            wallet = await wallet_repo.get_wallet(session, self.user_id, interaction.guild.id if interaction.guild else 0)
            self.balance = wallet.balance if wallet else 0
            gear_inv = await dungeon_repo.get_player_gear(session, self.user_id, interaction.guild.id if interaction.guild else 0)
            self.owned_gear_ids = {g.gear_id for g in gear_inv}
            player = await dungeon_repo.get_player(session, self.user_id, interaction.guild.id if interaction.guild else 0)
            if player:
                self.equipped_ids = {
                    gid for gid in [player.weapon_id, player.armor_id, player.accessory_id] if gid
                }

        # Rebuild view
        new_view = ShopView(
            self.user_id, self.sessionmaker, self.player_level, self.balance,
            self.owned_gear_ids, self.equipped_ids, self.category,
        )
        embed = _build_shop_embed(
            self.category, self.player_level, self.balance,
            self.owned_gear_ids, self.equipped_ids,
        )
        await interaction.response.edit_message(embed=embed, view=new_view)

    async def _buy_callback(self, interaction: discord.Interaction):
        item_id = self.buy_select.values[0]
        guild_id = interaction.guild.id if interaction.guild else 0

        async with self.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(session, self.user_id, guild_id)
            wallet = await wallet_repo.get_wallet(session, self.user_id, guild_id)
            balance = wallet.balance if wallet else 0

            if self.category == "items":
                item_def = dungeon_logic.get_item_by_id(item_id)
                if item_def is None:
                    await interaction.response.send_message("Item not found.", ephemeral=True)
                    return
                cost = item_def.get("cost", 0)
                if balance < cost:
                    await interaction.response.send_message(
                        f"Not enough gold! You need **{cost}g** but have **{balance}g**.",
                        ephemeral=True,
                    )
                    return
                wallet.balance -= cost
                await dungeon_repo.add_item(session, self.user_id, guild_id, item_id)
                self.balance = wallet.balance
                confirm_msg = f"Purchased **{item_def['name']}** for **{cost}g**!"
            else:
                gear_def = dungeon_logic.get_gear_by_id(item_id)
                if gear_def is None:
                    await interaction.response.send_message("Gear not found.", ephemeral=True)
                    return
                cost = gear_def.get("cost", 0)
                if balance < cost:
                    await interaction.response.send_message(
                        f"Not enough gold! You need **{cost}g** but have **{balance}g**.",
                        ephemeral=True,
                    )
                    return
                # Check if already owned or equipped
                equipped_ids = {
                    gid for gid in [player.weapon_id, player.armor_id, player.accessory_id] if gid
                }
                already_has = await dungeon_repo.has_gear(session, self.user_id, guild_id, item_id)
                if already_has or item_id in equipped_ids:
                    await interaction.response.send_message(
                        f"You already own **{gear_def['name']}**!", ephemeral=True
                    )
                    return

                wallet.balance -= cost
                await dungeon_repo.add_gear(session, self.user_id, guild_id, item_id)
                await session.commit()
                self.balance = wallet.balance
                self.owned_gear_ids.add(item_id)
                confirm_msg = (
                    f"Purchased **{gear_def['name']}** for **{cost}g**! "
                    f"Use `/dungeon inventory` to equip it."
                )

            # Refresh the shop embed with updated balance and ownership
            embed = _build_shop_embed(
                self.category, self.player_level, self.balance,
                self.owned_gear_ids, self.equipped_ids,
            )
            new_view = ShopView(
                self.user_id, self.sessionmaker, self.player_level, self.balance,
                self.owned_gear_ids, self.equipped_ids, self.category,
            )
            await interaction.response.edit_message(embed=embed, view=new_view)
            await interaction.followup.send(confirm_msg, ephemeral=True)


class InventoryView(discord.ui.View):
    """View for managing equipment — equip/unequip gear."""

    def __init__(self, user_id: int, sessionmaker, player, gear_inventory: list, item_inventory: list):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.user_id = user_id
        self.sessionmaker = sessionmaker

        # Build equip select from owned (unequipped) gear
        equip_options = []
        for pg in gear_inventory[:25]:
            gear_def = dungeon_logic.get_gear_by_id(pg.gear_id)
            if gear_def is None:
                continue
            slot = dungeon_logic.get_gear_slot(pg.gear_id)
            slot_label = f"[{slot}]" if slot else ""
            equip_options.append(discord.SelectOption(
                label=f"{gear_def['name']} {slot_label}",
                value=pg.gear_id,
                description=gear_def.get("description", "")[:100],
            ))

        if equip_options:
            self.equip_select = discord.ui.Select(
                placeholder="Equip gear...", options=equip_options, row=0,
            )
            self.equip_select.callback = self._equip_callback
            self.add_item(self.equip_select)

        # Build unequip buttons for equipped items
        equipped = []
        if player.weapon_id:
            equipped.append(("weapon", player.weapon_id))
        if player.armor_id:
            equipped.append(("armor", player.armor_id))
        if player.accessory_id:
            equipped.append(("accessory", player.accessory_id))

        if equipped:
            unequip_options = []
            for slot, gid in equipped:
                gear_def = dungeon_logic.get_gear_by_id(gid)
                name = gear_def["name"] if gear_def else gid
                unequip_options.append(discord.SelectOption(
                    label=f"Unequip {name} ({slot})",
                    value=f"{slot}:{gid}",
                ))
            self.unequip_select = discord.ui.Select(
                placeholder="Unequip gear...", options=unequip_options, row=1,
            )
            self.unequip_select.callback = self._unequip_callback
            self.add_item(self.unequip_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your inventory!", ephemeral=True
            )
            return False
        return True

    async def _equip_callback(self, interaction: discord.Interaction):
        gear_id = self.equip_select.values[0]
        guild_id = interaction.guild.id if interaction.guild else 0

        async with self.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(session, self.user_id, guild_id)
            gear_def = dungeon_logic.get_gear_by_id(gear_id)
            if gear_def is None:
                await interaction.response.send_message("Gear not found.", ephemeral=True)
                return

            slot = dungeon_logic.get_gear_slot(gear_id)
            if slot is None:
                await interaction.response.send_message("Unknown gear slot.", ephemeral=True)
                return

            # Check stat requirements
            meets_req, reason = dungeon_logic.check_stat_requirement(
                gear_id, player.strength, player.dexterity,
            )
            if not meets_req:
                await interaction.response.send_message(
                    f"You can't equip **{gear_def['name']}**! {reason}",
                    ephemeral=True,
                )
                return

            # Get the currently equipped item in that slot
            slot_attr = f"{slot}_id"
            old_equipped_id = getattr(player, slot_attr)

            # Remove new gear from inventory
            removed = await dungeon_repo.remove_gear(session, self.user_id, guild_id, gear_id)
            if not removed:
                await interaction.response.send_message("You don't have that gear!", ephemeral=True)
                return

            # If there was something equipped, put it back in inventory
            if old_equipped_id:
                await dungeon_repo.add_gear(session, self.user_id, guild_id, old_equipped_id)

            # Equip the new gear
            setattr(player, slot_attr, gear_id)
            await session.commit()

            old_name = ""
            if old_equipped_id:
                old_def = dungeon_logic.get_gear_by_id(old_equipped_id)
                old_name = f" (unequipped **{old_def['name']}**)" if old_def else ""

            # Rebuild inventory
            gear_inv = await dungeon_repo.get_player_gear(session, self.user_id, guild_id)
            item_inv = await dungeon_repo.get_player_items(session, self.user_id, guild_id)
            await session.refresh(player)

        embed = _build_inventory_embed(player, gear_inv, item_inv, interaction.user.display_name)
        new_view = InventoryView(self.user_id, self.sessionmaker, player, gear_inv, item_inv)
        await interaction.response.edit_message(embed=embed, view=new_view)

    async def _unequip_callback(self, interaction: discord.Interaction):
        value = self.unequip_select.values[0]
        slot, gear_id = value.split(":", 1)
        guild_id = interaction.guild.id if interaction.guild else 0

        async with self.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(session, self.user_id, guild_id)

            slot_attr = f"{slot}_id"
            if getattr(player, slot_attr) != gear_id:
                await interaction.response.send_message("That item isn't equipped!", ephemeral=True)
                return

            # Move to inventory
            await dungeon_repo.add_gear(session, self.user_id, guild_id, gear_id)
            setattr(player, slot_attr, None)
            await session.commit()

            gear_inv = await dungeon_repo.get_player_gear(session, self.user_id, guild_id)
            item_inv = await dungeon_repo.get_player_items(session, self.user_id, guild_id)
            await session.refresh(player)

        gear_def = dungeon_logic.get_gear_by_id(gear_id)
        name = gear_def["name"] if gear_def else gear_id
        embed = _build_inventory_embed(player, gear_inv, item_inv, interaction.user.display_name)
        new_view = InventoryView(self.user_id, self.sessionmaker, player, gear_inv, item_inv)
        await interaction.response.edit_message(embed=embed, view=new_view)


def _build_inventory_embed(player, gear_inventory, item_inventory, display_name: str) -> discord.Embed:
    """Build the inventory embed showing equipped gear and owned items."""
    embed = discord.Embed(
        title=f"Inventory — {display_name}",
        color=EMBED_COLOR,
    )

    # Equipped gear
    weapon_name = _gear_display(player.weapon_id, "Fists (1d4)")
    armor_name = _gear_display(player.armor_id, "None")
    accessory_name = _gear_display(player.accessory_id, "None")
    equipped_text = (
        f"\u2694\ufe0f **Weapon:** {weapon_name}\n"
        f"\U0001f6e1\ufe0f **Armor:** {armor_name}\n"
        f"\U0001f48d **Accessory:** {accessory_name}"
    )
    embed.add_field(name="Equipped", value=equipped_text, inline=False)

    # Owned (unequipped) gear
    if gear_inventory:
        lines = []
        for pg in gear_inventory:
            gear_def = dungeon_logic.get_gear_by_id(pg.gear_id)
            if gear_def:
                meets, _ = dungeon_logic.check_stat_requirement(
                    pg.gear_id, player.strength, player.dexterity,
                )
                icon = "\u2705" if meets else "\u274c"
                lines.append(f"{icon} {gear_def['name']} ({gear_def.get('rarity', 'common')})")
            else:
                lines.append(f"- {pg.gear_id}")
        embed.add_field(name="Gear Stash", value="\n".join(lines) or "Empty", inline=False)
    else:
        embed.add_field(name="Gear Stash", value="Empty", inline=False)

    # Consumables
    if item_inventory:
        lines = []
        for pi in item_inventory:
            item_def = dungeon_logic.get_item_by_id(pi.item_id)
            name = item_def["name"] if item_def else pi.item_id
            lines.append(f"- {name} x{pi.quantity}")
        embed.add_field(name="Consumables", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Consumables", value="None", inline=False)

    return embed


# ---------------------------------------------------------------------------
# Bestiary
# ---------------------------------------------------------------------------

BESTIARY_PAGE_SIZE = 8


def _build_lore_embed(
    dungeon_data: dict[str, Any],
    owned_fragment_ids: set[int],
    unlock_item_id: str | None,
) -> discord.Embed:
    """Render the player's lore book for a dungeon.

    Found fragments display in full prose. Missing fragments display as
    a stylized gap with the fragment number, so the player sees what's
    still out there.
    """
    fragments = sorted(
        (dungeon_data.get("lore_fragments") or []),
        key=lambda f: int(f.get("id", 0)),
    )
    total = len(fragments)
    found_count = sum(1 for f in fragments if f.get("id") in owned_fragment_ids)
    name = dungeon_data.get("name", "Unknown Dungeon")
    embed = discord.Embed(
        title=f"\U0001f4d6 {name} — Lore Book",
        color=EMBED_COLOR,
    )

    pieces: list[str] = []
    pieces.append(f"_{found_count} / {total} fragments collected_")
    pieces.append("")
    for f in fragments:
        fid = int(f.get("id", 0))
        if fid in owned_fragment_ids:
            text = (f.get("text") or "").strip()
            pieces.append(f"**[{fid}]**  {text}")
        else:
            pieces.append(f"**[{fid}]**  ░░░ unread ░░░")
        pieces.append("")
    # Trim trailing blank.
    while pieces and not pieces[-1]:
        pieces.pop()

    legendary = dungeon_data.get("legendary_reward") or {}
    if legendary.get("item_id"):
        legendary_name = legendary.get("name") or legendary["item_id"]
        if unlock_item_id:
            pieces.append("")
            pieces.append(
                f"_The book is complete. The dungeon's reward — "
                f"**{legendary_name}** — is yours._"
            )
        elif found_count >= total and total > 0:
            pieces.append("")
            pieces.append(
                f"_The book is complete. **{legendary_name}** awaits — "
                f"continue your delves to claim it._"
            )
        elif found_count > 0:
            pieces.append("")
            pieces.append(
                f"_Complete the book to unlock **{legendary_name}**._"
            )

    body = "\n".join(pieces)
    # Discord embed description cap is 4096 chars. Truncate if needed.
    if len(body) > 4000:
        body = body[:3990] + "\n\n_(truncated — fragments overflow)_"
    embed.description = body
    return embed


def _build_bestiary_embed(
    all_monsters: list[dict[str, Any]],
    discovered: dict[str, Any],
    page: int,
) -> discord.Embed:
    total_pages = max(1, -(-len(all_monsters) // BESTIARY_PAGE_SIZE))
    embed = discord.Embed(
        title="\U0001f9df Monster Mash — Bestiary",
        color=EMBED_COLOR,
    )
    start = page * BESTIARY_PAGE_SIZE
    end = start + BESTIARY_PAGE_SIZE
    page_monsters = all_monsters[start:end]

    lines = []
    for m in page_monsters:
        entry = discovered.get(m["id"])
        if entry:
            kills = entry.kill_count
            lines.append(
                f"**{m['name']}** — {kills} kill{'s' if kills != 1 else ''}\n"
                f"> *{m.get('description', '')}*"
            )
        else:
            lines.append("**???** — Not yet discovered")

    discovered_count = sum(1 for m in all_monsters if m["id"] in discovered)
    total_count = len(all_monsters)

    embed.description = "\n".join(lines) or "No monsters to display."
    embed.set_footer(
        text=f"Page {page + 1}/{total_pages} \u2022 {discovered_count}/{total_count} discovered"
    )
    return embed


class BestiaryView(discord.ui.View):
    """Paginated bestiary view."""

    def __init__(self, user_id: int, all_monsters: list, discovered: dict, page: int = 0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.user_id = user_id
        self.all_monsters = all_monsters
        self.discovered = discovered
        self.page = page
        self.total_pages = max(1, -(-len(all_monsters) // BESTIARY_PAGE_SIZE))

        self.prev_btn.disabled = page <= 0
        self.next_btn.disabled = page >= self.total_pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your bestiary!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="\u25c0\ufe0f")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(self.page - 1, 0)
        embed = _build_bestiary_embed(self.all_monsters, self.discovered, self.page)
        new_view = BestiaryView(self.user_id, self.all_monsters, self.discovered, self.page)
        await interaction.response.edit_message(embed=embed, view=new_view)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="\u25b6\ufe0f")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.page + 1, self.total_pages - 1)
        embed = _build_bestiary_embed(self.all_monsters, self.discovered, self.page)
        new_view = BestiaryView(self.user_id, self.all_monsters, self.discovered, self.page)
        await interaction.response.edit_message(embed=embed, view=new_view)


# ---------------------------------------------------------------------------
# Button action handlers
# ---------------------------------------------------------------------------


async def _load_run_context(session, run_id):
    """Load run + player + dungeon data. Returns (run, player, dungeon_data) or None."""
    run = await dungeon_repo.get_run(session, run_id)
    if run is None or not run.active:
        return None
    player = await dungeon_repo.get_player(session, run.user_id, run.guild_id)
    dungeon_data = dungeon_logic.get_dungeon(run.dungeon_id)
    return run, player, dungeon_data


async def _handle_enter_room(interaction, run_id, user_id, sessionmaker):
    """Blindly enter the next room and resolve it immediately."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx
        profile = await rpg_repo.get_or_create_profile(
            session, run.user_id, run.guild_id
        )
        race = profile.race

        rooms = json.loads(run.rooms_json)
        if run.room_index >= len(rooms):
            await interaction.response.send_message("No more rooms on this floor.", ephemeral=True)
            return

        room_data = rooms[run.room_index]
        room_type = room_data["type"]

        if room_type in ("combat", "boss"):
            # Enter combat state. Initialize combat_state_json with picked
            # variant / description / empty effect buckets, and apply variant
            # HP overrides before setting run.monster_*.
            base_monster = room_data["monster"]
            rng = random.Random()
            combat_state = dungeon_effects.initial_combat_state(base_monster, rng)
            combat_state["primary_monster_id"] = base_monster["id"]
            effective_monster = dungeon_logic.apply_variant(
                base_monster, combat_state.get("variant")
            )
            if "description" in combat_state:
                effective_monster["description"] = combat_state["description"]
            state_name = "boss" if room_type == "boss" else "combat"
            run.state = state_name
            run.monster_id = base_monster["id"]
            run.monster_hp = effective_monster["hp"]
            run.monster_max_hp = effective_monster["hp"]
            run.is_defending = False
            run.combat_state_json = json.dumps(combat_state)
            await session.commit()
            await session.refresh(run)

            embed = build_combat_start_embed(run, player, effective_monster, dungeon_data)
            view = CombatView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)

        elif room_type == "treasure":
            # Resolve treasure immediately
            tier = room_data.get("tier", "common")
            gold = dungeon_logic.roll_treasure_gold(
                tier,
                double_roll=get_racial_modifier(race, "dungeon.treasure_double_roll", False),
            )
            run.run_gold += gold
            run.room_index += 1
            run.state = "exploring"
            await session.commit()
            await session.refresh(run)

            embed = build_loot_embed(run, player, gold, [], dungeon_data)
            view = PostRoomView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)

        elif room_type == "trap":
            # Resolve trap immediately
            trap = room_data.get("trap", {})
            trap_dc = trap.get("dex_dc", 12)
            avoided = dungeon_logic.check_trap(
                player.dexterity, trap_dc,
                save_bonus=get_racial_modifier(race, "dungeon.trap_save_bonus", 0),
            )
            damage = 0
            if not avoided:
                damage = dungeon_logic.roll_trap_damage(trap.get("damage", [1, 4]))
                run.current_hp = max(run.current_hp - damage, 0)

            # Stoneblood: Dwarf survives killing blow once per run
            if run.current_hp <= 0 and not run.stoneblood_used and get_racial_modifier(race, "dungeon.stoneblood", False):
                run.current_hp = 1
                run.stoneblood_used = True

            run.room_index += 1
            run.state = "exploring"
            await session.commit()
            await session.refresh(run)

            # Check death from trap
            if run.current_hp <= 0:
                await _process_death(session, run, player, dungeon_data, interaction)
                return

            embed = build_trap_result_embed(run, player, trap, avoided, damage, dungeon_data)
            view = PostRoomView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)

        elif room_type == "rest":
            # Heal at shrine
            heal_frac = get_racial_modifier(race, "dungeon.rest_heal_fraction", dungeon_logic.REST_HEAL_FRACTION)
            heal_amount = int(run.max_hp * heal_frac)
            run.current_hp = min(run.current_hp + heal_amount, run.max_hp)
            run.room_index += 1
            run.state = "exploring"
            await session.commit()
            await session.refresh(run)

            embed = build_rest_embed(run, player, heal_amount, dungeon_data)
            view = PostRoomView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)


def _find_monster_def(dungeon_data: dict[str, Any], def_id: str) -> dict[str, Any] | None:
    """Look up a monster definition by id across all floors of a dungeon."""
    if not def_id:
        return None
    for floor in dungeon_data.get("floors", []) or []:
        for m in floor.get("monsters", []) or []:
            if m.get("id") == def_id:
                return m
        boss = floor.get("boss") or {}
        if boss.get("id") == def_id:
            return boss
    return None


def _spawn_pending_adds(
    combat_state: dict[str, Any],
    dungeon_data: dict[str, Any],
    run,
    narrative: list[str],
) -> None:
    """Materialize any pending-spawn adds from the combat state.

    If any add is flagged ``pending_spawn``, populate its hp/max_hp from
    the monster definition, snapshot the primary into ``state.primary``,
    and swap run.monster_* to the add's stats. Only one add becomes the
    active target at a time — others (if any) queue.
    """
    adds = combat_state.get("adds") or []
    pending = [a for a in adds if a.get("pending_spawn")]
    if not pending:
        return
    for add in pending:
        add_def = _find_monster_def(dungeon_data, add.get("def_id"))
        if add_def is None:
            add["hp"] = 0
            add["max_hp"] = 0
            add["pending_spawn"] = False
            continue
        add["hp"] = int(add_def.get("hp", 1))
        add["max_hp"] = int(add_def.get("hp", 1))
        add["pending_spawn"] = False
        # Assign a unique id
        existing_ids = [a.get("id") for a in adds if a.get("id")]
        idx = 0
        while f"add_{idx}" in existing_ids:
            idx += 1
        add["id"] = f"add_{idx}"

    # If no add is currently active, activate the first live one.
    if combat_state.get("active", "primary") == "primary":
        live = [a for a in adds if a.get("hp", 0) > 0]
        if live:
            new_active = live[0]
            # Snapshot the primary
            combat_state["primary"] = {
                "hp": run.monster_hp,
                "max_hp": run.monster_max_hp,
                "monster_id": run.monster_id,
            }
            run.monster_id = new_active.get("def_id")
            run.monster_hp = int(new_active.get("hp"))
            run.monster_max_hp = int(new_active.get("max_hp"))
            combat_state["active"] = new_active["id"]
            narrative.append(
                f"_A **{new_active.get('def_id', 'creature')}** rises from the page to intercept you!_"
            )


def _swap_back_to_primary(combat_state: dict[str, Any], run) -> None:
    """Restore the primary monster as the active target after an add dies."""
    primary = combat_state.get("primary") or {}
    run.monster_id = primary.get("monster_id") or combat_state.get("primary_monster_id")
    run.monster_hp = int(primary.get("hp", 1))
    run.monster_max_hp = int(primary.get("max_hp", 1))
    combat_state["active"] = "primary"
    combat_state.pop("primary", None)
    combat_state["untargetable_primary"] = False


async def _handle_combat_action(interaction, run_id, user_id, sessionmaker, action: str):
    """Process a combat action (attack, defend, flee).

    Turn resolution contract (attack/defend only; flee short-circuits):

      1. Increment combat_state.turn.
      2. Re-evaluate phase from primary-monster HP (at start of turn only).
      3. Fire monster on_turn abilities — may write into player_effects.
         Handle pending summon target-swap at this point.
      4. Apply bleed ticks; resolve player action using composed modifiers.
      5. Resolve monster action (normal AI OR pending special attack).
         Fire on_hit abilities if the monster landed a hit.
      6. Decrement effect durations; remove consumed single-use flags.
      7. Check death / phase-change outcomes. If an ADD died, swap back
         to primary and keep combat going. If PRIMARY died, run the
         normal kill flow (XP, loot, bestiary).
    """
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx
        profile = await rpg_repo.get_or_create_profile(
            session, run.user_id, run.guild_id
        )
        race = profile.race

        narrative: list[str] = []

        # Load / initialize combat state. Legacy runs from before this
        # column existed will have "{}" or an empty string.
        try:
            combat_state = json.loads(run.combat_state_json or "{}")
        except (json.JSONDecodeError, TypeError):
            combat_state = {}

        # Resolve the base monster definition. v1 reads from rooms_json
        # (which v2 doesn't populate); v2 looks up by run.monster_id in
        # the current floor's monster pool. The combat handler needs the
        # full def either way — for variant/phase merging, attack dice,
        # XP, loot, etc.
        base_monster: dict[str, Any] | None = None
        if combat_state.get("return_to") == "v2_explore":
            floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
            primary_id = (
                combat_state.get("primary_monster_id") or run.monster_id
            )
            base_monster = dungeon_explore.find_floor_monster(floor_data, primary_id)
        else:
            rooms = json.loads(run.rooms_json or "[]")
            if 0 <= run.room_index < len(rooms):
                room_data = rooms[run.room_index]
                base_monster = room_data.get("monster")

        if base_monster is None:
            await interaction.response.send_message(
                "Combat is in an inconsistent state — the monster definition could not "
                "be loaded. Please use `/dungeon abandon` and start over.",
                ephemeral=True,
            )
            return

        if not combat_state:
            init_rng = random.Random()
            combat_state = dungeon_effects.initial_combat_state(base_monster, init_rng)
            combat_state["primary_monster_id"] = base_monster["id"]

        # Effective monster for display / base stats (variant + description).
        base_with_variant = dungeon_logic.apply_variant(base_monster, combat_state.get("variant"))
        if "description" in combat_state:
            base_with_variant["description"] = combat_state["description"]

        if action == "flee":
            fled = dungeon_logic.check_flee(
                player.dexterity,
                flee_dc=get_racial_modifier(race, "dungeon.flee_dc", dungeon_logic.FLEE_BASE_DC),
            )
            if fled:
                narrative.append("You turn and run! You escape the fight.")
                # V2 dispatch: route flee back to the explore loop instead
                # of the v1 rooms_json path.
                if combat_state.get("return_to") == "v2_explore":
                    run.state = "exploring"
                    run.monster_id = None
                    run.monster_hp = 0
                    run.monster_max_hp = 0
                    run.combat_state_json = "{}"
                    floor_state = dungeon_explore.load_floor_state(run.floor_state_json)
                    cur = floor_state.get("current")
                    if cur is not None:
                        rs = floor_state.setdefault("room_states", {}).setdefault(cur, {})
                        rs["encounter_resolved"] = True
                    run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
                    await session.commit()
                    await session.refresh(run)
                    floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
                    embed = _build_v2_explore_embed(
                        run, player, dungeon_data, floor_state, narrative=narrative,
                    )
                    view = _v2_render_view(
                        run_id, user_id, sessionmaker, floor_state, floor_data,
                    )
                    await interaction.response.edit_message(embed=embed, view=view)
                    return

                run.room_index += 1
                run.state = "exploring"
                run.monster_id = None
                run.monster_hp = 0
                run.monster_max_hp = 0
                run.combat_state_json = "{}"
                await session.commit()
                await session.refresh(run)

                embed = build_combat_embed(run, player, base_with_variant, narrative, dungeon_data)
                view = PostRoomView(run_id, user_id, sessionmaker)
                await interaction.response.edit_message(embed=embed, view=view)
                return
            else:
                narrative.append("You try to flee but can't escape!")
                # Monster gets a free hit — use currently-active entity stats.
                active_def = base_with_variant
                active_id = combat_state.get("active", "primary")
                if active_id != "primary":
                    for a in combat_state.get("adds") or []:
                        if a.get("id") == active_id:
                            add_def = _find_monster_def(dungeon_data, a.get("def_id")) or active_def
                            active_def = add_def
                            break
                mon_dmg, _ = dungeon_logic.calc_monster_damage(
                    active_def["attack_dice"], active_def.get("attack_bonus", 0),
                    dungeon_logic.get_armor_defense(player.armor_id), False,
                )
                run.current_hp = max(run.current_hp - mon_dmg, 0)
                narrative.append(f"The {active_def['name']} strikes you for **{mon_dmg}** damage!")

                if run.current_hp <= 0:
                    await session.commit()
                    await session.refresh(run)
                    await _process_death(session, run, player, dungeon_data, interaction)
                    return

                run.is_defending = False
                run.combat_state_json = json.dumps(combat_state)
                await session.commit()
                await session.refresh(run)

                embed = build_combat_embed(run, player, base_with_variant, narrative, dungeon_data)
                view = CombatView(run_id, user_id, sessionmaker)
                await interaction.response.edit_message(embed=embed, view=view)
                return

        # =============================================================
        # Attack / defend resolution — follow the 7-step contract above.
        # =============================================================

        is_defending = action == "defend"

        # --- Step 1: increment turn counter ---
        combat_state["turn"] = int(combat_state.get("turn", 0)) + 1
        turn = combat_state["turn"]

        # --- Step 2: re-evaluate phase from PRIMARY HP (ignoring adds) ---
        if combat_state.get("active", "primary") == "primary":
            primary_hp = run.monster_hp
            primary_max = run.monster_max_hp
        else:
            snap = combat_state.get("primary") or {}
            primary_hp = int(snap.get("hp", run.monster_hp))
            primary_max = int(snap.get("max_hp", run.monster_max_hp))
        phases = base_monster.get("phases")
        new_phase = dungeon_logic.compute_phase(primary_hp, primary_max, phases)
        old_phase = int(combat_state.get("phase", 0))
        if new_phase != old_phase:
            combat_state["phase"] = new_phase
            phase_def = dungeon_logic.get_phase_def(base_monster, new_phase)
            if phase_def:
                on_enter = phase_def.get("on_enter") or {}
                if on_enter.get("text"):
                    narrative.append(on_enter["text"])

        # Compute the effective primary (variant + phase overrides).
        effective_primary = dungeon_logic.merge_phase_overrides(base_with_variant, new_phase)

        # Determine current active target (primary vs add).
        active_id = combat_state.get("active", "primary")
        active_def = effective_primary
        if active_id != "primary":
            for a in combat_state.get("adds") or []:
                if a.get("id") == active_id:
                    found = _find_monster_def(dungeon_data, a.get("def_id"))
                    if found is not None:
                        active_def = found
                    break

        # --- Step 3: fire monster on_turn abilities ---
        # Abilities come from the effective primary (not adds — adds stay simple).
        abilities = list(effective_primary.get("abilities") or [])
        fx_ctx = dungeon_effects.EncounterCtx(
            state=combat_state,
            monster_def=active_def,
            monster_hp=run.monster_hp,
            monster_max_hp=run.monster_max_hp,
            turn=turn,
            phase=new_phase,
            rng=random.Random(),
            narrative=narrative,
        )
        for ability in abilities:
            if dungeon_effects.should_trigger(
                ability,
                turn=turn,
                monster_hp=primary_hp,
                monster_max_hp=primary_max,
                state=combat_state,
            ):
                dungeon_effects.dispatch(fx_ctx, ability)
                if ability.get("trigger") == "on_hp_below_pct":
                    dungeon_effects.mark_hp_trigger_fired(ability, combat_state)

        # Materialize any adds that were just summoned this turn.
        _spawn_pending_adds(combat_state, dungeon_data, run, narrative)

        # Re-resolve the active target after possible summon swap.
        active_id = combat_state.get("active", "primary")
        if active_id != "primary":
            for a in combat_state.get("adds") or []:
                if a.get("id") == active_id:
                    found = _find_monster_def(dungeon_data, a.get("def_id"))
                    if found is not None:
                        active_def = found
                    break

        # --- Step 4: bleed tick, then resolve player action ---
        bleed_dmg = dungeon_resolver.resolve_bleed_damage(combat_state)
        if bleed_dmg > 0:
            run.current_hp = max(run.current_hp - bleed_dmg, 0)
            narrative.append(f"_Bleed deals **{bleed_dmg}** damage._")

        player_dmg = 0
        was_crit = False
        if action == "attack":
            d20 = dungeon_logic.roll_d20()
            was_crit = dungeon_logic.is_crit(
                d20,
                crit_threshold=get_racial_modifier(race, "dungeon.crit_threshold", 20),
            )
            crit_bonus = dungeon_logic.get_crit_bonus(player.accessory_id)
            if not was_crit and crit_bonus > 0:
                was_crit = d20 >= (20 - crit_bonus)
            weapon_dice = dungeon_logic.get_weapon_dice(player.weapon_id)
            weapon_bonus = dungeon_logic.get_weapon_bonus(player.weapon_id)
            str_mod = dungeon_logic.get_modifier(player.strength)
            p_mods = dungeon_resolver.resolve_player_attack_mods(
                race=race,
                run_current_hp=run.current_hp,
                run_max_hp=run.max_hp,
                state=combat_state,
            )
            effective_dice = dungeon_resolver.bump_dice(weapon_dice, p_mods.weapon_dice_step)
            # Monster defense includes any active-effect bonus (e.g. Alaric's wall
            # applies only to the primary; adds don't inherit it).
            target_defense = int(active_def.get("defense", 0))
            if active_id == "primary":
                target_defense += dungeon_resolver.resolve_monster_defense_bonus(combat_state)

            # Disadvantage overrides advantage: roll twice keep lower.
            if p_mods.damage_disadvantage:
                dmg_a, _ = dungeon_logic.calc_player_damage(
                    effective_dice, str_mod, weapon_bonus, target_defense, was_crit,
                    bonus_penalty=p_mods.bonus_penalty,
                    damage_bonus=p_mods.damage_bonus,
                )
                dmg_b, _ = dungeon_logic.calc_player_damage(
                    effective_dice, str_mod, weapon_bonus, target_defense, was_crit,
                    bonus_penalty=p_mods.bonus_penalty,
                    damage_bonus=p_mods.damage_bonus,
                )
                player_dmg = min(dmg_a, dmg_b)
            else:
                player_dmg, _ = dungeon_logic.calc_player_damage(
                    effective_dice, str_mod, weapon_bonus, target_defense, was_crit,
                    damage_advantage=p_mods.damage_advantage,
                    bonus_penalty=p_mods.bonus_penalty,
                    damage_bonus=p_mods.damage_bonus,
                )
            # Single-use flag cleanup. Durations tick at end of turn but the
            # advantage/invert flags specifically are "until consumed" — remove
            # now so they don't linger.
            dungeon_effects.consume_player_flag(combat_state, "advantage_next_attack")
            dungeon_effects.consume_player_flag(combat_state, "invert_next_attack")

            crit_text = " **CRITICAL HIT!**" if was_crit else ""
            narrative.append(f"You strike the {active_def['name']} for **{player_dmg}** damage!{crit_text}")
        else:
            narrative.append("You raise your guard and brace for impact.")

        # --- Step 5: monster action ---
        mon_dmg = 0
        special = combat_state.pop("_pending_special_attack", None)
        player_armor = dungeon_logic.get_armor_defense(player.armor_id)
        p_def = dungeon_resolver.resolve_player_defense_mods(combat_state)
        m_atk_mods = dungeon_resolver.resolve_monster_attack_mods(state=combat_state)

        if special is not None:
            kind = special.get("kind", "special")
            atk_dice = special.get("damage_dice", "2d6")
            extra_bonus = int(special.get("damage_bonus", 0))
            defense_ignore = int(special.get("defense_ignore", 0))
            effective_armor = max(0, player_armor - defense_ignore)
            raw_dmg, _ = dungeon_logic.calc_monster_damage(
                atk_dice, extra_bonus, effective_armor, is_defending,
            )
            mon_dmg = max(1, raw_dmg + m_atk_mods.flat_damage_bonus)
            if p_def.hit_chance_multiplier < 1.0 and random.random() > p_def.hit_chance_multiplier:
                mon_dmg = 0
                narrative.append(f"The {active_def['name']}'s attack unravels in the fog.")
            else:
                fallback_text = {
                    "existential_strike": f"The {active_def['name']} lifts its quill — you feel yourself being flattened, simplified, labeled. **{mon_dmg}** damage!",
                    "redraw_strike": f"The {active_def['name']} redraws the room around you for **{mon_dmg}** damage!",
                }.get(kind, f"A devastating strike lands for **{mon_dmg}** damage!")
                text = special.get("text") or fallback_text
                narrative.append(text)
        else:
            ai_weights = active_def.get("ai") or {"attack": 70, "heavy": 30}
            mon_action = dungeon_logic.select_monster_action(ai_weights)
            if mon_action in ("attack", "heavy"):
                base_atk_dice = active_def.get("attack_dice", "1d4")
                effective_atk_dice = dungeon_resolver.bump_dice(
                    base_atk_dice, m_atk_mods.attack_dice_step
                )
                raw_dmg, _ = dungeon_logic.calc_monster_damage(
                    effective_atk_dice, active_def.get("attack_bonus", 0),
                    player_armor, is_defending,
                )
                if mon_action == "heavy":
                    raw_dmg = int(raw_dmg * 1.5)
                mon_dmg = max(1, raw_dmg + m_atk_mods.flat_damage_bonus)
                if p_def.hit_chance_multiplier < 1.0 and random.random() > p_def.hit_chance_multiplier:
                    mon_dmg = 0
                    narrative.append(f"The {active_def['name']}'s attack dissipates in the fog.")
                else:
                    if mon_action == "heavy":
                        narrative.append(f"The {active_def['name']} unleashes a heavy attack for **{mon_dmg}** damage!")
                    else:
                        narrative.append(f"The {active_def['name']} attacks you for **{mon_dmg}** damage!")
                # Fire on_hit abilities when the monster lands damage.
                if mon_dmg > 0:
                    for ability in abilities:
                        if ability.get("trigger") == "on_hit":
                            dungeon_effects.dispatch(fx_ctx, ability)
            elif mon_action == "defend":
                narrative.append(f"The {active_def['name']} braces defensively.")
                if player_dmg > 0:
                    player_dmg = max(player_dmg // 2, 1)
                    # Rewrite the prior "You strike..." line to show the block.
                    if narrative and narrative[-2].startswith("You strike"):
                        narrative[-2] = (
                            f"You strike the {active_def['name']} for **{player_dmg}** damage! (blocked)"
                        )

        # Apply damage simultaneously.
        run.monster_hp = max(run.monster_hp - player_dmg, 0)
        run.current_hp = max(run.current_hp - mon_dmg, 0)
        run.is_defending = is_defending

        # Any pending self-heal from an effect atom (variant on_turn_effect, etc.).
        pending_heal = int(combat_state.pop("_pending_self_heal", 0))
        if pending_heal > 0:
            run.monster_hp = min(run.monster_hp + pending_heal, run.monster_max_hp)

        # Stoneblood
        if (
            run.current_hp <= 0
            and not run.stoneblood_used
            and get_racial_modifier(race, "dungeon.stoneblood", False)
        ):
            run.current_hp = 1
            run.stoneblood_used = True
            narrative.append(
                "**Stoneblood!** You refuse to fall — sheer dwarven stubbornness keeps you on your feet at 1 HP!"
            )

        # --- Step 6: tick effect durations ---
        dungeon_effects.tick_effects(combat_state)

        # --- Step 7: outcomes ---
        monster_dead = run.monster_hp <= 0
        player_dead = run.current_hp <= 0

        # If the ACTIVE entity is an add and it just died, swap back to primary
        # and continue combat. Adds don't award XP / loot.
        if monster_dead and combat_state.get("active", "primary") != "primary":
            # Mark add as dead in state
            for a in combat_state.get("adds") or []:
                if a.get("id") == combat_state.get("active"):
                    a["hp"] = 0
                    break
            narrative.append(f"_The {active_def['name']} is banished back to the parchment._")
            _swap_back_to_primary(combat_state, run)
            # Primary is alive — combat continues.
            monster_dead = False

        # Persist combat state before committing / branching.
        run.combat_state_json = json.dumps(combat_state)

        if monster_dead and player_dead:
            narrative.append(f"\nThe {active_def['name']} falls... but so do you.")
            await session.commit()
            await session.refresh(run)
            await _process_death(session, run, player, dungeon_data, interaction, narrative)
            return

        if player_dead:
            narrative.append(f"\nThe {active_def['name']} strikes you down!")
            await session.commit()
            await session.refresh(run)
            await _process_death(session, run, player, dungeon_data, interaction, narrative)
            return

        if monster_dead:
            # Primary died — normal kill flow. Reward based on the BASE monster
            # definition (so variant display doesn't affect XP/loot).
            on_death = base_monster.get("on_death_narration")
            if on_death:
                narrative.append(f"\n{on_death}")
            else:
                narrative.append(f"\nThe **{base_with_variant['name']}** is defeated!")
            xp_gained = base_monster.get("xp", 0)
            gold_range = base_monster.get("gold", [0, 0])
            gold_gained = dungeon_logic.roll_monster_gold(gold_range)
            loot_drops = dungeon_logic.roll_loot_drops(
                base_monster.get("loot", []),
                loot_chance_bonus=get_racial_modifier(race, "dungeon.loot_chance_bonus", 0),
            )

            run.run_xp += xp_gained
            run.run_gold += gold_gained

            found_items = json.loads(run.found_items_json)
            cross_game_drops: list[dict[str, Any]] = []
            regular_drops: list[dict[str, Any]] = []

            for drop in loot_drops:
                drop_type = drop.get("type", "")
                if drop_type == "cross_game_bait":
                    if _HAS_FISHING:
                        await fishing_repo.add_bait(
                            session, run.user_id, run.guild_id,
                            drop["item_id"], 1,
                        )
                        cross_game_drops.append(drop)
                elif drop_type == "cross_game_ingredient":
                    if _HAS_BREWING:
                        ingredient = await brewing_repo.get_ingredient_by_name(
                            session, drop["item_id"],
                        )
                        if ingredient:
                            await brewing_repo.add_player_ingredient(
                                session, run.user_id, run.guild_id,
                                ingredient.id, 1,
                            )
                            cross_game_drops.append(drop)
                else:
                    if drop.get("type") == "gear":
                        gear_def = dungeon_logic.get_gear_by_id(drop["item_id"])
                        equipped_ids = {
                            gid for gid in [player.weapon_id, player.armor_id, player.accessory_id] if gid
                        }
                        already_equipped = drop["item_id"] in equipped_ids
                        already_in_stash = await dungeon_repo.has_gear(
                            session, run.user_id, run.guild_id, drop["item_id"]
                        )
                        already_found = any(
                            fi.get("item_id") == drop["item_id"] for fi in found_items
                        )
                        if already_equipped or already_in_stash or already_found:
                            sell_gold = (gear_def.get("cost", 0) // 4) if gear_def else 0
                            run.run_gold += sell_gold
                            gold_gained += sell_gold
                            drop["duplicate"] = True
                            drop["sell_gold"] = sell_gold
                            regular_drops.append(drop)
                            continue

                    found_items.append(drop)
                    regular_drops.append(drop)

            run.found_items_json = json.dumps(found_items)

            now = datetime.now(timezone.utc)
            await dungeon_repo.upsert_bestiary_entry(
                session, run.user_id, run.guild_id, base_monster["id"], now,
            )

            player.total_kills += 1

            # V2 dispatch: if combat was started by the v2 explore loop,
            # hand off to the v2 post-combat path instead of v1's
            # rooms_json/room_index advancement. The XP/loot/bestiary
            # work above is identical for both.
            if combat_state.get("return_to") == "v2_explore":
                v2_was_boss = (
                    combat_state.get("combat_kind") == "boss"
                    or run.state == "boss"
                )
                if v2_was_boss and run.floor > player.deepest_floor:
                    player.deepest_floor = run.floor
                await _v2_post_combat_return(
                    interaction, session, sessionmaker, run, player, dungeon_data,
                    was_boss=v2_was_boss,
                )
                return

            is_boss = run.state == "boss"
            run.monster_id = None
            run.monster_hp = 0
            run.monster_max_hp = 0
            run.room_index += 1
            run.combat_state_json = "{}"  # Clear for next encounter.

            if is_boss:
                run.state = "floor_complete"
                if run.floor > player.deepest_floor:
                    player.deepest_floor = run.floor
                await session.commit()
                await session.refresh(run)

                embed = build_floor_complete_embed(run, player, dungeon_data)
                max_floor = dungeon_logic.get_max_floor(dungeon_data)
                can_descend = run.floor < max_floor
                view = FloorCompleteView(run_id, user_id, sessionmaker, can_descend)
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                run.state = "exploring"
                await session.commit()
                await session.refresh(run)

                embed = build_loot_embed(run, player, gold_gained, regular_drops, dungeon_data, cross_game_drops)
                view = PostRoomView(run_id, user_id, sessionmaker)
                await interaction.response.edit_message(embed=embed, view=view)
            return

        # Combat continues.
        await session.commit()
        await session.refresh(run)

        # Build the display monster — use active_def for the current target
        # (may be an add), with its display name reflecting variant/phase.
        display_monster = active_def if combat_state.get("active", "primary") != "primary" else effective_primary
        embed = build_combat_embed(
            run, player, display_monster, narrative, dungeon_data,
            combat_state=combat_state,
        )
        view = CombatView(run_id, user_id, sessionmaker)
        await interaction.response.edit_message(embed=embed, view=view)


async def _handle_use_item(interaction, run_id, user_id, sessionmaker):
    """Show item selection menu (found items + persistent inventory)."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx

        # Merge found consumables + persistent inventory
        found_items = json.loads(run.found_items_json)
        found_consumables = [i for i in found_items if i.get("type") != "gear"]
        persistent_items = await dungeon_repo.get_player_items(session, run.user_id, run.guild_id)

        # Build unified list: [{item_id, source: "found"|"inventory"}]
        consumables = []
        for fc in found_consumables:
            consumables.append({"item_id": fc["item_id"], "source": "found"})
        for pi in persistent_items:
            for _ in range(pi.quantity):
                consumables.append({"item_id": pi.item_id, "source": "inventory"})

        if not consumables:
            await interaction.response.send_message(
                "You don't have any items to use!", ephemeral=True
            )
            return

        # Deduplicate for display: show unique item_ids with source preference
        seen = {}
        display_items = []
        for c in consumables:
            key = c["item_id"]
            if key not in seen:
                seen[key] = c
                display_items.append(c)

        view = ItemSelectView(run_id, user_id, sessionmaker, display_items)
        await interaction.response.send_message(
            "Choose an item to use:", view=view, ephemeral=True
        )


async def _handle_use_item_selected(interaction, run_id, user_id, sessionmaker, item_id: str):
    """Process using a selected consumable item."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx

        item_def = dungeon_logic.get_item_by_id(item_id)
        if item_def is None:
            await interaction.response.send_message("Item not found.", ephemeral=True)
            return

        # Try to remove from found_items first, then from persistent inventory
        found_items = json.loads(run.found_items_json)
        removed = False
        for i, fi in enumerate(found_items):
            if fi.get("item_id") == item_id and fi.get("type") != "gear":
                found_items.pop(i)
                removed = True
                break

        if not removed:
            # Try persistent inventory
            removed = await dungeon_repo.remove_item(
                session, run.user_id, run.guild_id, item_id
            )

        if not removed:
            await interaction.response.send_message("You don't have that item!", ephemeral=True)
            return

        # Apply item effect
        effect = item_def.get("effect", {})
        effect_type = effect.get("type", "")
        narrative = []
        in_combat = run.state in ("combat", "boss") and run.monster_id is not None

        if effect_type == "heal":
            heal = effect.get("value", 0)
            old_hp = run.current_hp
            run.current_hp = min(run.current_hp + heal, run.max_hp)
            actual_heal = run.current_hp - old_hp
            narrative.append(f"You use **{item_def['name']}** and recover **{actual_heal}** HP!")
        elif effect_type == "guaranteed_flee":
            # Mark for guaranteed flee on next attempt
            narrative.append(f"You throw down a **{item_def['name']}**! Smoke fills the room.")
            run.room_index += 1
            run.state = "exploring"
            run.monster_id = None
            run.monster_hp = 0
            run.monster_max_hp = 0
            in_combat = False  # escaped — monster gets no turn

        # Using an item in combat costs your action — monster takes its turn
        if in_combat:
            profile = await rpg_repo.get_or_create_profile(
                session, run.user_id, run.guild_id
            )
            race = profile.race
            try:
                combat_state_local = json.loads(run.combat_state_json or "{}")
            except (json.JSONDecodeError, TypeError):
                combat_state_local = {}
            # v2 looks up the monster by id from the floor pool; v1 reads
            # from rooms_json[room_index].
            base_monster: dict[str, Any] | None = None
            if combat_state_local.get("return_to") == "v2_explore":
                floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
                primary_id = (
                    combat_state_local.get("primary_monster_id") or run.monster_id
                )
                base_monster = dungeon_explore.find_floor_monster(floor_data, primary_id)
            else:
                rooms = json.loads(run.rooms_json or "[]")
                if 0 <= run.room_index < len(rooms):
                    base_monster = rooms[run.room_index].get("monster")
            if base_monster is None:
                # Fall back to a stub so the use_item flow still resolves
                # without crashing — the monster turn will be a noop.
                base_monster = {"id": run.monster_id or "?", "name": "?", "attack_dice": "1d4"}
            # Use the variant/phase-adjusted monster for display + damage.
            monster = dungeon_logic.apply_variant(base_monster, combat_state_local.get("variant"))
            phase_idx = int(combat_state_local.get("phase", 0))
            monster = dungeon_logic.merge_phase_overrides(monster, phase_idx)
            m_atk_mods = dungeon_resolver.resolve_monster_attack_mods(state=combat_state_local)

            ai_weights = monster.get("ai", {"attack": 70, "heavy": 30})
            mon_action = dungeon_logic.select_monster_action(ai_weights)
            mon_dmg = 0
            if mon_action == "attack":
                base_dice = monster.get("attack_dice", "1d4")
                eff_dice = dungeon_resolver.bump_dice(base_dice, m_atk_mods.attack_dice_step)
                raw_dmg, _ = dungeon_logic.calc_monster_damage(
                    eff_dice, monster.get("attack_bonus", 0),
                    dungeon_logic.get_armor_defense(player.armor_id), False,
                )
                mon_dmg = max(1, raw_dmg + m_atk_mods.flat_damage_bonus)
                narrative.append(f"The {monster['name']} attacks you for **{mon_dmg}** damage!")
            elif mon_action == "heavy":
                base_dice = monster.get("attack_dice", "1d4")
                eff_dice = dungeon_resolver.bump_dice(base_dice, m_atk_mods.attack_dice_step)
                heavy_dmg, _ = dungeon_logic.calc_monster_damage(
                    eff_dice, monster.get("attack_bonus", 0),
                    dungeon_logic.get_armor_defense(player.armor_id), False,
                )
                mon_dmg = max(1, int(heavy_dmg * 1.5) + m_atk_mods.flat_damage_bonus)
                narrative.append(f"The {monster['name']} unleashes a heavy attack for **{mon_dmg}** damage!")
            elif mon_action == "defend":
                narrative.append(f"The {monster['name']} braces defensively.")

            run.current_hp = max(run.current_hp - mon_dmg, 0)
            run.is_defending = False

            # Stoneblood: Dwarf survives killing blow once per run
            if run.current_hp <= 0 and not run.stoneblood_used and get_racial_modifier(race, "dungeon.stoneblood", False):
                run.current_hp = 1
                run.stoneblood_used = True
                narrative.append("**Stoneblood!** You refuse to fall — sheer dwarven stubbornness keeps you on your feet at 1 HP!")

            if run.current_hp <= 0:
                narrative.append(f"\nThe {monster['name']} strikes you down!")
                run.found_items_json = json.dumps(found_items)
                await session.commit()
                await session.refresh(run)
                await _process_death(session, run, player, dungeon_data, interaction, narrative)
                return

        run.found_items_json = json.dumps(found_items)
        await session.commit()
        await session.refresh(run)

        if run.state == "exploring":
            # Fled via smoke bomb
            embed = discord.Embed(
                title="Escaped!",
                color=EMBED_COLOR,
                description="\n".join(narrative),
            )
            embed.set_footer(text=_status_line(run, player))
            view = PostRoomView(run_id, user_id, sessionmaker)
        else:
            # Healed mid-combat, rebuild combat embed (variant/effects-aware).
            try:
                combat_state_for_embed = json.loads(run.combat_state_json or "{}")
            except (json.JSONDecodeError, TypeError):
                combat_state_for_embed = {}
            base_monster: dict[str, Any] | None = None
            if combat_state_for_embed.get("return_to") == "v2_explore":
                floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
                primary_id = (
                    combat_state_for_embed.get("primary_monster_id") or run.monster_id
                )
                base_monster = dungeon_explore.find_floor_monster(floor_data, primary_id)
            else:
                rooms = json.loads(run.rooms_json or "[]")
                if 0 <= run.room_index < len(rooms):
                    base_monster = rooms[run.room_index].get("monster")
            if base_monster is None:
                base_monster = {"id": run.monster_id or "?", "name": "?"}
            display_monster = dungeon_logic.apply_variant(
                base_monster, combat_state_for_embed.get("variant")
            )
            if "description" in combat_state_for_embed:
                display_monster["description"] = combat_state_for_embed["description"]
            display_monster = dungeon_logic.merge_phase_overrides(
                display_monster, int(combat_state_for_embed.get("phase", 0))
            )
            embed = build_combat_embed(
                run, player, display_monster, narrative, dungeon_data,
                combat_state=combat_state_for_embed,
            )
            view = CombatView(run_id, user_id, sessionmaker)

        # Edit the original dungeon message
        try:
            channel = interaction.client.get_channel(run.thread_id or run.channel_id)
            if channel:
                msg = channel.get_partial_message(run.message_id)
                await msg.edit(embed=embed, view=view)
        except Exception:
            pass

        await interaction.response.send_message(
            "\n".join(narrative) if narrative else "Item used!",
            ephemeral=True,
        )


async def _handle_descend(interaction, run_id, user_id, sessionmaker):
    """Descend to the next floor."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx

        max_floor = dungeon_logic.get_max_floor(dungeon_data)
        if run.floor >= max_floor:
            await interaction.response.send_message(
                "You've reached the deepest floor!", ephemeral=True
            )
            return

        # Advance to the next floor. v2 generates a fresh floor graph;
        # v1 generates a linear room list.
        run.floor += 1
        run.room_index = 0
        floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor)
        if floor_data is None:
            await interaction.response.send_message(
                "No more floors in this dungeon.", ephemeral=True
            )
            return

        new_seed = random.randint(0, 2**31)
        is_v2 = dungeon_explore.floor_is_v2(floor_data)
        if is_v2:
            new_floor_state = dungeon_explore.initial_floor_state(
                floor_data, random.Random(new_seed),
            )
            run.floor_state_json = dungeon_explore.dump_floor_state(new_floor_state)
            run.rooms_json = "[]"
        else:
            rooms = dungeon_logic.generate_rooms(floor_data, new_seed)
            run.rooms_json = json.dumps(rooms)
            run.floor_state_json = "{}"
        run.room_seed = new_seed
        run.state = "exploring"
        await session.commit()
        await session.refresh(run)

        if is_v2:
            # Drop straight into the entrance room of the new floor.
            floor_state = dungeon_explore.load_floor_state(run.floor_state_json)
            # Pre-generate the new entrance's LLM intro before rendering.
            # (Defer the interaction so the LLM call doesn't blow the 3s
            # response budget.)
            if not interaction.response.is_done():
                await interaction.response.defer()
            # If the player has a corpse on this floor, seed it.
            await _v2_seed_corpse_if_present(session, run, dungeon_data, floor_state)
            await _v2_ensure_room_llm(run, dungeon_data, floor_state)
            run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
            await session.commit()
            await session.refresh(run)
            embed = _build_v2_explore_embed(
                run, player, dungeon_data, floor_state,
                narrative=[
                    "_You descend the stairs. The next floor opens around you._",
                ],
            )
            view = _v2_render_view(run_id, user_id, sessionmaker, floor_state, floor_data)
            await interaction.edit_original_response(embed=embed, view=view)
            return

        # v1: blind-room "Continue" UX.
        embed = discord.Embed(
            title=f"{dungeon_data['name']} — Floor {run.floor}",
            color=EMBED_COLOR,
            description=(
                f"You descend deeper into the darkness...\n\n"
                f"The air grows heavier. Something stirs ahead."
            ),
        )
        embed.set_footer(text=_status_line(run, player))
        view = ContinueView(run_id, user_id, sessionmaker)
        await interaction.response.edit_message(embed=embed, view=view)


async def _handle_retreat(interaction, run_id, user_id, sessionmaker):
    """Retreat safely to town."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx
        await _process_return(session, run, player, dungeon_data, interaction)


# ---------------------------------------------------------------------------
# V2 dungeon — explore room loop.
# ---------------------------------------------------------------------------


def _build_v2_explore_embed(
    run, player, dungeon_data, floor_state, narrative=None,
) -> discord.Embed:
    """Build the embed for the current room of a v2 dungeon.

    If an LLM-narrated intro is cached on the room (set by
    :func:`_v2_ensure_room_llm`), it replaces the authored description.
    Otherwise falls back to the picked authored description.
    """
    floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
    description, ambient_lines, _exits = dungeon_explore.render_room_intro(
        floor_state, floor_data,
    )
    cur = floor_state.get("current")
    rs = floor_state.get("room_states", {}).get(cur, {}) if cur else {}
    llm_intro = rs.get("llm_intro")
    if llm_intro:
        description = llm_intro

    bg = floor_data.get("background")
    floor_label = f"Floor {run.floor}"
    if isinstance(bg, str) and bg.strip():
        # Per-floor backgrounds get their first line surfaced as a subtitle.
        first_line = bg.strip().split("\n", 1)[0]
        floor_label = f"Floor {run.floor} — {first_line}"

    embed = discord.Embed(
        title=dungeon_data.get("name", "Dungeon"),
        color=EMBED_COLOR,
    )
    parts = [f"_{floor_label}_", "", description]
    if ambient_lines:
        parts.append("")
        parts.extend(ambient_lines)
    if narrative:
        parts.append("")
        parts.extend(narrative)
    # Fog-of-war floor map.
    map_block = dungeon_map.render_map(floor_state)
    if map_block:
        parts.append("")
        parts.append(map_block)
        parts.append(f"_{dungeon_map.map_legend()}_")
    embed.description = "\n".join(parts)
    embed.set_footer(text=_status_line(run, player))
    return embed


async def _v2_ensure_room_llm(run, dungeon_data, floor_state) -> None:
    """Generate and cache an LLM room intro for the current room, once.

    No-op if:
    - LLM is unavailable (no API key, SDK missing, etc.)
    - The current room already has ``llm_intro_attempted`` set (whether
      success or failure — we don't retry).

    Mutates ``floor_state`` in place. Caller commits to DB.
    """
    if not dungeon_llm.is_available():
        return
    cur = floor_state.get("current")
    if cur is None:
        return
    rs = floor_state.setdefault("room_states", {}).setdefault(cur, {})
    if rs.get("llm_intro_attempted"):
        return
    # Mark attempted up-front so a transient failure doesn't trigger
    # retries every action.
    rs["llm_intro_attempted"] = True

    floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
    graph = floor_state.get("graph") or {}
    room_def_id = (graph.get("rooms") or {}).get(cur, {}).get("room_def_id")
    pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
    room_def = pool_by_id.get(room_def_id, {})
    description = rs.get("description") or room_def.get("description") or ""
    if not description:
        return
    intro = await dungeon_llm.narrate_room_intro(
        dungeon_data=dungeon_data,
        room_description=description,
        ambient_pool=room_def.get("ambient_pool"),
        features=room_def.get("features"),
    )
    if intro:
        rs["llm_intro"] = intro


def _v2_room_def(floor_data: dict[str, Any], room_def_id: str) -> dict[str, Any]:
    for r in (floor_data.get("room_pool") or []):
        if r.get("id") == room_def_id:
            return r
    return {}


def _v2_render_view(run_id, user_id, sessionmaker, floor_state, floor_data) -> ExploreView:
    """Build an ExploreView for the current room state."""
    cur = floor_state.get("current")
    rs = floor_state.get("room_states", {}).get(cur, {}) if cur else {}
    looked = bool(rs.get("looked_around", False))
    _, _, exits = dungeon_explore.render_room_intro(floor_state, floor_data)
    investigate_buttons = dungeon_explore.visible_feature_buttons(floor_state, floor_data)
    return ExploreView(
        run_id, user_id, sessionmaker,
        looked_around=looked, exits=exits,
        investigate_buttons=investigate_buttons,
    )


def _v2_perception_modifier(player, race: str) -> int:
    """Compose the player's perception modifier for v2 search rolls.

    Reuses DEX modifier + the existing ``dungeon.trap_save_bonus`` racial
    knob (Elf +2, Dwarf -1, others 0). Once classes / gear ship, additional
    sources can plug in here without touching the explore module.
    """
    dex_mod = dungeon_logic.get_modifier(player.dexterity)
    racial = int(get_racial_modifier(race, "dungeon.trap_save_bonus", 0))
    return dex_mod + racial


def _apply_v2_rewards(run, found_items: list[dict[str, Any]], rewards: list[dict[str, Any]]) -> None:
    """Apply explore-action rewards to the run.

    Mutates ``run.run_gold`` and ``found_items`` in place. The caller is
    responsible for serializing ``found_items`` back to ``run.found_items_json``.

    Reward types handled here (run-state mutations only):
    - ``gold`` — adds to ``run.run_gold``
    - ``item`` — appends to found_items (consumable inventory drop)
    - ``gear`` — appends to found_items as a gear drop (corpse recovery)

    Reward types handled by the caller (DB writes):
    - ``lore_fragment`` — persisted via ``dungeon_repo.add_lore_fragment``
    - ``corpse_recovered`` — signals corpse cleanup; cleared in caller
    """
    for r in rewards or []:
        rtype = r.get("type")
        if rtype == "gold":
            run.run_gold = int(run.run_gold or 0) + int(r.get("amount", 0))
        elif rtype == "item":
            item_id = r.get("item_id")
            if item_id:
                found_items.append({"item_id": item_id})
        elif rtype == "gear":
            gear_id = r.get("gear_id")
            if gear_id:
                found_items.append({"item_id": gear_id, "type": "gear"})


async def _v2_seed_corpse_if_present(
    session, run, dungeon_data, floor_state,
) -> None:
    """If the player has a stored corpse for this dungeon AND it sits on
    the current floor, seed it into a random non-boss room of the floor
    state so the player can find it.

    No-op if no corpse, the corpse is on a different floor, or this isn't
    a v2 dungeon. Mutates ``floor_state`` in place; caller commits.
    """
    if not dungeon_explore.is_v2_dungeon(dungeon_data):
        return
    corpse_row = await dungeon_repo.get_corpse(
        session, run.user_id, run.guild_id, run.dungeon_id,
    )
    if corpse_row is None:
        return
    if corpse_row.floor != run.floor:
        return
    try:
        loot = json.loads(corpse_row.loot_json or "[]")
    except (json.JSONDecodeError, TypeError):
        loot = []
    if not loot:
        # Nothing to recover — clear the empty record so it doesn't linger.
        await dungeon_repo.delete_corpse(
            session, run.user_id, run.guild_id, run.dungeon_id,
        )
        return
    rng = random.Random()
    dungeon_explore.seed_corpse_in_floor(floor_state, rng, loot=loot)


async def _v2_apply_meta_rewards(
    session, run, dungeon_data, floor_state, rewards: list[dict[str, Any]],
) -> list[str]:
    """Persist lore fragments, grant legendary on full collection, and
    clear the corpse if recovered. Returns narrative lines to append.

    Called from the v2 explore handler after run-state rewards (gold,
    items) have been applied. DB writes happen here; commit happens
    in the caller along with the floor_state save.
    """
    extra: list[str] = []
    now = datetime.now(timezone.utc)

    # 1. Lore fragments — persist new ones, narrate which were collected.
    new_fragment_ids: list[int] = []
    already_owned: list[int] = []
    for r in rewards:
        if r.get("type") != "lore_fragment":
            continue
        fid = r.get("fragment_id")
        if not isinstance(fid, int):
            continue
        added = await dungeon_repo.add_lore_fragment(
            session, run.user_id, run.guild_id, run.dungeon_id, fid, now,
        )
        if added:
            new_fragment_ids.append(fid)
        else:
            already_owned.append(fid)
    if new_fragment_ids:
        if len(new_fragment_ids) == 1:
            extra.append(
                f"_You commit fragment **#{new_fragment_ids[0]}** to memory._"
            )
        else:
            ids_text = ", ".join(f"#{n}" for n in new_fragment_ids)
            extra.append(f"_You commit fragments **{ids_text}** to memory._")
    if already_owned:
        ids_text = ", ".join(f"#{n}" for n in already_owned)
        extra.append(
            f"_You've recorded {ids_text} before. The page is already in your book._"
        )

    # 2. Legendary unlock — fires once when all fragments are collected.
    if new_fragment_ids:
        all_ids = {f["id"] for f in (dungeon_data.get("lore_fragments") or [])}
        if all_ids:
            owned = await dungeon_repo.get_lore_fragments(
                session, run.user_id, run.guild_id, run.dungeon_id,
            )
            owned_ids = {row.fragment_id for row in owned}
            if all_ids.issubset(owned_ids):
                legendary = dungeon_data.get("legendary_reward") or {}
                item_id = legendary.get("item_id")
                if item_id:
                    grant = await dungeon_repo.record_legendary_unlock(
                        session, run.user_id, run.guild_id, run.dungeon_id,
                        item_id, now,
                    )
                    if grant is not None:
                        # Drop the legendary into the run's found_items as gear.
                        try:
                            fi = json.loads(run.found_items_json or "[]")
                        except (json.JSONDecodeError, TypeError):
                            fi = []
                        fi.append({"item_id": item_id, "type": "gear"})
                        run.found_items_json = json.dumps(fi)
                        flavor = legendary.get("flavor") or (
                            f"_The book is complete. A passage flares to life — "
                            f"and **{item_id}** is in your hand. The dungeon's "
                            f"final secret is yours._"
                        )
                        extra.append(flavor)

    # 3. Corpse recovered — clear the DB row + floor_state marker.
    if any(r.get("type") == "corpse_recovered" for r in rewards):
        await dungeon_repo.delete_corpse(
            session, run.user_id, run.guild_id, run.dungeon_id,
        )
        corpse = floor_state.get("corpse")
        if corpse:
            corpse["recovered"] = True

    return extra


async def _v2_start_combat(
    interaction, session, sessionmaker, run, player, dungeon_data, floor_state, monster_id, kind,
):
    """Set up run.monster_* + combat_state_json from a monster id and a v2
    combat trigger (boss / ambush / wandering). Routes to CombatView.
    """
    floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}

    # Look up the monster definition (boss field or monsters list).
    monster_def = dungeon_explore.find_floor_monster(floor_data, monster_id)
    if monster_def is None:
        # Defensive: monster id missing — abort to explore.
        floor_state["pending_combat"] = None
        run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
        await session.commit()
        await session.refresh(run)
        embed = _build_v2_explore_embed(
            run, player, dungeon_data, floor_state,
            narrative=[f"_(combat aborted: monster '{monster_id}' not found)_"],
        )
        view = _v2_render_view(run.id, run.user_id, sessionmaker, floor_state, floor_data)
        await interaction.response.edit_message(embed=embed, view=view)
        return

    # Initialize combat state via the existing effects pipeline so phases /
    # variants / abilities all work for v2 monsters too.
    rng = random.Random()
    combat_state = dungeon_effects.initial_combat_state(monster_def, rng)
    combat_state["primary_monster_id"] = monster_def["id"]
    combat_state["return_to"] = "v2_explore"
    combat_state["combat_kind"] = kind   # boss | ambush | wandering

    effective = dungeon_logic.apply_variant(monster_def, combat_state.get("variant"))
    if "description" in combat_state:
        effective["description"] = combat_state["description"]

    run.state = "boss" if kind == "boss" else "combat"
    run.monster_id = monster_def["id"]
    run.monster_hp = effective["hp"]
    run.monster_max_hp = effective["hp"]
    run.is_defending = False
    run.combat_state_json = json.dumps(combat_state)

    # Persist floor_state too so the pending_combat flag is cleared.
    floor_state["pending_combat"] = None
    run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)

    await session.commit()
    await session.refresh(run)

    intro = build_combat_start_embed(run, player, effective, dungeon_data)
    view = CombatView(run.id, run.user_id, sessionmaker)
    await interaction.response.edit_message(embed=intro, view=view)


async def _handle_v2_explore_action(
    interaction, run_id, user_id, sessionmaker,
    *,
    action: str,
    target_node: str | None = None,
    feature_id: str | None = None,
):
    """Apply a v2 exploration action (Look / Listen / Move on / Investigate).

    LLM narration may add ~500-1500ms of latency. We defer the interaction
    response up-front so we don't blow Discord's 3-second initial response
    window, then ``edit_original_response`` once the work is done.
    """
    # Defer immediately — LLM call(s) below may push us past the 3s limit.
    if not interaction.response.is_done():
        await interaction.response.defer()

    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.followup.send("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx
        profile = await rpg_repo.get_or_create_profile(
            session, run.user_id, run.guild_id
        )
        race = profile.race
        floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
        floor_state = dungeon_explore.load_floor_state(run.floor_state_json)
        if not floor_state:
            # Defensive: somehow the state is missing. Re-init.
            floor_state = dungeon_explore.initial_floor_state(floor_data, random.Random())

        rng = random.Random()
        if action == "look_around":
            result = dungeon_explore.take_look_around(
                floor_state, floor_data, rng,
                perception_modifier=_v2_perception_modifier(player, race),
            )
        elif action == "listen":
            result = dungeon_explore.take_listen(floor_state, floor_data, rng)
        elif action == "move_on" and target_node:
            result = dungeon_explore.take_move_on(
                floor_state, floor_data, rng, target_node=target_node,
            )
        elif action == "investigate" and feature_id:
            result = dungeon_explore.take_investigate(
                floor_state, floor_data, rng, feature_id=feature_id,
            )
        else:
            await interaction.followup.send("Unknown action.", ephemeral=True)
            return

        # If a search just succeeded, ask the LLM to dress up the outcome.
        # Authored ``flavor_success`` plus engine rewards stay in result.narrative
        # as a safe fallback if the LLM is down or returns nothing.
        if (
            action == "investigate"
            and feature_id
            and result.next_step == "explore"
            and (result.rewards or any(not n.startswith("_(") for n in result.narrative))
        ):
            llm_outcome = await _v2_narrate_search_outcome(
                dungeon_data, floor_data, floor_state, feature_id, result.rewards,
            )
            if llm_outcome:
                # Replace the narrative entirely so we don't double up flavor.
                result.narrative = [llm_outcome]

        # Apply rewards (gold + items) if any. Done before combat handoff
        # so the player keeps anything they'd already found this room
        # before a wandering encounter would otherwise interrupt — but in
        # take_investigate the rewards are only set when no combat fired,
        # so this is safe either way.
        if result.rewards:
            try:
                found_items = json.loads(run.found_items_json or "[]")
            except (json.JSONDecodeError, TypeError):
                found_items = []
            _apply_v2_rewards(run, found_items, result.rewards)
            run.found_items_json = json.dumps(found_items)

            # Persist lore fragments + check legendary unlock + clear corpse.
            extra_narrative = await _v2_apply_meta_rewards(
                session, run, dungeon_data, floor_state, result.rewards,
            )
            if extra_narrative:
                result.narrative = list(result.narrative) + extra_narrative

        # Branch on outcome.
        if result.next_step == "combat":
            pending = floor_state.get("pending_combat") or {}
            monster_id = pending.get("monster_id")
            kind = pending.get("kind", "wandering")
            await _v2_start_combat(
                interaction, session, sessionmaker, run, player, dungeon_data, floor_state,
                monster_id, kind,
            )
            return

        # On a room transition, ensure the new room has its LLM intro generated
        # and cached before we render the embed. Subsequent actions in the
        # same room read the cached intro — no LLM call.
        if action == "move_on" and result.next_step == "transition":
            await _v2_ensure_room_llm(run, dungeon_data, floor_state)

        # explore / transition: persist floor_state and re-render.
        run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
        await session.commit()
        await session.refresh(run)

        embed = _build_v2_explore_embed(
            run, player, dungeon_data, floor_state, narrative=result.narrative,
        )
        view = _v2_render_view(run_id, user_id, sessionmaker, floor_state, floor_data)
        await interaction.edit_original_response(embed=embed, view=view)


async def _v2_narrate_search_outcome(
    dungeon_data: dict[str, Any],
    floor_data: dict[str, Any],
    floor_state: dict[str, Any],
    feature_id: str,
    rewards: list[dict[str, Any]],
) -> str | None:
    """Wrap dungeon_llm.narrate_search_outcome with feature lookup."""
    cur = floor_state.get("current")
    graph = floor_state.get("graph") or {}
    room_def_id = (graph.get("rooms") or {}).get(cur, {}).get("room_def_id")
    pool_by_id = {r["id"]: r for r in (floor_data.get("room_pool") or [])}
    room_def = pool_by_id.get(room_def_id, {})
    feature_def = next(
        (f for f in (room_def.get("features") or []) if f.get("id") == feature_id),
        None,
    )
    if feature_def is None:
        return None
    return await dungeon_llm.narrate_search_outcome(
        dungeon_data=dungeon_data,
        feature_name=feature_def.get("name") or feature_id,
        feature_flavor=feature_def.get("flavor_success"),
        rewards=rewards,
    )


async def _v2_post_combat_return(
    interaction, session, sessionmaker, run, player, dungeon_data, *, was_boss: bool,
):
    """Called from the combat handler when a v2 monster has died.

    The XP/loot/bestiary path has already been run by the combat handler
    (it's identical for v1 and v2). This function decides what UI to show
    next based on whether the kill was a boss or a regular encounter, and
    updates floor_state to mark the room's encounter as resolved.
    """
    floor_state = dungeon_explore.load_floor_state(run.floor_state_json)
    cur = floor_state.get("current")
    if cur is not None:
        rs = floor_state.setdefault("room_states", {}).setdefault(cur, {})
        rs["encounter_resolved"] = True

    if was_boss:
        # Boss kill → floor complete. Re-use the v1 floor-complete UI.
        run.state = "floor_complete"
        run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
        await session.commit()
        await session.refresh(run)
        embed = build_floor_complete_embed(run, player, dungeon_data)
        max_floor = dungeon_logic.get_max_floor(dungeon_data)
        can_descend = run.floor < max_floor
        view = FloorCompleteView(run.id, run.user_id, sessionmaker, can_descend)
        await interaction.response.edit_message(embed=embed, view=view)
        return

    # Non-boss kill → return to explore. Clear combat state, keep floor state.
    run.state = "exploring"
    run.combat_state_json = "{}"
    run.monster_id = None
    run.monster_hp = 0
    run.monster_max_hp = 0
    run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
    await session.commit()
    await session.refresh(run)

    floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor) or {}
    embed = _build_v2_explore_embed(
        run, player, dungeon_data, floor_state,
        narrative=["_The corridor settles. You return your attention to the room._"],
    )
    view = _v2_render_view(run.id, run.user_id, sessionmaker, floor_state, floor_data)
    await interaction.response.edit_message(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Run end processing
# ---------------------------------------------------------------------------


async def _process_death(session, run, player, dungeon_data, interaction, narrative=None):
    """Handle player death: apply penalties, end run."""
    gold_lost = int(run.run_gold * dungeon_logic.DEATH_GOLD_PENALTY)
    gold_kept = run.run_gold - gold_lost

    # Apply XP (Human +15% bonus)
    profile = await rpg_repo.get_or_create_profile(session, run.user_id, run.guild_id)
    xp_mult = get_racial_modifier(profile.race, "global.xp_multiplier", 1.0)
    effective_xp = int(run.run_xp * xp_mult)
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += effective_xp
    new_level = dungeon_logic.get_level(player.xp)
    levels_gained = new_level - old_level
    if levels_gained > 0:
        player.level = new_level
        player.unspent_stat_points += levels_gained

    # Deposit surviving gold
    if gold_kept > 0:
        wallet = await wallet_repo.get_wallet(session, run.user_id, run.guild_id)
        if wallet:
            wallet.balance += gold_kept
        else:
            await wallet_repo.create_wallet(
                session, user_id=run.user_id, guild_id=run.guild_id, balance=gold_kept
            )

    # Update career stats
    player.total_runs += 1

    # V2: snapshot run gear/items as a corpse so the player has a chance
    # to recover them on a future delve. New deaths overwrite old corpses
    # — only one corpse per (player, dungeon) ever.
    is_v2 = dungeon_explore.is_v2_dungeon(dungeon_data)
    if is_v2:
        try:
            found_items = json.loads(run.found_items_json or "[]")
        except (json.JSONDecodeError, TypeError):
            found_items = []
        corpse_loot: list[dict[str, Any]] = []
        # Carry the full found-items list onto the corpse — these are
        # things the player picked up but never made it home with.
        for item in found_items:
            if not isinstance(item, dict):
                continue
            iid = item.get("item_id")
            if not iid:
                continue
            if item.get("type") == "gear":
                corpse_loot.append({"type": "gear", "gear_id": iid})
            else:
                corpse_loot.append({"type": "item", "item_id": iid})
        if corpse_loot:
            await dungeon_repo.upsert_corpse(
                session,
                user_id=run.user_id,
                guild_id=run.guild_id,
                dungeon_id=run.dungeon_id,
                floor=run.floor,
                loot_json=json.dumps(corpse_loot),
                died_at=datetime.now(timezone.utc),
            )

    # End run
    run.active = False
    run.state = "dead"
    await session.commit()

    embed = build_death_embed(run, player, gold_lost, dungeon_data)
    if narrative:
        embed.description = "\n".join(narrative) + "\n\n" + embed.description
    view = discord.ui.View()  # Empty view, no buttons
    await interaction.response.edit_message(embed=embed, view=view)
    await _archive_thread(interaction, run)


async def _process_return(session, run, player, dungeon_data, interaction):
    """Handle safe return to town: bank rewards, end run."""
    # Apply XP (Human +15% bonus)
    profile = await rpg_repo.get_or_create_profile(session, run.user_id, run.guild_id)
    xp_mult = get_racial_modifier(profile.race, "global.xp_multiplier", 1.0)
    effective_xp = int(run.run_xp * xp_mult)
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += effective_xp
    new_level = dungeon_logic.get_level(player.xp)
    levels_gained = new_level - old_level
    if levels_gained > 0:
        player.level = new_level
        player.unspent_stat_points += levels_gained

    # Deposit all gold
    if run.run_gold > 0:
        wallet = await wallet_repo.get_wallet(session, run.user_id, run.guild_id)
        if wallet:
            wallet.balance += run.run_gold
        else:
            await wallet_repo.create_wallet(
                session, user_id=run.user_id, guild_id=run.guild_id, balance=run.run_gold
            )

    # Save found gear to inventory (auto-equip if slot is empty + stat met)
    found_items = json.loads(run.found_items_json)
    for item in found_items:
        if item.get("type") == "gear":
            gear_def = dungeon_logic.get_gear_by_id(item["item_id"])
            if gear_def:
                slot = dungeon_logic.get_gear_slot(item["item_id"])
                slot_attr = f"{slot}_id" if slot else None

                # Check for duplicates (already equipped or in stash)
                equipped_ids = {
                    gid for gid in [player.weapon_id, player.armor_id, player.accessory_id] if gid
                }
                already_has = await dungeon_repo.has_gear(
                    session, run.user_id, run.guild_id, item["item_id"]
                )
                if item["item_id"] in equipped_ids or already_has:
                    # Duplicate — convert to bonus gold
                    run.run_gold += gear_def.get("cost", 0) // 4
                    continue

                if slot_attr and getattr(player, slot_attr) is None:
                    # Auto-equip only if stat requirement met
                    meets_req, _ = dungeon_logic.check_stat_requirement(
                        item["item_id"], player.strength, player.dexterity,
                    )
                    if meets_req:
                        setattr(player, slot_attr, item["item_id"])
                    else:
                        await dungeon_repo.add_gear(
                            session, run.user_id, run.guild_id, item["item_id"]
                        )
                else:
                    # Store in inventory
                    await dungeon_repo.add_gear(
                        session, run.user_id, run.guild_id, item["item_id"]
                    )
        else:
            # Consumable — add to persistent inventory
            await dungeon_repo.add_item(
                session, run.user_id, run.guild_id, item["item_id"]
            )

    # Update career stats
    player.total_runs += 1

    # End run
    run.active = False
    run.state = "town"
    await session.commit()

    embed = build_return_embed(run, player, levels_gained, dungeon_data)
    view = discord.ui.View()  # Empty view, no buttons
    await interaction.response.edit_message(embed=embed, view=view)
    await _archive_thread(interaction, run)


async def _archive_thread(interaction, run):
    """Archive the dungeon thread when a run ends."""
    if run.thread_id:
        try:
            thread = interaction.client.get_channel(run.thread_id)
            if thread and isinstance(thread, discord.Thread):
                await thread.edit(archived=True)
        except Exception:
            pass


# ===========================================================================
# Cog definition
# ===========================================================================


class DungeonCrawler(commands.Cog, name="dungeoncrawler"):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: Context) -> bool:
        return await checks.in_bot_channel(ctx, "dungeon_channel")

    # ------------------------------------------------------------------
    # /dungeon  (top-level group)
    # ------------------------------------------------------------------

    @commands.hybrid_group(name="dungeon", description="Monster Mash dungeon crawler")
    async def dungeon(self, context: Context) -> None:
        if context.invoked_subcommand is None:
            await context.send(
                "Use `/dungeon delve`, `/dungeon stats`, `/dungeon shop`, "
                "`/dungeon inventory`, or `/dungeon bestiary`.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /dungeon delve
    # ------------------------------------------------------------------

    @dungeon.command(name="delve", description="Enter a dungeon")
    @app_commands.describe(dungeon_name="Which dungeon to enter")
    async def dungeon_delve(
        self, context: Context, dungeon_name: str | None = None,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Check for active run — offer resume or abandon
            active_run = await dungeon_repo.get_active_run(session, user_id, guild_id)
            if active_run:
                dungeon_data = dungeon_logic.get_dungeon(active_run.dungeon_id) or {"name": "Unknown"}
                dname = dungeon_data.get("name", "Unknown")
                thread_mention = ""
                if active_run.thread_id:
                    thread_mention = f"\nThread: <#{active_run.thread_id}>"
                view = RunRecoveryView(
                    active_run.id, user_id,
                    self.bot.scheduler.sessionmaker,
                    active_run.thread_id,
                )
                await context.send(
                    f"You have an active run in **{dname}** "
                    f"(Floor {active_run.floor}).{thread_mention}",
                    view=view,
                    ephemeral=True,
                )
                return

            # Pick dungeon — filter out role-gated ones the user can't access.
            all_dungeons = dungeon_logic.load_dungeons()
            dungeons = {
                k: d for k, d in all_dungeons.items()
                if checks.author_has_role(context, d.get("min_role"))
            }
            if not dungeons:
                await context.send("No dungeons available.", ephemeral=True)
                return

            if dungeon_name and dungeon_name in all_dungeons:
                # User asked for a specific dungeon by name. Honor the role
                # gate explicitly so they get a permissions error rather
                # than a silent "not found."
                target = all_dungeons[dungeon_name]
                if not checks.author_has_role(context, target.get("min_role")):
                    await context.send(
                        f"That dungeon requires the **{target.get('min_role')}** role.",
                        ephemeral=True,
                    )
                    return
                dungeon_key = dungeon_name
            elif len(dungeons) == 1:
                dungeon_key = next(iter(dungeons))
            else:
                # Multiple dungeons available — ask the player to pick.
                # Prefer the new background.pitch if authored; fall back to
                # the top-level description for older dungeons.
                def _pitch(d: dict[str, Any]) -> str:
                    bg = d.get("background") or {}
                    return bg.get("pitch") or d.get("description", "")

                listing = "\n".join(
                    f"• **{d.get('name', k)}** — `/dungeon delve {k}`\n  *{_pitch(d)}*"
                    for k, d in dungeons.items()
                )
                await context.send(
                    f"**Choose a dungeon:**\n{listing}",
                    ephemeral=True,
                )
                return

            dungeon_data = dungeons[dungeon_key]

            # Get/create player
            player = await dungeon_repo.get_or_create_player(session, user_id, guild_id)
            profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)
            race = profile.race

            # Calculate starting HP
            hp_bonus = dungeon_logic.get_accessory_hp_bonus(player.accessory_id)
            max_hp = dungeon_logic.get_max_hp(
                player.constitution, hp_bonus,
                hp_multiplier=get_racial_modifier(race, "dungeon.hp_multiplier", 2.0),
            )

            # Floor 1 must exist.
            floor_data = dungeon_logic.get_floor_data(dungeon_data, 1)
            if floor_data is None:
                await context.send("Dungeon has no floors!", ephemeral=True)
                return

            seed = random.randint(0, 2**31)
            is_v2 = dungeon_explore.is_v2_dungeon(dungeon_data)
            rooms_json = "[]"
            floor_state_json = "{}"
            if is_v2:
                # V2 path: build a procedural floor graph + room states.
                floor_state = dungeon_explore.initial_floor_state(
                    floor_data, random.Random(seed),
                )
                floor_state_json = dungeon_explore.dump_floor_state(floor_state)
            else:
                # V1 path: linear room list (unchanged).
                rooms = dungeon_logic.generate_rooms(floor_data, seed)
                rooms_json = json.dumps(rooms)

            # Create a thread for this run
            display_name = context.author.display_name
            thread_name = f"Monster Mash — {display_name} — {dungeon_data['name']}"
            thread = await context.channel.create_thread(
                name=thread_name[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=60,
            )

            # Post intro embed inside the thread
            intro_bg = dungeon_data.get("background") or {}
            intro_flavor = intro_bg.get("pitch") or dungeon_data.get("description", "")
            embed = discord.Embed(
                title=f"Entering {dungeon_data['name']}...",
                description=(
                    f"*{intro_flavor}*\n\n"
                    f"You steel yourself and step into the darkness.\n"
                    f"There's no telling what lies ahead."
                ),
                color=EMBED_COLOR,
            )
            embed.add_field(
                name="Ready?",
                value=f"HP: {max_hp}/{max_hp}  |  Floor 1",
                inline=False,
            )

            msg = await thread.send(embed=embed)
            message_id = msg.id
            channel_id = context.channel.id

            # Create run in DB
            now = datetime.now(timezone.utc)
            run = await dungeon_repo.create_run(
                session,
                user_id=user_id,
                guild_id=guild_id,
                channel_id=channel_id,
                thread_id=thread.id,
                message_id=message_id,
                dungeon_id=dungeon_key,
                floor=1,
                room_index=0,
                current_hp=max_hp,
                max_hp=max_hp,
                run_gold=0,
                run_xp=0,
                state="exploring",
                room_seed=seed,
                rooms_json=rooms_json,
                started_at=now,
            )

            if is_v2:
                # V2: stash floor_state and render the entrance room directly
                # via the explore view (no "Continue Deeper" intermediate).
                run.floor_state_json = floor_state_json
                await session.commit()
                await session.refresh(run)
                floor_state = dungeon_explore.load_floor_state(run.floor_state_json)
                # If the player died here previously and the corpse sits on
                # floor 1, seed it into the floor state.
                await _v2_seed_corpse_if_present(session, run, dungeon_data, floor_state)
                # Pre-generate LLM intro for the entrance room so the very
                # first thing the player reads is properly atmospheric.
                await _v2_ensure_room_llm(run, dungeon_data, floor_state)
                run.floor_state_json = dungeon_explore.dump_floor_state(floor_state)
                await session.commit()
                await session.refresh(run)
                explore_embed = _build_v2_explore_embed(
                    run, player, dungeon_data, floor_state,
                    narrative=[
                        "_You step inside. The dungeon settles around you, waiting._",
                    ],
                )
                explore_view = _v2_render_view(
                    run.id, user_id, self.bot.scheduler.sessionmaker,
                    floor_state, floor_data,
                )
                await msg.edit(embed=explore_embed, view=explore_view)
            else:
                # V1: blind first room.
                view = ContinueView(run.id, user_id, self.bot.scheduler.sessionmaker)
                await msg.edit(embed=embed, view=view)

            # Notify in main channel
            await context.send(
                f"Your dungeon run has started! Head to {thread.mention} to begin your delve.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /dungeon stats
    # ------------------------------------------------------------------

    @dungeon.command(name="stats", description="View your dungeon character sheet")
    @app_commands.describe(show="Show your character sheet publicly to the channel")
    async def dungeon_stats(self, context: Context, show: bool = False) -> None:
        await context.defer(ephemeral=not show)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(
                session, user_id, guild_id
            )
            profile = await rpg_repo.get_or_create_profile(session, user_id, guild_id)
            race = profile.race

            # Stat modifiers
            str_mod = dungeon_logic.get_modifier(player.strength)
            dex_mod = dungeon_logic.get_modifier(player.dexterity)
            con_mod = dungeon_logic.get_modifier(player.constitution)

            # HP
            hp_bonus = dungeon_logic.get_accessory_hp_bonus(player.accessory_id)
            max_hp = dungeon_logic.get_max_hp(
                player.constitution, hp_bonus,
                hp_multiplier=get_racial_modifier(race, "dungeon.hp_multiplier", 2.0),
            )

            # XP progress
            progress = dungeon_logic.xp_progress(player.xp)
            if progress:
                xp_in_level, xp_needed = progress
                xp_bar = _build_progress_bar(xp_in_level, xp_needed)
                xp_text = f"{xp_bar}  {xp_in_level}/{xp_needed} XP"
            else:
                xp_text = "MAX LEVEL"

            # Gear names
            weapon_name = _gear_display(player.weapon_id, "Fists (1d4)")
            armor_name = _gear_display(player.armor_id, "None")
            accessory_name = _gear_display(player.accessory_id, "None")

            # Build embed
            race_label = race.title() if race else "Human"
            embed = discord.Embed(
                title=f"Monster Mash — {context.author.display_name}",
                description=f"Race: **{race_label}**",
                color=EMBED_COLOR,
            )
            embed.set_thumbnail(url=context.author.display_avatar.url)

            # Stats block
            stats_text = (
                f"**STR** {player.strength} ({_format_mod(str_mod)})  •  "
                f"**DEX** {player.dexterity} ({_format_mod(dex_mod)})  •  "
                f"**CON** {player.constitution} ({_format_mod(con_mod)})"
            )
            embed.add_field(
                name=f"Level {player.level}",
                value=f"{stats_text}\n{xp_text}",
                inline=False,
            )

            embed.add_field(name="HP", value=str(max_hp), inline=True)

            if player.unspent_stat_points > 0:
                embed.add_field(
                    name="Unspent Points",
                    value=f"**{player.unspent_stat_points}** stat point(s) available!",
                    inline=True,
                )

            # Gear block
            gear_text = (
                f"**Weapon:** {weapon_name}\n"
                f"**Armor:** {armor_name}\n"
                f"**Accessory:** {accessory_name}"
            )
            embed.add_field(name="Equipment", value=gear_text, inline=False)

            # Career stats
            career_text = (
                f"Runs: {player.total_runs}  •  "
                f"Deepest Floor: {player.deepest_floor}  •  "
                f"Kills: {player.total_kills}"
            )
            embed.set_footer(text=career_text)

        await context.send(embed=embed, ephemeral=not show)

    # ------------------------------------------------------------------
    # /dungeon allocate — spend unspent stat points
    # ------------------------------------------------------------------

    @dungeon.command(name="allocate", description="Allocate unspent stat points")
    @app_commands.describe(stat="Which stat to increase")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Strength (STR)", value="strength"),
        app_commands.Choice(name="Dexterity (DEX)", value="dexterity"),
        app_commands.Choice(name="Constitution (CON)", value="constitution"),
    ])
    async def dungeon_allocate(
        self, context: Context, stat: str
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(
                session, user_id, guild_id
            )

            if player.unspent_stat_points <= 0:
                await context.send(
                    "You have no unspent stat points.", ephemeral=True
                )
                return

            old_value = getattr(player, stat)
            new_value = old_value + 1
            await dungeon_repo.update_player(
                session, user_id, guild_id,
                **{stat: new_value, "unspent_stat_points": player.unspent_stat_points - 1},
            )

        stat_label = stat.upper()[:3]
        remaining = player.unspent_stat_points
        await context.send(
            f"**{stat_label}** increased to **{new_value}** "
            f"({remaining} point(s) remaining).",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /dungeon shop
    # ------------------------------------------------------------------

    @dungeon.command(name="shop", description="Browse and buy gear and items")
    async def dungeon_shop(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(session, user_id, guild_id)
            wallet = await wallet_repo.get_wallet(session, user_id, guild_id)
            balance = wallet.balance if wallet else 0
            gear_inv = await dungeon_repo.get_player_gear(session, user_id, guild_id)
            owned_gear_ids = {g.gear_id for g in gear_inv}
            equipped_ids = {
                gid for gid in [player.weapon_id, player.armor_id, player.accessory_id] if gid
            }

        embed = _build_shop_embed("weapons", player.level, balance, owned_gear_ids, equipped_ids)
        view = ShopView(
            user_id, self.bot.scheduler.sessionmaker, player.level, balance,
            owned_gear_ids, equipped_ids, "weapons",
        )
        await context.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /dungeon inventory
    # ------------------------------------------------------------------

    @dungeon.command(name="inventory", description="View and manage your equipment")
    async def dungeon_inventory(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(session, user_id, guild_id)
            gear_inv = await dungeon_repo.get_player_gear(session, user_id, guild_id)
            item_inv = await dungeon_repo.get_player_items(session, user_id, guild_id)

        embed = _build_inventory_embed(player, gear_inv, item_inv, context.author.display_name)
        view = InventoryView(user_id, self.bot.scheduler.sessionmaker, player, gear_inv, item_inv)
        await context.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # /dungeon bestiary
    # ------------------------------------------------------------------

    @dungeon.command(name="bestiary", description="View your monster bestiary")
    async def dungeon_bestiary(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            entries = await dungeon_repo.get_bestiary_entries(session, user_id, guild_id)

        discovered = {e.monster_id: e for e in entries}
        all_monsters = dungeon_logic.get_all_monsters()

        embed = _build_bestiary_embed(all_monsters, discovered, 0)
        view = BestiaryView(user_id, all_monsters, discovered, 0)
        await context.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /dungeon lore <dungeon_name>
    # ------------------------------------------------------------------

    @dungeon.command(
        name="lore",
        description="View the lore fragments you've collected from a dungeon",
    )
    @app_commands.describe(dungeon_name="Which dungeon's lore to view")
    async def dungeon_lore(
        self, context: Context, dungeon_name: str | None = None,
    ) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        all_dungeons = dungeon_logic.load_dungeons()
        # Filter to dungeons the user can access AND that have authored lore.
        visible: dict[str, dict[str, Any]] = {}
        for k, d in all_dungeons.items():
            if not checks.author_has_role(context, d.get("min_role")):
                continue
            if not (d.get("lore_fragments") or []):
                continue
            visible[k] = d

        if not visible:
            await context.send(
                "No dungeons with lore are available yet.", ephemeral=True,
            )
            return

        if dungeon_name and dungeon_name in visible:
            target_key = dungeon_name
        elif dungeon_name and dungeon_name in all_dungeons:
            await context.send(
                "That dungeon either has no lore or isn't available to you.",
                ephemeral=True,
            )
            return
        elif len(visible) == 1:
            target_key = next(iter(visible))
        else:
            listing = "\n".join(
                f"• `/dungeon lore {k}` — **{d.get('name', k)}**"
                for k, d in visible.items()
            )
            await context.send(
                f"**Choose a dungeon's lore to read:**\n{listing}",
                ephemeral=True,
            )
            return

        target = visible[target_key]
        async with self.bot.scheduler.sessionmaker() as session:
            owned = await dungeon_repo.get_lore_fragments(
                session, user_id, guild_id, target_key,
            )
            unlock = await dungeon_repo.get_legendary_unlock(
                session, user_id, guild_id, target_key,
            )
        owned_ids = {row.fragment_id for row in owned}

        embed = _build_lore_embed(target, owned_ids, unlock_item_id=unlock.item_id if unlock else None)
        await context.send(embed=embed, ephemeral=True)

    @dungeon_lore.autocomplete("dungeon_name")
    async def dungeon_lore_autocomplete(
        self, interaction: discord.Interaction, current: str,
    ) -> list[app_commands.Choice[str]]:
        dungeons = dungeon_logic.load_dungeons()
        user_role_names = {
            getattr(r, "name", None)
            for r in getattr(getattr(interaction, "user", None), "roles", []) or []
        }
        visible = {
            k: d for k, d in dungeons.items()
            if (not d.get("min_role") or d.get("min_role") in user_role_names)
            and (d.get("lore_fragments") or [])
        }
        return filter_choices(
            visible.items(),
            current,
            label=lambda kv: kv[1].get("name", kv[0]),
            value=lambda kv: kv[0],
            match=lambda kv: f"{kv[1].get('name', kv[0])} {kv[0]}",
        )

    # /dungeon abandon — cancel an active run
    # ------------------------------------------------------------------

    @dungeon.command(name="abandon", description="Abandon your current dungeon run")
    async def dungeon_abandon(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            run = await dungeon_repo.get_active_run(session, user_id, guild_id)
            if run is None:
                await context.send(
                    "You don't have an active dungeon run.", ephemeral=True
                )
                return

            player = await dungeon_repo.get_or_create_player(session, user_id, guild_id)
            dungeon_data = dungeon_logic.get_dungeon(run.dungeon_id) or {"name": "Unknown"}

            # Treat abandon as death
            await _process_abandon(session, run, player)

        await context.send(
            f"You fled from **{dungeon_data['name']}**, losing your loot. "
            f"XP has been saved.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Dungeon autocomplete
    # ------------------------------------------------------------------

    @dungeon_delve.autocomplete("dungeon_name")
    async def dungeon_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # Filter by min_role so role-gated dungeons don't appear in
        # autocomplete unless the user qualifies.
        dungeons = dungeon_logic.load_dungeons()
        user_role_names = {
            getattr(r, "name", None)
            for r in getattr(getattr(interaction, "user", None), "roles", []) or []
        }
        visible = {
            k: d for k, d in dungeons.items()
            if not d.get("min_role") or d.get("min_role") in user_role_names
        }
        return filter_choices(
            visible.items(),
            current,
            label=lambda kv: kv[1].get("name", kv[0]),
            value=lambda kv: kv[0],
            match=lambda kv: f"{kv[1].get('name', kv[0])} {kv[0]}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _process_abandon(session, run, player):
    """Handle abandoning a run (same as death but no gold salvage)."""
    # Apply XP only (Human +15% bonus)
    profile = await rpg_repo.get_or_create_profile(session, run.user_id, run.guild_id)
    xp_mult = get_racial_modifier(profile.race, "global.xp_multiplier", 1.0)
    effective_xp = int(run.run_xp * xp_mult)
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += effective_xp
    new_level = dungeon_logic.get_level(player.xp)
    levels_gained = new_level - old_level
    if levels_gained > 0:
        player.level = new_level
        player.unspent_stat_points += levels_gained

    player.total_runs += 1
    run.active = False
    run.state = "dead"
    await session.commit()


def _gear_display(gear_id: str | None, default: str) -> str:
    """Return a display string for a gear item, or the default."""
    if gear_id is None:
        return default
    gear = dungeon_logic.get_gear_by_id(gear_id)
    if gear is None:
        return default
    extra = ""
    if "dice" in gear:
        bonus = gear.get("bonus", 0)
        bonus_str = f"+{bonus}" if bonus > 0 else ""
        extra = f" ({gear['dice']}{bonus_str})"
    elif "defense" in gear:
        extra = f" (+{gear['defense']} DEF)"
    elif "effect" in gear:
        eff = gear["effect"]
        extra = f" ({eff.get('type', '').replace('_', ' ')})"
    # Show stat requirements
    str_req = gear.get("str_requirement", 0)
    dex_req = gear.get("dex_requirement", 0)
    if str_req > 0:
        extra += f" [STR {str_req}]"
    if dex_req > 0:
        extra += f" [DEX {dex_req}]"
    return f"{gear['name']}{extra}"


def _format_mod(mod: int) -> str:
    return f"+{mod}" if mod >= 0 else str(mod)


def _build_progress_bar(current: int, total: int, length: int = 10) -> str:
    if total <= 0:
        return "[" + "=" * length + "]"
    filled = int(length * current / total)
    empty = length - filled
    return "[" + "=" * filled + " " * empty + "]"


async def setup(bot) -> None:
    await bot.add_cog(DungeonCrawler(bot))
