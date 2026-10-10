"""Loads config.yaml into a typed Config."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class ModelConfig(BaseModel):
    provider: str
    id: str
    price_in: float | None = None
    price_cached: float | None = None
    price_out: float | None = None


class BudgetConfig(BaseModel):
    cap_usd: float
    degrade_pct: float


class GroqLimits(BaseModel):
    max_call_tokens: int
    daily_budget_pct: float


class Config(BaseModel):
    models: dict[str, ModelConfig]
    routing: dict[str, str]
    budget: BudgetConfig
    groq_limits: GroqLimits
    default_max_tokens: int
    db_path: str
    work_dir: str


def load_config(path: str | Path) -> Config:
    return Config.model_validate(yaml.safe_load(Path(path).read_text()))
