"""Tests for RacerBuff repository functions."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from derby import repositories as repo
import derby.models  # noqa: F401
import economy.models  # noqa: F401


GUILD_ID = 100


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture()
async def racer(session: AsyncSession):
    r = await repo.create_racer(
        session,
        name="Speedy",
        owner_id=1,
        speed=10,
        cornering=12,
        stamina=8,
        temperament="Agile",
        guild_id=GUILD_ID,
    )
    return r


@pytest.mark.asyncio
async def test_create_and_get_buff(session: AsyncSession, racer):
    buff = await repo.create_racer_buff(
        session,
        racer_id=racer.id,
        guild_id=GUILD_ID,
        buff_type="speed",
        value=3,
    )
    assert buff.id is not None
    assert buff.buff_type == "speed"
    assert buff.value == 3
    assert buff.races_remaining == 1

    buffs = await repo.get_racer_buffs(session, racer.id)
    assert len(buffs) == 1
    assert buffs[0].id == buff.id


@pytest.mark.asyncio
async def test_get_buffs_empty(session: AsyncSession, racer):
    buffs = await repo.get_racer_buffs(session, racer.id)
    assert buffs == []


@pytest.mark.asyncio
async def test_get_race_buffs_for_racers(session: AsyncSession, racer):
    racer2 = await repo.create_racer(
        session,
        name="Dasher",
        owner_id=2,
        speed=8,
        cornering=9,
        stamina=11,
        temperament="Burly",
        guild_id=GUILD_ID,
    )
    await repo.create_racer_buff(
        session, racer_id=racer.id, guild_id=GUILD_ID,
        buff_type="speed", value=2,
    )
    await repo.create_racer_buff(
        session, racer_id=racer2.id, guild_id=GUILD_ID,
        buff_type="cornering", value=4,
    )

    buffs_map = await repo.get_race_buffs_for_racers(
        session, [racer.id, racer2.id]
    )
    assert racer.id in buffs_map
    assert racer2.id in buffs_map
    assert len(buffs_map[racer.id]) == 1
    assert buffs_map[racer.id][0].buff_type == "speed"
    assert buffs_map[racer2.id][0].buff_type == "cornering"


@pytest.mark.asyncio
async def test_get_race_buffs_empty_list(session: AsyncSession):
    result = await repo.get_race_buffs_for_racers(session, [])
    assert result == {}


@pytest.mark.asyncio
async def test_consume_racer_buffs_deletes_expired(session: AsyncSession, racer):
    await repo.create_racer_buff(
        session, racer_id=racer.id, guild_id=GUILD_ID,
        buff_type="speed", value=5, races_remaining=1,
    )
    await repo.consume_racer_buffs(session, [racer.id])

    buffs = await repo.get_racer_buffs(session, racer.id)
    assert buffs == []


@pytest.mark.asyncio
async def test_consume_racer_buffs_decrements(session: AsyncSession, racer):
    await repo.create_racer_buff(
        session, racer_id=racer.id, guild_id=GUILD_ID,
        buff_type="stamina", value=3, races_remaining=3,
    )
    await repo.consume_racer_buffs(session, [racer.id])

    buffs = await repo.get_racer_buffs(session, racer.id)
    assert len(buffs) == 1
    assert buffs[0].races_remaining == 2


@pytest.mark.asyncio
async def test_consume_racer_buffs_empty_list(session: AsyncSession):
    # Should not raise
    await repo.consume_racer_buffs(session, [])


@pytest.mark.asyncio
async def test_multiple_buffs_on_same_racer(session: AsyncSession, racer):
    await repo.create_racer_buff(
        session, racer_id=racer.id, guild_id=GUILD_ID,
        buff_type="speed", value=2,
    )
    await repo.create_racer_buff(
        session, racer_id=racer.id, guild_id=GUILD_ID,
        buff_type="cornering", value=4,
    )
    buffs = await repo.get_racer_buffs(session, racer.id)
    assert len(buffs) == 2
    types = {b.buff_type for b in buffs}
    assert types == {"speed", "cornering"}
