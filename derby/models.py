from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class Racer(Base):
    __tablename__ = "racers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    speed: Mapped[int] = mapped_column(Integer, default=0)
    cornering: Mapped[int] = mapped_column(Integer, default=0)
    stamina: Mapped[int] = mapped_column(Integer, default=0)
    temperament: Mapped[str] = mapped_column(String, default="Quirky")
    mood: Mapped[int] = mapped_column(Integer, default=3)
    injuries: Mapped[str] = mapped_column(String, default="")
    injury_races_remaining: Mapped[int] = mapped_column(Integer, default=0)
    races_completed: Mapped[int] = mapped_column(Integer, default=0)
    career_length: Mapped[int] = mapped_column(Integer, default=30)
    peak_end: Mapped[int] = mapped_column(Integer, default=18)
    gender: Mapped[str] = mapped_column(String, default="M")
    sire_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    dam_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    foal_count: Mapped[int] = mapped_column(Integer, default=0)
    breed_cooldown: Mapped[int] = mapped_column(Integer, default=0)
    training_count: Mapped[int] = mapped_column(Integer, default=0)


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    winner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_multiplier: Mapped[float] = mapped_column(Float, default=2.0)


class RaceEntry(Base):
    """Links a racer to a specific race as a participant."""

    __tablename__ = "race_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)


class CourseSegment(Base):
    __tablename__ = "course_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String, default="")


class GuildSettings(Base):
    """Per-guild setting overrides.  Nullable columns mean 'use the global
    default from config.yaml'."""

    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    default_wallet: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    retirement_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bet_window: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    countdown_total: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_racers_per_race: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    commentary_delay: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    channel_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    racer_buy_base: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    racer_buy_multiplier: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    racer_sell_fraction: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    max_racers_per_owner: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    min_pool_size: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    placement_prizes: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    training_base: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    training_multiplier: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    rest_cost: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    feed_cost: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    stable_upgrade_costs: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    female_buy_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    retired_sell_penalty: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    foal_sell_penalty: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    min_training_to_race: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    breeding_fee: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    breeding_cooldown: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    min_races_to_breed: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_foals_per_female: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class PlayerData(Base):
    """Per-player per-guild data such as stable slot upgrades."""

    __tablename__ = "player_data"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extra_slots: Mapped[int] = mapped_column(Integer, default=0)


__all__ = [
    "Base",
    "Racer",
    "Race",
    "RaceEntry",
    "Bet",
    "CourseSegment",
    "GuildSettings",
    "PlayerData",
]
