import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from derby import repositories as repo
from derby.models import Base


@pytest.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_racer_crud(session: AsyncSession):
    racer = await repo.create_racer(
        session,
        name="Speedy",
        owner_id=1,
        speed=5,
        cornering=6,
        stamina=7,
        temperament=8,
        mood=2,
        injuries="sprained ankle",
    )
    assert racer.id is not None

    fetched = await repo.get_racer(session, racer.id)
    assert fetched.name == "Speedy"
    assert fetched.speed == 5
    assert fetched.cornering == 6
    assert fetched.stamina == 7
    assert fetched.temperament == 8
    assert fetched.mood == 2
    assert fetched.injuries == "sprained ankle"

    updated = await repo.update_racer(session, racer.id, name="Zoom")
    assert updated.name == "Zoom"

    await repo.delete_racer(session, racer.id)
    assert await repo.get_racer(session, racer.id) is None


@pytest.mark.asyncio
async def test_race_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=123)
    assert race.id is not None

    fetched = await repo.get_race(session, race.id)
    assert fetched.guild_id == 123

    updated = await repo.update_race(session, race.id, finished=True)
    assert updated.finished is True

    await repo.delete_race(session, race.id)
    assert await repo.get_race(session, race.id) is None


@pytest.mark.asyncio
async def test_bet_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=1)
    racer = await repo.create_racer(session, name="A", owner_id=1)

    bet = await repo.create_bet(
        session, race_id=race.id, user_id=2, racer_id=racer.id, amount=50
    )
    assert bet.id is not None

    fetched = await repo.get_bet(session, bet.id)
    assert fetched.amount == 50

    updated = await repo.update_bet(session, bet.id, amount=75)
    assert updated.amount == 75

    await repo.delete_bet(session, bet.id)
    assert await repo.get_bet(session, bet.id) is None


@pytest.mark.asyncio
async def test_wallet_crud(session: AsyncSession):
    wallet = await repo.create_wallet(session, user_id=1, balance=100)
    assert wallet.balance == 100

    fetched = await repo.get_wallet(session, wallet.user_id)
    assert fetched.user_id == 1

    updated = await repo.update_wallet(session, wallet.user_id, balance=150)
    assert updated.balance == 150

    await repo.delete_wallet(session, wallet.user_id)
    assert await repo.get_wallet(session, wallet.user_id) is None


@pytest.mark.asyncio
async def test_course_segment_crud(session: AsyncSession):
    race = await repo.create_race(session, guild_id=1)
    seg = await repo.create_course_segment(
        session, race_id=race.id, position=1, description="Start"
    )
    assert seg.id is not None

    fetched = await repo.get_course_segment(session, seg.id)
    assert fetched.position == 1

    updated = await repo.update_course_segment(session, seg.id, description="Mid")
    assert updated.description == "Mid"

    await repo.delete_course_segment(session, seg.id)
    assert await repo.get_course_segment(session, seg.id) is None


@pytest.mark.asyncio
async def test_guild_settings_crud(session: AsyncSession):
    settings = await repo.create_guild_settings(session, guild_id=1)
    assert settings.guild_id == 1

    fetched = await repo.get_guild_settings(session, 1)
    assert fetched.guild_id == 1

    updated = await repo.update_guild_settings(session, 1, race_frequency=2)
    assert updated.race_frequency == 2

    await repo.delete_guild_settings(session, 1)
    assert await repo.get_guild_settings(session, 1) is None
