from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Wallet


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
