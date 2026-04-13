from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BestiaryEntry, DungeonPlayer, DungeonRun, PlayerGear, PlayerItem


# ---------------------------------------------------------------------------
# Player data
# ---------------------------------------------------------------------------


async def get_player(
    session: AsyncSession, user_id: int, guild_id: int
) -> DungeonPlayer | None:
    result = await session.execute(
        select(DungeonPlayer).where(
            DungeonPlayer.user_id == user_id,
            DungeonPlayer.guild_id == guild_id,
        )
    )
    return result.scalars().first()


async def get_or_create_player(
    session: AsyncSession, user_id: int, guild_id: int
) -> DungeonPlayer:
    player = await get_player(session, user_id, guild_id)
    if player is not None:
        return player
    player = DungeonPlayer(user_id=user_id, guild_id=guild_id)
    session.add(player)
    await session.commit()
    await session.refresh(player)
    return player


async def update_player(
    session: AsyncSession, user_id: int, guild_id: int, **kwargs
) -> DungeonPlayer | None:
    player = await get_player(session, user_id, guild_id)
    if player is None:
        return None
    for key, value in kwargs.items():
        setattr(player, key, value)
    await session.commit()
    await session.refresh(player)
    return player


# ---------------------------------------------------------------------------
# Dungeon runs
# ---------------------------------------------------------------------------


async def get_active_run(
    session: AsyncSession, user_id: int, guild_id: int
) -> DungeonRun | None:
    result = await session.execute(
        select(DungeonRun).where(
            DungeonRun.user_id == user_id,
            DungeonRun.guild_id == guild_id,
            DungeonRun.active == True,  # noqa: E712
        )
    )
    return result.scalars().first()


async def get_run(
    session: AsyncSession, run_id: int
) -> DungeonRun | None:
    result = await session.execute(
        select(DungeonRun).where(DungeonRun.id == run_id)
    )
    return result.scalars().first()


async def create_run(
    session: AsyncSession, **kwargs
) -> DungeonRun:
    run = DungeonRun(**kwargs)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def update_run(
    session: AsyncSession, run_id: int, **kwargs
) -> DungeonRun | None:
    run = await get_run(session, run_id)
    if run is None:
        return None
    for key, value in kwargs.items():
        setattr(run, key, value)
    await session.commit()
    await session.refresh(run)
    return run


async def end_run(
    session: AsyncSession, run_id: int
) -> DungeonRun | None:
    return await update_run(session, run_id, active=False)


# ---------------------------------------------------------------------------
# Bestiary
# ---------------------------------------------------------------------------


async def upsert_bestiary_entry(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    monster_id: str,
    now: datetime,
    kills: int = 1,
) -> BestiaryEntry:
    """Record a monster encounter — create or increment kill count."""
    result = await session.execute(
        select(BestiaryEntry).where(
            BestiaryEntry.user_id == user_id,
            BestiaryEntry.guild_id == guild_id,
            BestiaryEntry.monster_id == monster_id,
        )
    )
    existing = result.scalars().first()

    if existing:
        existing.kill_count += kills
        await session.commit()
        await session.refresh(existing)
        return existing

    entry = BestiaryEntry(
        user_id=user_id,
        guild_id=guild_id,
        monster_id=monster_id,
        kill_count=kills,
        first_seen_at=now,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def get_bestiary_entries(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[BestiaryEntry]:
    result = await session.execute(
        select(BestiaryEntry).where(
            BestiaryEntry.user_id == user_id,
            BestiaryEntry.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Player gear inventory
# ---------------------------------------------------------------------------


async def get_player_gear(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[PlayerGear]:
    result = await session.execute(
        select(PlayerGear).where(
            PlayerGear.user_id == user_id,
            PlayerGear.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


async def has_gear(
    session: AsyncSession, user_id: int, guild_id: int, gear_id: str
) -> bool:
    """Check if a player owns a specific gear piece (in inventory or equipped)."""
    result = await session.execute(
        select(PlayerGear).where(
            PlayerGear.user_id == user_id,
            PlayerGear.guild_id == guild_id,
            PlayerGear.gear_id == gear_id,
        )
    )
    return result.scalars().first() is not None


async def add_gear(
    session: AsyncSession, user_id: int, guild_id: int, gear_id: str
) -> PlayerGear:
    """Add a gear piece to the player's inventory."""
    entry = PlayerGear(user_id=user_id, guild_id=guild_id, gear_id=gear_id)
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def remove_gear(
    session: AsyncSession, user_id: int, guild_id: int, gear_id: str
) -> bool:
    """Remove a gear piece from the player's inventory. Returns True if found."""
    result = await session.execute(
        select(PlayerGear).where(
            PlayerGear.user_id == user_id,
            PlayerGear.guild_id == guild_id,
            PlayerGear.gear_id == gear_id,
        )
    )
    entry = result.scalars().first()
    if entry is None:
        return False
    await session.delete(entry)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Player item (consumable) inventory
# ---------------------------------------------------------------------------


async def get_player_items(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[PlayerItem]:
    result = await session.execute(
        select(PlayerItem).where(
            PlayerItem.user_id == user_id,
            PlayerItem.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


async def add_item(
    session: AsyncSession, user_id: int, guild_id: int, item_id: str, quantity: int = 1
) -> PlayerItem:
    """Add consumable items (or increment quantity if already owned)."""
    result = await session.execute(
        select(PlayerItem).where(
            PlayerItem.user_id == user_id,
            PlayerItem.guild_id == guild_id,
            PlayerItem.item_id == item_id,
        )
    )
    existing = result.scalars().first()
    if existing:
        existing.quantity += quantity
        await session.commit()
        await session.refresh(existing)
        return existing

    entry = PlayerItem(
        user_id=user_id, guild_id=guild_id, item_id=item_id, quantity=quantity
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def remove_item(
    session: AsyncSession, user_id: int, guild_id: int, item_id: str, quantity: int = 1
) -> bool:
    """Remove consumable items. Returns True if successful."""
    result = await session.execute(
        select(PlayerItem).where(
            PlayerItem.user_id == user_id,
            PlayerItem.guild_id == guild_id,
            PlayerItem.item_id == item_id,
        )
    )
    existing = result.scalars().first()
    if existing is None or existing.quantity < quantity:
        return False
    existing.quantity -= quantity
    if existing.quantity <= 0:
        await session.delete(existing)
    await session.commit()
    return True
