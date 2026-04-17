from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db_base import Base


class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)


class GiftLog(Base):
    """Tracks coins gifted per (sender, recipient, date) for daily limits.

    The daily window resets at 00:00 UTC. A row is created or updated
    each time a player uses /gift; the ``amount`` column is the cumulative
    total sent from *sender* to *recipient* on *date*.
    """

    __tablename__ = "gift_logs"

    sender_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)  # "YYYY-MM-DD" UTC
    amount: Mapped[int] = mapped_column(Integer, default=0)
