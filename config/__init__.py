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
    channel_name: str | None = None  # legacy single-channel fallback

    # Per-game channel restrictions
    derby_channel: str | None = "downtime-derby"
    brewing_channel: str | None = "potion-panic"
    fishing_channel: str | None = "lazy-lures"
    dungeon_channel: str | None = "monster-mash"
    racer_buy_base: int = 20
    racer_buy_multiplier: int = 5
    racer_sell_fraction: float = 0.5
    max_racers_per_owner: int = 3
    min_pool_size: int = 40
    placement_prizes: str = "50,30,20"
    training_base: int = 10
    training_multiplier: int = 2
    rest_cost: int = 0
    feed_cost: int = 30
    stable_upgrade_costs: str = "500,1000,2000"
    female_buy_multiplier: float = 2.0
    retired_sell_penalty: float = 0.6
    foal_sell_penalty: float = 0.3
    min_training_to_race: int = 5
    max_trains_per_race: int = 5
    breeding_fee: int = 25
    breeding_cooldown: int = 6
    min_races_to_breed: int = 5
    max_foals_per_female: int = 3
    racer_flavor: str | None = None
    race_stat_window: int = 35
    racer_emoji: str = "\U0001f3c7"
    daily_min: int = 15
    daily_max: int = 30
    tournament_enabled: bool = True
    # Render a live per-segment standings bar chart alongside race commentary.
    # Guilds can disable via /derby settings set live_standings_chart false.
    live_standings_chart: bool = True

    # Brewing / Potion Panic
    bottle_fee: int = 10
    base_potency: int = 10
    min_potency_no_match: int = 2
    triple_instability: int = 50
    explosion_threshold_min: int = 70
    explosion_threshold_max: int = 130
    rare_drop_potency: int = 200
    potion_min_potency: int = 100

    # Fishing
    fishing_bait_costs: str = "2,5,12,20"
    fishing_cast_multiplier: float = 1.0

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
