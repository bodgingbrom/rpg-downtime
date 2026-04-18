from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ActiveFishingEventLog,
    DailyCatchSummary,
    FishCatch,
    FishingPlayer,
    FishingSession,
    LegendaryEncounter,
    LegendaryFish,
    PlayerBait,
    PlayerHaiku,
)


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


# ---------------------------------------------------------------------------
# Player haikus (completed rare catches in active mode)
# ---------------------------------------------------------------------------


async def save_haiku(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    location_name: str,
    fish_species: str,
    line_1: str,
    line_2: str,
    line_3: str,
    created_at: datetime,
) -> PlayerHaiku:
    """Store a completed haiku from a successful rare catch."""
    haiku = PlayerHaiku(
        user_id=user_id,
        guild_id=guild_id,
        location_name=location_name,
        fish_species=fish_species,
        line_1=line_1,
        line_2=line_2,
        line_3=line_3,
        created_at=created_at,
    )
    session.add(haiku)
    await session.commit()
    await session.refresh(haiku)
    return haiku


async def get_player_haikus(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    limit: int = 10,
) -> list[PlayerHaiku]:
    """Return a player's haikus, most recent first."""
    result = await session.execute(
        select(PlayerHaiku)
        .where(
            PlayerHaiku.user_id == user_id,
            PlayerHaiku.guild_id == guild_id,
        )
        .order_by(PlayerHaiku.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_random_guild_haiku(
    session: AsyncSession, guild_id: int
) -> PlayerHaiku | None:
    """Return a single random haiku from any player in the guild."""
    from sqlalchemy import func

    result = await session.execute(
        select(PlayerHaiku)
        .where(PlayerHaiku.guild_id == guild_id)
        .order_by(func.random())
        .limit(1)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Legendary fish + encounters
# ---------------------------------------------------------------------------


async def get_active_legendary(
    session: AsyncSession, guild_id: int, location_name: str
) -> LegendaryFish | None:
    """Return the currently active legendary for a (guild, location), if any."""
    result = await session.execute(
        select(LegendaryFish).where(
            LegendaryFish.guild_id == guild_id,
            LegendaryFish.location_name == location_name,
            LegendaryFish.active == True,  # noqa: E712
        )
    )
    return result.scalars().first()


async def create_legendary(
    session: AsyncSession,
    guild_id: int,
    location_name: str,
    species_name: str,
    name: str,
    personality: str,
    created_at: datetime,
) -> LegendaryFish:
    """Create a new active legendary for a (guild, location)."""
    legendary = LegendaryFish(
        guild_id=guild_id,
        location_name=location_name,
        species_name=species_name,
        name=name,
        personality=personality,
        active=True,
        created_at=created_at,
    )
    session.add(legendary)
    await session.commit()
    await session.refresh(legendary)
    return legendary


async def mark_legendary_caught(
    session: AsyncSession,
    legendary_id: int,
    caught_by: int,
    caught_at: datetime,
) -> LegendaryFish | None:
    """Retire a legendary — mark inactive, record who caught it and when."""
    result = await session.execute(
        select(LegendaryFish).where(LegendaryFish.id == legendary_id)
    )
    legendary = result.scalars().first()
    if legendary is None:
        return None
    legendary.active = False
    legendary.caught_by = caught_by
    legendary.caught_at = caught_at
    await session.commit()
    await session.refresh(legendary)
    return legendary


async def save_encounter(
    session: AsyncSession,
    legendary_id: int,
    user_id: int,
    outcome: str,
    dialogue_summary: str,
    created_at: datetime,
) -> LegendaryEncounter:
    """Record a single encounter with a legendary."""
    enc = LegendaryEncounter(
        legendary_id=legendary_id,
        user_id=user_id,
        outcome=outcome,
        dialogue_summary=dialogue_summary,
        created_at=created_at,
    )
    session.add(enc)
    await session.commit()
    await session.refresh(enc)
    return enc


async def get_player_encounter_history(
    session: AsyncSession,
    legendary_id: int,
    user_id: int,
    limit: int = 5,
) -> list[LegendaryEncounter]:
    """Past encounters a specific player has had with this legendary."""
    result = await session.execute(
        select(LegendaryEncounter)
        .where(
            LegendaryEncounter.legendary_id == legendary_id,
            LegendaryEncounter.user_id == user_id,
        )
        .order_by(LegendaryEncounter.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_recent_legendary_encounters(
    session: AsyncSession,
    legendary_id: int,
    exclude_user_id: int,
    limit: int = 5,
) -> list[LegendaryEncounter]:
    """Recent encounters with this legendary by OTHER players."""
    result = await session.execute(
        select(LegendaryEncounter)
        .where(
            LegendaryEncounter.legendary_id == legendary_id,
            LegendaryEncounter.user_id != exclude_user_id,
        )
        .order_by(LegendaryEncounter.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_caught_legendaries(
    session: AsyncSession,
    guild_id: int,
    user_id: int | None = None,
    limit: int = 20,
) -> list[LegendaryFish]:
    """All legendaries that have been caught. If user_id given, filters to
    ones that specific player caught."""
    query = select(LegendaryFish).where(
        LegendaryFish.guild_id == guild_id,
        LegendaryFish.active == False,  # noqa: E712
        LegendaryFish.caught_at.is_not(None),
    )
    if user_id is not None:
        query = query.where(LegendaryFish.caught_by == user_id)
    query = query.order_by(LegendaryFish.caught_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_recent_guild_encounters(
    session: AsyncSession,
    guild_id: int,
    since: datetime,
    limit: int = 10,
) -> list[tuple[LegendaryEncounter, LegendaryFish]]:
    """Recent guild-wide legendary encounters, joined with the fish character.

    Used by the /reports fishing-legendary report. Returns tuples of
    ``(encounter, legendary)`` ordered by most-recent first.
    """
    result = await session.execute(
        select(LegendaryEncounter, LegendaryFish)
        .join(LegendaryFish, LegendaryFish.id == LegendaryEncounter.legendary_id)
        .where(
            LegendaryFish.guild_id == guild_id,
            LegendaryEncounter.created_at >= since,
        )
        .order_by(LegendaryEncounter.created_at.desc())
        .limit(limit)
    )
    return [(enc, leg) for enc, leg in result.all()]


async def get_legendary_outcome_counts(
    session: AsyncSession,
    guild_id: int,
    since: datetime,
) -> dict[str, int]:
    """Count legendary encounters by outcome within the window."""
    from sqlalchemy import func

    result = await session.execute(
        select(LegendaryEncounter.outcome, func.count())
        .join(LegendaryFish, LegendaryFish.id == LegendaryEncounter.legendary_id)
        .where(
            LegendaryFish.guild_id == guild_id,
            LegendaryEncounter.created_at >= since,
        )
        .group_by(LegendaryEncounter.outcome)
    )
    return {outcome: count for outcome, count in result.all()}


# ---------------------------------------------------------------------------
# Active fishing event log (uncommons + rares) — for /reports visibility
# ---------------------------------------------------------------------------


async def log_active_event(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    rarity: str,
    location_name: str,
    fish_species: str,
    prompt_text: str,
    player_response: str,
    outcome: str,
    created_at: datetime,
) -> ActiveFishingEventLog:
    """Record a single completed uncommon or rare active-mode event."""
    row = ActiveFishingEventLog(
        user_id=user_id,
        guild_id=guild_id,
        rarity=rarity,
        location_name=location_name,
        fish_species=fish_species,
        prompt_text=prompt_text,
        player_response=player_response,
        outcome=outcome,
        created_at=created_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_recent_active_events(
    session: AsyncSession,
    guild_id: int,
    rarity: str,
    since: datetime,
    limit: int = 10,
) -> list[ActiveFishingEventLog]:
    """Last N events of a given rarity within the window, newest first."""
    result = await session.execute(
        select(ActiveFishingEventLog)
        .where(
            ActiveFishingEventLog.guild_id == guild_id,
            ActiveFishingEventLog.rarity == rarity,
            ActiveFishingEventLog.created_at >= since,
        )
        .order_by(ActiveFishingEventLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_active_event_counts(
    session: AsyncSession,
    guild_id: int,
    since: datetime,
) -> dict[tuple[str, str], int]:
    """Counts grouped by (rarity, outcome) within the window.

    Returns e.g. ``{("uncommon", "caught"): 87, ("rare", "escaped"): 28, ...}``.
    """
    from sqlalchemy import func

    result = await session.execute(
        select(
            ActiveFishingEventLog.rarity,
            ActiveFishingEventLog.outcome,
            func.count(),
        )
        .where(
            ActiveFishingEventLog.guild_id == guild_id,
            ActiveFishingEventLog.created_at >= since,
        )
        .group_by(
            ActiveFishingEventLog.rarity,
            ActiveFishingEventLog.outcome,
        )
    )
    return {(rarity, outcome): count for rarity, outcome, count in result.all()}
