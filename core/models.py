"""Cross-game ORM models.

Models that no single mini-game owns:
  - GuildSettings: per-guild overrides for both global and per-game settings.
    The columns are sectioned by owning game so adding a new game's settings
    column doesn't require editing files inside another game's module.
  - CommandLog: append-only log of every successful command invocation.

Tables register on ``db_base.Base.metadata`` at import time, so anywhere
that calls ``Base.metadata.create_all`` must import this module first.
``derby/scheduler.py`` imports it explicitly for that reason.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class GuildSettings(Base):
    """Per-guild setting overrides.  Nullable columns mean 'use the global
    default from config.yaml'."""

    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ----- Cross-game / global -----
    default_wallet: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    channel_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    daily_min: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    daily_max: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # ----- Per-game channel restrictions -----
    derby_channel: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    brewing_channel: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    fishing_channel: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    dungeon_channel: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    # ----- Derby (Downtime Derby) -----
    retirement_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    bet_window: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    countdown_total: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_racers_per_race: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    commentary_delay: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
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
    racer_flavor: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    race_stat_window: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    racer_emoji: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    max_trains_per_race: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Live per-segment standings bar chart rendered alongside commentary.
    # Nullable → use global default; explicit False turns it off per-guild.
    live_standings_chart: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    # ----- Fishing (Lazy Lures) -----
    fishing_bait_costs: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    fishing_cast_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)


class CommandLog(Base):
    """Append-only log of every successful command invocation."""

    __tablename__ = "command_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    command: Mapped[str] = mapped_column(String, nullable=False)
    cog: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
