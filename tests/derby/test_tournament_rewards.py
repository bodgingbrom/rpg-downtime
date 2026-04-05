"""Tests for PR 4: Accolades, prizes, rewards, and sell price bonus."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from derby import logic, repositories as repo
from derby.logic import (
    TOURNAMENT_PRIZES,
    TOURNAMENT_SELL_BONUS,
    apply_tournament_rewards,
    calculate_sell_price,
    calculate_tournament_sell_bonus,
    resolve_tournament_prizes,
)
from derby.models import Racer
from economy.models import Wallet


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


async def _make_racer(
    session: AsyncSession, name: str, owner_id: int = 0,
    speed: int = 15, cornering: int = 15, stamina: int = 15,
    rank: str = "C", mood: int = 3, **kw,
) -> Racer:
    return await repo.create_racer(
        session, name=name, owner_id=owner_id, guild_id=1,
        speed=speed, cornering=cornering, stamina=stamina,
        rank=rank, mood=mood, **kw,
    )


async def _ensure_wallet(session: AsyncSession, user_id: int, balance: int = 100) -> Wallet:
    w = Wallet(user_id=user_id, guild_id=1, balance=balance)
    session.add(w)
    await session.flush()
    return w


# ---------------------------------------------------------------------------
# resolve_tournament_prizes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_tournament_prizes_pays_owners(session: AsyncSession):
    """Top-4 player-owned racers get coins."""
    r1 = await _make_racer(session, "A", owner_id=10, rank="C")
    r2 = await _make_racer(session, "B", owner_id=20, rank="C")
    r3 = await _make_racer(session, "C", owner_id=30, rank="C")
    r4 = await _make_racer(session, "D", owner_id=40, rank="C")

    await _ensure_wallet(session, 10, 0)
    await _ensure_wallet(session, 20, 0)
    await _ensure_wallet(session, 30, 0)
    await _ensure_wallet(session, 40, 0)

    placements = [r1.id, r2.id, r3.id, r4.id, 99, 98, 97, 96]
    entry_map = {r1.id: 10, r2.id: 20, r3.id: 30, r4.id: 40}

    awards = await resolve_tournament_prizes(
        session, "C", placements, entry_map, guild_id=1
    )

    assert len(awards) == 4
    expected = TOURNAMENT_PRIZES["C"]
    assert awards[0] == (10, r1.id, expected[0])
    assert awards[1] == (20, r2.id, expected[1])
    assert awards[2] == (30, r3.id, expected[2])
    assert awards[3] == (40, r4.id, expected[3])

    # Check wallet balances
    w1 = (await session.execute(select(Wallet).where(Wallet.user_id == 10))).scalars().first()
    assert w1.balance == expected[0]


@pytest.mark.asyncio
async def test_resolve_tournament_prizes_skips_pool(session: AsyncSession):
    """Pool racers (owner_id=0) should not receive prizes."""
    r1 = await _make_racer(session, "Player", owner_id=10, rank="B")
    r2 = await _make_racer(session, "Pool1", owner_id=0, rank="B")

    await _ensure_wallet(session, 10, 0)

    placements = [r2.id, r1.id, r2.id, r2.id]
    entry_map = {r1.id: 10, r2.id: 0}

    awards = await resolve_tournament_prizes(
        session, "B", placements, entry_map, guild_id=1
    )

    # Only the player should get a prize (2nd place)
    assert len(awards) == 1
    assert awards[0][0] == 10


@pytest.mark.asyncio
async def test_resolve_tournament_prizes_creates_wallet(session: AsyncSession):
    """If the player has no wallet, one is created."""
    r1 = await _make_racer(session, "New", owner_id=99, rank="D")
    placements = [r1.id, 2, 3, 4]
    entry_map = {r1.id: 99}

    awards = await resolve_tournament_prizes(
        session, "D", placements, entry_map, guild_id=1
    )

    assert len(awards) == 1
    w = (await session.execute(select(Wallet).where(Wallet.user_id == 99))).scalars().first()
    assert w is not None
    assert w.balance == TOURNAMENT_PRIZES["D"][0]


# ---------------------------------------------------------------------------
# apply_tournament_rewards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_tournament_rewards_first_place(session: AsyncSession):
    """1st place: mood=5, breed_cooldown=0, tournament_wins+=1."""
    r = await _make_racer(session, "Champ", owner_id=10, mood=2, rank="A")
    r.breed_cooldown = 5
    await session.commit()

    entry_map = {r.id: 10}
    await apply_tournament_rewards(session, "A", [r.id], entry_map)

    await session.refresh(r)
    assert r.mood == 5
    assert r.breed_cooldown == 0
    assert r.tournament_wins == 1


@pytest.mark.asyncio
async def test_apply_tournament_rewards_second_through_fourth(session: AsyncSession):
    """2nd-4th: mood+1 (cap 5), tournament_placements+=1."""
    r1 = await _make_racer(session, "W", owner_id=10, mood=3, rank="C")
    r2 = await _make_racer(session, "X", owner_id=20, mood=3, rank="C")
    r3 = await _make_racer(session, "Y", owner_id=30, mood=3, rank="C")
    r4 = await _make_racer(session, "Z", owner_id=40, mood=3, rank="C")

    entry_map = {r1.id: 10, r2.id: 20, r3.id: 30, r4.id: 40}
    await apply_tournament_rewards(
        session, "C", [r1.id, r2.id, r3.id, r4.id], entry_map
    )

    await session.refresh(r2)
    await session.refresh(r3)
    await session.refresh(r4)
    assert r2.mood == 4
    assert r2.tournament_placements == 1
    assert r3.mood == 4
    assert r4.mood == 4


@pytest.mark.asyncio
async def test_apply_tournament_rewards_mood_cap(session: AsyncSession):
    """Mood should not exceed 5."""
    r = await _make_racer(session, "Happy", owner_id=10, mood=5, rank="B")
    # Place 2nd — mood+1 should still cap at 5
    entry_map = {r.id: 10, 999: 20}
    await apply_tournament_rewards(session, "B", [999, r.id], entry_map)

    await session.refresh(r)
    assert r.mood == 5


@pytest.mark.asyncio
async def test_apply_tournament_rewards_skips_pool(session: AsyncSession):
    """Pool racers should not get reward modifications."""
    r = await _make_racer(session, "PoolWinner", owner_id=0, mood=2, rank="C")
    entry_map = {r.id: 0}
    await apply_tournament_rewards(session, "C", [r.id], entry_map)

    await session.refresh(r)
    assert r.mood == 2  # unchanged
    assert (r.tournament_wins or 0) == 0


# ---------------------------------------------------------------------------
# Sell price bonus
# ---------------------------------------------------------------------------


def test_sell_price_includes_tournament_bonus():
    """Tournament wins should add a flat bonus to sell price."""
    r = Racer(
        id=1, name="T", owner_id=1, speed=15, cornering=15, stamina=15,
        rank="B", tournament_wins=3,
    )
    price_with = calculate_sell_price(
        r, base_cost=20, multiplier=2, sell_fraction=0.5,
        tournament_bonus=calculate_tournament_sell_bonus(r),
    )
    price_without = calculate_sell_price(
        r, base_cost=20, multiplier=2, sell_fraction=0.5,
        tournament_bonus=0,
    )
    expected_bonus = TOURNAMENT_SELL_BONUS["B"] * 3
    assert price_with - price_without == expected_bonus


def test_sell_price_no_tournament_wins():
    """Zero wins should add no bonus."""
    r = Racer(
        id=1, name="T", owner_id=1, speed=15, cornering=15, stamina=15,
        rank="C", tournament_wins=0,
    )
    bonus = calculate_tournament_sell_bonus(r)
    assert bonus == 0


def test_calculate_tournament_sell_bonus_stacks():
    """Bonus should stack per win."""
    r = Racer(
        id=1, name="T", owner_id=1, speed=15, cornering=15, stamina=15,
        rank="S", tournament_wins=5,
    )
    bonus = calculate_tournament_sell_bonus(r)
    assert bonus == TOURNAMENT_SELL_BONUS["S"] * 5
