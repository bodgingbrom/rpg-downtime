from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Wallet


async def create_wallet(session: AsyncSession, **kwargs) -> Wallet:
    wallet = Wallet(**kwargs)
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def get_wallet(session: AsyncSession, user_id: int) -> Wallet | None:
    return await session.get(Wallet, user_id)


async def update_wallet(
    session: AsyncSession, user_id: int, **kwargs
) -> Wallet | None:
    wallet = await get_wallet(session, user_id)
    if wallet is None:
        return None
    for key, value in kwargs.items():
        setattr(wallet, key, value)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def delete_wallet(session: AsyncSession, user_id: int) -> None:
    wallet = await get_wallet(session, user_id)
    if wallet is not None:
        await session.delete(wallet)
        await session.commit()
