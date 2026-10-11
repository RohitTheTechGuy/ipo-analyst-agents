"""Operator commands outside the daily run: a pre-flight smoke test of the real model, and a dev reset of
one IPO's spend (run_daily.py --smoke, run_daily.py --ipo X --reset-budget)."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from ipo_agents.analysis import CHECKPOINT_DB
from ipo_agents.budget import BudgetMeter, MeteredLLM, _status
from ipo_agents.config import Config
from ipo_agents.contracts import Synthesis, ValuationPlan
from ipo_agents.llm import LLM, LLMParseError
from ipo_agents.router import FALLBACK
from ipo_agents.sections import SECTIONS
from ipo_agents.sentiment import SentimentPoints, StanceTag

# --- smoke --------------------------------------------------------------------------------------

SMOKE_TASK = "smoke"
SMOKE_MODEL = FALLBACK  # Luna: the cheap model, and the one every other model falls back to
# Small, but with room for low-effort reasoning tokens (billed as output and counted against this),
# so a cut-off reply means something is wrong rather than that the test was too tight.
SMOKE_MAX_TOKENS = 500
SMOKE_SYSTEM = ("This is a connectivity test. Return the smallest valid answer for the schema: empty lists, "
                "null or not-disclosed values, empty strings, and the most neutral choice where one is required.")
SMOKE_USER = "[page 1]\nThis page is intentionally blank."


def smoke_schemas() -> list[type]:
    """Every schema the pipeline sends to a model."""
    return [*(spec.schema for spec in SECTIONS.values()), StanceTag, SentimentPoints, ValuationPlan, Synthesis]


def _diagnose(e: Exception) -> str:
    """One line on what a failed smoke call means for a live run."""
    status, text = _status(e), str(e)
    lowered = text.lower()
    if status == 404 or ("model" in lowered and ("does not exist" in lowered or "not found" in lowered)):
        return f"MODEL ID: the provider does not know this model ({text[:200]})"
    if status == 400 and ("schema" in lowered or "response_format" in lowered):
        return f"SCHEMA REJECTED: strict structured output refused the schema ({text[:200]})"
    if "no token usage" in lowered:
        return "NO USAGE: the reply carried no token usage, so the meter can't cost it"
    if isinstance(e, LLMParseError):
        return f"UNPARSED: the reply did not fit the schema ({text[:200]})"
    return f"ERROR {type(e).__name__}: {text[:200]}"


def smoke(config: Config, llm: LLM, today: date, out: Callable[[str], None] = print) -> bool:
    """One tiny call per schema on Luna, metered under ipo_id SMOKE-<date>. True if every call was OK."""
    config = config.model_copy(update={"routing": {**config.routing, SMOKE_TASK: SMOKE_MODEL}})
    meter = BudgetMeter(f"SMOKE-{today:%Y-%m-%d}", config)
    metered = MeteredLLM(llm, meter)
    ok = 0
    schemas = smoke_schemas()
    out(f"Smoke test: {len(schemas)} schemas on {SMOKE_MODEL} ({config.models[SMOKE_MODEL].id}), "
        f"max_tokens {SMOKE_MAX_TOKENS}, metered as {meter.ipo_id}")
    for schema in schemas:
        try:
            result = metered.invoke_structured(task=SMOKE_TASK, system=SMOKE_SYSTEM, user=SMOKE_USER,
                                               schema=schema, max_tokens=SMOKE_MAX_TOKENS)
        except Exception as e:
            out(f"  {schema.__name__:<22} {_diagnose(e)}")
            continue
        if not (result.input_tokens and result.output_tokens):
            out(f"  {schema.__name__:<22} NO USAGE: reply reported {result.input_tokens} in / "
                f"{result.output_tokens} out tokens")
            continue
        ok += 1
        out(f"  {schema.__name__:<22} OK ({result.input_tokens} in / {result.output_tokens} out)")
    out(f"{ok}/{len(schemas)} OK; ${meter.spent:.4f} spent on {meter.ipo_id} today")
    return ok == len(schemas)


# --- dev reset ----------------------------------------------------------------------------------

def _checkpoint_threads(path: Path, ipo_id: str) -> list[str]:
    """Checkpoint thread ids of this IPO (one per RHP: '<ipo_id>:<sha256>')."""
    if not path.exists():
        return []
    prefix = f"{ipo_id}:"
    with closing(sqlite3.connect(path)) as conn:
        try:
            rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints WHERE substr(thread_id, 1, ?) = ?",
                                (len(prefix), prefix)).fetchall()
        except sqlite3.OperationalError:  # no checkpoints table yet
            return []
    return [tid for (tid,) in rows]


def reset_budget(ipo_id: str, config: Config, ask: Callable[[str], str] = input,
                 out: Callable[[str], None] = print) -> bool:
    """Shows the IPO's spend and saved checkpoints, and after the typed word 'reset' deletes its llm_calls
    rows and checkpoint threads. Dev use only: it lifts the per-IPO cap that otherwise never resets."""
    meter = BudgetMeter(ipo_id, config)
    with closing(sqlite3.connect(config.db_path)) as db:
        (calls,) = db.execute("SELECT COUNT(*) FROM llm_calls WHERE ipo_id = ?", (ipo_id,)).fetchone()
    checkpoints = Path(config.db_path).with_name(CHECKPOINT_DB)
    threads = _checkpoint_threads(checkpoints, ipo_id)
    out(f"{ipo_id}: ${meter.spent:.4f} spent of ${config.budget.cap_usd:.2f} in {calls} logged call(s); "
        f"{len(threads)} saved checkpoint thread(s)")
    if ask("Type 'reset' to delete this IPO's spend log and checkpoints: ").strip() != "reset":
        out("Not reset.")
        return False
    with closing(sqlite3.connect(config.db_path)) as db, db:
        db.execute("DELETE FROM llm_calls WHERE ipo_id = ?", (ipo_id,))
    if threads:
        with closing(sqlite3.connect(checkpoints, check_same_thread=False)) as conn:
            saver = SqliteSaver(conn)
            for thread_id in threads:
                saver.delete_thread(thread_id)
    out(f"Reset {ipo_id}: deleted {calls} call(s) and {len(threads)} checkpoint thread(s). "
        f"Its status is unchanged: re-run it with --ipo {ipo_id} --force")
    return True
