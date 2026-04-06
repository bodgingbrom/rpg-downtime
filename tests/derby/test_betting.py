"""Tests for the expanded betting system: odds engine and payout resolution."""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from derby.logic import (
    _bet_wins,
    _exact_order_probability,
    _place_probability,
    _win_probabilities,
    calculate_bet_odds,
    resolve_payouts,
    RaceMap,
    MapSegment,
)
from derby.models import Bet, Race, Racer
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


# ---------------------------------------------------------------------------
# _win_probabilities
# ---------------------------------------------------------------------------


def test_win_probabilities_sum_to_one():
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=20, cornering=15, stamina=10),
        Racer(id=2, name="B", owner_id=2, speed=10, cornering=10, stamina=10),
        Racer(id=3, name="C", owner_id=3, speed=5, cornering=5, stamina=5),
    ]
    probs = _win_probabilities(racers)
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_win_probabilities_favor_strong_racer():
    strong = Racer(id=1, name="S", owner_id=1, speed=30, cornering=30, stamina=30)
    weak = Racer(id=2, name="W", owner_id=2, speed=5, cornering=5, stamina=5)
    probs = _win_probabilities([strong, weak])
    assert probs[1] > probs[2]


# ---------------------------------------------------------------------------
# _place_probability
# ---------------------------------------------------------------------------


def test_place_probability_higher_than_win():
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=20, cornering=20, stamina=20),
        Racer(id=2, name="B", owner_id=2, speed=15, cornering=15, stamina=15),
        Racer(id=3, name="C", owner_id=3, speed=10, cornering=10, stamina=10),
    ]
    probs = _win_probabilities(racers)
    for rid in probs:
        place_p = _place_probability(rid, probs)
        assert place_p >= probs[rid], (
            f"Place probability ({place_p}) should be >= win probability ({probs[rid]})"
        )


def test_place_probability_sum():
    """Sum of all place probabilities should be ~2.0 (2 slots available)."""
    racers = [
        Racer(id=i, name=f"R{i}", owner_id=i, speed=10 + i, cornering=10, stamina=10)
        for i in range(1, 7)
    ]
    probs = _win_probabilities(racers)
    total = sum(_place_probability(rid, probs) for rid in probs)
    assert abs(total - 2.0) < 0.01, f"Sum of place probs = {total}, expected ~2.0"


# ---------------------------------------------------------------------------
# _exact_order_probability
# ---------------------------------------------------------------------------


def test_exact_order_two_picks():
    """Exacta probability should be less than win probability for pick[0]."""
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=25, cornering=25, stamina=25),
        Racer(id=2, name="B", owner_id=2, speed=15, cornering=15, stamina=15),
        Racer(id=3, name="C", owner_id=3, speed=10, cornering=10, stamina=10),
    ]
    probs = _win_probabilities(racers)
    exacta_p = _exact_order_probability([1, 2], probs)
    assert 0 < exacta_p < probs[1]


def test_exact_order_three_picks():
    """Trifecta probability should be smaller than exacta."""
    racers = [
        Racer(id=i, name=f"R{i}", owner_id=i, speed=10 * i, cornering=10, stamina=10)
        for i in range(1, 5)
    ]
    probs = _win_probabilities(racers)
    exacta_p = _exact_order_probability([4, 3], probs)
    trifecta_p = _exact_order_probability([4, 3, 2], probs)
    assert trifecta_p < exacta_p


def test_exact_order_full_field():
    """Superfecta (all 6 in order) should be a very small probability."""
    racers = [
        Racer(id=i, name=f"R{i}", owner_id=i, speed=10 + i, cornering=10, stamina=10)
        for i in range(1, 7)
    ]
    probs = _win_probabilities(racers)
    picks = list(range(1, 7))
    super_p = _exact_order_probability(picks, probs)
    assert 0 < super_p < 0.01, f"Superfecta prob {super_p} should be very small"


# ---------------------------------------------------------------------------
# calculate_bet_odds
# ---------------------------------------------------------------------------


def test_calculate_bet_odds_house_edge():
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=20, cornering=20, stamina=20),
        Racer(id=2, name="B", owner_id=2, speed=20, cornering=20, stamina=20),
    ]
    # With 0 house edge, equal racers should pay 2.0x for win
    odds_no_edge = calculate_bet_odds(racers, None, 0.0, "win", [1])
    assert abs(odds_no_edge - 2.0) < 0.1

    # With 10% house edge, payout should be lower
    odds_with_edge = calculate_bet_odds(racers, None, 0.1, "win", [1])
    assert odds_with_edge < odds_no_edge


def test_calculate_bet_odds_minimum_clamp():
    """Heavily favored racer with place bet should still pay at least 1.1x."""
    strong = Racer(id=1, name="S", owner_id=1, speed=31, cornering=31, stamina=31)
    weak = Racer(id=2, name="W", owner_id=2, speed=1, cornering=1, stamina=1)
    odds = calculate_bet_odds([strong, weak], None, 0.1, "place", [1])
    assert odds >= 1.1


# ---------------------------------------------------------------------------
# _bet_wins
# ---------------------------------------------------------------------------


def test_bet_wins_all_types():
    placements = [10, 20, 30, 40, 50, 60]

    # Win
    assert _bet_wins("win", 10, "[]", placements) is True
    assert _bet_wins("win", 20, "[]", placements) is False

    # Place
    assert _bet_wins("place", 10, "[]", placements) is True
    assert _bet_wins("place", 20, "[]", placements) is True
    assert _bet_wins("place", 30, "[]", placements) is False

    # Exacta
    assert _bet_wins("exacta", 10, json.dumps([10, 20]), placements) is True
    assert _bet_wins("exacta", 10, json.dumps([20, 10]), placements) is False

    # Trifecta
    assert _bet_wins("trifecta", 10, json.dumps([10, 20, 30]), placements) is True
    assert _bet_wins("trifecta", 10, json.dumps([10, 30, 20]), placements) is False

    # Superfecta
    assert _bet_wins("superfecta", 10, json.dumps([10, 20, 30, 40, 50, 60]), placements) is True
    assert _bet_wins("superfecta", 10, json.dumps([10, 20, 30, 40, 60, 50]), placements) is False


# ---------------------------------------------------------------------------
# resolve_payouts (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_payouts_multiple_bet_types(session: AsyncSession):
    r1 = Racer(name="A", owner_id=1, guild_id=1)
    r2 = Racer(name="B", owner_id=2, guild_id=1)
    r3 = Racer(name="C", owner_id=3, guild_id=1)
    session.add_all([r1, r2, r3])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)
    await session.refresh(r3)

    race = Race(guild_id=1)
    session.add(race)
    await session.commit()
    await session.refresh(race)

    placements = [r1.id, r2.id, r3.id]

    # User 1: win bet on r1 (should win)
    session.add(Bet(
        race_id=race.id, user_id=1, racer_id=r1.id, amount=100,
        payout_multiplier=2.5, bet_type="win", racer_ids="[]",
    ))
    # User 2: place bet on r2 (should win — r2 is 2nd)
    session.add(Bet(
        race_id=race.id, user_id=2, racer_id=r2.id, amount=50,
        payout_multiplier=1.8, bet_type="place", racer_ids="[]",
    ))
    # User 3: exacta bet (wrong order — should lose)
    session.add(Bet(
        race_id=race.id, user_id=3, racer_id=r2.id, amount=30,
        payout_multiplier=8.0, bet_type="exacta",
        racer_ids=json.dumps([r2.id, r1.id]),
    ))
    session.add(Wallet(user_id=1, guild_id=1, balance=0))
    session.add(Wallet(user_id=2, guild_id=1, balance=0))
    session.add(Wallet(user_id=3, guild_id=1, balance=0))
    await session.commit()

    results = await resolve_payouts(session, race.id, placements, guild_id=1)

    assert len(results) == 3
    win_result = next(r for r in results if r["bet_type"] == "win")
    place_result = next(r for r in results if r["bet_type"] == "place")
    exacta_result = next(r for r in results if r["bet_type"] == "exacta")

    assert win_result["won"] is True
    assert win_result["payout"] == 250

    assert place_result["won"] is True
    assert place_result["payout"] == 90

    assert exacta_result["won"] is False
    assert exacta_result["payout"] == 0

    # Check wallets
    w1 = (await session.execute(
        select(Wallet).where(Wallet.user_id == 1, Wallet.guild_id == 1)
    )).scalars().first()
    w2 = (await session.execute(
        select(Wallet).where(Wallet.user_id == 2, Wallet.guild_id == 1)
    )).scalars().first()
    w3 = (await session.execute(
        select(Wallet).where(Wallet.user_id == 3, Wallet.guild_id == 1)
    )).scalars().first()
    assert w1.balance == 250
    assert w2.balance == 90
    assert w3.balance == 0


@pytest.mark.asyncio
async def test_resolve_payouts_backward_compat(session: AsyncSession):
    """Old bets without bet_type/racer_ids should still resolve as win bets."""
    r1 = Racer(name="A", owner_id=1, guild_id=1)
    r2 = Racer(name="B", owner_id=2, guild_id=1)
    session.add_all([r1, r2])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)

    race = Race(guild_id=1)
    session.add(race)
    await session.commit()
    await session.refresh(race)

    # Simulate an old-style bet (default bet_type="win", racer_ids="[]")
    session.add(Bet(
        race_id=race.id, user_id=1, racer_id=r1.id, amount=10,
        payout_multiplier=2.0,
    ))
    session.add(Wallet(user_id=1, guild_id=1, balance=50))
    await session.commit()

    results = await resolve_payouts(session, race.id, [r1.id, r2.id], guild_id=1)

    assert len(results) == 1
    assert results[0]["won"] is True
    assert results[0]["payout"] == 20

    w1 = (await session.execute(
        select(Wallet).where(Wallet.user_id == 1, Wallet.guild_id == 1)
    )).scalars().first()
    assert w1.balance == 70
