import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from derby.logic import (
    apply_temperament,
    calculate_odds,
    resolve_payouts,
    simulate_race,
)
from db_base import Base
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


def test_calculate_odds_equal_stats():
    racers = [
        Racer(id=1, name="A", owner_id=1, speed=10, cornering=10, stamina=10),
        Racer(id=2, name="B", owner_id=2, speed=10, cornering=10, stamina=10),
    ]
    odds = calculate_odds(racers, [], 0.1)
    assert odds[1] == odds[2]


def test_calculate_odds_stat_weighted():
    strong = Racer(id=1, name="A", owner_id=1, speed=30, cornering=30, stamina=30)
    weak = Racer(id=2, name="B", owner_id=2, speed=0, cornering=0, stamina=0)
    odds = calculate_odds([strong, weak], [], 0.1)
    # Strong racer should have lower payout (more likely to win)
    assert odds[1] < odds[2]


def test_simulate_race():
    race = {"racers": [1, 2, 3], "course_segments": [1, 2]}
    placements, log = simulate_race(race, seed=123)
    assert placements == [3, 2, 1]
    assert log == [
        "Segment 1: Racer 3 takes the lead",
        "Segment 2: Racer 2 takes the lead",
    ]


def test_apply_temperament() -> None:
    stats = {"speed": 10, "cornering": 10, "stamina": 10}
    result = apply_temperament(stats, "Agile", 0.1)
    assert result["speed"] == 11
    assert result["stamina"] == 9


@pytest.mark.asyncio
async def test_resolve_payouts(session: AsyncSession):
    r1 = Racer(name="A", owner_id=1)
    r2 = Racer(name="B", owner_id=2)
    session.add_all([r1, r2])
    await session.commit()
    await session.refresh(r1)
    await session.refresh(r2)

    race = Race(guild_id=1)
    session.add(race)
    await session.commit()
    await session.refresh(race)

    session.add_all(
        [
            Bet(race_id=race.id, user_id=1, racer_id=r1.id, amount=10),
            Bet(race_id=race.id, user_id=2, racer_id=r2.id, amount=20),
        ]
    )
    session.add(Wallet(user_id=1, balance=50))
    await session.commit()

    await resolve_payouts(session, race.id, winner_id=r1.id)

    w1 = await session.get(Wallet, 1)
    w2 = await session.get(Wallet, 2)
    assert w1.balance == 70
    assert w2.balance == 0

    bets = (await session.execute(select(Bet))).scalars().all()
    assert bets == []


def test_simulate_race_stat_influence():
    strong = Racer(id=1, name="Strong", owner_id=1, speed=31, cornering=31, stamina=31)
    weak = Racer(id=2, name="Weak", owner_id=2, speed=0, cornering=0, stamina=0)
    wins = 0
    for seed in range(100):
        placements, _ = simulate_race({"racers": [strong, weak]}, seed=seed)
        if placements[0] == 1:
            wins += 1
    # Strong racer should win the vast majority
    assert wins > 80
