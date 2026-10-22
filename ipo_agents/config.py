"""Loads config.yaml into a typed Config."""
from __future__ import annotations

from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ModelConfig(BaseModel):
    provider: Literal["openai", "groq", "xai"]
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


class AdvisorConfig(BaseModel):
    """The Financial Advisor's own ledger per IPO ("<ipo_id>:advisor"), separate from budget.cap_usd."""

    cap_usd: float = Field(default=0.50, gt=0, allow_inf_nan=False)
    max_tokens: int = Field(default=12_000, gt=0)  # reasoning + answer
    closing_day_refresh: bool = True
    # Closing-day demand check (demand_check.py): hot at or above either threshold, weak below demand_weak_x.
    demand_hot_total_x: float = Field(default=10.0, gt=0)
    demand_hot_retail_x: float = Field(default=5.0, gt=0)
    demand_weak_x: float = Field(default=1.0, gt=0)
    # The market disagrees with the call: a NO BUY with total subscription at least devil_total_x, retail above
    # devil_retail_x (when published) and a GMP at least devil_gmp_pct of the upper band; or a BUY with total
    # under devil_total_x and a GMP under devil_gmp_pct. Then the analysis itself runs the demand check (the
    # market as devil's advocate), and a closing-day one re-sends the PDF.
    devil_total_x: float = Field(default=2.0, gt=0)
    devil_retail_x: float = Field(default=1.0, ge=0)
    devil_gmp_pct: float = Field(default=10.0, gt=0)
    demand_check_max_tokens: int = Field(default=8_000, gt=0)  # reasoning + answer


class QaConfig(BaseModel):
    """Telegram questions on an analysed RHP (rhp_qa.py), answered by the Claude Code CLI on the subscription."""

    model: str = "sonnet"  # a Claude Code model alias
    timeout_s: float = Field(default=180, gt=0)
    max_words: int = Field(default=150, gt=0)
    window_s: float = Field(default=600, ge=0)  # after an answer, keep listening this long for follow-ups
    # Within this long of the last answer, a short reply the picks job doesn't understand is a follow-up question.
    follow_up_s: float = Field(default=1800, ge=0)


class Config(BaseModel):
    models: dict[str, ModelConfig]
    routing: dict[str, str]
    budget: BudgetConfig
    groq_limits: GroqLimits
    default_max_tokens: int = Field(gt=0)
    # Map (part-extraction) calls always run on the map model (Luna), whatever the section's own model:
    # dense parts (peer tables, KPIs) need room, and Luna's worst case at 8k is about $0.01.
    map_max_tokens: int = Field(default=8000, gt=0)
    db_path: str
    work_dir: str
    reports_dir: str = "reports"  # archive of every report and Telegram message (gitignored)
    sentiment: SentimentConfig = SentimentConfig()
    peers: PeersConfig = PeersConfig()
    advisor: AdvisorConfig = AdvisorConfig()
    qa: QaConfig = QaConfig()

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
    for key in ("db_path", "work_dir", "reports_dir"):
        if isinstance(raw.get(key), str):
            p = Path(raw[key]).expanduser()
            raw[key] = str(p if p.is_absolute() else REPO_ROOT / p)
    return Config.model_validate(raw)
