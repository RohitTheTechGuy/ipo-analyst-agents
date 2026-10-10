"""Picks the model key for a task (plan 6.3): config routing, degraded to Luna near the cap, Groq only for small calls."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from ipo_agents.config import Config

if TYPE_CHECKING:
    from ipo_agents.budget import BudgetMeter

# Model keys in config.yaml `models`.
DEGRADE_FROM = "terra"  # once degraded, tasks routed here go to FALLBACK
FALLBACK = "luna"  # also where Groq calls go when they don't fit or fail
PER_MILLION = 1_000_000


def estimate_run_usd(rhp_pdf: Path, config: Config) -> float:
    """Expected cost of analysing this RHP, before anything runs: the Luna map step scales with
    page count, everything else is a fixed allowance (config budget.estimate)."""
    with fitz.open(rhp_pdf) as pdf:
        pages = pdf.page_count
    est, luna = config.budget.estimate, config.models[FALLBACK]
    per_page = (est.tokens_per_page * luna.price_in + est.output_tokens_per_page * luna.price_out) / PER_MILLION
    return pages * per_page + est.fixed_usd


def get_model(task: str, est_tokens: int, meter: BudgetMeter) -> str:
    """The model key for one call of `task` whose prompt + max_tokens is `est_tokens`."""
    config = meter.config
    if task not in config.routing:
        raise ValueError(f"task {task!r} has no entry in routing")
    model = config.routing[task]
    if model == DEGRADE_FROM and meter.degraded:
        return FALLBACK
    if is_groq(model, config) and not _groq_has_room(est_tokens, meter):
        return FALLBACK
    return model


def _groq_has_room(est_tokens: int, meter: BudgetMeter) -> bool:
    """Small call, and the last 24h of Groq usage under daily_budget_pct of Groq's daily cap."""
    limits = meter.config.groq_limits
    if est_tokens > limits.max_call_tokens:
        return False
    groq_keys = [k for k in meter.config.models if is_groq(k, meter.config)]
    return meter.tokens_last_24h(groq_keys) < limits.daily_cap_tokens * limits.daily_budget_pct / 100


def is_groq(model: str, config: Config) -> bool:
    cfg = config.models.get(model)
    return cfg is not None and cfg.provider == "groq"
