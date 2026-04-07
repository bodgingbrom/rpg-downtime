import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db_base import Base
from brewing import repositories as brew_repo
from brewing.seed_data import seed_if_empty
import brewing.models  # noqa: F401
import economy.models  # noqa: F401

GUILD_ID = 100
USER_ID = 1


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


# ---------------------------------------------------------------------------
# Brew session CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_brew_session(session: AsyncSession):
    brew = await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    assert brew.id is not None
    assert brew.status == "active"
    assert brew.potency == 0
    assert brew.instability == 0
    assert brew.explosion_threshold == 100
    assert brew.bottle_cost == 10
    assert brew.ingredient_cost_total == 0
    assert brew.payout is None


@pytest.mark.asyncio
async def test_get_active_brew(session: AsyncSession):
    # No active brew initially
    assert await brew_repo.get_active_brew(session, USER_ID, GUILD_ID) is None

    # Create one
    brew = await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    active = await brew_repo.get_active_brew(session, USER_ID, GUILD_ID)
    assert active is not None
    assert active.id == brew.id

    # Mark as exploded — should no longer be found
    brew.status = "exploded"
    await session.commit()
    assert await brew_repo.get_active_brew(session, USER_ID, GUILD_ID) is None


@pytest.mark.asyncio
async def test_get_active_brew_guild_isolation(session: AsyncSession):
    await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    # Different guild should have no active brew
    assert await brew_repo.get_active_brew(session, USER_ID, 999) is None


@pytest.mark.asyncio
async def test_get_brew_session(session: AsyncSession):
    brew = await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    fetched = await brew_repo.get_brew_session(session, brew.id)
    assert fetched is not None
    assert fetched.id == brew.id

    assert await brew_repo.get_brew_session(session, 9999) is None


# ---------------------------------------------------------------------------
# Brew ingredient CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_get_brew_ingredients(session: AsyncSession):
    brew = await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    brimstone = await brew_repo.get_ingredient_by_name(session, "Brimstone Dust")

    bi1 = await brew_repo.add_brew_ingredient(
        session,
        brew_session_id=brew.id,
        ingredient_id=ember.id,
        add_order=1,
        potency_gained=2,
        instability_after=0,
    )
    bi2 = await brew_repo.add_brew_ingredient(
        session,
        brew_session_id=brew.id,
        ingredient_id=brimstone.id,
        add_order=2,
        potency_gained=10,
        instability_after=50,
    )

    ingredients = await brew_repo.get_brew_ingredients(session, brew.id)
    assert len(ingredients) == 2
    assert ingredients[0].add_order == 1
    assert ingredients[0].ingredient_id == ember.id
    assert ingredients[1].add_order == 2
    assert ingredients[1].potency_gained == 10
    assert ingredients[1].instability_after == 50


@pytest.mark.asyncio
async def test_brew_ingredients_ordered(session: AsyncSession):
    brew = await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    ingredients = await brew_repo.get_all_ingredients(session)
    # Add in reverse order to verify ordering
    for i, ing in enumerate(reversed(ingredients[:3])):
        await brew_repo.add_brew_ingredient(
            session,
            brew_session_id=brew.id,
            ingredient_id=ing.id,
            add_order=i + 1,
            potency_gained=0,
            instability_after=0,
        )

    result = await brew_repo.get_brew_ingredients(session, brew.id)
    assert [bi.add_order for bi in result] == [1, 2, 3]
