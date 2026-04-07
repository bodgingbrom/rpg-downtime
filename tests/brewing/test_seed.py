import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from brewing import repositories as brew_repo
from brewing.seed_data import seed_if_empty
import brewing.models  # noqa: F401


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_populates_empty_db(session: AsyncSession):
    await seed_if_empty(session)
    ingredients = await brew_repo.get_all_ingredients(session)
    triples = await brew_repo.get_all_dangerous_triples(session)
    assert len(ingredients) == 28
    assert len(triples) == 5


@pytest.mark.asyncio
async def test_seed_idempotent(session: AsyncSession):
    await seed_if_empty(session)
    await seed_if_empty(session)
    ingredients = await brew_repo.get_all_ingredients(session)
    triples = await brew_repo.get_all_dangerous_triples(session)
    assert len(ingredients) == 28
    assert len(triples) == 5


@pytest.mark.asyncio
async def test_seed_correct_rarity_breakdown(session: AsyncSession):
    await seed_if_empty(session)
    free = await brew_repo.get_ingredients_by_rarity(session, "free")
    uncommon = await brew_repo.get_ingredients_by_rarity(session, "uncommon")
    rare = await brew_repo.get_ingredients_by_rarity(session, "rare")
    assert len(free) == 6
    assert len(uncommon) == 15
    assert len(rare) == 7
