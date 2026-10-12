"""Offline regression run of the summary evaluation (run by hand, not in pytest). See evals/eval.md.

The daily run already evaluates every new summary before sending it (plain_summary.evaluated_summary:
editor draft -> rulebook + Sol judge -> one revision with the feedback -> send the best faithful draft).
This script runs that same loop on saved reports and fixtures, so a prompt or model change can be checked
before it reaches Telegram. Writes evals/results/<date>_<time>.md.

    .venv/bin/python -m evals.summary_eval            # editor + judge, up to 2 rounds, ~$0.03-0.06 per case, ledger "eval-summary"
    .venv/bin/python -m evals.summary_eval --offline  # the rulebook on today's full summaries only; no model

Pass: no rulebook problem, every score >= 4, faithfulness 5.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ipo_agents.budget import BudgetMeter, MeteredLLM
from ipo_agents.config import load_config
from ipo_agents.contracts import Report
from ipo_agents.plain_summary import evaluated_summary, problems
from ipo_agents.summary_judge import SCORES, passed
from ipo_agents.runner import format_summary

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "evals" / "results"
LEDGER = "eval-summary"
SAVED = {  # real reports from live runs
    "FusionCX": REPO / "reports/FUSIONCX-2026-10-14/2026-10-14_193612/report.json",
    "HD Fire": REPO / "reports/HDFIRE-2026-10-13/2026-10-11_1644_run4/report.json",
}


def cases() -> dict[str, Report]:
    from tests.report_fixture import full_report  # synthetic cases; tests/ is importable from the repo root

    out = {name: Report.model_validate_json(p.read_text(encoding="utf-8")) for name, p in SAVED.items() if p.exists()}
    out["Fixture: BUY"] = full_report()
    out["Fixture: no advisor call"] = full_report().model_copy(update={"advice": None})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="rulebook on the full summaries only; no model calls")
    ap.add_argument("--cap", type=float, default=1.00, help="USD cap on the eval-summary ledger (cumulative)")
    args = ap.parse_args(argv)

    config = load_config(REPO / "config.yaml")
    llm = None
    if not args.offline:
        load_dotenv(REPO / ".env")
        from ipo_agents.llm import LangChainLLM

        llm = MeteredLLM(LangChainLLM(config.models), BudgetMeter(LEDGER, config, cap=args.cap))
        spent_before = llm.meter.spent

    rows, sections = [], []
    for name, report in cases().items():
        full = format_summary(report)
        if args.offline:
            text, scores, rounds = full, None, 0
        else:
            sent, drafts = evaluated_summary(report, full, llm)
            text = sent or full
            chosen = next((d for d in drafts if d.text == sent), None)
            scores, rounds = (chosen.scores if chosen else None), len(drafts)
        rules = problems(text, full, report)
        ok = passed(rules, scores)
        score_cells = [str(getattr(scores, k).score) for k in SCORES] if scores else ["–"] * len(SCORES)
        sent_kind = "full (no faithful draft)" if not args.offline and text == full else ("full" if args.offline else "plain")
        rows.append(f"| {name} | {'✅' if ok else '❌'} | {sent_kind} | {rounds} | {len([l for l in text.splitlines() if l.strip()])} | "
                    + " | ".join(score_cells) + f" | {len(rules)} |")
        detail = [f"## {name}", "", "**Rulebook:** " + ("; ".join(rules) or "clean"), ""]
        if scores:
            detail += [f"- **{k}** {getattr(scores, k).score}: {getattr(scores, k).reason}" for k in SCORES]
            for label, items in (("Leftover jargon", scores.leftover_jargon),
                                 ("Missing decisive points", scores.missing_decisive_points),
                                 ("Filler", scores.filler)):
                if items:
                    detail.append(f"- **{label}:** " + "; ".join(items))
            detail.append("")
        detail += ["Message sent:", "```", text, "```", ""]
        sections.append("\n".join(detail))

    header = ["| Case | Pass | Sent | Drafts | Lines | " + " | ".join(SCORES) + " | Rule problems |",
              "|---|---|---|---|---|" + "---|" * len(SCORES) + "---|"]
    table = "\n".join(header + rows)
    cost = "" if args.offline else f"\n\nSpent this run: ${llm.meter.spent - spent_before:.2f} (ledger {LEDGER})"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y-%m-%d_%H%M}{'_offline' if args.offline else ''}.md"
    out.write_text(f"# Telegram summary eval\n\n{table}{cost}\n\n" + "\n".join(sections), encoding="utf-8")
    print(table + cost)
    print(f"\nDetails: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
