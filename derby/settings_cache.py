"""Per-guild GuildSettings cache + resolver.

GuildSettings rows are admin-edited infrequently but read on every
slash-command, autocomplete keystroke, and scheduler tick. ~50 production
sites do `get_guild_settings(...)` then `resolve_guild_setting(...)` against
the result. Without caching that's a SELECT-by-PK on every read.

This module provides a small write-through-on-bust cache. Hits skip the DB
entirely; misses populate the cache and last `max_age` seconds (default 5s).
Admin commands that mutate GuildSettings must call ``bust(guild_id)`` so
their next read sees the new value.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Settings, resolve_guild_setting
from core import repositories as core_repo
from core.models import GuildSettings


class GuildSettingsResolver:
    """Cache + resolver for per-guild GuildSettings rows."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        global_settings: Settings,
        *,
        max_age: float = 5.0,
    ) -> None:
        self._sm = sessionmaker
        self._global = global_settings
        self._max_age = max_age
        # guild_id → (cached_at_monotonic, GuildSettings row or None)
        self._cache: dict[int, tuple[float, GuildSettings | None]] = {}

    async def get(self, guild_id: int) -> GuildSettings | None:
        """Return the GuildSettings row for `guild_id` (cached).

        Returns ``None`` when the guild has no row yet — which is the
        signal for `resolve_guild_setting` to fall back to globals.
        """
        now = time.monotonic()
        cached = self._cache.get(guild_id)
        if cached is not None:
            ts, gs = cached
            if now - ts < self._max_age:
                return gs
        async with self._sm() as session:
            gs = await core_repo.get_guild_settings(session, guild_id)
        self._cache[guild_id] = (now, gs)
        return gs

    async def resolve(self, guild_id: int, key: str) -> Any:
        """Return the resolved value for `key` in `guild_id`.

        Equivalent to ``resolve_guild_setting(await get(guild_id), settings, key)``
        — the common one-liner case.
        """
        gs = await self.get(guild_id)
        return resolve_guild_setting(gs, self._global, key)

    def bust(self, guild_id: int) -> None:
        """Invalidate the cache for `guild_id`.

        Call this after any code path that mutates GuildSettings (e.g.
        `/derby settings set`) so subsequent reads see the new value
        instead of waiting up to ``max_age`` seconds for natural expiry.
        """
        self._cache.pop(guild_id, None)
