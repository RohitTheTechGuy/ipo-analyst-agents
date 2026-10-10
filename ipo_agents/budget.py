"""Per-IPO budget meter and the metered LLM every agent calls through (spec: Budget rules)."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

import tiktoken

from ipo_agents.config import Config, ModelConfig
from ipo_agents.llm import LLM, LLMParseError
from ipo_agents.router import FALLBACK, get_model, is_groq

PER_MILLION = 1_000_000
# Tokens the provider bills beyond the prompt text: message framing, tool/response-format wrapper.
REQUEST_OVERHEAD_TOKENS = 200
# Headroom for tokenizer drift between tiktoken and the provider's own count.
PROMPT_SAFETY = 1.1

# Transient OpenAI errors (429/5xx) are retried here, not by the client, so each attempt is metered.
MAX_RETRIES = 2
BACKOFF_S = 0.5  # waits 0.5s, then 1s

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


def _count(text: str) -> int:
    enc = _encoding()
    if enc is None:
        # English averages ~4 chars/token; assuming 2 over-counts, which errs on the safe side of the cap.
        return (len(text) + 1) // 2
    return len(enc.encode(text))


def _prompt_tokens(system: str, user: str, schema: type) -> int:
    """Billable input estimate: prompt text + the output schema sent as a tool definition + framing."""
    schema_json = json.dumps(schema.model_json_schema()) if hasattr(schema, "model_json_schema") else ""
    text = _count(system) + _count(user) + _count(schema_json)
    return int(text * PROMPT_SAFETY) + REQUEST_OVERHEAD_TOKENS


def _transient(e: Exception) -> bool:
    """A rate limit or server error (openai/groq APIStatusError carry status_code); worth retrying."""
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


class BudgetMeter:
    """Tracks spend for one IPO against the cap, starting from what is already logged for it."""

    def __init__(self, ipo_id: str, config: Config):
        self.ipo_id = ipo_id
        self.config = config
        self.cap = config.budget.cap_usd
        self._models = config.models
        self._db_path = config.db_path
        # Parallel graph branches share one meter: reservations stop them all passing the same check.
        self._lock = threading.Lock()
        self.reserved = 0.0
        # Pre-run cost estimate for this analysis; set by analyse_rhp before the graph runs.
        self.estimate_usd = 0.0
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            (self.spent,) = db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE ipo_id = ?", (ipo_id,)
            ).fetchone()

    @property
    def degraded(self) -> bool:
        """Spend has reached degrade_pct of the cap, or the pre-run estimate is over it:
        Terra tasks run on Luna and sentiment reads fewer sources."""
        threshold = self.cap * self.config.budget.degrade_pct / 100
        return self.spent >= threshold or self.estimate_usd > threshold

    def tokens_today(self, models: list[str]) -> int:
        """Tokens logged today (UTC) on these model keys, across all IPOs: Groq's limit is per day, not per IPO."""
        marks = ",".join("?" * len(models))
        with self._db() as db:
            (n,) = db.execute(
                f"""SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM llm_calls
                    WHERE model IN ({marks}) AND date(ts) = date('now')""",
                models,
            ).fetchone()
        return n

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection that commits on success and is always closed."""
        with closing(sqlite3.connect(self._db_path, timeout=30)) as conn, conn:
            yield conn

    def _model(self, model: str) -> ModelConfig:
        cfg = self._models.get(model)
        if cfg is None:
            raise ValueError(f"model {model!r} is not configured")
        return cfg

    def reserve(self, model: str, prompt_tokens: int, max_tokens: int) -> float:
        """Holds the call's worst-case cost against the cap; raises BudgetExceeded if it doesn't fit."""
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError(f"every call must set a positive max_tokens (got {max_tokens!r})")
        cfg = self._model(model)
        worst = (prompt_tokens * cfg.price_in + max_tokens * cfg.price_out) / PER_MILLION
        with self._lock:
            if self.spent + self.reserved + worst > self.cap:
                raise BudgetExceeded(
                    f"{self.ipo_id}: spent ${self.spent:.4f} + in flight ${self.reserved:.4f} "
                    f"+ worst case ${worst:.4f} would exceed ${self.cap:.2f}"
                )
            self.reserved += worst
        return worst

    def settle(self, reservation: float, *, task: str, model: str,
               input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        """Swaps a reservation for the call's actual cost and logs it. Groq's zero prices log it at $0."""
        cfg = self._model(model)
        uncached = max(0, input_tokens - cached_tokens)
        cost = (uncached * cfg.price_in + cached_tokens * cfg.price_cached + output_tokens * cfg.price_out) / PER_MILLION
        with self._db() as db:
            db.execute(
                """INSERT INTO llm_calls (ipo_id, task, model, input_tokens, cached_tokens, output_tokens, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.ipo_id, task, model, input_tokens, cached_tokens, output_tokens, cost),
            )
        with self._lock:
            self.reserved -= reservation
            self.spent += cost
        return cost


class MeteredLLM:
    """Wraps the provider LLM: checks the cap before each call and records spend after it."""

    def __init__(self, inner: LLM, meter: BudgetMeter, sleep: Callable[[float], None] | None = None):
        self._inner = inner
        self.meter = meter
        self._sleep = sleep or time.sleep  # looked up at construction so tests can stub it

    def invoke_structured(self, *, task, system, user, schema, max_tokens):
        """Callers name the task; the router picks the model. A failed Groq call is redone on Luna at once."""
        prompt_tokens = _prompt_tokens(system, user, schema)
        model = get_model(task, prompt_tokens + max_tokens, self.meter)
        call = dict(task=task, system=system, user=user, schema=schema, max_tokens=max_tokens)
        if is_groq(model, self.meter.config):
            try:
                return self._metered_call(model, prompt_tokens, **call)
            except Exception as e:
                # Rate limit (429) or any other Groq failure: nothing ever waits on Groq.
                log.warning("%s on groq failed (%s); falling back to %s", task, e, FALLBACK)
                model = FALLBACK
        for attempt in range(MAX_RETRIES + 1):
            try:
                return self._metered_call(model, prompt_tokens, **call)
            except Exception as e:
                if attempt == MAX_RETRIES or not _transient(e):
                    raise
                log.warning("%s on %s failed (%s); retry %d", task, model, e, attempt + 1)
                self._sleep(BACKOFF_S * 2**attempt)

    def _metered_call(self, model, prompt_tokens, *, task, system, user, schema, max_tokens):
        """One provider call: reserve its worst case, call, settle what it actually cost."""
        held = self.meter.reserve(model, prompt_tokens, max_tokens)
        try:
            result = self._inner.invoke_structured(
                model=model, system=system, user=user, schema=schema, max_tokens=max_tokens, task=task
            )
        except LLMParseError as e:
            # The provider billed for the unparseable reply: record it, then let the caller retry or fail.
            self.meter.settle(held, task=task, model=model, input_tokens=e.input_tokens,
                              cached_tokens=e.cached_tokens, output_tokens=e.output_tokens)
            raise
        except Exception:
            # Usage is unknown (timeout, dropped connection after the request went out), so charge the
            # worst case: the cap must hold even if the provider billed for it.
            self.meter.settle(held, task=task, model=model, input_tokens=prompt_tokens,
                              cached_tokens=0, output_tokens=max_tokens)
            raise
        self.meter.settle(held, task=task, model=model, input_tokens=result.input_tokens,
                          cached_tokens=result.cached_tokens, output_tokens=result.output_tokens)
        return result
