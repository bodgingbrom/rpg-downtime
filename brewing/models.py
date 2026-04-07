from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    rarity: Mapped[str] = mapped_column(String, nullable=False)  # free/uncommon/rare
    base_cost: Mapped[int] = mapped_column(Integer, default=0)
    tag_1: Mapped[str] = mapped_column(String, nullable=False)
    tag_2: Mapped[str] = mapped_column(String, nullable=False)
    flavor_text: Mapped[str] = mapped_column(String, default="")


class DangerousTriple(Base):
    __tablename__ = "dangerous_triples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag_1: Mapped[str] = mapped_column(String, nullable=False)
    tag_2: Mapped[str] = mapped_column(String, nullable=False)
    tag_3: Mapped[str] = mapped_column(String, nullable=False)
    instability_value: Mapped[int] = mapped_column(Integer, default=50)


class PlayerIngredient(Base):
    __tablename__ = "player_ingredients"
    __table_args__ = (
        UniqueConstraint("user_id", "guild_id", "ingredient_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)


class BrewSession(Base):
    __tablename__ = "brew_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")
    potency: Mapped[int] = mapped_column(Integer, default=0)
    instability: Mapped[int] = mapped_column(Integer, default=0)
    explosion_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    bottle_cost: Mapped[int] = mapped_column(Integer, default=0)
    ingredient_cost_total: Mapped[int] = mapped_column(Integer, default=0)
    payout: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )


class PlayerPotion(Base):
    __tablename__ = "player_potions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    potion_type: Mapped[str] = mapped_column(String, nullable=False)
    effect_value: Mapped[int] = mapped_column(Integer, nullable=False)
    potion_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BrewIngredient(Base):
    __tablename__ = "brew_ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brew_session_id: Mapped[int] = mapped_column(
        ForeignKey("brew_sessions.id"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id"), nullable=False
    )
    add_order: Mapped[int] = mapped_column(Integer, nullable=False)
    potency_gained: Mapped[int] = mapped_column(Integer, default=0)
    instability_after: Mapped[int] = mapped_column(Integer, default=0)


__all__ = [
    "Ingredient",
    "DangerousTriple",
    "PlayerIngredient",
    "PlayerPotion",
    "BrewSession",
    "BrewIngredient",
]
