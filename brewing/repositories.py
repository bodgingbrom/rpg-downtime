from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DangerousTriple, Ingredient, PlayerIngredient


# ---------------------------------------------------------------------------
# Ingredient lookups (static reference data)
# ---------------------------------------------------------------------------


async def get_all_ingredients(session: AsyncSession) -> list[Ingredient]:
    result = await session.execute(select(Ingredient).order_by(Ingredient.id))
    return list(result.scalars().all())


async def get_ingredients_by_rarity(
    session: AsyncSession, rarity: str
) -> list[Ingredient]:
    result = await session.execute(
        select(Ingredient)
        .where(Ingredient.rarity == rarity)
        .order_by(Ingredient.id)
    )
    return list(result.scalars().all())


async def get_ingredient_by_id(
    session: AsyncSession, ingredient_id: int
) -> Ingredient | None:
    result = await session.execute(
        select(Ingredient).where(Ingredient.id == ingredient_id)
    )
    return result.scalars().first()


async def get_ingredient_by_name(
    session: AsyncSession, name: str
) -> Ingredient | None:
    result = await session.execute(
        select(Ingredient).where(Ingredient.name == name)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Player inventory
# ---------------------------------------------------------------------------


async def get_player_ingredients(
    session: AsyncSession, user_id: int, guild_id: int
) -> list[PlayerIngredient]:
    result = await session.execute(
        select(PlayerIngredient).where(
            PlayerIngredient.user_id == user_id,
            PlayerIngredient.guild_id == guild_id,
        )
    )
    return list(result.scalars().all())


async def get_player_ingredient(
    session: AsyncSession, user_id: int, guild_id: int, ingredient_id: int
) -> PlayerIngredient | None:
    result = await session.execute(
        select(PlayerIngredient).where(
            PlayerIngredient.user_id == user_id,
            PlayerIngredient.guild_id == guild_id,
            PlayerIngredient.ingredient_id == ingredient_id,
        )
    )
    return result.scalars().first()


async def add_player_ingredient(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    ingredient_id: int,
    quantity: int = 1,
) -> PlayerIngredient:
    existing = await get_player_ingredient(session, user_id, guild_id, ingredient_id)
    if existing:
        existing.quantity += quantity
    else:
        existing = PlayerIngredient(
            user_id=user_id,
            guild_id=guild_id,
            ingredient_id=ingredient_id,
            quantity=quantity,
        )
        session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


async def remove_player_ingredient(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    ingredient_id: int,
    quantity: int = 1,
) -> PlayerIngredient | None:
    existing = await get_player_ingredient(session, user_id, guild_id, ingredient_id)
    if existing is None or existing.quantity < quantity:
        return None
    existing.quantity -= quantity
    if existing.quantity <= 0:
        await session.delete(existing)
        await session.commit()
        return None
    await session.commit()
    await session.refresh(existing)
    return existing


# ---------------------------------------------------------------------------
# Dangerous triples
# ---------------------------------------------------------------------------


async def get_all_dangerous_triples(
    session: AsyncSession,
) -> list[DangerousTriple]:
    result = await session.execute(
        select(DangerousTriple).order_by(DangerousTriple.id)
    )
    return list(result.scalars().all())
