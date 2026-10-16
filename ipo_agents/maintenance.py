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
from ipo_agents.advisor import TASK as ADVISOR_TASK
from ipo_agents.advisor import ledger_id
from ipo_agents.contracts import Advice, DemandCheck, Synthesis, ValuationPlan
from ipo_agents.demand_check import TASK as DEMAND_TASK
from ipo_agents.llm import LLM, LLMParseError
from ipo_agents.router import FALLBACK
from ipo_agents.sections import SECTIONS
from ipo_agents.sentiment import SentimentPoints, StanceTag
from ipo_agents.summary_judge import TASK as JUDGE_TASK
from ipo_agents.summary_judge import SummaryScores

# --- smoke --------------------------------------------------------------------------------------

SMOKE_TASK = "smoke"
SMOKE_MODEL = FALLBACK  # Luna: the cheap model, and the one every other model falls back to
# Small, but with room for low-effort reasoning tokens (billed as output and counted against this),
# so a cut-off reply means something is wrong rather than that the test was too tight.
SMOKE_MAX_TOKENS = 500
# One call on the advisor's model (sol, reasoning effort high): room for its reasoning tokens. Worst case
# ~600 in x $2/1M + 1,500 out x $10/1M = ~$0.016; a typical reply costs about $0.01.
SMOKE_ADVISOR_TASK = "smoke_advisor"
SMOKE_ADVISOR_MAX_TOKENS = 1_500
# And one on the summary judge's model (another provider: grok), to check its model ID and strict schema.
SMOKE_JUDGE_TASK = "smoke_judge"
SMOKE_JUDGE_MAX_TOKENS = 1_500
# And one DemandCheck on the closing-day demand check's model, so a schema it rejects shows before a closing day.
SMOKE_DEMAND_TASK = "smoke_demand"
SMOKE_SYSTEM = ("This is a connectivity test. Return the smallest valid answer for the schema: empty lists, "
                "null or not-disclosed values, empty strings, and the most neutral choice where one is required.")
SMOKE_USER = "[page 1]\nThis page is intentionally blank."


def smoke_schemas() -> list[type]:
    """Every schema the pipeline sends to a model."""
    return [*(spec.schema for spec in SECTIONS.values()), StanceTag, SentimentPoints, ValuationPlan, Synthesis, Advice]


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
    """One tiny call per schema on Luna, plus one Advice call on the advisor's model (sol) and one
    SummaryScores call on the summary judge's model (grok) and one DemandCheck call on the demand check's
    model (grok) to check their model IDs and the strict schema there; all metered under ipo_id
    SMOKE-<date>. True if every call was OK."""
    advisor_model, judge_model = config.routing[ADVISOR_TASK], config.routing[JUDGE_TASK]
    demand_model = config.routing[DEMAND_TASK]
    config = config.model_copy(update={"routing": {**config.routing, SMOKE_TASK: SMOKE_MODEL,
                                                   SMOKE_ADVISOR_TASK: advisor_model,
                                                   SMOKE_JUDGE_TASK: judge_model,
                                                   SMOKE_DEMAND_TASK: demand_model}})
    meter = BudgetMeter(f"SMOKE-{today:%Y-%m-%d}", config)
    metered = MeteredLLM(llm, meter)
    schemas = smoke_schemas()
    calls = [(SMOKE_TASK, schema, schema.__name__, SMOKE_MAX_TOKENS) for schema in schemas]
    calls.append((SMOKE_ADVISOR_TASK, Advice, f"Advice on {advisor_model}", SMOKE_ADVISOR_MAX_TOKENS))
    calls.append((SMOKE_JUDGE_TASK, SummaryScores, f"Judge on {judge_model}", SMOKE_JUDGE_MAX_TOKENS))
    calls.append((SMOKE_DEMAND_TASK, DemandCheck, f"Demand on {demand_model}", SMOKE_JUDGE_MAX_TOKENS))
    out(f"Smoke test: {len(schemas)} schemas on {SMOKE_MODEL} ({config.models[SMOKE_MODEL].id}), "
        f"max_tokens {SMOKE_MAX_TOKENS}, Advice on {advisor_model} ({config.models[advisor_model].id}) and "
        f"SummaryScores on {judge_model} ({config.models[judge_model].id}) and DemandCheck on {demand_model}, max_tokens "
        f"{SMOKE_ADVISOR_MAX_TOKENS}; metered as {meter.ipo_id}")
    ok = 0
    for task, schema, name, max_tokens in calls:
        try:
            result = metered.invoke_structured(task=task, system=SMOKE_SYSTEM, user=SMOKE_USER,
                                               schema=schema, max_tokens=max_tokens)
        except Exception as e:
            out(f"  {name:<22} {_diagnose(e)}")
            continue
        if not (result.input_tokens and result.output_tokens):
            out(f"  {name:<22} NO USAGE: reply reported {result.input_tokens} in / "
                f"{result.output_tokens} out tokens")
            continue
        ok += 1
        out(f"  {name:<22} OK ({result.input_tokens} in / {result.output_tokens} out)")
    out(f"{ok}/{len(calls)} OK; ${meter.spent:.4f} spent on {meter.ipo_id} today")
    return ok == len(calls)


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
    """Shows the IPO's spend on both its ledgers (analysis and advisor) and its saved checkpoints, and after
    the typed word 'reset' deletes their llm_calls rows and the checkpoint threads. Dev use only: it lifts
    the per-IPO caps that otherwise never reset."""
    ledgers = (ipo_id, ledger_id(ipo_id))
    analysis = BudgetMeter(ipo_id, config)
    advisor = BudgetMeter(ledger_id(ipo_id), config, cap=config.advisor.cap_usd)
    with closing(sqlite3.connect(config.db_path)) as db:
        (calls,) = db.execute("SELECT COUNT(*) FROM llm_calls WHERE ipo_id IN (?, ?)", ledgers).fetchone()
    checkpoints = Path(config.db_path).with_name(CHECKPOINT_DB)
    threads = _checkpoint_threads(checkpoints, ipo_id)
    out(f"{ipo_id}: ${analysis.spent:.4f} spent of ${config.budget.cap_usd:.2f}, advisor ${advisor.spent:.4f} "
        f"of ${config.advisor.cap_usd:.2f}, in {calls} logged call(s); {len(threads)} saved checkpoint thread(s)")
    if ask("Type 'reset' to delete this IPO's spend log and checkpoints: ").strip() != "reset":
        out("Not reset.")
        return False
    with closing(sqlite3.connect(config.db_path)) as db, db:
        db.execute("DELETE FROM llm_calls WHERE ipo_id IN (?, ?)", ledgers)
    if threads:
        with closing(sqlite3.connect(checkpoints, check_same_thread=False)) as conn:
            saver = SqliteSaver(conn)
            for thread_id in threads:
                saver.delete_thread(thread_id)
    out(f"Reset {ipo_id}: deleted {calls} call(s) and {len(threads)} checkpoint thread(s). "
        f"Its status is unchanged: re-run it with --ipo {ipo_id} --force")
    return True
