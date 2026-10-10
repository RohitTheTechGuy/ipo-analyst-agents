"""Loads config.yaml into a typed Config."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


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

    @model_validator(mode="after")
    def _routed_models_are_priced(self):
        """Missing price => refuse to start: the budget meter can't cost an unpriced call."""
        problems = []
        for task, key in self.routing.items():
            model = self.models.get(key)
            if model is None:
                problems.append(f"routing.{task} -> {key!r} is not a key in models")
                continue
            missing = [p for p in ("price_in", "price_cached", "price_out") if getattr(model, p) is None]
            if missing:
                problems.append(f"routing.{task} -> model {key} has no {', '.join(missing)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_config(path: str | Path) -> Config:
    return Config.model_validate(yaml.safe_load(Path(path).read_text()))
