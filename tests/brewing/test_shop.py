import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db_base import Base
from brewing import repositories as brew_repo
from brewing.seed_data import seed_if_empty
from brewing.shop import get_daily_shop
import brewing.models  # noqa: F401


@pytest_asyncio.fixture()
async def all_ingredients():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        await seed_if_empty(sess)
        ingredients = await brew_repo.get_all_ingredients(sess)
    await engine.dispose()
    return ingredients


@pytest.mark.asyncio
async def test_shop_deterministic(all_ingredients):
    shop1 = get_daily_shop("2026-04-06", all_ingredients)
    shop2 = get_daily_shop("2026-04-06", all_ingredients)
    assert [i.id for i in shop1] == [i.id for i in shop2]


@pytest.mark.asyncio
async def test_shop_different_dates(all_ingredients):
    shop1 = get_daily_shop("2026-04-06", all_ingredients)
    shop2 = get_daily_shop("2026-04-07", all_ingredients)
    # Extremely unlikely to be identical; different seed should give different selection
    assert [i.id for i in shop1] != [i.id for i in shop2]


@pytest.mark.asyncio
async def test_shop_correct_counts(all_ingredients):
    shop = get_daily_shop("2026-04-06", all_ingredients)
    assert len(shop) in (5, 6)  # 4-5 uncommon + 1 rare
    rare_count = sum(1 for i in shop if i.rarity == "rare")
    uncommon_count = sum(1 for i in shop if i.rarity == "uncommon")
    assert rare_count == 1
    assert uncommon_count in (4, 5)


@pytest.mark.asyncio
async def test_shop_no_free_ingredients(all_ingredients):
    shop = get_daily_shop("2026-04-06", all_ingredients)
    for item in shop:
        assert item.rarity != "free"


@pytest.mark.asyncio
async def test_shop_no_duplicates(all_ingredients):
    shop = get_daily_shop("2026-04-06", all_ingredients)
    ids = [i.id for i in shop]
    assert len(ids) == len(set(ids))
