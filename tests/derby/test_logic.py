import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from derby.models import Base, Racer, Race, Bet, Wallet
from derby.logic import calculate_odds, simulate_race, resolve_payouts


@pytest.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


def test_calculate_odds():
    racers = [Racer(id=1, name="A", owner_id=1), Racer(id=2, name="B", owner_id=2)]
    odds = calculate_odds(racers, [], 0.1)
    assert odds == {1: 1.8, 2: 1.8}


def test_simulate_race():
    race = {"racers": [1, 2, 3], "course_segments": [1, 2]}
    placements, log = simulate_race(race, seed=123)
    assert placements == [3, 2, 1]
    assert log == [
        "Segment 1: Racer 3 takes the lead",
        "Segment 2: Racer 2 takes the lead",
    ]


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

    session.add_all([
        Bet(race_id=race.id, user_id=1, racer_id=r1.id, amount=10),
        Bet(race_id=race.id, user_id=2, racer_id=r2.id, amount=20),
    ])
    session.add(Wallet(user_id=1, balance=50))
    await session.commit()

    await resolve_payouts(session, race.id)

    w1 = await session.get(Wallet, 1)
    w2 = await session.get(Wallet, 2)
    assert w1.balance == 70
    assert w2.balance == 0

    bets = (await session.execute(select(Bet))).scalars().all()
    assert bets == []
