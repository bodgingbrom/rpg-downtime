from __future__ import annotations

from typing import Type, TypeVar

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Bet,
    CommandLog,
    CourseSegment,
    DailyReward,
    GuildSettings,
    NPC,
    PlayerData,
    Race,
    RaceEntry,
    Racer,
    RacerBuff,
    Tournament,
    TournamentEntry,
)

ModelT = TypeVar(
    "ModelT", Racer, Race, Bet, CourseSegment, GuildSettings, Tournament, TournamentEntry
)


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
    description: str | None = None,
    pool_expires_at: datetime | None = None,
    npc_id: int | None = None,
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
        description=description,
        pool_expires_at=pool_expires_at,
        npc_id=npc_id,
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
            or_(Racer.pool_expires_at.is_(None), Racer.pool_expires_at > func.now()),
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
    """Count unowned, non-retired, non-injured, non-expired racers for a guild."""
    result = await session.execute(
        select(func.count(Racer.id)).where(
            Racer.guild_id == guild_id,
            Racer.owner_id == 0,
            Racer.retired.is_(False),
            Racer.injury_races_remaining == 0,
            or_(Racer.pool_expires_at.is_(None), Racer.pool_expires_at > func.now()),
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


# Tournament
async def create_tournament(session: AsyncSession, **kwargs) -> Tournament:
    return await _create(session, Tournament, **kwargs)


async def get_tournament(session: AsyncSession, tournament_id: int) -> Tournament | None:
    return await _get(session, Tournament, tournament_id)


async def update_tournament(
    session: AsyncSession, tournament_id: int, **kwargs
) -> Tournament | None:
    return await _update(session, Tournament, tournament_id, **kwargs)


async def get_pending_tournament(
    session: AsyncSession, guild_id: int, rank: str
) -> Tournament | None:
    """Return the pending tournament for a guild+rank, if one exists."""
    result = await session.execute(
        select(Tournament).where(
            Tournament.guild_id == guild_id,
            Tournament.rank == rank,
            Tournament.status == "pending",
        )
    )
    return result.scalars().first()


# TournamentEntry
async def create_tournament_entry(session: AsyncSession, **kwargs) -> TournamentEntry:
    return await _create(session, TournamentEntry, **kwargs)


async def get_tournament_entries(
    session: AsyncSession, tournament_id: int
) -> list[TournamentEntry]:
    """Return all entries for a given tournament."""
    result = await session.execute(
        select(TournamentEntry).where(
            TournamentEntry.tournament_id == tournament_id
        )
    )
    return result.scalars().all()


async def update_tournament_entry(
    session: AsyncSession, entry_id: int, **kwargs
) -> TournamentEntry | None:
    return await _update(session, TournamentEntry, entry_id, **kwargs)


async def get_player_tournament_entry(
    session: AsyncSession, tournament_id: int, owner_id: int
) -> TournamentEntry | None:
    """Return a player's entry in a specific tournament, if any."""
    result = await session.execute(
        select(TournamentEntry).where(
            TournamentEntry.tournament_id == tournament_id,
            TournamentEntry.owner_id == owner_id,
        )
    )
    return result.scalars().first()


# Daily rewards
async def get_daily_reward(
    session: AsyncSession, user_id: int, guild_id: int, date_str: str
) -> DailyReward | None:
    """Return a player's daily reward for a specific date, if any."""
    result = await session.execute(
        select(DailyReward).where(
            DailyReward.user_id == user_id,
            DailyReward.guild_id == guild_id,
            DailyReward.date == date_str,
        )
    )
    return result.scalars().first()


async def create_daily_reward(session: AsyncSession, **kwargs) -> DailyReward:
    return await _create(session, DailyReward, **kwargs)


async def get_racer_owner_ids(session: AsyncSession, guild_id: int) -> list[int]:
    """Return distinct owner IDs of non-retired racers in a guild (excluding pool)."""
    result = await session.execute(
        select(Racer.owner_id).where(
            Racer.guild_id == guild_id,
            Racer.owner_id != 0,
            Racer.retired.is_(False),
        ).distinct()
    )
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# NPC trainers
# ---------------------------------------------------------------------------


async def create_npc(session: AsyncSession, **kwargs) -> NPC:
    return await _create(session, NPC, **kwargs)


async def get_npc(session: AsyncSession, npc_id: int) -> NPC | None:
    return await _get(session, NPC, npc_id)


async def update_npc(session: AsyncSession, npc_id: int, **kwargs) -> NPC | None:
    return await _update(session, NPC, npc_id, **kwargs)


async def delete_npc(session: AsyncSession, npc_id: int) -> None:
    await _delete(session, NPC, npc_id)


async def get_guild_npcs(session: AsyncSession, guild_id: int) -> list[NPC]:
    """Return all NPCs for a guild."""
    result = await session.execute(
        select(NPC).where(NPC.guild_id == guild_id)
    )
    return list(result.scalars().all())


async def get_npc_racers(session: AsyncSession, npc_id: int) -> list[Racer]:
    """Return racers belonging to a specific NPC."""
    result = await session.execute(
        select(Racer).where(Racer.npc_id == npc_id, Racer.retired.is_(False))
    )
    return list(result.scalars().all())


async def get_guild_npc_racers(
    session: AsyncSession, guild_id: int
) -> list[Racer]:
    """Return all non-retired NPC-owned racers in a guild."""
    result = await session.execute(
        select(Racer).where(
            Racer.guild_id == guild_id,
            Racer.npc_id.isnot(None),
            Racer.retired.is_(False),
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Finished races in a date range (for daily digest)
# ---------------------------------------------------------------------------


async def get_races_finished_between(
    session: AsyncSession, guild_id: int, start_dt: datetime, end_dt: datetime
) -> list[Race]:
    """Return finished races for a guild within a datetime range."""
    result = await session.execute(
        select(Race).where(
            Race.guild_id == guild_id,
            Race.finished.is_(True),
            Race.started_at >= start_dt,
            Race.started_at < end_dt,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Racer buffs (potion effects)
# ---------------------------------------------------------------------------


async def create_racer_buff(
    session: AsyncSession,
    *,
    racer_id: int,
    guild_id: int,
    buff_type: str,
    value: int,
    races_remaining: int = 1,
) -> RacerBuff:
    buff = RacerBuff(
        racer_id=racer_id,
        guild_id=guild_id,
        buff_type=buff_type,
        value=value,
        races_remaining=races_remaining,
    )
    session.add(buff)
    await session.commit()
    await session.refresh(buff)
    return buff


async def get_racer_buffs(
    session: AsyncSession, racer_id: int
) -> list[RacerBuff]:
    result = await session.execute(
        select(RacerBuff).where(RacerBuff.racer_id == racer_id)
    )
    return list(result.scalars().all())


async def get_race_buffs_for_racers(
    session: AsyncSession, racer_ids: list[int]
) -> dict[int, list[RacerBuff]]:
    """Load active buffs for multiple racers, grouped by racer_id."""
    if not racer_ids:
        return {}
    result = await session.execute(
        select(RacerBuff).where(RacerBuff.racer_id.in_(racer_ids))
    )
    buffs: dict[int, list[RacerBuff]] = {}
    for b in result.scalars().all():
        buffs.setdefault(b.racer_id, []).append(b)
    return buffs


async def consume_racer_buffs(
    session: AsyncSession, racer_ids: list[int]
) -> None:
    """Decrement races_remaining for buffs on given racers; delete expired."""
    if not racer_ids:
        return
    result = await session.execute(
        select(RacerBuff).where(RacerBuff.racer_id.in_(racer_ids))
    )
    for buff in result.scalars().all():
        buff.races_remaining -= 1
        if buff.races_remaining <= 0:
            await session.delete(buff)
    await session.commit()


# ---------------------------------------------------------------------------
# Command logging / analytics
# ---------------------------------------------------------------------------


async def log_command(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    command: str,
    cog: str = "unknown",
) -> CommandLog:
    """Insert a command log entry."""
    entry = CommandLog(
        guild_id=guild_id,
        user_id=user_id,
        command=command,
        cog=cog,
    )
    session.add(entry)
    await session.commit()
    return entry


async def get_command_usage(
    session: AsyncSession, guild_id: int, since: datetime
) -> list:
    """Return (command, count, unique_users) grouped by command, ordered by count desc."""
    result = await session.execute(
        select(
            CommandLog.command,
            func.count(CommandLog.id).label("cnt"),
            func.count(func.distinct(CommandLog.user_id)).label("users"),
        )
        .where(
            CommandLog.guild_id == guild_id,
            CommandLog.created_at >= since,
        )
        .group_by(CommandLog.command)
        .order_by(func.count(CommandLog.id).desc())
    )
    return result.all()


async def get_player_activity(
    session: AsyncSession, guild_id: int, since: datetime
) -> list:
    """Return (user_id, count) grouped by user, ordered by count desc."""
    result = await session.execute(
        select(
            CommandLog.user_id,
            func.count(CommandLog.id).label("cnt"),
        )
        .where(
            CommandLog.guild_id == guild_id,
            CommandLog.created_at >= since,
        )
        .group_by(CommandLog.user_id)
        .order_by(func.count(CommandLog.id).desc())
    )
    return result.all()


async def get_player_top_command(
    session: AsyncSession, guild_id: int, user_id: int, since: datetime
) -> str | None:
    """Return the most-used command for a specific player."""
    result = await session.execute(
        select(CommandLog.command)
        .where(
            CommandLog.guild_id == guild_id,
            CommandLog.user_id == user_id,
            CommandLog.created_at >= since,
        )
        .group_by(CommandLog.command)
        .order_by(func.count(CommandLog.id).desc())
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


async def get_weekly_totals(
    session: AsyncSession, guild_id: int, start: datetime, end: datetime
) -> tuple[int, int]:
    """Return (total_commands, unique_users) for a date range."""
    result = await session.execute(
        select(
            func.count(CommandLog.id),
            func.count(func.distinct(CommandLog.user_id)),
        ).where(
            CommandLog.guild_id == guild_id,
            CommandLog.created_at >= start,
            CommandLog.created_at < end,
        )
    )
    row = result.first()
    return (row[0] or 0, row[1] or 0) if row else (0, 0)


async def get_commands_in_period(
    session: AsyncSession, guild_id: int, start: datetime, end: datetime
) -> set[str]:
    """Return the set of distinct command names used in a date range."""
    result = await session.execute(
        select(func.distinct(CommandLog.command)).where(
            CommandLog.guild_id == guild_id,
            CommandLog.created_at >= start,
            CommandLog.created_at < end,
        )
    )
    return {row[0] for row in result.all()}
