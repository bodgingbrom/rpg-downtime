from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GiftLog, Wallet


async def create_wallet(
    session: AsyncSession,
    *,
    user_id: int,
    guild_id: int,
    balance: int = 0,
) -> Wallet:
    wallet = Wallet(user_id=user_id, guild_id=guild_id, balance=balance)
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def get_wallet(
    session: AsyncSession, user_id: int, guild_id: int
) -> Wallet | None:
    result = await session.execute(
        select(Wallet).where(
            Wallet.user_id == user_id, Wallet.guild_id == guild_id
        )
    )
    return result.scalars().first()


async def update_wallet(
    session: AsyncSession, user_id: int, guild_id: int, **kwargs
) -> Wallet | None:
    wallet = await get_wallet(session, user_id, guild_id)
    if wallet is None:
        return None
    for key, value in kwargs.items():
        setattr(wallet, key, value)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def delete_wallet(
    session: AsyncSession, user_id: int, guild_id: int
) -> None:
    wallet = await get_wallet(session, user_id, guild_id)
    if wallet is not None:
        await session.delete(wallet)
        await session.commit()


async def get_gift_total_today(
    session: AsyncSession,
    *,
    sender_id: int,
    recipient_id: int,
    guild_id: int,
    date: str,
) -> int:
    """Return the total coins *sender* has gifted to *recipient* on *date*.

    *date* must be a UTC "YYYY-MM-DD" string. Returns 0 if no entry exists.
    """
    result = await session.execute(
        select(GiftLog).where(
            GiftLog.sender_id == sender_id,
            GiftLog.recipient_id == recipient_id,
            GiftLog.guild_id == guild_id,
            GiftLog.date == date,
        )
    )
    log = result.scalars().first()
    return log.amount if log else 0


async def add_gift(
    session: AsyncSession,
    *,
    sender_id: int,
    recipient_id: int,
    guild_id: int,
    date: str,
    amount: int,
) -> GiftLog:
    """Record *amount* coins as gifted from sender to recipient on *date*.

    Creates a new row if none exists, otherwise increments the existing total.
    Caller is responsible for committing the session.
    """
    result = await session.execute(
        select(GiftLog).where(
            GiftLog.sender_id == sender_id,
            GiftLog.recipient_id == recipient_id,
            GiftLog.guild_id == guild_id,
            GiftLog.date == date,
        )
    )
    log = result.scalars().first()
    if log is None:
        log = GiftLog(
            sender_id=sender_id,
            recipient_id=recipient_id,
            guild_id=guild_id,
            date=date,
            amount=amount,
        )
        session.add(log)
    else:
        log.amount += amount
    return log
