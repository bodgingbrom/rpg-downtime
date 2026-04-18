from __future__ import annotations

import glob
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
import yaml

# ---------------------------------------------------------------------------
# Bait definitions (simple enough to keep in code)
# ---------------------------------------------------------------------------

BAIT_TYPES: dict[str, dict[str, Any]] = {
    "worm": {"name": "Worm", "cost": 2, "cast_reduction": 0.0, "preference_boost": 1.5},
    "insect": {"name": "Insect", "cost": 5, "cast_reduction": 0.02, "preference_boost": 1.8},
    "shiny_lure": {"name": "Shiny Lure", "cost": 12, "cast_reduction": 0.05, "preference_boost": 2.0},
    "premium": {"name": "Premium Bait", "cost": 20, "cast_reduction": 0.08, "preference_boost": 2.5},
}

# Module-level caches
_rods_cache: dict[str, dict[str, Any]] | None = None
_locations_cache: dict[str, dict[str, Any]] | None = None

_FISHING_DIR = Path(os.path.realpath(os.path.dirname(__file__)))
_RODS_PATH = _FISHING_DIR / "rods.yaml"
_LOCATIONS_DIR = _FISHING_DIR / "locations"

# ---------------------------------------------------------------------------
# XP & Level constants
# ---------------------------------------------------------------------------

XP_PER_RARITY: dict[str, int] = {
    "trash": 1,
    "common": 5,
    "uncommon": 15,
    "rare": 40,
    "legendary": 100,
}

LEVEL_THRESHOLDS: list[tuple[int, int]] = [
    (1, 0),
    (2, 100),
    (3, 300),
    (4, 600),
    (5, 1000),
]

SKILL_BONUS_PER_LEVEL = 0.02   # 2% cast reduction per level above requirement
TROPHY_CAST_REDUCTION = 0.10   # 10% cast reduction at trophy locations

# Rarity colour mapping for embeds
_RARITY_COLORS = {
    "common": 0x95A5A6,     # grey
    "uncommon": 0x3498DB,   # blue
    "rare": 0x9B59B6,       # purple
    "legendary": 0xF1C40F,  # gold
}


# ---------------------------------------------------------------------------
# Rod loading
# ---------------------------------------------------------------------------


def load_rods(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load rod definitions from YAML, keyed by rod ``id``.  Cached after
    the first call."""
    global _rods_cache
    if _rods_cache is not None:
        return _rods_cache

    path = path or _RODS_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw: list[dict] = yaml.safe_load(f) or []

    rods = {}
    for rod in raw:
        rods[rod["id"]] = rod
    _rods_cache = rods
    return rods


def get_rod(rod_id: str) -> dict[str, Any]:
    """Return a single rod definition.  Falls back to ``basic`` if the id
    is not found (e.g. a rod was removed from the YAML)."""
    rods = load_rods()
    return rods.get(rod_id, rods["basic"])


def get_upgrade_path(current_rod_id: str) -> dict[str, Any] | None:
    """Return the next rod in tier order, or ``None`` if already at max."""
    rods = load_rods()
    current = rods.get(current_rod_id)
    if current is None:
        current = rods["basic"]

    current_tier = current["tier"]
    # Find the rod with the next-highest tier
    candidates = [r for r in rods.values() if r["tier"] > current_tier]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r["tier"])


# ---------------------------------------------------------------------------
# Location loading
# ---------------------------------------------------------------------------


def load_locations(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load all location YAML files, keyed by filename stem.  Cached after
    the first call."""
    global _locations_cache
    if _locations_cache is not None:
        return _locations_cache

    directory = directory or _LOCATIONS_DIR
    locations: dict[str, dict[str, Any]] = {}
    for yaml_file in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data:
                key = Path(yaml_file).stem
                locations[key] = data
    _locations_cache = locations
    return locations


# ---------------------------------------------------------------------------
# Cast time calculation
# ---------------------------------------------------------------------------


def calculate_cast_time(
    base_cast_time: int,
    rod_data: dict[str, Any],
    bait_type: str,
    skill_reduction: float = 0.0,
    trophy_reduction: float = 0.0,
    cast_multiplier: float = 1.0,
) -> int:
    """Return seconds until next catch.

    Formula: ``base * (1 - rod) * (1 - bait) * (1 - skill) * (1 - trophy) * mult``

    *cast_multiplier* is a final multiplier on cast time.  Callers should
    compose racial, gear, and buff contributions (e.g. ``0.90 * 0.95``).
    """
    rod_reduction = rod_data.get("cast_reduction", 0.0)
    bait_info = BAIT_TYPES.get(bait_type, {})
    bait_reduction = bait_info.get("cast_reduction", 0.0)

    result = (
        base_cast_time
        * (1 - rod_reduction)
        * (1 - bait_reduction)
        * (1 - skill_reduction)
        * (1 - trophy_reduction)
        * cast_multiplier
    )
    return max(int(result), 60)  # minimum 60 seconds


# ---------------------------------------------------------------------------
# Active-mode timing
# ---------------------------------------------------------------------------

ACTIVE_BITE_MIN_BASE = 30   # seconds
ACTIVE_BITE_MAX_BASE = 90   # seconds
ACTIVE_BITE_FLOOR = 15      # seconds (minimum after all reductions)


def calculate_active_cast_time(
    rod_data: dict[str, Any],
    bait_type: str,
    skill_reduction: float = 0.0,
    trophy_reduction: float = 0.0,
    cast_multiplier: float = 1.0,
) -> int:
    """Return seconds until the next active-mode bite.

    Uses a random 30-90s base (not the location's AFK base). All the same
    reductions apply (rod, bait, skill, trophy, racial multiplier), with a
    hard floor of 15s to keep things engaging.
    """
    base = random.randint(ACTIVE_BITE_MIN_BASE, ACTIVE_BITE_MAX_BASE)
    rod_reduction = rod_data.get("cast_reduction", 0.0)
    bait_info = BAIT_TYPES.get(bait_type, {})
    bait_reduction = bait_info.get("cast_reduction", 0.0)
    result = (
        base
        * (1 - rod_reduction)
        * (1 - bait_reduction)
        * (1 - skill_reduction)
        * (1 - trophy_reduction)
        * cast_multiplier
    )
    return max(int(result), ACTIVE_BITE_FLOOR)


# ---------------------------------------------------------------------------
# XP & Level functions
# ---------------------------------------------------------------------------


def calculate_catch_xp(catch: dict[str, Any], location_data: dict[str, Any]) -> int:
    """Return XP earned for a catch, scaled by location difficulty."""
    rarity = "trash" if catch.get("is_trash") else catch.get("rarity", "common")
    base_xp = XP_PER_RARITY.get(rarity, 1)
    skill_level = location_data.get("skill_level", 1)
    return base_xp * skill_level


def get_level(xp: int) -> int:
    """Return the player's fishing level based on cumulative XP."""
    level = 1
    for lvl, threshold in LEVEL_THRESHOLDS:
        if xp >= threshold:
            level = lvl
    return level


def get_xp_for_next_level(xp: int) -> tuple[int, int] | None:
    """Return ``(xp_needed, next_level)`` or ``None`` if at max level."""
    current = get_level(xp)
    for lvl, threshold in LEVEL_THRESHOLDS:
        if lvl > current:
            return (threshold - xp, lvl)
    return None


def can_fish_at_location(player_level: int, location_data: dict[str, Any]) -> bool:
    """Check if a player's level meets the location's skill requirement."""
    return player_level >= location_data.get("skill_level", 1)


def get_skill_cast_reduction(player_level: int, location_skill_level: int) -> float:
    """Return bonus cast reduction from over-leveling a location."""
    levels_above = max(0, player_level - location_skill_level)
    return levels_above * SKILL_BONUS_PER_LEVEL


def has_location_trophy(
    caught_species: set[str], location_data: dict[str, Any]
) -> bool:
    """Check if a player has caught all non-trash species at a location."""
    fish_pool = location_data.get("fish", [])
    all_species = {f["name"] for f in fish_pool}
    return len(all_species) > 0 and all_species.issubset(caught_species)


# ---------------------------------------------------------------------------
# Catch resolution
# ---------------------------------------------------------------------------


def select_catch(
    location_data: dict[str, Any],
    rod_data: dict[str, Any],
    bait_type: str,
    rare_weight_bonus: float = 0.0,
    include_trash: bool = True,
) -> dict[str, Any]:
    """Pick a catch from the location's fish + trash pool.

    Returns a dict with keys:
    ``name``, ``is_trash``, ``rarity`` (or None for trash),
    ``value``, ``length`` (or None for trash).

    *rare_weight_bonus*: additive boost to rare+ fish weight (Human +0.05).
    *include_trash*: when False (active mode), exclude trash from the pool.
    """
    fish_pool: list[dict] = location_data.get("fish", [])
    trash_pool: list[dict] = location_data.get("trash", []) if include_trash else []

    trash_multiplier = rod_data.get("trash_multiplier", 1.0)
    rare_boost = rod_data.get("rare_boost", 0.0) + rare_weight_bonus
    bait_info = BAIT_TYPES.get(bait_type, {})
    preference_boost = bait_info.get("preference_boost", 1.0)

    entries: list[dict[str, Any]] = []
    weights: list[float] = []

    for fish in fish_pool:
        # Skip fish that require a different bait
        required = fish.get("required_bait")
        if required and required != bait_type:
            continue

        weight = float(fish.get("weight", 1))

        # Boost weight if this fish prefers the current bait
        preferred = fish.get("preferred_bait")
        if preferred and preferred == bait_type:
            weight *= preference_boost

        # Boost uncommon+ fish based on rod quality
        rarity = fish.get("rarity", "common")
        if rarity in ("uncommon", "rare", "legendary"):
            weight *= (1 + rare_boost)

        entries.append({"type": "fish", "data": fish})
        weights.append(weight)

    for trash in trash_pool:
        weight = float(trash.get("weight", 1)) * trash_multiplier
        if weight > 0:
            entries.append({"type": "trash", "data": trash})
            weights.append(weight)

    # Fallback: if pool is empty (no matching bait, etc.), return trash
    if not entries:
        return {
            "name": "Nothing",
            "is_trash": True,
            "rarity": None,
            "value": 0,
            "length": None,
        }

    chosen = random.choices(entries, weights=weights, k=1)[0]

    if chosen["type"] == "trash":
        return {
            "name": chosen["data"]["name"],
            "is_trash": True,
            "rarity": None,
            "value": chosen["data"].get("value", 0),
            "length": None,
        }

    fish_data = chosen["data"]
    value_range = fish_data.get("value_range", [1, 1])
    length_range = fish_data.get("length_range", [1, 1])

    return {
        "name": fish_data["name"],
        "is_trash": False,
        "rarity": fish_data.get("rarity", "common"),
        "value": random.randint(value_range[0], value_range[1]),
        "length": random.randint(length_range[0], length_range[1]),
    }


# ---------------------------------------------------------------------------
# Embed building
# ---------------------------------------------------------------------------


def _rarity_emoji(rarity: str | None) -> str:
    if rarity == "legendary":
        return "\u2B50"
    if rarity == "rare":
        return "\U0001F48E"
    if rarity == "uncommon":
        return "\U0001F539"
    return ""


def build_session_embed(
    fs: Any,
    catch: dict[str, Any] | None,
    session_ended: bool,
    end_reason: str | None = None,
) -> discord.Embed:
    """Build the fishing session embed.

    Parameters
    ----------
    fs : FishingSession
        The current session row (or an object with matching attributes).
    catch : dict or None
        The most recent catch result, or ``None`` if no catch yet.
    session_ended : bool
        Whether the session is now over.
    end_reason : str or None
        Optional reason for ending (e.g. "Ran out of bait!").
    """
    rod = get_rod(fs.rod_id)
    bait_name = BAIT_TYPES.get(fs.bait_type, {}).get("name", fs.bait_type)
    locations = load_locations()
    loc = locations.get(fs.location_name, {})
    loc_display = loc.get("name", fs.location_name)

    if session_ended:
        embed = discord.Embed(
            title="\U0001F3A3 Lazy Lures \u2014 Session Complete",
            color=0xF1C40F,
        )
        # Calculate duration
        now = datetime.now(timezone.utc)
        started = fs.started_at
        if started.tzinfo is None:
            from datetime import timezone as _tz
            started = started.replace(tzinfo=_tz.utc)
        duration = now - started
        minutes = int(duration.total_seconds() // 60)
        embed.description = f"Fished at **{loc_display}** for {minutes} minutes."
        if end_reason:
            embed.description += f"\n{end_reason}"

        embed.add_field(name="Fish Caught", value=str(fs.total_fish), inline=True)
        embed.add_field(name="Coins Earned", value=f"{fs.total_coins} coins", inline=True)
        if fs.last_catch_name:
            last = fs.last_catch_name
            if fs.last_catch_length:
                last += f" ({fs.last_catch_length}in)"
            if fs.last_catch_value:
                last += f" \u2014 {fs.last_catch_value} coins"
            embed.add_field(name="Last Catch", value=last, inline=False)
    else:
        # Determine embed colour from last catch rarity
        color = 0x2ECC71  # default green
        if catch:
            if catch["is_trash"]:
                color = 0x95A5A6
            else:
                color = _RARITY_COLORS.get(catch.get("rarity", "common"), 0x2ECC71)

        embed = discord.Embed(
            title=f"\U0001F3A3 Lazy Lures \u2014 {loc_display}",
            color=color,
        )
        embed.add_field(name="Rod", value=rod["name"], inline=True)
        embed.add_field(
            name="Bait",
            value=f"{bait_name} ({fs.bait_remaining} left)",
            inline=True,
        )

        # Last catch
        if catch:
            if catch["is_trash"]:
                catch_text = f"\U0001FAA3 {catch['name']} \u2014 junk!"
            else:
                emoji = _rarity_emoji(catch.get("rarity"))
                catch_text = f"{emoji} **{catch['name']}**"
                if catch.get("length"):
                    catch_text += f" ({catch['length']}in)"
                catch_text += f" \u2014 {catch['value']} coins"
                rarity = catch.get("rarity", "common")
                catch_text += f" [{rarity}]"
            embed.add_field(name="Last Catch", value=catch_text, inline=False)
        elif fs.last_catch_name:
            # Resuming display with no new catch this tick
            last = fs.last_catch_name
            if fs.last_catch_length:
                last += f" ({fs.last_catch_length}in)"
            if fs.last_catch_value:
                last += f" \u2014 {fs.last_catch_value} coins"
            embed.add_field(name="Last Catch", value=last, inline=False)
        else:
            embed.add_field(name="Last Catch", value="Waiting for a bite...", inline=False)

        embed.add_field(
            name="Session",
            value=f"{fs.total_fish} fish | {fs.total_coins} coins",
            inline=True,
        )

        # Next catch ETA
        next_ts = fs.next_catch_at
        if next_ts.tzinfo is None:
            from datetime import timezone as _tz
            next_ts = next_ts.replace(tzinfo=_tz.utc)
        unix_ts = int(next_ts.timestamp())
        embed.add_field(
            name="Next Catch",
            value=f"<t:{unix_ts}:R>",
            inline=True,
        )
        embed.set_footer(text="Use /fish stop to end your session")

    return embed


def build_compact_embed(
    fs: Any,
    catch: dict[str, Any] | None,
    session_ended: bool,
    end_reason: str | None = None,
) -> discord.Embed:
    """Build a minimal public embed for the channel.

    Keeps channel noise low — just a single description line with key info.
    """
    locations = load_locations()
    loc = locations.get(fs.location_name, {})
    loc_display = loc.get("name", fs.location_name)

    if session_ended:
        now = datetime.now(timezone.utc)
        started = fs.started_at
        if started.tzinfo is None:
            from datetime import timezone as _tz
            started = started.replace(tzinfo=_tz.utc)
        minutes = int((now - started).total_seconds() // 60)

        reason = f" {end_reason}" if end_reason else ""
        embed = discord.Embed(
            description=(
                f"\U0001F3A3 **{loc_display}** \u2014 Session over!{reason}\n"
                f"Caught **{fs.total_fish}** fish \u2022 Earned **{fs.total_coins}** coins \u2022 {minutes}min"
            ),
            color=0xF1C40F,
        )
        return embed

    # Active session
    color = 0x2ECC71
    if catch:
        if catch["is_trash"]:
            color = 0x95A5A6
        else:
            color = _RARITY_COLORS.get(catch.get("rarity", "common"), 0x2ECC71)

    # Build the one-liner
    parts = [f"\U0001F3A3 **{loc_display}**"]

    if catch:
        if catch["is_trash"]:
            parts.append(f"\U0001FAA3 {catch['name']} \u2014 junk!")
        else:
            emoji = _rarity_emoji(catch.get("rarity"))
            length_str = f" ({catch['length']}in)" if catch.get("length") else ""
            parts.append(f"{emoji} {catch['name']}{length_str} \u2014 {catch['value']} coins")
    else:
        parts.append("Waiting for a bite...")

    parts.append(
        f"{fs.total_fish} fish \u2022 {fs.total_coins} coins \u2022 "
        f"{fs.bait_remaining} bait left"
    )

    embed = discord.Embed(
        description="\n".join(parts),
        color=color,
    )
    return embed
