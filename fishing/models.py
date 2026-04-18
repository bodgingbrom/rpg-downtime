from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class FishingPlayer(Base):
    """Per-player per-guild persistent fishing data (rod, preferences)."""

    __tablename__ = "fishing_players"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rod_id: Mapped[str] = mapped_column(String, default="basic")
    notify_on_catch: Mapped[bool] = mapped_column(Boolean, default=False)
    fishing_xp: Mapped[int] = mapped_column(Integer, default=0)


class PlayerBait(Base):
    """Per-player per-guild bait inventory, one row per bait type."""

    __tablename__ = "player_bait"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "bait_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bait_type: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)


class FishingSession(Base):
    """An active (or completed) fishing session."""

    __tablename__ = "fishing_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    rod_id: Mapped[str] = mapped_column(String, nullable=False)
    bait_type: Mapped[str] = mapped_column(String, nullable=False)
    bait_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    next_catch_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_fish: Mapped[int] = mapped_column(Integer, default=0)
    total_coins: Mapped[int] = mapped_column(Integer, default=0)
    last_catch_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    last_catch_value: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    last_catch_length: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "afk" (scheduler-driven, passive) or "active" (asyncio-driven, interactive)
    mode: Mapped[str] = mapped_column(String, default="afk", nullable=False)


class FishCatch(Base):
    """Tracks species discovery and records per player per location."""

    __tablename__ = "fish_catches"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "fish_name", "location_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fish_name: Mapped[str] = mapped_column(String, nullable=False)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    rarity: Mapped[str] = mapped_column(String, nullable=False)
    best_length: Mapped[int] = mapped_column(Integer, default=0)
    best_value: Mapped[int] = mapped_column(Integer, default=0)
    catch_count: Mapped[int] = mapped_column(Integer, default=0)
    first_caught_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_caught_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DailyCatchSummary(Base):
    """Per-user daily fishing totals for the daily digest."""

    __tablename__ = "daily_catch_summaries"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    total_fish: Mapped[int] = mapped_column(Integer, default=0)
    total_coins: Mapped[int] = mapped_column(Integer, default=0)
    biggest_catch_name: Mapped[str | None] = mapped_column(String, nullable=True)
    biggest_catch_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    biggest_catch_value: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PlayerHaiku(Base):
    """A completed haiku from an active-mode rare catch.

    Stored per-player, attributable so they can be resurfaced by
    ``/fish haiku random`` as ambient guild poetry.
    """

    __tablename__ = "player_haikus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    fish_species: Mapped[str] = mapped_column(String, nullable=False)
    line_1: Mapped[str] = mapped_column(String, nullable=False)
    line_2: Mapped[str] = mapped_column(String, nullable=False)
    line_3: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LegendaryFish(Base):
    """A unique, persistent legendary fish for a (guild, location).

    At most one is ``active=True`` per (guild, location). When caught, it is
    marked inactive (``caught_by`` and ``caught_at`` set) and a new one is
    generated to take its place. Old legendaries are preserved as history
    for the legendary hall.
    """

    __tablename__ = "legendary_fish"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    species_name: Mapped[str] = mapped_column(String, nullable=False)  # YAML species
    name: Mapped[str] = mapped_column(String, nullable=False)  # unique character name
    personality: Mapped[str] = mapped_column(String, nullable=False)  # full char sheet
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    caught_by: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    caught_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )


class ActiveFishingEventLog(Base):
    """Structured log of LLM-driven active fishing events (uncommon + rare).

    Lets us inspect what the LLM asked, what the player typed, and whether
    it succeeded — useful for tuning prompts and seeing where the judge is
    being too generous/strict. Legendaries use ``legendary_encounters``
    instead; commons are not logged (always pass, high volume).
    """

    __tablename__ = "active_fishing_event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rarity: Mapped[str] = mapped_column(String, nullable=False)  # "uncommon" | "rare"
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    fish_species: Mapped[str] = mapped_column(String, nullable=False)
    # Uncommon: the atmospheric passage. Rare: "line1\nline2".
    prompt_text: Mapped[str] = mapped_column(String, nullable=False)
    # Uncommon: the one word. Rare: the closing line. Empty on timeout.
    player_response: Mapped[str] = mapped_column(String, nullable=False, default="")
    # "caught" (judge PASS) | "escaped" (judge FAIL) | "timeout"
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class LegendaryEncounter(Base):
    """A single encounter between a player and a legendary fish.

    Accumulates per-legendary so the fish can remember past interactions —
    both with this player and with others. Used to seed the LLM's system
    prompt on subsequent encounters.
    """

    __tablename__ = "legendary_encounters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    legendary_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # "caught" | "escaped" | "unconvinced"
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    dialogue_summary: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "FishingPlayer",
    "PlayerBait",
    "FishingSession",
    "FishCatch",
    "DailyCatchSummary",
    "PlayerHaiku",
    "ActiveFishingEventLog",
    "LegendaryFish",
    "LegendaryEncounter",
]
