from __future__ import annotations

from typing import Type, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Bet, CourseSegment, GuildSettings, PlayerData, Race, RaceEntry, Racer

ModelT = TypeVar("ModelT", Racer, Race, Bet, CourseSegment, GuildSettings)


async def _create(session: AsyncSession, model: Type[ModelT], **kwargs) -> ModelT:
    obj = model(**kwargs)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def _get(
    session: AsyncSession, model: Type[ModelT], obj_id: int
) -> ModelT | None:
    result = await session.get(model, obj_id)
    return result


async def _update(
    session: AsyncSession, model: Type[ModelT], obj_id: int, **kwargs
) -> ModelT | None:
    obj = await _get(session, model, obj_id)
    if obj is None:
        return None
    for key, value in kwargs.items():
        setattr(obj, key, value)
    await session.commit()
    await session.refresh(obj)
    return obj


async def _delete(session: AsyncSession, model: Type[ModelT], obj_id: int) -> None:
    obj = await _get(session, model, obj_id)
    if obj is not None:
        await session.delete(obj)
        await session.commit()


# Racer
async def create_racer(
    session: AsyncSession,
    *,
    name: str,
    owner_id: int,
    guild_id: int = 0,
    retired: bool = False,
    speed: int = 0,
    cornering: int = 0,
    stamina: int = 0,
    temperament: str = "Quirky",
    mood: int = 3,
    injuries: str = "",
    career_length: int = 30,
    peak_end: int = 18,
    gender: str = "M",
    sire_id: int | None = None,
    dam_id: int | None = None,
    foal_count: int = 0,
    breed_cooldown: int = 0,
    training_count: int = 0,
    rank: str | None = None,
) -> Racer:
    return await _create(
        session,
        Racer,
        name=name,
        owner_id=owner_id,
        guild_id=guild_id,
        retired=retired,
        speed=speed,
        cornering=cornering,
        stamina=stamina,
        temperament=temperament,
        mood=mood,
        injuries=injuries,
        career_length=career_length,
        peak_end=peak_end,
        gender=gender,
        sire_id=sire_id,
        dam_id=dam_id,
        foal_count=foal_count,
        breed_cooldown=breed_cooldown,
        training_count=training_count,
        rank=rank,
    )


async def get_racer(session: AsyncSession, racer_id: int) -> Racer | None:
    return await _get(session, Racer, racer_id)


async def update_racer(session: AsyncSession, racer_id: int, **kwargs) -> Racer | None:
    return await _update(session, Racer, racer_id, **kwargs)


async def delete_racer(session: AsyncSession, racer_id: int) -> None:
    await _delete(session, Racer, racer_id)


async def get_guild_racers(
    session: AsyncSession,
    guild_id: int,
    *,
    eligible_only: bool = True,
    min_training: int | None = None,
) -> list[Racer]:
    """Return racers belonging to a guild.

    When ``eligible_only`` is True (default), only non-retired racers
    with no active injuries are returned.

    When ``min_training`` is set, bred racers (those with a ``sire_id``)
    whose ``training_count`` is below the threshold are excluded.
    """
    stmt = select(Racer).where(Racer.guild_id == guild_id)
    if eligible_only:
        stmt = stmt.where(
            Racer.retired.is_(False),
            Racer.injury_races_remaining == 0,
        )
    result = await session.execute(stmt)
    racers = result.scalars().all()
    if eligible_only and min_training is not None:
        # Bred racers (have a sire) must meet the training gate
        racers = [
            r for r in racers
            if r.sire_id is None or (r.training_count or 0) >= min_training
        ]
    return racers


async def get_unowned_guild_racers(
    session: AsyncSession, guild_id: int, *, eligible_only: bool = True
) -> list[Racer]:
    """Return unowned racers (owner_id == 0) for a guild."""
    stmt = select(Racer).where(Racer.guild_id == guild_id, Racer.owner_id == 0)
    if eligible_only:
        stmt = stmt.where(
            Racer.retired.is_(False),
            Racer.injury_races_remaining == 0,
        )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_racers_by_rank(
    session: AsyncSession,
    guild_id: int,
    rank: str,
    *,
    unowned_only: bool = False,
) -> list[Racer]:
    """Return non-retired racers in a guild with a specific rank."""
    stmt = select(Racer).where(
        Racer.guild_id == guild_id,
        Racer.rank == rank,
        Racer.retired.is_(False),
    )
    if unowned_only:
        stmt = stmt.where(Racer.owner_id == 0)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_owned_racers(
    session: AsyncSession, owner_id: int, guild_id: int
) -> list[Racer]:
    """Return non-retired racers owned by a specific user in a guild."""
    result = await session.execute(
        select(Racer).where(
            Racer.owner_id == owner_id,
            Racer.guild_id == guild_id,
            Racer.retired.is_(False),
        )
    )
    return result.scalars().all()


async def get_stable_racers(
    session: AsyncSession, owner_id: int, guild_id: int
) -> list[Racer]:
    """Return ALL racers owned by a user in a guild, including retired.

    Used for stable slot counting — retired racers still occupy a slot.
    """
    result = await session.execute(
        select(Racer).where(
            Racer.owner_id == owner_id,
            Racer.guild_id == guild_id,
        )
    )
    return result.scalars().all()


async def count_unowned_eligible_racers(
    session: AsyncSession, guild_id: int
) -> int:
    """Count unowned, non-retired, non-injured racers for a guild."""
    result = await session.execute(
        select(func.count(Racer.id)).where(
            Racer.guild_id == guild_id,
            Racer.owner_id == 0,
            Racer.retired.is_(False),
            Racer.injury_races_remaining == 0,
        )
    )
    return result.scalar() or 0


# Race
async def create_race(session: AsyncSession, **kwargs) -> Race:
    return await _create(session, Race, **kwargs)


async def get_race(session: AsyncSession, race_id: int) -> Race | None:
    return await _get(session, Race, race_id)


async def update_race(session: AsyncSession, race_id: int, **kwargs) -> Race | None:
    return await _update(session, Race, race_id, **kwargs)


async def delete_race(session: AsyncSession, race_id: int) -> None:
    await _delete(session, Race, race_id)


# Bet
async def create_bet(session: AsyncSession, **kwargs) -> Bet:
    return await _create(session, Bet, **kwargs)


async def get_bet(session: AsyncSession, bet_id: int) -> Bet | None:
    return await _get(session, Bet, bet_id)


async def update_bet(session: AsyncSession, bet_id: int, **kwargs) -> Bet | None:
    return await _update(session, Bet, bet_id, **kwargs)


async def delete_bet(session: AsyncSession, bet_id: int) -> None:
    await _delete(session, Bet, bet_id)


# RaceEntry
async def create_race_entries(
    session: AsyncSession, race_id: int, racer_ids: list[int]
) -> list[RaceEntry]:
    """Bulk-create race entries linking racers to a race."""
    entries = [RaceEntry(race_id=race_id, racer_id=rid) for rid in racer_ids]
    session.add_all(entries)
    await session.commit()
    return entries


async def get_race_entries(
    session: AsyncSession, race_id: int
) -> list[RaceEntry]:
    """Return all entries for a given race."""
    result = await session.execute(
        select(RaceEntry).where(RaceEntry.race_id == race_id)
    )
    return result.scalars().all()


async def get_race_participants(
    session: AsyncSession, race_id: int
) -> list[Racer]:
    """Return the Racer objects assigned to a race via RaceEntry."""
    result = await session.execute(
        select(Racer).join(RaceEntry, RaceEntry.racer_id == Racer.id).where(
            RaceEntry.race_id == race_id
        )
    )
    return result.scalars().all()


# CourseSegment
async def create_course_segment(session: AsyncSession, **kwargs) -> CourseSegment:
    return await _create(session, CourseSegment, **kwargs)


async def get_course_segment(
    session: AsyncSession, segment_id: int
) -> CourseSegment | None:
    return await _get(session, CourseSegment, segment_id)


async def update_course_segment(
    session: AsyncSession, segment_id: int, **kwargs
) -> CourseSegment | None:
    return await _update(session, CourseSegment, segment_id, **kwargs)


async def delete_course_segment(session: AsyncSession, segment_id: int) -> None:
    await _delete(session, CourseSegment, segment_id)


# GuildSettings
async def create_guild_settings(session: AsyncSession, **kwargs) -> GuildSettings:
    return await _create(session, GuildSettings, **kwargs)


async def get_guild_settings(
    session: AsyncSession, guild_id: int
) -> GuildSettings | None:
    return await _get(session, GuildSettings, guild_id)


async def update_guild_settings(
    session: AsyncSession, guild_id: int, **kwargs
) -> GuildSettings | None:
    return await _update(session, GuildSettings, guild_id, **kwargs)


async def delete_guild_settings(session: AsyncSession, guild_id: int) -> None:
    await _delete(session, GuildSettings, guild_id)


# History
async def get_race_history(
    session: AsyncSession, guild_id: int, limit: int
) -> list[tuple[Race, int | None, int]]:
    """Return the last ``limit`` finished races for ``guild_id``.

    Each entry is ``(Race, winning_racer_id | None, total_payout)`` where
    ``total_payout`` is the sum paid to winning bets (double the bet amount).
    ``winning_racer_id`` will be ``None`` if no bets exist for the race.
    """

    result = await session.execute(
        select(Race)
        .where(Race.guild_id == guild_id, Race.finished.is_(True))
        .order_by(Race.id.desc())
        .limit(limit)
    )
    races = result.scalars().all()

    history: list[tuple[Race, int | None, int]] = []
    for race in races:
        winner = race.winner_id
        if winner is not None:
            bet_rows = await session.execute(
                select(Bet).where(Bet.race_id == race.id, Bet.racer_id == winner)
            )
            bets = bet_rows.scalars().all()
            payout = sum(int(b.amount * b.payout_multiplier) for b in bets)
        else:
            payout = 0
        history.append((race, winner, payout))
    return history


# PlayerData
async def get_player_data(
    session: AsyncSession, user_id: int, guild_id: int
) -> PlayerData | None:
    result = await session.execute(
        select(PlayerData).where(
            PlayerData.user_id == user_id,
            PlayerData.guild_id == guild_id,
        )
    )
    return result.scalars().first()


async def create_player_data(
    session: AsyncSession, *, user_id: int, guild_id: int, extra_slots: int = 0
) -> PlayerData:
    pd = PlayerData(user_id=user_id, guild_id=guild_id, extra_slots=extra_slots)
    session.add(pd)
    await session.commit()
    await session.refresh(pd)
    return pd
