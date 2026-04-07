from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BrewIngredient, BrewSession, DangerousTriple, Ingredient, PlayerIngredient


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


# ---------------------------------------------------------------------------
# Brew sessions
# ---------------------------------------------------------------------------


async def create_brew_session(
    session: AsyncSession,
    *,
    user_id: int,
    guild_id: int,
    explosion_threshold: int,
    bottle_cost: int,
) -> BrewSession:
    brew = BrewSession(
        user_id=user_id,
        guild_id=guild_id,
        explosion_threshold=explosion_threshold,
        bottle_cost=bottle_cost,
    )
    session.add(brew)
    await session.commit()
    await session.refresh(brew)
    return brew


async def get_active_brew(
    session: AsyncSession, user_id: int, guild_id: int
) -> BrewSession | None:
    result = await session.execute(
        select(BrewSession).where(
            BrewSession.user_id == user_id,
            BrewSession.guild_id == guild_id,
            BrewSession.status == "active",
        )
    )
    return result.scalars().first()


async def get_brew_session(
    session: AsyncSession, brew_id: int
) -> BrewSession | None:
    result = await session.execute(
        select(BrewSession).where(BrewSession.id == brew_id)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Brew ingredients (ingredients added to a brew session)
# ---------------------------------------------------------------------------


async def add_brew_ingredient(
    session: AsyncSession,
    *,
    brew_session_id: int,
    ingredient_id: int,
    add_order: int,
    potency_gained: int,
    instability_after: int,
) -> BrewIngredient:
    bi = BrewIngredient(
        brew_session_id=brew_session_id,
        ingredient_id=ingredient_id,
        add_order=add_order,
        potency_gained=potency_gained,
        instability_after=instability_after,
    )
    session.add(bi)
    await session.commit()
    await session.refresh(bi)
    return bi


async def get_brew_ingredients(
    session: AsyncSession, brew_session_id: int
) -> list[BrewIngredient]:
    result = await session.execute(
        select(BrewIngredient)
        .where(BrewIngredient.brew_session_id == brew_session_id)
        .order_by(BrewIngredient.add_order)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Brew history / journal
# ---------------------------------------------------------------------------


async def get_brew_history(
    session: AsyncSession, user_id: int, guild_id: int, limit: int = 20
) -> list[BrewSession]:
    result = await session.execute(
        select(BrewSession)
        .where(
            BrewSession.user_id == user_id,
            BrewSession.guild_id == guild_id,
            BrewSession.status != "active",
        )
        .order_by(BrewSession.completed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_brews_with_ingredient(
    session: AsyncSession,
    user_id: int,
    guild_id: int,
    ingredient_id: int,
    limit: int = 20,
) -> list[BrewSession]:
    result = await session.execute(
        select(BrewSession)
        .join(BrewIngredient, BrewIngredient.brew_session_id == BrewSession.id)
        .where(
            BrewSession.user_id == user_id,
            BrewSession.guild_id == guild_id,
            BrewSession.status != "active",
            BrewIngredient.ingredient_id == ingredient_id,
        )
        .order_by(BrewSession.completed_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
