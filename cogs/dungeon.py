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

from dungeon import logic as dungeon_logic
from dungeon import repositories as dungeon_repo
from economy import repositories as wallet_repo

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

EMBED_COLOR = 0xE74C3C       # Monster Mash red
EMBED_COLOR_COMBAT = 0xE67E22  # orange for combat
EMBED_COLOR_LOOT = 0xF1C40F   # gold for treasure
EMBED_COLOR_TRAP = 0x9B59B6   # purple for traps
EMBED_COLOR_REST = 0x2ECC71   # green for rest
EMBED_COLOR_BOSS = 0xC0392B   # dark red for bosses
EMBED_COLOR_DEATH = 0x2C3E50  # dark grey for death
EMBED_COLOR_RETURN = 0x27AE60  # green for safe return

VIEW_TIMEOUT = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def _hp_bar(current: int, maximum: int, length: int = 10) -> str:
    if maximum <= 0:
        return "[??????????]"
    ratio = max(current, 0) / maximum
    filled = int(length * ratio)
    empty = length - filled
    return "[" + "=" * filled + " " * empty + "]"


def _status_line(run, player) -> str:
    """Compact status line: HP, gold, floor/room."""
    return (
        f"HP {run.current_hp}/{run.max_hp} {_hp_bar(run.current_hp, run.max_hp)}  |  "
        f"Gold: {run.run_gold}  |  Floor {run.floor}, Room {run.room_index + 1}"
    )


def build_combat_start_embed(
    run, player, monster_data: dict[str, Any], dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build the embed when combat begins (monster appears)."""
    is_boss = run.state == "boss"
    rooms = json.loads(run.rooms_json)
    room_num = run.room_index + 1
    total_rooms = len(rooms)

    embed = discord.Embed(
        title=f"{dungeon_data['name']} — Floor {run.floor}",
        color=EMBED_COLOR_BOSS if is_boss else EMBED_COLOR_COMBAT,
    )
    prefix = "**BOSS:** " if is_boss else ""
    embed.description = (
        f"Room {room_num}/{total_rooms}\n\n"
        f"{prefix}A **{monster_data['name']}** ambushes you!\n"
        f"*{monster_data.get('description', '')}*"
    )
    embed.add_field(
        name=monster_data["name"],
        value=f"HP {monster_data['hp']}/{monster_data['hp']} {_hp_bar(monster_data['hp'], monster_data['hp'])}",
        inline=False,
    )
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_combat_embed(
    run, player, monster_data: dict[str, Any],
    narrative: list[str], dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build the embed for an ongoing combat round."""
    is_boss = run.state == "boss"
    embed = discord.Embed(
        title=f"{'BOSS: ' if is_boss else ''}{monster_data['name']}",
        color=EMBED_COLOR_BOSS if is_boss else EMBED_COLOR_COMBAT,
    )
    embed.description = "\n".join(narrative)
    embed.add_field(
        name=monster_data["name"],
        value=f"HP {run.monster_hp}/{run.monster_max_hp} {_hp_bar(run.monster_hp, run.monster_max_hp)}",
        inline=True,
    )
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_loot_embed(
    run, player, gold: int, item_drops: list[dict[str, Any]],
    dungeon_data: dict[str, Any],
    cross_game_drops: list[dict[str, Any]] | None = None,
) -> discord.Embed:
    """Build embed for post-combat or treasure room loot."""
    embed = discord.Embed(
        title="Loot!",
        color=EMBED_COLOR_LOOT,
    )
    lines = []
    if gold > 0:
        lines.append(f"**{gold}** gold")
    for drop in item_drops:
        item = dungeon_logic.get_gear_by_id(drop["item_id"]) or dungeon_logic.get_item_by_id(drop["item_id"])
        name = item["name"] if item else drop["item_id"]
        is_gear = drop.get("type") == "gear"
        if is_gear and item:
            # Show stat requirement hint for enchanted gear
            str_req = item.get("str_requirement", 0)
            dex_req = item.get("dex_requirement", 0)
            req_text = ""
            if str_req > 0:
                req_text = f" [STR {str_req}]"
            elif dex_req > 0:
                req_text = f" [DEX {dex_req}]"
            lines.append(f"\u2694\ufe0f **{name}**{req_text}")
        else:
            lines.append(f"{name}")
    # Cross-game drops
    for drop in (cross_game_drops or []):
        drop_type = drop.get("type", "")
        if drop_type == "cross_game_bait":
            bait_name = _BAIT_TYPES.get(drop["item_id"], {}).get("name", drop["item_id"])
            lines.append(f"\U0001f3a3 {bait_name}")
        elif drop_type == "cross_game_ingredient":
            name = drop.get("name", drop["item_id"])
            lines.append(f"\U0001f9ea {name}")
    if not lines:
        lines.append("Nothing of value.")
    embed.description = "\n".join(lines)
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_trap_result_embed(
    run, player, trap_data: dict[str, Any], avoided: bool,
    damage: int, dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build embed for trap resolution."""
    embed = discord.Embed(
        title=trap_data.get("name", "Trap"),
        color=EMBED_COLOR_REST if avoided else EMBED_COLOR_TRAP,
    )
    if avoided:
        embed.description = trap_data.get("flavor_success", "You avoided the trap!")
    else:
        embed.description = (
            f"{trap_data.get('flavor_fail', 'You triggered the trap!')}\n\n"
            f"You take **{damage}** damage!"
        )
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_rest_embed(
    run, player, hp_restored: int, dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build embed for rest shrine."""
    embed = discord.Embed(
        title="Rest Shrine",
        color=EMBED_COLOR_REST,
        description=(
            f"The shrine's warmth washes over you.\n"
            f"You recover **{hp_restored}** HP."
        ),
    )
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_floor_complete_embed(
    run, player, dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build embed for completing a floor."""
    max_floor = dungeon_logic.get_max_floor(dungeon_data)
    can_descend = run.floor < max_floor

    embed = discord.Embed(
        title=f"Floor {run.floor} Complete!",
        color=EMBED_COLOR_RETURN,
        description=(
            f"The boss falls and the way forward opens.\n\n"
            f"**Gold collected:** {run.run_gold}\n"
            f"**XP earned:** {run.run_xp}\n"
            f"**HP:** {run.current_hp}/{run.max_hp}"
        ),
    )
    if can_descend:
        embed.add_field(
            name="What do you do?",
            value="Descend deeper into the darkness, or return to town with your haul?",
            inline=False,
        )
    else:
        embed.add_field(
            name="Dungeon Complete!",
            value="You've reached the deepest floor. Return to town with your spoils!",
            inline=False,
        )
    embed.set_footer(text=_status_line(run, player))
    return embed


def build_death_embed(
    run, player, gold_lost: int, dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build the death screen embed."""
    gold_kept = run.run_gold - gold_lost
    embed = discord.Embed(
        title="You Have Fallen...",
        color=EMBED_COLOR_DEATH,
        description=(
            f"Darkness takes you on Floor {run.floor} of {dungeon_data['name']}.\n\n"
            f"**Gold lost:** {gold_lost}\n"
            f"**Gold salvaged:** {gold_kept}\n"
            f"**XP earned:** {run.run_xp}\n"
            f"**Items lost:** Everything found this run"
        ),
    )
    embed.set_footer(text="Use /dungeon stats to check your character.")
    return embed


def build_return_embed(
    run, player, levels_gained: int, dungeon_data: dict[str, Any],
) -> discord.Embed:
    """Build the safe return to town embed."""
    embed = discord.Embed(
        title="Returned to Town!",
        color=EMBED_COLOR_RETURN,
        description=(
            f"You emerge from {dungeon_data['name']} alive!\n\n"
            f"**Gold banked:** {run.run_gold}\n"
            f"**XP earned:** {run.run_xp}"
        ),
    )
    if levels_gained > 0:
        embed.add_field(
            name="Level Up!",
            value=(
                f"You gained **{levels_gained}** level(s)! "
                f"Use `/dungeon allocate` to spend your stat points."
            ),
            inline=False,
        )
    found_items = json.loads(run.found_items_json)
    gear_items = [i for i in found_items if i.get("type") == "gear"]
    if gear_items:
        gear_names = []
        for g in gear_items:
            gear_def = dungeon_logic.get_gear_by_id(g["item_id"])
            gear_names.append(gear_def["name"] if gear_def else g["item_id"])
        embed.add_field(
            name="Gear Found",
            value="\n".join(gear_names) + "\nUse `/dungeon inventory` to manage equipment.",
            inline=False,
        )
    embed.set_footer(text="Use /dungeon delve to venture forth again!")
    return embed


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

        rooms = json.loads(run.rooms_json)
        if run.room_index >= len(rooms):
            await interaction.response.send_message("No more rooms on this floor.", ephemeral=True)
            return

        room_data = rooms[run.room_index]
        room_type = room_data["type"]

        if room_type in ("combat", "boss"):
            # Enter combat state
            monster = room_data["monster"]
            state = "boss" if room_type == "boss" else "combat"
            run.state = state
            run.monster_id = monster["id"]
            run.monster_hp = monster["hp"]
            run.monster_max_hp = monster["hp"]
            run.is_defending = False
            await session.commit()
            await session.refresh(run)

            embed = build_combat_start_embed(run, player, monster, dungeon_data)
            view = CombatView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)

        elif room_type == "treasure":
            # Resolve treasure immediately
            tier = room_data.get("tier", "common")
            gold = dungeon_logic.roll_treasure_gold(tier)
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
            avoided = dungeon_logic.check_trap(player.dexterity, trap_dc)
            damage = 0
            if not avoided:
                damage = dungeon_logic.roll_trap_damage(trap.get("damage", [1, 4]))
                run.current_hp = max(run.current_hp - damage, 0)

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
            heal_amount = int(run.max_hp * dungeon_logic.REST_HEAL_FRACTION)
            run.current_hp = min(run.current_hp + heal_amount, run.max_hp)
            run.room_index += 1
            run.state = "exploring"
            await session.commit()
            await session.refresh(run)

            embed = build_rest_embed(run, player, heal_amount, dungeon_data)
            view = PostRoomView(run_id, user_id, sessionmaker)
            await interaction.response.edit_message(embed=embed, view=view)


async def _handle_combat_action(interaction, run_id, user_id, sessionmaker, action: str):
    """Process a combat action (attack, defend, flee)."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx

        rooms = json.loads(run.rooms_json)
        room_data = rooms[run.room_index]
        monster = room_data["monster"]
        narrative: list[str] = []

        if action == "flee":
            fled = dungeon_logic.check_flee(player.dexterity)
            if fled:
                narrative.append("You turn and run! You escape the fight.")
                run.room_index += 1
                run.state = "exploring"
                run.monster_id = None
                run.monster_hp = 0
                run.monster_max_hp = 0
                await session.commit()
                await session.refresh(run)

                embed = build_combat_embed(run, player, monster, narrative, dungeon_data)
                view = PostRoomView(run_id, user_id, sessionmaker)
                await interaction.response.edit_message(embed=embed, view=view)
                return
            else:
                narrative.append("You try to flee but can't escape!")
                # Monster gets a free hit
                mon_dmg, _ = dungeon_logic.calc_monster_damage(
                    monster["attack_dice"], monster.get("attack_bonus", 0),
                    dungeon_logic.get_armor_defense(player.armor_id), False,
                )
                run.current_hp = max(run.current_hp - mon_dmg, 0)
                narrative.append(f"The {monster['name']} strikes you for **{mon_dmg}** damage!")

                if run.current_hp <= 0:
                    await session.commit()
                    await session.refresh(run)
                    await _process_death(session, run, player, dungeon_data, interaction)
                    return

                run.is_defending = False
                await session.commit()
                await session.refresh(run)

                embed = build_combat_embed(run, player, monster, narrative, dungeon_data)
                view = CombatView(run_id, user_id, sessionmaker)
                await interaction.response.edit_message(embed=embed, view=view)
                return

        # Simultaneous resolution for attack/defend
        is_defending = action == "defend"

        # Player attack (only if attacking, not defending)
        player_dmg = 0
        was_crit = False
        if action == "attack":
            d20 = dungeon_logic.roll_d20()
            was_crit = dungeon_logic.is_crit(d20)
            crit_bonus = dungeon_logic.get_crit_bonus(player.accessory_id)
            if not was_crit and crit_bonus > 0:
                was_crit = d20 >= (20 - crit_bonus)
            weapon_dice = dungeon_logic.get_weapon_dice(player.weapon_id)
            weapon_bonus = dungeon_logic.get_weapon_bonus(player.weapon_id)
            str_mod = dungeon_logic.get_modifier(player.strength)
            player_dmg, _ = dungeon_logic.calc_player_damage(
                weapon_dice, str_mod, weapon_bonus, monster.get("defense", 0), was_crit,
            )
            crit_text = " **CRITICAL HIT!**" if was_crit else ""
            narrative.append(f"You strike the {monster['name']} for **{player_dmg}** damage!{crit_text}")
        else:
            narrative.append("You raise your guard and brace for impact.")

        # Monster action
        ai_weights = monster.get("ai", {"attack": 70, "heavy": 30})
        mon_action = dungeon_logic.select_monster_action(ai_weights)

        mon_dmg = 0
        if mon_action == "attack":
            mon_dmg, _ = dungeon_logic.calc_monster_damage(
                monster["attack_dice"], monster.get("attack_bonus", 0),
                dungeon_logic.get_armor_defense(player.armor_id), is_defending,
            )
            narrative.append(f"The {monster['name']} attacks you for **{mon_dmg}** damage!")
        elif mon_action == "heavy":
            heavy_dmg, _ = dungeon_logic.calc_monster_damage(
                monster["attack_dice"], monster.get("attack_bonus", 0),
                dungeon_logic.get_armor_defense(player.armor_id), is_defending,
            )
            heavy_dmg = int(heavy_dmg * 1.5)
            mon_dmg = heavy_dmg
            narrative.append(f"The {monster['name']} unleashes a heavy attack for **{mon_dmg}** damage!")
        elif mon_action == "defend":
            narrative.append(f"The {monster['name']} braces defensively.")
            # Monster defending reduces player damage this round
            player_dmg = max(player_dmg // 2, 1) if player_dmg > 0 else 0
            if action == "attack":
                narrative[-2] = f"You strike the {monster['name']} for **{player_dmg}** damage! (blocked)"

        # Apply damage simultaneously
        run.monster_hp = max(run.monster_hp - player_dmg, 0)
        run.current_hp = max(run.current_hp - mon_dmg, 0)
        run.is_defending = is_defending

        # Check outcomes
        monster_dead = run.monster_hp <= 0
        player_dead = run.current_hp <= 0

        if monster_dead and player_dead:
            # Both die simultaneously — player dies
            narrative.append(f"\nThe {monster['name']} falls... but so do you.")
            await session.commit()
            await session.refresh(run)
            await _process_death(session, run, player, dungeon_data, interaction, narrative)
            return

        if player_dead:
            narrative.append(f"\nThe {monster['name']} strikes you down!")
            await session.commit()
            await session.refresh(run)
            await _process_death(session, run, player, dungeon_data, interaction, narrative)
            return

        if monster_dead:
            narrative.append(f"\nThe **{monster['name']}** is defeated!")
            # Award XP, gold, loot
            xp_gained = monster.get("xp", 0)
            gold_range = monster.get("gold", [0, 0])
            gold_gained = dungeon_logic.roll_monster_gold(gold_range)
            loot_drops = dungeon_logic.roll_loot_drops(monster.get("loot", []))

            run.run_xp += xp_gained
            run.run_gold += gold_gained

            # Split loot: regular items go to found_items, cross-game instant
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
                    found_items.append(drop)
                    regular_drops.append(drop)

            run.found_items_json = json.dumps(found_items)

            # Bestiary tracking
            now = datetime.now(timezone.utc)
            await dungeon_repo.upsert_bestiary_entry(
                session, run.user_id, run.guild_id, monster["id"], now,
            )

            # Update player kill count
            player.total_kills += 1

            # Check if this was the boss
            is_boss = run.state == "boss"
            run.monster_id = None
            run.monster_hp = 0
            run.monster_max_hp = 0
            run.room_index += 1

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

        # Combat continues
        await session.commit()
        await session.refresh(run)

        embed = build_combat_embed(run, player, monster, narrative, dungeon_data)
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
            # Healed mid-combat, rebuild combat embed
            rooms = json.loads(run.rooms_json)
            room_data = rooms[run.room_index]
            monster = room_data["monster"]
            embed = build_combat_embed(run, player, monster, narrative, dungeon_data)
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

        # Generate rooms for the next floor
        run.floor += 1
        run.room_index = 0
        floor_data = dungeon_logic.get_floor_data(dungeon_data, run.floor)
        if floor_data is None:
            await interaction.response.send_message(
                "No more floors in this dungeon.", ephemeral=True
            )
            return

        new_seed = random.randint(0, 2**31)
        rooms = dungeon_logic.generate_rooms(floor_data, new_seed)
        run.rooms_json = json.dumps(rooms)
        run.room_seed = new_seed
        run.state = "exploring"
        await session.commit()
        await session.refresh(run)

        # Show a "descending" message — player must press Continue to enter first room
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
# Run end processing
# ---------------------------------------------------------------------------


async def _process_death(session, run, player, dungeon_data, interaction, narrative=None):
    """Handle player death: apply penalties, end run."""
    gold_lost = int(run.run_gold * dungeon_logic.DEATH_GOLD_PENALTY)
    gold_kept = run.run_gold - gold_lost

    # Apply XP
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += run.run_xp
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
    # Apply XP
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += run.run_xp
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
        await context.defer()
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            # Check for active run
            active_run = await dungeon_repo.get_active_run(session, user_id, guild_id)
            if active_run:
                await context.send(
                    "You already have an active dungeon run! "
                    "Finish or abandon it first.",
                    ephemeral=True,
                )
                return

            # Pick dungeon
            dungeons = dungeon_logic.load_dungeons()
            if not dungeons:
                await context.send("No dungeons available.", ephemeral=True)
                return

            if dungeon_name and dungeon_name in dungeons:
                dungeon_key = dungeon_name
            elif len(dungeons) == 1:
                dungeon_key = next(iter(dungeons))
            else:
                # Multiple dungeons available — ask the player to pick
                listing = "\n".join(
                    f"• **{d.get('name', k)}** — `/dungeon delve {k}`\n  *{d.get('description', '')}*"
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

            # Calculate starting HP
            hp_bonus = dungeon_logic.get_accessory_hp_bonus(player.accessory_id)
            max_hp = dungeon_logic.get_max_hp(player.constitution, hp_bonus)

            # Generate floor 1 rooms
            floor_data = dungeon_logic.get_floor_data(dungeon_data, 1)
            if floor_data is None:
                await context.send("Dungeon has no floors!", ephemeral=True)
                return

            seed = random.randint(0, 2**31)
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
            embed = discord.Embed(
                title=f"Entering {dungeon_data['name']}...",
                description=(
                    f"*{dungeon_data.get('description', '')}*\n\n"
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

            # Add ContinueView — first room is blind
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
    async def dungeon_stats(self, context: Context) -> None:
        await context.defer(ephemeral=True)
        guild_id = context.guild.id if context.guild else 0
        user_id = context.author.id

        async with self.bot.scheduler.sessionmaker() as session:
            player = await dungeon_repo.get_or_create_player(
                session, user_id, guild_id
            )

            # Stat modifiers
            str_mod = dungeon_logic.get_modifier(player.strength)
            dex_mod = dungeon_logic.get_modifier(player.dexterity)
            con_mod = dungeon_logic.get_modifier(player.constitution)

            # HP
            hp_bonus = dungeon_logic.get_accessory_hp_bonus(player.accessory_id)
            max_hp = dungeon_logic.get_max_hp(player.constitution, hp_bonus)

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
            embed = discord.Embed(
                title=f"Monster Mash — {context.author.display_name}",
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

        await context.send(embed=embed, ephemeral=True)

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
        dungeons = dungeon_logic.load_dungeons()
        choices = []
        current_lower = current.lower()
        for key, data in dungeons.items():
            name = data.get("name", key)
            if current_lower in name.lower() or current_lower in key.lower():
                choices.append(app_commands.Choice(name=name, value=key))
            if len(choices) >= 25:
                break
        return choices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _process_abandon(session, run, player):
    """Handle abandoning a run (same as death but no gold salvage)."""
    # Apply XP only
    old_level = dungeon_logic.get_level(player.xp)
    player.xp += run.run_xp
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
