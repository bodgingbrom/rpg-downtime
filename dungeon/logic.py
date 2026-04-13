from __future__ import annotations

import glob
import os
import random
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Directory / cache setup
# ---------------------------------------------------------------------------

_DUNGEON_DIR = Path(os.path.realpath(os.path.dirname(__file__)))
_DATA_DIR = _DUNGEON_DIR / "data"
_DUNGEONS_DIR = _DATA_DIR / "dungeons"
_GEAR_PATH = _DATA_DIR / "gear.yaml"
_ITEMS_PATH = _DATA_DIR / "items.yaml"

_gear_cache: dict[str, Any] | None = None
_items_cache: list[dict[str, Any]] | None = None
_dungeons_cache: dict[str, dict[str, Any]] | None = None

# ---------------------------------------------------------------------------
# XP & Level constants
# ---------------------------------------------------------------------------

LEVEL_THRESHOLDS: list[tuple[int, int]] = [
    (1, 0),
    (2, 50),
    (3, 120),
    (4, 220),
    (5, 360),
    (6, 550),
    (7, 800),
    (8, 1100),
    (9, 1500),
    (10, 2000),
]

# Rarity colour mapping for embeds
RARITY_COLORS: dict[str, int] = {
    "common": 0x95A5A6,      # grey
    "uncommon": 0x2ECC71,    # green
    "rare": 0x3498DB,        # blue
    "epic": 0x9B59B6,        # purple
}

# Death penalty
DEATH_GOLD_PENALTY = 0.5  # lose 50% of run gold

# Rest shrine healing
REST_HEAL_FRACTION = 0.30  # heal 30% of max HP

# Flee base DC
FLEE_BASE_DC = 12

# Treasure gold ranges per tier
TREASURE_GOLD: dict[str, tuple[int, int]] = {
    "common": (3, 12),
    "uncommon": (8, 20),
    "rare": (15, 35),
    "epic": (25, 60),
}


# ---------------------------------------------------------------------------
# YAML loading (cached)
# ---------------------------------------------------------------------------


def load_gear() -> dict[str, Any]:
    """Load gear definitions from gear.yaml. Cached after first call."""
    global _gear_cache
    if _gear_cache is not None:
        return _gear_cache
    with open(_GEAR_PATH, "r", encoding="utf-8") as f:
        _gear_cache = yaml.safe_load(f)
    return _gear_cache


def load_items() -> list[dict[str, Any]]:
    """Load consumable item definitions. Cached after first call."""
    global _items_cache
    if _items_cache is not None:
        return _items_cache
    with open(_ITEMS_PATH, "r", encoding="utf-8") as f:
        _items_cache = yaml.safe_load(f)
    return _items_cache


def load_dungeons() -> dict[str, dict[str, Any]]:
    """Load all dungeon YAML files, keyed by filename stem. Cached."""
    global _dungeons_cache
    if _dungeons_cache is not None:
        return _dungeons_cache

    dungeons: dict[str, dict[str, Any]] = {}
    for yaml_file in sorted(glob.glob(os.path.join(_DUNGEONS_DIR, "*.yaml"))):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data:
                key = Path(yaml_file).stem
                dungeons[key] = data
    _dungeons_cache = dungeons
    return dungeons


def get_dungeon(dungeon_key: str) -> dict[str, Any] | None:
    """Return a single dungeon definition by key."""
    return load_dungeons().get(dungeon_key)


def get_gear_by_id(gear_id: str) -> dict[str, Any] | None:
    """Look up a single gear item across all slots."""
    gear = load_gear()
    for slot_key in ("weapons", "armors", "accessories"):
        for item in gear.get(slot_key, []):
            if item["id"] == gear_id:
                return item
    return None


def get_item_by_id(item_id: str) -> dict[str, Any] | None:
    """Look up a consumable item by ID."""
    for item in load_items():
        if item["id"] == item_id:
            return item
    return None


# ---------------------------------------------------------------------------
# Stat calculations
# ---------------------------------------------------------------------------


def get_modifier(stat_value: int) -> int:
    """D&D-style ability modifier: (stat - 10) // 2."""
    return (stat_value - 10) // 2


def get_max_hp(constitution: int, accessory_bonus: int = 0) -> int:
    """Max HP derived from CON. Base = CON * 2, plus accessory bonuses."""
    return constitution * 2 + accessory_bonus


# ---------------------------------------------------------------------------
# XP & Leveling
# ---------------------------------------------------------------------------


def get_level(xp: int) -> int:
    """Return current level based on total XP."""
    level = 1
    for lvl, threshold in LEVEL_THRESHOLDS:
        if xp >= threshold:
            level = lvl
        else:
            break
    return level


def xp_for_next_level(current_xp: int) -> int | None:
    """Return XP needed for the next level, or None if max level."""
    current_level = get_level(current_xp)
    for lvl, threshold in LEVEL_THRESHOLDS:
        if lvl == current_level + 1:
            return threshold - current_xp
    return None


def xp_progress(current_xp: int) -> tuple[int, int] | None:
    """Return (current_xp_in_level, xp_needed_for_level) or None if max."""
    current_level = get_level(current_xp)
    current_threshold = 0
    next_threshold = None
    for lvl, threshold in LEVEL_THRESHOLDS:
        if lvl == current_level:
            current_threshold = threshold
        elif lvl == current_level + 1:
            next_threshold = threshold
            break
    if next_threshold is None:
        return None
    return (current_xp - current_threshold, next_threshold - current_threshold)


# ---------------------------------------------------------------------------
# Equipped gear stats
# ---------------------------------------------------------------------------


def get_weapon_dice(weapon_id: str | None) -> str:
    """Return the dice string for the equipped weapon, or 1d4 (fists)."""
    if weapon_id is None:
        return "1d4"
    weapon = get_gear_by_id(weapon_id)
    if weapon is None:
        return "1d4"
    return weapon.get("dice", "1d4")


def get_weapon_bonus(weapon_id: str | None) -> int:
    """Return the bonus for the equipped weapon."""
    if weapon_id is None:
        return 0
    weapon = get_gear_by_id(weapon_id)
    if weapon is None:
        return 0
    return weapon.get("bonus", 0)


def get_armor_defense(armor_id: str | None) -> int:
    """Return the defense value of equipped armor."""
    if armor_id is None:
        return 0
    armor = get_gear_by_id(armor_id)
    if armor is None:
        return 0
    return armor.get("defense", 0)


def get_accessory_effect(accessory_id: str | None) -> dict[str, Any]:
    """Return the accessory effect dict, or empty dict."""
    if accessory_id is None:
        return {}
    acc = get_gear_by_id(accessory_id)
    if acc is None:
        return {}
    return acc.get("effect", {})


def get_accessory_hp_bonus(accessory_id: str | None) -> int:
    """Return the max HP bonus from an accessory, if any."""
    effect = get_accessory_effect(accessory_id)
    if effect.get("type") == "max_hp_bonus":
        return effect.get("value", 0)
    return 0


def get_crit_bonus(accessory_id: str | None) -> int:
    """Return the crit chance bonus % from an accessory."""
    effect = get_accessory_effect(accessory_id)
    if effect.get("type") == "crit_bonus":
        return effect.get("value", 0)
    return 0


# ---------------------------------------------------------------------------
# Dice rolling
# ---------------------------------------------------------------------------


def roll_dice(dice_str: str, rng: random.Random | None = None) -> int:
    """Parse and roll a dice string like '1d6', '2d8+2'. Returns total."""
    rng = rng or random.Random()
    bonus = 0
    if "+" in dice_str:
        dice_part, bonus_str = dice_str.split("+", 1)
        bonus = int(bonus_str)
    elif dice_str.count("-") > 0 and "d" in dice_str:
        # Handle negative bonus like "1d4-1", but not the 'd' separator
        parts = dice_str.split("d", 1)
        if "-" in parts[1]:
            die_part, neg_bonus = parts[1].rsplit("-", 1)
            dice_part = f"{parts[0]}d{die_part}"
            bonus = -int(neg_bonus)
        else:
            dice_part = dice_str
    else:
        dice_part = dice_str

    count_str, sides_str = dice_part.split("d", 1)
    count = int(count_str) if count_str else 1
    sides = int(sides_str)

    total = sum(rng.randint(1, sides) for _ in range(count)) + bonus
    return max(total, 0)


def roll_d20(rng: random.Random | None = None) -> int:
    """Roll a d20."""
    rng = rng or random.Random()
    return rng.randint(1, 20)


def is_crit(d20_roll: int) -> bool:
    """Check if a d20 roll is a natural 20 (5% crit)."""
    return d20_roll == 20


# ---------------------------------------------------------------------------
# Room generation
# ---------------------------------------------------------------------------


def generate_rooms(
    floor_data: dict[str, Any], seed: int
) -> list[dict[str, Any]]:
    """Generate a procedural room sequence for a dungeon floor.

    Returns a list of room dicts with 'type' and associated data.
    Boss room is always last.
    """
    rng = random.Random(seed)
    room_range = floor_data.get("rooms", [5, 6])
    num_rooms = rng.randint(room_range[0], room_range[1])

    weights = floor_data.get("room_weights", {
        "combat": 50, "treasure": 20, "trap": 15, "rest": 15,
    })

    # Build the weighted pool
    room_types = list(weights.keys())
    room_weights = [weights[rt] for rt in room_types]

    rooms: list[dict[str, Any]] = []

    # Ensure at least one rest shrine if the floor defines them
    rest_count = floor_data.get("rest_shrines", 0)
    guaranteed_rest = rest_count if isinstance(rest_count, int) else 0

    for i in range(num_rooms):
        if guaranteed_rest > 0 and i == num_rooms - 1 and not any(
            r["type"] == "rest" for r in rooms
        ):
            # Force a rest room if we haven't placed one and floor guarantees it
            room_type = "rest"
            guaranteed_rest -= 1
        else:
            room_type = rng.choices(room_types, weights=room_weights, k=1)[0]
            # Never place a rest shrine as the very first room
            if room_type == "rest" and i == 0:
                room_type = "combat"
            elif room_type == "rest" and guaranteed_rest <= 0 and any(
                r["type"] == "rest" for r in rooms
            ):
                # Don't place extra rest rooms beyond what's guaranteed
                room_type = "combat"

        room: dict[str, Any] = {"type": room_type}

        if room_type == "combat":
            monsters = floor_data.get("monsters", [])
            if monsters:
                room["monster"] = rng.choice(monsters)
        elif room_type == "trap":
            traps = floor_data.get("traps", [])
            if traps:
                room["trap"] = rng.choice(traps)
            else:
                room["type"] = "combat"
                monsters = floor_data.get("monsters", [])
                if monsters:
                    room["monster"] = rng.choice(monsters)
        elif room_type == "treasure":
            room["tier"] = floor_data.get("treasure_tier", "common")
        elif room_type == "rest":
            pass  # Rest rooms just heal

        rooms.append(room)

    # Boss room is always last
    boss_data = floor_data.get("boss")
    if boss_data:
        rooms.append({"type": "boss", "monster": boss_data})

    return rooms


def get_floor_data(dungeon_data: dict[str, Any], floor_num: int) -> dict[str, Any] | None:
    """Get floor data for a specific floor number from a dungeon definition."""
    for floor in dungeon_data.get("floors", []):
        if floor.get("floor") == floor_num:
            return floor
    return None


def get_max_floor(dungeon_data: dict[str, Any]) -> int:
    """Return the highest floor number in a dungeon."""
    floors = dungeon_data.get("floors", [])
    if not floors:
        return 0
    return max(f.get("floor", 0) for f in floors)


# ---------------------------------------------------------------------------
# Combat logic
# ---------------------------------------------------------------------------


def calc_player_damage(
    weapon_dice: str,
    str_mod: int,
    weapon_bonus: int,
    monster_defense: int,
    is_crit_hit: bool,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    """Calculate player damage. Returns (damage, raw_roll)."""
    rng = rng or random.Random()
    raw_roll = roll_dice(weapon_dice, rng)
    if is_crit_hit:
        raw_roll += roll_dice(weapon_dice, rng)  # double dice on crit
    damage = max(raw_roll + str_mod + weapon_bonus - monster_defense, 1)
    return damage, raw_roll


def calc_monster_damage(
    attack_dice: str,
    attack_bonus: int,
    player_armor: int,
    is_player_defending: bool,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    """Calculate monster damage. Returns (damage, raw_roll)."""
    rng = rng or random.Random()
    raw_roll = roll_dice(attack_dice, rng)
    damage = max(raw_roll + attack_bonus - player_armor, 1)
    if is_player_defending:
        damage = max(damage // 2, 1)
    return damage, raw_roll


def select_monster_action(ai_weights: dict[str, int], rng: random.Random | None = None) -> str:
    """Pick a monster action based on weighted random."""
    rng = rng or random.Random()
    actions = list(ai_weights.keys())
    weights = [ai_weights[a] for a in actions]
    return rng.choices(actions, weights=weights, k=1)[0]


def check_flee(dex: int, rng: random.Random | None = None) -> bool:
    """DEX-based flee check. Roll d20 + DEX modifier vs FLEE_BASE_DC."""
    rng = rng or random.Random()
    roll = rng.randint(1, 20)
    return (roll + get_modifier(dex)) >= FLEE_BASE_DC


def check_trap(dex: int, trap_dc: int, rng: random.Random | None = None) -> bool:
    """DEX-based trap avoidance. Roll d20 + DEX modifier vs trap DC."""
    rng = rng or random.Random()
    roll = rng.randint(1, 20)
    return (roll + get_modifier(dex)) >= trap_dc


def roll_trap_damage(
    damage_range: list[int], rng: random.Random | None = None
) -> int:
    """Roll random damage within a trap's damage range."""
    rng = rng or random.Random()
    return rng.randint(damage_range[0], damage_range[1])


def roll_monster_gold(
    gold_range: list[int], rng: random.Random | None = None
) -> int:
    """Roll gold drop for a monster."""
    rng = rng or random.Random()
    return rng.randint(gold_range[0], gold_range[1])


def roll_treasure_gold(
    tier: str, rng: random.Random | None = None
) -> int:
    """Roll gold for a treasure room based on tier."""
    rng = rng or random.Random()
    gold_range = TREASURE_GOLD.get(tier, TREASURE_GOLD["common"])
    return rng.randint(gold_range[0], gold_range[1])


def roll_loot_drops(
    loot_table: list[dict[str, Any]], rng: random.Random | None = None
) -> list[dict[str, Any]]:
    """Roll each loot entry against its chance. Returns list of dropped items."""
    rng = rng or random.Random()
    drops: list[dict[str, Any]] = []
    for entry in loot_table:
        if rng.randint(1, 100) <= entry.get("chance", 0):
            drops.append(entry)
    return drops
