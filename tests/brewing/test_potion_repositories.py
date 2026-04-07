"""Tests for PlayerBrewEffect and RevealedIngredient repository functions."""

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
# PlayerBrewEffect CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_brew_effect(session: AsyncSession):
    effect = await brew_repo.create_player_brew_effect(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        effect_type="fortification",
        effect_value=90,
    )
    assert effect.id is not None
    assert effect.effect_type == "fortification"
    assert effect.effect_value == 90

    fetched = await brew_repo.get_player_brew_effect(
        session, USER_ID, GUILD_ID, "fortification"
    )
    assert fetched is not None
    assert fetched.id == effect.id


@pytest.mark.asyncio
async def test_get_brew_effect_missing(session: AsyncSession):
    result = await brew_repo.get_player_brew_effect(
        session, USER_ID, GUILD_ID, "foresight"
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_brew_effect(session: AsyncSession):
    effect = await brew_repo.create_player_brew_effect(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        effect_type="foresight",
        effect_value=0,
    )
    await brew_repo.delete_player_brew_effect(session, effect.id)
    assert await brew_repo.get_player_brew_effect(
        session, USER_ID, GUILD_ID, "foresight"
    ) is None


@pytest.mark.asyncio
async def test_brew_effect_guild_isolation(session: AsyncSession):
    await brew_repo.create_player_brew_effect(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        effect_type="fortification",
        effect_value=80,
    )
    # Different guild should not see it
    result = await brew_repo.get_player_brew_effect(
        session, USER_ID, 999, "fortification"
    )
    assert result is None


# ---------------------------------------------------------------------------
# RevealedIngredient CRUD
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def ingredient_id(session: AsyncSession) -> int:
    """Return the id of the first seeded ingredient."""
    ings = await brew_repo.get_all_ingredients(session)
    return ings[0].id


@pytest.mark.asyncio
async def test_create_and_get_revealed(session: AsyncSession, ingredient_id: int):
    revealed = await brew_repo.create_revealed_ingredient(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        ingredient_id=ingredient_id,
    )
    assert revealed.id is not None
    assert revealed.ingredient_id == ingredient_id

    all_revealed = await brew_repo.get_revealed_ingredients(
        session, USER_ID, GUILD_ID
    )
    assert len(all_revealed) == 1
    assert all_revealed[0].ingredient_id == ingredient_id


@pytest.mark.asyncio
async def test_is_ingredient_revealed(session: AsyncSession, ingredient_id: int):
    assert not await brew_repo.is_ingredient_revealed(
        session, USER_ID, GUILD_ID, ingredient_id
    )
    await brew_repo.create_revealed_ingredient(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        ingredient_id=ingredient_id,
    )
    assert await brew_repo.is_ingredient_revealed(
        session, USER_ID, GUILD_ID, ingredient_id
    )


@pytest.mark.asyncio
async def test_revealed_guild_isolation(session: AsyncSession, ingredient_id: int):
    await brew_repo.create_revealed_ingredient(
        session,
        user_id=USER_ID,
        guild_id=GUILD_ID,
        ingredient_id=ingredient_id,
    )
    # Different guild should not see it
    assert not await brew_repo.is_ingredient_revealed(
        session, USER_ID, 999, ingredient_id
    )
    revealed = await brew_repo.get_revealed_ingredients(session, USER_ID, 999)
    assert revealed == []


@pytest.mark.asyncio
async def test_multiple_revealed_ingredients(session: AsyncSession):
    ings = await brew_repo.get_all_ingredients(session)
    assert len(ings) >= 3

    for ing in ings[:3]:
        await brew_repo.create_revealed_ingredient(
            session,
            user_id=USER_ID,
            guild_id=GUILD_ID,
            ingredient_id=ing.id,
        )

    all_revealed = await brew_repo.get_revealed_ingredients(
        session, USER_ID, GUILD_ID
    )
    assert len(all_revealed) == 3
    revealed_ids = {r.ingredient_id for r in all_revealed}
    expected_ids = {ing.id for ing in ings[:3]}
    assert revealed_ids == expected_ids
