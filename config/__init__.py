from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    race_times: list[str] = ["09:00", "15:00", "21:00"]
    default_wallet: int = 100
    retirement_threshold: int = 96
    bet_window: int = 120
    countdown_total: int = 10
    max_racers_per_race: int = 6
    commentary_delay: float = 6.0
    channel_name: str | None = None
    racer_buy_base: int = 20
    racer_buy_multiplier: int = 2
    racer_sell_fraction: float = 0.5
    max_racers_per_owner: int = 3
    min_pool_size: int = 20

    @classmethod
    def from_yaml(cls, path: str | Path = Path("config.yaml")) -> "Settings":
        data: dict[str, Any]
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


def resolve_guild_setting(
    guild_settings: Any | None,
    global_settings: Settings,
    key: str,
) -> Any:
    """Return a per-guild override if set, otherwise the global default.

    ``guild_settings`` is a :class:`GuildSettings` row (or ``None``).
    Nullable columns that are ``None`` mean "use the global default".
    """
    if guild_settings is not None:
        val = getattr(guild_settings, key, None)
        if val is not None:
            return val
    return getattr(global_settings, key)


__all__ = ["Settings", "resolve_guild_setting"]
