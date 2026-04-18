from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DailyCatchSummary, FishCatch, FishingPlayer, FishingSession, PlayerBait


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
    """Return the player's active session in any mode (AFK or active)."""
    result = await session.execute(
        select(FishingSession).where(
            FishingSession.user_id == user_id,
            FishingSession.guild_id == guild_id,
            FishingSession.active == True,  # noqa: E712
        )
    )
    return result.scalars().first()


async def get_session_by_id(
    session: AsyncSession, session_id: int
) -> FishingSession | None:
    result = await session.execute(
        select(FishingSession).where(FishingSession.id == session_id)
    )
    return result.scalars().first()


async def get_orphaned_active_sessions(
    session: AsyncSession,
) -> list[FishingSession]:
    """All still-active active-mode sessions (used for startup cleanup)."""
    result = await session.execute(
        select(FishingSession).where(
            FishingSession.active == True,  # noqa: E712
            FishingSession.mode == "active",
        )
    )
    return list(result.scalars().all())


async def get_all_due_sessions(
    session: AsyncSession, now: datetime
) -> list[FishingSession]:
    """Return all AFK-mode active sessions whose next catch time has elapsed.

    Active-mode sessions are managed by their own asyncio task runner,
    not the scheduler tick.
    """
    result = await session.execute(
        select(FishingSession).where(
            FishingSession.active == True,  # noqa: E712
            FishingSession.mode == "afk",
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


# ---------------------------------------------------------------------------
# XP
# ---------------------------------------------------------------------------


async def add_xp(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    xp_amount: int,
) -> tuple[FishingPlayer, int, int]:
    """Add XP to a player and return (player, old_level, new_level)."""
    from . import logic as fish_logic

    player = await get_or_create_player(session, user_id, guild_id)
    old_level = fish_logic.get_level(player.fishing_xp)
    player.fishing_xp += xp_amount
    new_level = fish_logic.get_level(player.fishing_xp)
    await session.commit()
    await session.refresh(player)
    return player, old_level, new_level


# ---------------------------------------------------------------------------
# Fish catch log
# ---------------------------------------------------------------------------


async def upsert_fish_catch(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    fish_name: str,
    location_name: str,
    rarity: str,
    length: int,
    value: int,
    now: datetime,
) -> FishCatch:
    """Record a catch — create or update the species entry."""
    result = await session.execute(
        select(FishCatch).where(
            FishCatch.user_id == user_id,
            FishCatch.guild_id == guild_id,
            FishCatch.fish_name == fish_name,
            FishCatch.location_name == location_name,
        )
    )
    existing = result.scalars().first()

    if existing:
        existing.catch_count += 1
        existing.last_caught_at = now
        if length > existing.best_length:
            existing.best_length = length
        if value > existing.best_value:
            existing.best_value = value
        await session.commit()
        await session.refresh(existing)
        return existing

    catch = FishCatch(
        user_id=user_id,
        guild_id=guild_id,
        fish_name=fish_name,
        location_name=location_name,
        rarity=rarity,
        best_length=length,
        best_value=value,
        catch_count=1,
        first_caught_at=now,
        last_caught_at=now,
    )
    session.add(catch)
    await session.commit()
    await session.refresh(catch)
    return catch


async def get_fish_catches_for_location(
    session: AsyncSession, user_id: int, guild_id: int, location_name: str
) -> list[FishCatch]:
    result = await session.execute(
        select(FishCatch).where(
            FishCatch.user_id == user_id,
            FishCatch.guild_id == guild_id,
            FishCatch.location_name == location_name,
        )
    )
    return list(result.scalars().all())


async def get_all_fish_catches(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[FishCatch]:
    result = await session.execute(
        select(FishCatch).where(
            FishCatch.user_id == user_id,
            FishCatch.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


async def get_caught_species_at_location(
    session: AsyncSession, user_id: int, guild_id: int, location_name: str
) -> set[str]:
    result = await session.execute(
        select(FishCatch.fish_name).where(
            FishCatch.user_id == user_id,
            FishCatch.guild_id == guild_id,
            FishCatch.location_name == location_name,
        )
    )
    return {row[0] for row in result.all()}


# ---------------------------------------------------------------------------
# Daily catch summary (for digest)
# ---------------------------------------------------------------------------


async def upsert_daily_summary(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    date_str: str,
    catch: dict,
) -> DailyCatchSummary:
    """Update or create the daily catch summary for a player."""
    result = await session.execute(
        select(DailyCatchSummary).where(
            DailyCatchSummary.user_id == user_id,
            DailyCatchSummary.guild_id == guild_id,
            DailyCatchSummary.date == date_str,
        )
    )
    existing = result.scalars().first()

    is_fish = not catch.get("is_trash", True)
    value = catch.get("value", 0)
    length = catch.get("length") or 0
    name = catch.get("name", "")

    if existing:
        if is_fish:
            existing.total_fish += 1
        existing.total_coins += value
        if length > (existing.biggest_catch_length or 0):
            existing.biggest_catch_name = name
            existing.biggest_catch_length = length
            existing.biggest_catch_value = value
        await session.commit()
        await session.refresh(existing)
        return existing

    summary = DailyCatchSummary(
        user_id=user_id,
        guild_id=guild_id,
        date=date_str,
        total_fish=1 if is_fish else 0,
        total_coins=value,
        biggest_catch_name=name if is_fish else None,
        biggest_catch_length=length if is_fish else None,
        biggest_catch_value=value if is_fish else None,
    )
    session.add(summary)
    await session.commit()
    await session.refresh(summary)
    return summary


async def get_guild_daily_summaries(
    session: AsyncSession, guild_id: int, date_str: str
) -> list[DailyCatchSummary]:
    result = await session.execute(
        select(DailyCatchSummary).where(
            DailyCatchSummary.guild_id == guild_id,
            DailyCatchSummary.date == date_str,
        )
    )
    return list(result.scalars().all())
