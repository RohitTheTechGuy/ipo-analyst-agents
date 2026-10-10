"""Per-IPO budget meter and the metered LLM every agent calls through (spec: Budget rules)."""
from __future__ import annotations

import logging

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

import tiktoken

from ipo_agents.config import Config, ModelConfig
from ipo_agents.llm import LLM, LLMParseError

PER_MILLION = 1_000_000

# The call log is the meter's own table: spend per ipo_id survives restarts (crash-resume).
SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY,
  ipo_id TEXT NOT NULL, task TEXT, model TEXT NOT NULL,
  input_tokens INT, cached_tokens INT, output_tokens INT,
  cost_usd REAL NOT NULL,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS llm_calls_ipo ON llm_calls (ipo_id);
"""

log = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Raised before a call whose worst-case cost would push the IPO's spend over the cap."""


@lru_cache(maxsize=1)
def _encoding():
    """o200k_base, or None if it can't be loaded (tiktoken downloads it once into its cache)."""
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        log.warning("tiktoken o200k_base unavailable; using a conservative chars/2 token estimate")
        return None


def _prompt_tokens(system: str, user: str) -> int:
    enc = _encoding()
    if enc is None:
        # English averages ~4 chars/token; assuming 2 over-counts, which errs on the safe side of the cap.
        return (len(system) + len(user) + 1) // 2
    return len(enc.encode(system)) + len(enc.encode(user))


class BudgetMeter:
    """Tracks spend for one IPO against the cap, starting from what is already logged for it."""

    def __init__(self, ipo_id: str, config: Config):
        self.ipo_id = ipo_id
        self.cap = config.budget.cap_usd
        self._models = config.models
        self._db_path = config.db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
            (self.spent,) = db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE ipo_id = ?", (ipo_id,)
            ).fetchone()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection that commits on success and is always closed."""
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            yield conn

    def _model(self, model: str) -> ModelConfig:
        cfg = self._models.get(model)
        if cfg is None:
            raise ValueError(f"model {model!r} is not configured")
        return cfg

    def check(self, model: str, prompt_tokens: int, max_tokens: int) -> None:
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError(f"every call must set a positive max_tokens (got {max_tokens!r})")
        cfg = self._model(model)
        worst = (prompt_tokens * cfg.price_in + max_tokens * cfg.price_out) / PER_MILLION
        if self.spent + worst > self.cap:
            raise BudgetExceeded(
                f"{self.ipo_id}: spent ${self.spent:.4f} + worst case ${worst:.4f} would exceed ${self.cap:.2f}"
            )

    def record(self, *, task: str, model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        """Adds the actual cost of a finished call and logs it. Groq's zero prices log it at $0."""
        cfg = self._model(model)
        uncached = input_tokens - cached_tokens
        cost = (uncached * cfg.price_in + cached_tokens * cfg.price_cached + output_tokens * cfg.price_out) / PER_MILLION
        with self._db() as db:
            db.execute(
                """INSERT INTO llm_calls (ipo_id, task, model, input_tokens, cached_tokens, output_tokens, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.ipo_id, task, model, input_tokens, cached_tokens, output_tokens, cost),
            )
        self.spent += cost
        return cost


class MeteredLLM:
    """Wraps the provider LLM: checks the cap before each call and records spend after it."""

    def __init__(self, inner: LLM, meter: BudgetMeter):
        self._inner = inner
        self.meter = meter

    def invoke_structured(self, *, model, system, user, schema, max_tokens, task=""):
        self.meter.check(model, _prompt_tokens(system, user), max_tokens)
        try:
            result = self._inner.invoke_structured(
                model=model, system=system, user=user, schema=schema, max_tokens=max_tokens, task=task
            )
        except LLMParseError as e:
            # The provider billed for the unparseable reply: record it, then let the caller retry or fail.
            self.meter.record(task=task, model=model, input_tokens=e.input_tokens,
                              cached_tokens=e.cached_tokens, output_tokens=e.output_tokens)
            raise
        self.meter.record(task=task, model=model, input_tokens=result.input_tokens,
                          cached_tokens=result.cached_tokens, output_tokens=result.output_tokens)
        return result
