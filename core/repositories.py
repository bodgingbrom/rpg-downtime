"""Repository functions for cross-game models (GuildSettings, CommandLog)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import CommandLog, GuildSettings


async def create_guild_settings(session: AsyncSession, **kwargs) -> GuildSettings:
    obj = GuildSettings(**kwargs)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return obj


async def get_guild_settings(
    session: AsyncSession, guild_id: int
) -> GuildSettings | None:
    return await session.get(GuildSettings, guild_id)


async def update_guild_settings(
    session: AsyncSession, guild_id: int, **kwargs
) -> GuildSettings | None:
    obj = await session.get(GuildSettings, guild_id)
    if obj is None:
        return None
    for key, value in kwargs.items():
        setattr(obj, key, value)
    await session.commit()
    await session.refresh(obj)
    return obj


async def delete_guild_settings(session: AsyncSession, guild_id: int) -> None:
    obj = await session.get(GuildSettings, guild_id)
    if obj is not None:
        await session.delete(obj)
        await session.commit()


async def log_command(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    command: str,
    cog: str = "unknown",
) -> CommandLog:
    """Insert a command log entry."""
    entry = CommandLog(
        guild_id=guild_id,
        user_id=user_id,
        command=command,
        cog=cog,
    )
    session.add(entry)
    await session.commit()
    return entry
