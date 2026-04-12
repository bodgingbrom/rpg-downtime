from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FishingPlayer, FishingSession, PlayerBait


# ---------------------------------------------------------------------------
# Player data
# ---------------------------------------------------------------------------


async def get_player(
    session: AsyncSession, user_id: int, guild_id: int
) -> FishingPlayer | None:
    result = await session.execute(
        select(FishingPlayer).where(
            FishingPlayer.user_id == user_id,
            FishingPlayer.guild_id == guild_id,
        )
    )
    return result.scalars().first()


async def get_or_create_player(
    session: AsyncSession, user_id: int, guild_id: int
) -> FishingPlayer:
    player = await get_player(session, user_id, guild_id)
    if player is not None:
        return player
    player = FishingPlayer(user_id=user_id, guild_id=guild_id)
    session.add(player)
    await session.commit()
    await session.refresh(player)
    return player


async def update_player(
    session: AsyncSession, user_id: int, guild_id: int, **kwargs
) -> FishingPlayer | None:
    player = await get_player(session, user_id, guild_id)
    if player is None:
        return None
    for key, value in kwargs.items():
        setattr(player, key, value)
    await session.commit()
    await session.refresh(player)
    return player


# ---------------------------------------------------------------------------
# Bait inventory
# ---------------------------------------------------------------------------


async def get_bait(
    session: AsyncSession, user_id: int, guild_id: int, bait_type: str
) -> PlayerBait | None:
    result = await session.execute(
        select(PlayerBait).where(
            PlayerBait.user_id == user_id,
            PlayerBait.guild_id == guild_id,
            PlayerBait.bait_type == bait_type,
        )
    )
    return result.scalars().first()


async def get_all_bait(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[PlayerBait]:
    result = await session.execute(
        select(PlayerBait).where(
            PlayerBait.user_id == user_id,
            PlayerBait.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


async def add_bait(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    bait_type: str,
    quantity: int,
) -> PlayerBait:
    existing = await get_bait(session, user_id, guild_id, bait_type)
    if existing:
        existing.quantity += quantity
    else:
        existing = PlayerBait(
            user_id=user_id,
            guild_id=guild_id,
            bait_type=bait_type,
            quantity=quantity,
        )
        session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


async def consume_bait(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    bait_type: str,
    amount: int = 1,
) -> bool:
    """Decrement bait quantity. Returns False if insufficient."""
    existing = await get_bait(session, user_id, guild_id, bait_type)
    if existing is None or existing.quantity < amount:
        return False
    existing.quantity -= amount
    await session.commit()
    await session.refresh(existing)
    return True


# ---------------------------------------------------------------------------
# Fishing sessions
# ---------------------------------------------------------------------------


async def get_active_session(
    session: AsyncSession, user_id: int, guild_id: int
) -> FishingSession | None:
    result = await session.execute(
        select(FishingSession).where(
            FishingSession.user_id == user_id,
            FishingSession.guild_id == guild_id,
            FishingSession.active == True,  # noqa: E712
        )
    )
    return result.scalars().first()


async def get_all_due_sessions(
    session: AsyncSession, now: datetime
) -> list[FishingSession]:
    """Return all active sessions whose next catch time has elapsed."""
    result = await session.execute(
        select(FishingSession).where(
            FishingSession.active == True,  # noqa: E712
            FishingSession.next_catch_at <= now,
        )
    )
    return list(result.scalars().all())


async def create_session(
    session: AsyncSession, **kwargs
) -> FishingSession:
    fs = FishingSession(**kwargs)
    session.add(fs)
    await session.commit()
    await session.refresh(fs)
    return fs


async def update_session(
    session: AsyncSession, session_id: int, **kwargs
) -> FishingSession | None:
    result = await session.execute(
        select(FishingSession).where(FishingSession.id == session_id)
    )
    fs = result.scalars().first()
    if fs is None:
        return None
    for key, value in kwargs.items():
        setattr(fs, key, value)
    await session.commit()
    await session.refresh(fs)
    return fs


async def end_session(
    session: AsyncSession, session_id: int
) -> FishingSession | None:
    return await update_session(session, session_id, active=False)
