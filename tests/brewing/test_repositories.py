import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from brewing import repositories as brew_repo
from brewing.seed_data import seed_if_empty
import brewing.models  # noqa: F401
import economy.models  # noqa: F401

GUILD_A = 100
GUILD_B = 200
USER = 1


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as sess:
        await seed_if_empty(sess)
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_all_ingredients(session: AsyncSession):
    ingredients = await brew_repo.get_all_ingredients(session)
    assert len(ingredients) == 28


@pytest.mark.asyncio
async def test_get_ingredients_by_rarity(session: AsyncSession):
    free = await brew_repo.get_ingredients_by_rarity(session, "free")
    uncommon = await brew_repo.get_ingredients_by_rarity(session, "uncommon")
    rare = await brew_repo.get_ingredients_by_rarity(session, "rare")
    assert len(free) == 6
    assert len(uncommon) == 15
    assert len(rare) == 7


@pytest.mark.asyncio
async def test_get_ingredient_by_name(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    assert ember is not None
    assert ember.tag_1 == "Thermal"
    assert ember.tag_2 == "Volatile"
    assert ember.rarity == "free"

    missing = await brew_repo.get_ingredient_by_name(session, "Nonexistent")
    assert missing is None


@pytest.mark.asyncio
async def test_add_player_ingredient_creates_new(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    pi = await brew_repo.add_player_ingredient(
        session, USER, GUILD_A, ember.id, 3
    )
    assert pi.quantity == 3
    assert pi.ingredient_id == ember.id


@pytest.mark.asyncio
async def test_add_player_ingredient_increments(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 2)
    pi = await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 5)
    assert pi.quantity == 7


@pytest.mark.asyncio
async def test_remove_player_ingredient_decrements(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 10)
    pi = await brew_repo.remove_player_ingredient(session, USER, GUILD_A, ember.id, 3)
    assert pi is not None
    assert pi.quantity == 7


@pytest.mark.asyncio
async def test_remove_player_ingredient_insufficient(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 2)
    result = await brew_repo.remove_player_ingredient(
        session, USER, GUILD_A, ember.id, 5
    )
    assert result is None
    # Original should be unchanged
    pi = await brew_repo.get_player_ingredient(session, USER, GUILD_A, ember.id)
    assert pi.quantity == 2


@pytest.mark.asyncio
async def test_remove_player_ingredient_deletes_at_zero(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 3)
    result = await brew_repo.remove_player_ingredient(
        session, USER, GUILD_A, ember.id, 3
    )
    assert result is None  # row deleted
    pi = await brew_repo.get_player_ingredient(session, USER, GUILD_A, ember.id)
    assert pi is None


@pytest.mark.asyncio
async def test_player_ingredients_guild_isolation(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    await brew_repo.add_player_ingredient(session, USER, GUILD_A, ember.id, 5)
    await brew_repo.add_player_ingredient(session, USER, GUILD_B, ember.id, 10)

    inv_a = await brew_repo.get_player_ingredients(session, USER, GUILD_A)
    inv_b = await brew_repo.get_player_ingredients(session, USER, GUILD_B)

    assert len(inv_a) == 1
    assert inv_a[0].quantity == 5
    assert len(inv_b) == 1
    assert inv_b[0].quantity == 10


@pytest.mark.asyncio
async def test_get_all_dangerous_triples(session: AsyncSession):
    triples = await brew_repo.get_all_dangerous_triples(session)
    assert len(triples) == 5
    tags = {(t.tag_1, t.tag_2, t.tag_3) for t in triples}
    assert ("Volatile", "Thermal", "Corrosive") in tags
