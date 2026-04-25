from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class DungeonPlayer(Base):
    """Per-player per-guild persistent dungeon character data."""

    __tablename__ = "dungeon_players"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    strength: Mapped[int] = mapped_column(Integer, default=10)
    dexterity: Mapped[int] = mapped_column(Integer, default=10)
    constitution: Mapped[int] = mapped_column(Integer, default=10)
    unspent_stat_points: Mapped[int] = mapped_column(Integer, default=0)
    weapon_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    armor_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    accessory_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    deepest_floor: Mapped[int] = mapped_column(Integer, default=0)
    total_kills: Mapped[int] = mapped_column(Integer, default=0)


class DungeonRun(Base):
    """An active (or completed) dungeon run session."""

    __tablename__ = "dungeon_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dungeon_id: Mapped[str] = mapped_column(String, nullable=False)
    floor: Mapped[int] = mapped_column(Integer, default=1)
    room_index: Mapped[int] = mapped_column(Integer, default=0)
    current_hp: Mapped[int] = mapped_column(Integer, nullable=False)
    max_hp: Mapped[int] = mapped_column(Integer, nullable=False)
    run_gold: Mapped[int] = mapped_column(Integer, default=0)
    run_xp: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String, default="exploring")
    monster_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    monster_hp: Mapped[int] = mapped_column(Integer, default=0)
    monster_max_hp: Mapped[int] = mapped_column(Integer, default=0)
    is_defending: Mapped[bool] = mapped_column(Boolean, default=False)
    found_items_json: Mapped[str] = mapped_column(String, default="[]")
    room_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    rooms_json: Mapped[str] = mapped_column(String, default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stoneblood_used: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-encounter combat state blob — turn counter, phase index, picked variant,
    # picked description, active effects, summon adds, etc. See dungeon/effects.py
    # for the shape. Default "{}" means "not in an encounter" or legacy pre-column row.
    combat_state_json: Mapped[str] = mapped_column(String, default="{}")
    # Per-floor exploration state blob (v2 dungeons only) — the procedural graph,
    # discovered rooms, per-room state (searched features, found items, ambush flags),
    # and danger / wandering counters. See dungeon/explore.py for shape. Default "{}"
    # means "not in a v2 floor" or legacy pre-column row.
    floor_state_json: Mapped[str] = mapped_column(String, default="{}")


class BestiaryEntry(Base):
    """Tracks monster discovery and kill counts per player."""

    __tablename__ = "bestiary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "monster_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    monster_id: Mapped[str] = mapped_column(String, nullable=False)
    kill_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PlayerGear(Base):
    """Owned gear items in a player's inventory (not currently equipped)."""

    __tablename__ = "dungeon_player_gear"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "gear_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    gear_id: Mapped[str] = mapped_column(String, nullable=False)


class PlayerItem(Base):
    """Persistent consumable inventory between runs."""

    __tablename__ = "dungeon_player_items"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)


# ---------------------------------------------------------------------------
# V2 — meta-progression tables.
# ---------------------------------------------------------------------------


class LoreFragment(Base):
    """A lore fragment a player has discovered, persistent across deaths.

    Fragments are numbered 1..N per dungeon. Each (user, guild, dungeon,
    fragment_id) combination has at most one row. Once collected, never
    lost. The full set unlocks the dungeon's legendary completion reward.
    """

    __tablename__ = "dungeon_lore_fragments"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "dungeon_id", "fragment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dungeon_id: Mapped[str] = mapped_column(String, nullable=False)
    fragment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    found_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LegendaryUnlock(Base):
    """Tracks which dungeon legendary rewards a player has claimed.

    A player unlocks a dungeon's legendary item exactly once — by
    collecting all of its lore fragments. The grant happens automatically
    when the final fragment is found. This row records that the grant
    has fired so it doesn't re-fire on future fragment-finds (which
    would be no-ops anyway since fragments don't refresh).
    """

    __tablename__ = "dungeon_legendary_unlocks"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "dungeon_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dungeon_id: Mapped[str] = mapped_column(String, nullable=False)
    item_id: Mapped[str] = mapped_column(String, nullable=False)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Corpse(Base):
    """The player's last-death loot snapshot, keyed per-dungeon.

    One row per (user, guild, dungeon). New deaths overwrite. Cleared
    when the player recovers the corpse during a future delve. Holds
    the floor of death and a JSON-serialized list of items that can be
    recovered.
    """

    __tablename__ = "dungeon_corpses"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "dungeon_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dungeon_id: Mapped[str] = mapped_column(String, nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False)
    # JSON list of {"type": "gold"|"item"|"gear", ...} entries.
    loot_json: Mapped[str] = mapped_column(String, default="[]")
    died_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "DungeonPlayer",
    "DungeonRun",
    "BestiaryEntry",
    "PlayerGear",
    "PlayerItem",
    "LoreFragment",
    "LegendaryUnlock",
    "Corpse",
]
