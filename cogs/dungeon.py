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
        lines.append(f"{'[GEAR] ' if is_gear else ''}{name}")
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

            # Track found items
            found_items = json.loads(run.found_items_json)
            for drop in loot_drops:
                found_items.append(drop)
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

                embed = build_loot_embed(run, player, gold_gained, loot_drops, dungeon_data)
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
    """Show item selection menu."""
    async with sessionmaker() as session:
        ctx = await _load_run_context(session, run_id)
        if ctx is None:
            await interaction.response.send_message("Run not found.", ephemeral=True)
            return
        run, player, dungeon_data = ctx

        found_items = json.loads(run.found_items_json)
        consumables = [i for i in found_items if i.get("type") != "gear"]

        if not consumables:
            await interaction.response.send_message(
                "You don't have any items to use!", ephemeral=True
            )
            return

        view = ItemSelectView(run_id, user_id, sessionmaker, consumables)
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

        # Remove one of this item from found_items
        found_items = json.loads(run.found_items_json)
        removed = False
        for i, fi in enumerate(found_items):
            if fi.get("item_id") == item_id and fi.get("type") != "gear":
                found_items.pop(i)
                removed = True
                break

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

    # Move found gear to equipped slots (auto-equip if slot is empty)
    found_items = json.loads(run.found_items_json)
    for item in found_items:
        if item.get("type") == "gear":
            gear_def = dungeon_logic.get_gear_by_id(item["item_id"])
            if gear_def:
                if "dice" in gear_def and player.weapon_id is None:
                    player.weapon_id = item["item_id"]
                elif "defense" in gear_def and player.armor_id is None:
                    player.armor_id = item["item_id"]
                elif "effect" in gear_def and player.accessory_id is None:
                    player.accessory_id = item["item_id"]

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
        return await checks.in_bot_channel(ctx)

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
            else:
                # Default to first dungeon
                dungeon_key = next(iter(dungeons))

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
        await context.send(
            f"**{stat_label}** increased to **{new_value}** "
            f"({player.unspent_stat_points - 1} point(s) remaining).",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
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
