from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class Racer(Base):
    __tablename__ = "racers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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


class CourseSegment(Base):
    __tablename__ = "course_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String, default="")


class GuildSettings(Base):
    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_frequency: Mapped[int] = mapped_column(Integer, default=1)
    default_wallet: Mapped[int] = mapped_column(Integer, default=100)
    retirement_threshold: Mapped[int] = mapped_column(Integer, default=96)


__all__ = [
    "Base",
    "Racer",
    "Race",
    "Bet",
    "CourseSegment",
    "GuildSettings",
]
