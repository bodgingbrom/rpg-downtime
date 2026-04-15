from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PlayerProfile


async def get_profile(
    session: AsyncSession, user_id: int, guild_id: int
) -> PlayerProfile | None:
    result = await session.execute(
        select(PlayerProfile).where(
            PlayerProfile.user_id == user_id,
            PlayerProfile.guild_id == guild_id,
        )
    )
    return result.scalars().first()


async def get_or_create_profile(
    session: AsyncSession, user_id: int, guild_id: int
) -> PlayerProfile:
    profile = await get_profile(session, user_id, guild_id)
    if profile is not None:
        return profile
    profile = PlayerProfile(user_id=user_id, guild_id=guild_id)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_race(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    race: str,
    *,
    is_change: bool = False,
) -> PlayerProfile:
    """Set a player's race.

    If *is_change* is True the ``race_changes`` counter is incremented
    (used for escalating cost calculation).
    """
    profile = await get_or_create_profile(session, user_id, guild_id)
    profile.race = race
    profile.chosen_at = datetime.utcnow()
    if is_change:
        profile.race_changes += 1
    await session.commit()
    await session.refresh(profile)
    return profile
