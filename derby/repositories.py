from __future__ import annotations

from typing import Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Bet, CourseSegment, GuildSettings, Race, Racer, Wallet

ModelT = TypeVar("ModelT", Racer, Race, Bet, Wallet, CourseSegment, GuildSettings)


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
    retired: bool = False,
    speed: int = 0,
    cornering: int = 0,
    stamina: int = 0,
    temperament: str = "Quirky",
    mood: int = 3,
    injuries: str = "",
) -> Racer:
    return await _create(
        session,
        Racer,
        name=name,
        owner_id=owner_id,
        retired=retired,
        speed=speed,
        cornering=cornering,
        stamina=stamina,
        temperament=temperament,
        mood=mood,
        injuries=injuries,
    )


async def get_racer(session: AsyncSession, racer_id: int) -> Racer | None:
    return await _get(session, Racer, racer_id)


async def update_racer(session: AsyncSession, racer_id: int, **kwargs) -> Racer | None:
    return await _update(session, Racer, racer_id, **kwargs)


async def delete_racer(session: AsyncSession, racer_id: int) -> None:
    await _delete(session, Racer, racer_id)


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


# Wallet
async def create_wallet(session: AsyncSession, **kwargs) -> Wallet:
    return await _create(session, Wallet, **kwargs)


async def get_wallet(session: AsyncSession, user_id: int) -> Wallet | None:
    return await _get(session, Wallet, user_id)


async def update_wallet(session: AsyncSession, user_id: int, **kwargs) -> Wallet | None:
    return await _update(session, Wallet, user_id, **kwargs)


async def delete_wallet(session: AsyncSession, user_id: int) -> None:
    await _delete(session, Wallet, user_id)


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
        bet_rows = await session.execute(select(Bet).where(Bet.race_id == race.id))
        bets = bet_rows.scalars().all()
        if bets:
            winner = min(b.racer_id for b in bets)
            payout = sum(b.amount * 2 for b in bets if b.racer_id == winner)
        else:
            winner = None
            payout = 0
        history.append((race, winner, payout))
    return history
