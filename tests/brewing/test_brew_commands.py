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


# ---------------------------------------------------------------------------
# Brew history / journal
# ---------------------------------------------------------------------------


async def _create_completed_brew(session, user_id, guild_id, status, potency, payout=None):
    """Helper to create a completed brew for history tests."""
    from datetime import datetime, timezone

    brew = await brew_repo.create_brew_session(
        session,
        user_id=user_id,
        guild_id=guild_id,
        explosion_threshold=100,
        bottle_cost=10,
    )
    brew.status = status
    brew.potency = potency
    brew.payout = payout
    brew.completed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(brew)
    return brew


@pytest.mark.asyncio
async def test_get_brew_history_empty(session: AsyncSession):
    history = await brew_repo.get_brew_history(session, USER_ID, GUILD_ID)
    assert history == []


@pytest.mark.asyncio
async def test_get_brew_history_excludes_active(session: AsyncSession):
    # Active brew should not appear in history
    await brew_repo.create_brew_session(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        explosion_threshold=100,
        bottle_cost=10,
    )
    history = await brew_repo.get_brew_history(session, USER_ID, GUILD_ID)
    assert history == []


@pytest.mark.asyncio
async def test_get_brew_history_returns_completed(session: AsyncSession):
    await _create_completed_brew(session, USER_ID, GUILD_ID, "cashed_out", 50, 75)
    await _create_completed_brew(session, USER_ID, GUILD_ID, "exploded", 30)

    history = await brew_repo.get_brew_history(session, USER_ID, GUILD_ID)
    assert len(history) == 2
    # Most recent first
    assert history[0].status == "exploded"
    assert history[1].status == "cashed_out"


@pytest.mark.asyncio
async def test_get_brew_history_respects_limit(session: AsyncSession):
    for i in range(5):
        await _create_completed_brew(session, USER_ID, GUILD_ID, "cashed_out", i * 10, i * 5)

    history = await brew_repo.get_brew_history(session, USER_ID, GUILD_ID, limit=3)
    assert len(history) == 3


@pytest.mark.asyncio
async def test_get_brew_history_guild_isolation(session: AsyncSession):
    await _create_completed_brew(session, USER_ID, GUILD_ID, "cashed_out", 50, 75)
    await _create_completed_brew(session, USER_ID, 999, "cashed_out", 30, 45)

    history = await brew_repo.get_brew_history(session, USER_ID, GUILD_ID)
    assert len(history) == 1
    assert history[0].potency == 50


@pytest.mark.asyncio
async def test_get_brews_with_ingredient(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")
    moonpetal = await brew_repo.get_ingredient_by_name(session, "Moonpetal")

    # Brew 1: has Ember Salt
    brew1 = await _create_completed_brew(session, USER_ID, GUILD_ID, "cashed_out", 40, 60)
    await brew_repo.add_brew_ingredient(
        session, brew_session_id=brew1.id, ingredient_id=ember.id,
        add_order=1, potency_gained=2, instability_after=0,
    )

    # Brew 2: has Moonpetal only (no Ember Salt)
    brew2 = await _create_completed_brew(session, USER_ID, GUILD_ID, "exploded", 20)
    await brew_repo.add_brew_ingredient(
        session, brew_session_id=brew2.id, ingredient_id=moonpetal.id,
        add_order=1, potency_gained=2, instability_after=0,
    )

    # Brew 3: has both
    brew3 = await _create_completed_brew(session, USER_ID, GUILD_ID, "cashed_out", 60, 90)
    await brew_repo.add_brew_ingredient(
        session, brew_session_id=brew3.id, ingredient_id=ember.id,
        add_order=1, potency_gained=2, instability_after=0,
    )
    await brew_repo.add_brew_ingredient(
        session, brew_session_id=brew3.id, ingredient_id=moonpetal.id,
        add_order=2, potency_gained=10, instability_after=0,
    )

    # Query for Ember Salt brews
    ember_brews = await brew_repo.get_brews_with_ingredient(
        session, USER_ID, GUILD_ID, ember.id
    )
    assert len(ember_brews) == 2
    ember_ids = {b.id for b in ember_brews}
    assert brew1.id in ember_ids
    assert brew3.id in ember_ids
    assert brew2.id not in ember_ids


@pytest.mark.asyncio
async def test_get_brews_with_ingredient_excludes_active(session: AsyncSession):
    ember = await brew_repo.get_ingredient_by_name(session, "Ember Salt")

    # Active brew with Ember Salt should not appear
    active_brew = await brew_repo.create_brew_session(
        session, user_id=USER_ID, guild_id=GUILD_ID,
        explosion_threshold=100, bottle_cost=10,
    )
    await brew_repo.add_brew_ingredient(
        session, brew_session_id=active_brew.id, ingredient_id=ember.id,
        add_order=1, potency_gained=2, instability_after=0,
    )

    result = await brew_repo.get_brews_with_ingredient(
        session, USER_ID, GUILD_ID, ember.id
    )
    assert result == []
