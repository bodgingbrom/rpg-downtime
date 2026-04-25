"""Pure embed builders for Monster Mash.

These functions take run/player/monster state and return a fully-built
``discord.Embed``. They have no side effects (no DB, no Discord API)
and don't construct View instances — those concerns live in
``cogs/dungeon.py``.
"""

from __future__ import annotations

import json
from typing import Any

import discord

from dungeon import logic as dungeon_logic

# Cross-game imports — match the optional/graceful pattern in cogs/dungeon.py
# so a guild can run dungeon-only without fishing installed.
try:
    from fishing.logic import BAIT_TYPES as _BAIT_TYPES
except ImportError:
    _BAIT_TYPES = {}


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

EMBED_COLOR = 0xE74C3C       # Monster Mash red
EMBED_COLOR_COMBAT = 0xE67E22  # orange for combat
EMBED_COLOR_LOOT = 0xF1C40F   # gold for treasure
EMBED_COLOR_TRAP = 0x9B59B6   # purple for traps
EMBED_COLOR_REST = 0x2ECC71   # green for rest
EMBED_COLOR_BOSS = 0xC0392B   # dark red for bosses
EMBED_COLOR_DEATH = 0x2C3E50  # dark grey for death
EMBED_COLOR_RETURN = 0x27AE60  # green for safe return


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


def _format_player_effects(combat_state: dict[str, Any] | None) -> str:
    """Return a one-line summary of active player-affecting effects, or ''."""
    if not combat_state:
        return ""
    effs = combat_state.get("player_effects") or []
    if not effs:
        return ""
    parts: list[str] = []
    for e in effs:
        t = e.get("type")
        if t == "advantage_next_attack":
            parts.append("\u2728 Next attack: advantage")
        elif t == "invert_next_attack":
            parts.append("\U0001f501 Next attack: inverted")
        elif t == "hit_chance_reduction":
            pct = int(float(e.get("amount", 0)) * 100)
            parts.append(f"\U0001f32b -{pct}% hit chance")
        elif t == "bleed":
            dmg = int(e.get("damage", 0))
            remaining = int(e.get("remaining", 0))
            parts.append(f"\U0001fa78 Bleed {dmg}/turn ({remaining}t)")
    return "  \u2022  ".join(parts)


def build_combat_embed(
    run, player, monster_data: dict[str, Any],
    narrative: list[str], dungeon_data: dict[str, Any],
    combat_state: dict[str, Any] | None = None,
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
    effects_line = _format_player_effects(combat_state)
    if effects_line:
        embed.add_field(name="Effects", value=effects_line, inline=False)
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
        if is_gear and drop.get("duplicate"):
            sell_gold = drop.get("sell_gold", 0)
            lines.append(f"\u2694\ufe0f ~~{name}~~ \u2192 **{sell_gold}g** (duplicate)")
        elif is_gear and item:
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
        # exploring, or any other state → continue/retreat
        return ContinueView(run.id, user_id, sessionmaker)


def _build_resume_embed(run, player, dungeon_data):
    """Build a 'welcome back' embed showing current run status."""
    state = run.state
    rooms = json.loads(run.rooms_json)
    room_num = run.room_index + 1
    total_rooms = len(rooms)

    embed = discord.Embed(
        title=f"{dungeon_data['name']} — Floor {run.floor}",
        color=EMBED_COLOR,
    )

    if state in ("combat", "boss"):
        monster_name = run.monster_id or "???"
        # Try to get actual monster name from room data (apply variant if present).
        if run.room_index < len(rooms):
            room_data = rooms[run.room_index]
            monster_data = room_data.get("monster", {})
            try:
                cs = json.loads(run.combat_state_json or "{}")
            except (json.JSONDecodeError, TypeError):
                cs = {}
            monster_data = dungeon_logic.apply_variant(monster_data, cs.get("variant"))
            monster_name = monster_data.get("name", monster_name)
        prefix = "**BOSS:** " if state == "boss" else ""
        embed.color = EMBED_COLOR_BOSS if state == "boss" else EMBED_COLOR_COMBAT
        embed.description = (
            f"Room {room_num}/{total_rooms}\n\n"
            f"*You steel yourself and re-enter the fray...*\n\n"
            f"{prefix}A **{monster_name}** stands before you!\n"
            f"Monster HP: {run.monster_hp}/{run.monster_max_hp} "
            f"{_hp_bar(run.monster_hp, run.monster_max_hp)}"
        )
    elif state == "floor_complete":
        embed.description = (
            f"*You return to the stairwell...*\n\n"
            f"Floor {run.floor} is clear. You can descend deeper or retreat."
        )
    else:
        embed.description = (
            f"Room {room_num}/{total_rooms}\n\n"
            f"*You dust yourself off and press on...*"
        )

    embed.set_footer(text=_status_line(run, player))
    return embed
