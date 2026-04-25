"""Tests for GuildSettingsResolver — caching + bust semantics."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import Settings
from db_base import Base
from core import repositories as repo  # all repo calls below are core fns
from derby.settings_cache import GuildSettingsResolver

GUILD_ID = 1


async def _setup(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings = Settings(
        race_times=["12:00"],
        default_wallet=100,
        bet_window=120,
    )
    return engine, sessionmaker, settings


@pytest.mark.asyncio
async def test_get_returns_none_when_no_row(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    resolver = GuildSettingsResolver(sessionmaker, settings)

    gs = await resolver.get(GUILD_ID)
    assert gs is None


@pytest.mark.asyncio
async def test_get_returns_row_when_present(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=GUILD_ID, bet_window=999)

    resolver = GuildSettingsResolver(sessionmaker, settings)
    gs = await resolver.get(GUILD_ID)
    assert gs is not None
    assert gs.bet_window == 999


@pytest.mark.asyncio
async def test_resolve_falls_back_to_global_when_no_row(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    resolver = GuildSettingsResolver(sessionmaker, settings)

    val = await resolver.resolve(GUILD_ID, "bet_window")
    assert val == 120  # global default


@pytest.mark.asyncio
async def test_resolve_uses_guild_override(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=GUILD_ID, bet_window=42)

    resolver = GuildSettingsResolver(sessionmaker, settings)
    val = await resolver.resolve(GUILD_ID, "bet_window")
    assert val == 42


@pytest.mark.asyncio
async def test_cache_hit_skips_db(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=GUILD_ID, bet_window=42)

    resolver = GuildSettingsResolver(sessionmaker, settings, max_age=60.0)
    first = await resolver.resolve(GUILD_ID, "bet_window")
    assert first == 42

    # Mutate the row directly via repo — cached resolver should NOT see the change
    async with sessionmaker() as session:
        await repo.update_guild_settings(session, GUILD_ID, bet_window=999)

    second = await resolver.resolve(GUILD_ID, "bet_window")
    assert second == 42  # served from cache


@pytest.mark.asyncio
async def test_bust_invalidates_cache(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=GUILD_ID, bet_window=42)

    resolver = GuildSettingsResolver(sessionmaker, settings, max_age=60.0)
    assert await resolver.resolve(GUILD_ID, "bet_window") == 42

    async with sessionmaker() as session:
        await repo.update_guild_settings(session, GUILD_ID, bet_window=999)

    resolver.bust(GUILD_ID)
    assert await resolver.resolve(GUILD_ID, "bet_window") == 999


@pytest.mark.asyncio
async def test_cache_expires_after_max_age(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=GUILD_ID, bet_window=42)

    # Use a very short TTL so we can observe expiry without sleeping
    resolver = GuildSettingsResolver(sessionmaker, settings, max_age=0.05)
    assert await resolver.resolve(GUILD_ID, "bet_window") == 42

    async with sessionmaker() as session:
        await repo.update_guild_settings(session, GUILD_ID, bet_window=999)

    time.sleep(0.1)  # exceed TTL
    assert await resolver.resolve(GUILD_ID, "bet_window") == 999


@pytest.mark.asyncio
async def test_bust_unknown_guild_is_noop(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    resolver = GuildSettingsResolver(sessionmaker, settings)

    # Should not raise
    resolver.bust(9999)
    resolver.bust(9999)


@pytest.mark.asyncio
async def test_independent_guild_caches(tmp_path: Path) -> None:
    _, sessionmaker, settings = await _setup(tmp_path)
    async with sessionmaker() as session:
        await repo.create_guild_settings(session, guild_id=1, bet_window=10)
        await repo.create_guild_settings(session, guild_id=2, bet_window=20)

    resolver = GuildSettingsResolver(sessionmaker, settings, max_age=60.0)
    assert await resolver.resolve(1, "bet_window") == 10
    assert await resolver.resolve(2, "bet_window") == 20

    # Busting one guild's cache must not affect the other
    async with sessionmaker() as session:
        await repo.update_guild_settings(session, 1, bet_window=99)
        await repo.update_guild_settings(session, 2, bet_window=88)

    resolver.bust(1)
    assert await resolver.resolve(1, "bet_window") == 99
    assert await resolver.resolve(2, "bet_window") == 20  # still cached
