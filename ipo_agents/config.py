"""Loads config.yaml into a typed Config."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class ModelConfig(BaseModel):
    provider: str
    id: str
    price_in: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_cached: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_out: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Sent to the provider only when set (OpenAI reasoning models: minimal/low/medium/high).
    reasoning_effort: str | None = None


class RunEstimate(BaseModel):
    """Pre-run cost estimate from the RHP page count: ~$0.93 for a 520-page RHP (the arithmetic is in
    config.yaml). Per-page figures include per-call prompt/schema overhead and reasoning tokens."""

    tokens_per_page: int = Field(default=1600, ge=0)  # map step input, Luna
    output_tokens_per_page: int = Field(default=350, ge=0)  # map step output incl. reasoning, Luna
    fixed_usd: float = Field(default=0.55, ge=0, allow_inf_nan=False)  # Terra steps + other Luna steps


class BudgetConfig(BaseModel):
    cap_usd: float = Field(gt=0, allow_inf_nan=False)
    degrade_pct: float
    estimate: RunEstimate = RunEstimate()
    # Kept free for the synthesizer: verifier re-asks stop once spend + in flight passes cap - this.
    synth_reserve_usd: float = Field(default=0.15, ge=0, allow_inf_nan=False)


class GroqLimits(BaseModel):
    max_call_tokens: int  # prompt + max_tokens above this => Luna
    daily_budget_pct: float  # today's Groq tokens at or above this % of daily_cap_tokens => Luna
    daily_cap_tokens: int = Field(gt=0)  # Groq's published per-model tokens/day limit


class SentimentConfig(BaseModel):
    # Sites whose IPO pages are paid promotion; a host matches the domain itself or any subdomain of it.
    denylist_domains: list[str] = []


class PeersConfig(BaseModel):
    # Peer name (as the RHP or the agent writes it) -> NSE symbol, for names NSE's search gets wrong.
    symbol_overrides: dict[str, str] = {}


class Config(BaseModel):
    models: dict[str, ModelConfig]
    routing: dict[str, str]
    budget: BudgetConfig
    groq_limits: GroqLimits
    default_max_tokens: int = Field(gt=0)
    db_path: str
    work_dir: str
    sentiment: SentimentConfig = SentimentConfig()
    peers: PeersConfig = PeersConfig()

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
            elif model.provider != "groq" and (model.price_in == 0 or model.price_out == 0):
                # Only Groq is free; a zero price on a paid provider would silently switch metering off.
                problems.append(f"routing.{task} -> model {key} ({model.provider}) can't be priced as free")
        if problems:
            raise ValueError("; ".join(problems))
        return self


REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path) -> Config:
    """The config, with a relative db_path or work_dir resolved against the repo root: a manual run from
    any directory uses the same store, budget log and checkpoints as the scheduled one."""
    raw = yaml.safe_load(Path(path).read_text())
    for key in ("db_path", "work_dir"):
        if isinstance(raw.get(key), str):
            p = Path(raw[key]).expanduser()
            raw[key] = str(p if p.is_absolute() else REPO_ROOT / p)
    return Config.model_validate(raw)
