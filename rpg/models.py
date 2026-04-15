from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class PlayerProfile(Base):
    __tablename__ = "player_profiles"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race: Mapped[str] = mapped_column(String, nullable=False, default="human")
    race_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chosen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )


__all__ = ["PlayerProfile"]
