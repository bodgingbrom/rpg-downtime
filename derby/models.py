from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class Racer(Base):
    __tablename__ = "racers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    retired: Mapped[bool] = mapped_column(Boolean, default=False)
    speed: Mapped[int] = mapped_column(Integer, default=0)
    cornering: Mapped[int] = mapped_column(Integer, default=0)
    stamina: Mapped[int] = mapped_column(Integer, default=0)
    temperament: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[int] = mapped_column(Integer, default=0)
    injuries: Mapped[str] = mapped_column(String, default="")


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    racer_id: Mapped[int] = mapped_column(ForeignKey("racers.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)


class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)


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
    retirement_threshold: Mapped[int] = mapped_column(Integer, default=65)


__all__ = [
    "Base",
    "Racer",
    "Race",
    "Bet",
    "Wallet",
    "CourseSegment",
    "GuildSettings",
]
