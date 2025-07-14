from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseSettings


class Settings(BaseSettings):
    race_frequency: int
    default_wallet: int
    retirement_threshold: int
    bet_window: int = 120
    countdown_total: int = 10
    channel_name: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path = Path("config.yaml")) -> "Settings":
        data: dict[str, Any]
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


__all__ = ["Settings"]
