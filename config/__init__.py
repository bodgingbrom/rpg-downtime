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
    commentary_delay: float = 6.0
    channel_name: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path = Path("config.yaml")) -> "Settings":
        data: dict[str, Any]
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


__all__ = ["Settings"]
