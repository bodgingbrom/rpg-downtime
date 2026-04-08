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
    rank: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    tournament_wins: Mapped[int] = mapped_column(Integer, default=0)
    tournament_placements: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    pool_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    npc_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    winner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    placements: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    biggest_payout: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    biggest_payout_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    biggest_payout_racer_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_multiplier: Mapped[float] = mapped_column(Float, default=2.0)
    bet_type: Mapped[str] = mapped_column(String, default="win")
    racer_ids: Mapped[str] = mapped_column(String, default="[]")
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)


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
    racer_flavor: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    race_stat_window: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    daily_min: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    daily_max: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)


class DailyReward(Base):
    """Pre-generated daily check-in rewards for players."""

    __tablename__ = "daily_rewards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM-DD"
    racer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    racer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    flavor_text: Mapped[str] = mapped_column(String, nullable=False)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)


class PlayerData(Base):
    """Per-player per-guild data such as stable slot upgrades."""

    __tablename__ = "player_data"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extra_slots: Mapped[int] = mapped_column(Integer, default=0)


class Tournament(Base):
    """A scheduled tournament bracket for a specific rank tier."""

    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/running/finished
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)


class TournamentEntry(Base):
    """Links a racer to a tournament as a participant."""

    __tablename__ = "tournament_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    placement: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    eliminated_round: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    is_pool_filler: Mapped[bool] = mapped_column(Boolean, default=False)


class NPC(Base):
    """A persistent NPC rival trainer with personality and quips."""

    __tablename__ = "npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    personality: Mapped[str] = mapped_column(String, nullable=False)  # archetype label
    personality_desc: Mapped[str] = mapped_column(String, nullable=False)  # LLM context
    rank_min: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "D"
    rank_max: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "C"
    win_quips: Mapped[str] = mapped_column(String, default="[]")  # JSON list
    loss_quips: Mapped[str] = mapped_column(String, default="[]")  # JSON list
    win_quips_used: Mapped[str] = mapped_column(String, default="[]")  # JSON list of indices
    loss_quips_used: Mapped[str] = mapped_column(String, default="[]")  # JSON list of indices
    emoji: Mapped[str] = mapped_column(String, default="")
    catchphrase: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RacerBuff(Base):
    """Temporary potion buff applied to a racer for their next race/tournament."""

    __tablename__ = "racer_buffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buff_type: Mapped[str] = mapped_column(String, nullable=False)  # speed/cornering/stamina/mood/all_stats
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    races_remaining: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


__all__ = [
    "Base",
    "Racer",
    "Race",
    "RaceEntry",
    "Bet",
    "CourseSegment",
    "DailyReward",
    "GuildSettings",
    "PlayerData",
    "Tournament",
    "TournamentEntry",
    "NPC",
    "RacerBuff",
]
