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

# Minimum player level to buy each rarity tier
RARITY_LEVEL_GATES: dict[str, int] = {
    "common": 1,
    "uncommon": 3,
    "rare": 5,
    "epic": 8,
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
    """Load all dungeon YAML files, keyed by filename stem. Cached.

    Validates the new optional monster fields (abilities, variants, phases,
    description_pool) at load time. Validation errors are logged and the
    offending dungeon is SKIPPED rather than crashing the whole bot.
    """
    global _dungeons_cache
    if _dungeons_cache is not None:
        return _dungeons_cache

    # Lazy import to avoid a circular dependency (effects imports nothing
    # from logic, but logic-time validation can be heavy at module import).
    from dungeon import effects as _effects
    from dungeon import explore as _explore

    dungeons: dict[str, dict[str, Any]] = {}
    for yaml_file in sorted(glob.glob(os.path.join(_DUNGEONS_DIR, "*.yaml"))):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not data:
                continue
            key = Path(yaml_file).stem
            errors: list[str] = []
            for f_idx, floor in enumerate(data.get("floors", []) or []):
                for m_idx, monster in enumerate(floor.get("monsters", []) or []):
                    errors.extend(
                        _effects.validate_monster(
                            monster, path=f"[{key}] floor[{f_idx}].monsters[{m_idx}]."
                        )
                    )
                boss = floor.get("boss")
                if boss:
                    errors.extend(
                        _effects.validate_monster(boss, path=f"[{key}] floor[{f_idx}].boss.")
                    )
                # v2 room-pool schema validation.
                for r_idx, room in enumerate(floor.get("room_pool") or []):
                    errors.extend(
                        _explore.validate_room(
                            room, path=f"[{key}] floor[{f_idx}].room_pool[{r_idx}]."
                        )
                    )
            # v2 dungeon-level meta — lore_fragments + legendary_reward.
            errors.extend(_explore.validate_dungeon_meta(data, path=f"[{key}] "))
            if errors:
                # Print to stderr; refusal to load keeps the bot healthy for
                # other dungeons, and the authoring error gets surfaced.
                import sys
                for e in errors:
                    print(f"dungeon schema error: {e}", file=sys.stderr)
                continue
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


def get_max_hp(
    constitution: int, accessory_bonus: int = 0, *, hp_multiplier: float = 2.0
) -> int:
    """Max HP derived from CON, plus accessory bonuses.

    *hp_multiplier* scales the CON base (default 2.0).  Sources such as
    racial passives, gear, or buffs should be composed by the caller.
    """
    return int(constitution * hp_multiplier) + accessory_bonus


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


def is_crit(d20_roll: int, *, crit_threshold: int = 20) -> bool:
    """Check if a d20 roll meets or exceeds the crit threshold.

    Default is nat-20 only.  Callers should lower the threshold to
    account for racial passives, gear, or buffs.
    """
    return d20_roll >= crit_threshold


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
    *,
    damage_advantage: bool = False,
    current_hp: int = 0,
    max_hp: int = 1,
    bonus_penalty: int = 0,
    damage_bonus: int = 0,
) -> tuple[int, int]:
    """Calculate player damage. Returns (damage, raw_roll).

    *damage_advantage*: roll dice twice keep higher (e.g. Orc Bloodrage
    when below 50 % HP).  The caller decides when to enable this.
    *bonus_penalty*: subtracted from weapon_bonus (min 0).
    *damage_bonus*: flat additive bonus after all other calculation.
    """
    rng = rng or random.Random()

    # Damage advantage: roll dice twice keep higher
    if damage_advantage:
        roll_a = roll_dice(weapon_dice, rng)
        roll_b = roll_dice(weapon_dice, rng)
        raw_roll = max(roll_a, roll_b)
    else:
        raw_roll = roll_dice(weapon_dice, rng)

    if is_crit_hit:
        raw_roll += roll_dice(weapon_dice, rng)  # double dice on crit

    effective_bonus = max(weapon_bonus - bonus_penalty, 0)

    damage = max(raw_roll + str_mod + effective_bonus + damage_bonus - monster_defense, 1)
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


# ---------------------------------------------------------------------------
# Variant / phase / description helpers (PR 1: reusable combat mechanics)
# ---------------------------------------------------------------------------


def apply_variant(monster_def: dict[str, Any], variant: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``monster_def`` with variant overrides applied.

    Variants contribute via additive deltas where possible so base stats
    remain meaningful:

    - ``hp_delta`` (int): added to ``hp``.
    - ``defense_delta`` (int): added to ``defense``.
    - ``attack_bonus_delta`` (int): added to ``attack_bonus``.
    - ``name_suffix`` (str): appended to ``name`` for display.
    - ``attack_dice`` (str): replaces ``attack_dice`` outright.
    - Any other key is merged in verbatim (``on_hit_effect``,
      ``on_turn_effect``, etc. — read by the combat loop).

    If ``variant`` is None, the monster_def is returned unchanged.
    """
    if not variant:
        return monster_def
    out = dict(monster_def)
    if "hp_delta" in variant:
        out["hp"] = out.get("hp", 0) + int(variant["hp_delta"])
    if "defense_delta" in variant:
        out["defense"] = out.get("defense", 0) + int(variant["defense_delta"])
    if "attack_bonus_delta" in variant:
        out["attack_bonus"] = out.get("attack_bonus", 0) + int(variant["attack_bonus_delta"])
    if "name_suffix" in variant and variant.get("name_suffix"):
        out["name"] = f"{out.get('name', '')} {variant['name_suffix']}".strip()
    if "attack_dice" in variant:
        out["attack_dice"] = variant["attack_dice"]
    # Pass-through for ability-triggering keys the combat loop reads.
    for passthrough in ("on_hit_effect", "on_turn_effect", "description"):
        if passthrough in variant:
            out[passthrough] = variant[passthrough]
    return out


def compute_phase(monster_hp: int, monster_max_hp: int, phases: list[dict[str, Any]] | None) -> int:
    """Return the active phase index based on HP fraction.

    Phases are ordered highest-threshold-first in YAML (e.g. 66 then 33).
    The returned index is the last phase whose ``hp_below_pct`` has been
    crossed. 0 means "baseline, no phase triggered".

    Baseline (no phase triggered) is index 0; phase 1 is phases[0], phase
    2 is phases[1], etc.
    """
    if not phases or monster_max_hp <= 0:
        return 0
    hp_pct = (monster_hp / monster_max_hp) * 100.0
    active = 0
    for i, p in enumerate(phases, start=1):
        threshold = float(p.get("hp_below_pct", 0))
        if hp_pct < threshold:
            active = i
    return active


def pick_description(
    monster_def: dict[str, Any], rng: random.Random | None = None
) -> str | None:
    """Pick a random flavor description from ``description_pool`` if present.

    Returns None if no pool; caller should fall back to the static
    ``description`` field.
    """
    pool = monster_def.get("description_pool")
    if not pool:
        return None
    rng = rng or random.Random()
    return rng.choice(pool)


def get_phase_def(
    monster_def: dict[str, Any], phase_index: int
) -> dict[str, Any] | None:
    """Return the phase dict for ``phase_index`` (1-based), or None."""
    if phase_index <= 0:
        return None
    phases = monster_def.get("phases") or []
    if phase_index > len(phases):
        return None
    return phases[phase_index - 1]


def merge_phase_overrides(
    monster_def: dict[str, Any], phase_index: int
) -> dict[str, Any]:
    """Return a copy of monster_def with phase overrides merged in.

    Phase fields supported:
    - ``attack_dice``: replaces base
    - ``attack_bonus``: replaces base
    - ``defense``: replaces base
    - ``ai``: replaces base ``ai``
    - ``abilities_add``: extends base ``abilities``
    """
    phase = get_phase_def(monster_def, phase_index)
    if phase is None:
        return monster_def
    out = dict(monster_def)
    for key in ("attack_dice", "attack_bonus", "defense", "ai"):
        if key in phase:
            out[key] = phase[key]
    extras = phase.get("abilities_add")
    if extras:
        base_abilities = list(out.get("abilities") or [])
        out["abilities"] = base_abilities + list(extras)
    return out


def check_flee(
    dex: int, rng: random.Random | None = None, *, flee_dc: int = FLEE_BASE_DC
) -> bool:
    """DEX-based flee check. Roll d20 + DEX modifier vs *flee_dc*.

    Callers should adjust the DC for racial passives, gear, or debuffs.
    """
    rng = rng or random.Random()
    roll = rng.randint(1, 20)
    return (roll + get_modifier(dex)) >= flee_dc


def check_trap(
    dex: int, trap_dc: int, rng: random.Random | None = None, *, save_bonus: int = 0
) -> bool:
    """DEX-based trap avoidance. Roll d20 + DEX modifier + *save_bonus* vs DC.

    *save_bonus* is additive — combine racial, gear, and buff bonuses
    before passing.
    """
    rng = rng or random.Random()
    roll = rng.randint(1, 20)
    return (roll + get_modifier(dex) + save_bonus) >= trap_dc


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
    tier: str, rng: random.Random | None = None, *, double_roll: bool = False
) -> int:
    """Roll gold for a treasure room based on tier.

    *double_roll*: roll twice, keep the better result (e.g. Halfling Lucky).
    """
    rng = rng or random.Random()
    gold_range = TREASURE_GOLD.get(tier, TREASURE_GOLD["common"])
    roll = rng.randint(gold_range[0], gold_range[1])
    if double_roll:
        roll = max(roll, rng.randint(gold_range[0], gold_range[1]))
    return roll


def roll_loot_drops(
    loot_table: list[dict[str, Any]],
    rng: random.Random | None = None,
    *,
    loot_chance_bonus: int = 0,
) -> list[dict[str, Any]]:
    """Roll each loot entry against its chance. Returns list of dropped items.

    *loot_chance_bonus*: additive percentage bonus to every drop chance.
    Combine racial, gear, and buff bonuses before passing.
    """
    rng = rng or random.Random()
    drops: list[dict[str, Any]] = []
    for entry in loot_table:
        if rng.randint(1, 100) <= entry.get("chance", 0) + loot_chance_bonus:
            drops.append(entry)
    return drops


# ---------------------------------------------------------------------------
# Shop helpers
# ---------------------------------------------------------------------------


def get_shop_gear(slot: str, player_level: int) -> list[dict[str, Any]]:
    """Return gear available for purchase in a given slot, filtered by level.

    slot should be one of: 'weapons', 'armors', 'accessories'.
    """
    gear = load_gear()
    items = gear.get(slot, [])
    return [
        g for g in items
        if player_level >= RARITY_LEVEL_GATES.get(g.get("rarity", "common"), 1)
        and g.get("shop", True)
    ]


def get_shop_items(player_level: int) -> list[dict[str, Any]]:
    """Return consumable items available for purchase."""
    return list(load_items())


def get_gear_slot(gear_id: str) -> str | None:
    """Determine which equipment slot a gear piece belongs to."""
    gear = load_gear()
    for item in gear.get("weapons", []):
        if item["id"] == gear_id:
            return "weapon"
    for item in gear.get("armors", []):
        if item["id"] == gear_id:
            return "armor"
    for item in gear.get("accessories", []):
        if item["id"] == gear_id:
            return "accessory"
    return None


def check_stat_requirement(
    gear_id: str, player_str: int, player_dex: int
) -> tuple[bool, str]:
    """Check if a player meets the stat requirements for a piece of gear.

    Returns ``(meets_requirement, failure_reason)``.
    """
    gear = get_gear_by_id(gear_id)
    if gear is None:
        return True, ""

    str_req = gear.get("str_requirement", 0)
    if str_req > 0 and player_str < str_req:
        return False, f"Requires STR {str_req} (you have {player_str})"

    dex_req = gear.get("dex_requirement", 0)
    if dex_req > 0 and player_dex < dex_req:
        return False, f"Requires DEX {dex_req} (you have {player_dex})"

    return True, ""


def get_all_monsters() -> list[dict[str, Any]]:
    """Return all unique monsters across every dungeon (regulars + bosses)."""
    seen_ids: set[str] = set()
    monsters: list[dict[str, Any]] = []
    for dungeon_data in load_dungeons().values():
        for floor in dungeon_data.get("floors", []):
            for m in floor.get("monsters", []):
                if m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    monsters.append(m)
            boss = floor.get("boss")
            if boss and boss["id"] not in seen_ids:
                seen_ids.add(boss["id"])
                monsters.append(boss)
    return monsters
