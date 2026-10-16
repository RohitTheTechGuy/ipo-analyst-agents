"""Side-by-side Financial Advisor run on saved reports: Sol (the routed advisor) vs Grok (xAI), on the same
digest and day (run by hand, not in pytest). It changes no routing; it shows whether a model from another
company reaches the same BUY / NO BUY call before anyone decides to switch. Writes
evals/results/<date>_<time>_advisor_compare.md.

    .venv/bin/python -m evals.advisor_compare          # 2 calls per saved report, ~$0.10-0.20 each, ledger "eval-advisor"
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv

from evals.summary_eval import REPO, RESULTS, SAVED
from ipo_agents.advisor import TASK, advise
from ipo_agents.budget import BudgetMeter, MeteredLLM
from ipo_agents.config import load_config
from ipo_agents.contracts import CheckedAdvice, Report

LEDGER = "eval-advisor"
MODELS = ("sol", "grok")


def _section(advice: CheckedAdvice | None) -> list[str]:
    if advice is None:
        return ["- no advice (failed, or fewer than 2 reasons survived the checks; see logs/ipo-agents.log)"]
    out = [f"- **Call:** {advice.call.upper()} ({advice.conviction} conviction, horizon {advice.horizon})",
           f"- **Headline:** {advice.headline or '(dropped)'}", "- **Reasons:**"]
    out += [f"  - {r.text} — {', '.join(r.evidence)}" for r in advice.reasons]
    out += ["- **Key risks:**", *(f"  - {r.text} — {', '.join(r.evidence)}" for r in advice.key_risks)]
    out += [f"- **What would change its mind:** {advice.what_would_change_my_mind or '(dropped)'}",
            f"- **Dropped or downgraded by the checks:** {'; '.join(advice.notes) or 'nothing'}"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cap", type=float, default=1.00, help="USD cap on the eval-advisor ledger (cumulative)")
    args = ap.parse_args(argv)

    load_dotenv(REPO / ".env")
    from ipo_agents.llm import LangChainLLM

    config = load_config(REPO / "config.yaml")
    meter = BudgetMeter(LEDGER, config, cap=args.cap)
    spent_before = meter.spent
    rows, sections = [], []
    for name, path in SAVED.items():
        if not path.exists():
            continue
        report = Report.model_validate_json(path.read_text(encoding="utf-8"))
        today = report.advice.as_of if report.advice else report.ipo.open_date  # the day it was advised
        results = {}
        for model in MODELS:
            routed = config.model_copy(update={"routing": {**config.routing, TASK: model}})
            llm = MeteredLLM(LangChainLLM(routed.models), BudgetMeter(LEDGER, routed, cap=args.cap))
            results[model] = advise(report, report.sentiment, llm, routed, today)
        calls = {m: (a.call if a else None) for m, a in results.items()}
        agree = "yes" if None not in calls.values() and len(set(calls.values())) == 1 else "no"
        saved = report.advice.call.upper() if report.advice else "–"
        rows.append(f"| {name} | {saved} | " + " | ".join(
            f"{a.call.upper()} ({a.conviction})" if a else "none" for a in results.values()) + f" | {agree} |")
        detail = [f"## {name} (advised as of {today})", ""]
        for model, advice in results.items():
            detail += [f"### {model} ({config.models[model].id})", *_section(advice), ""]
        sections.append("\n".join(detail))

    header = ["| IPO | Saved call (Sol, live run) | " + " | ".join(f"{m} now" for m in MODELS) + " | Agree |",
              "|---|---|" + "---|" * len(MODELS) + "---|"]
    table = "\n".join(header + rows)
    cost = f"\n\nSpent this run: ${BudgetMeter(LEDGER, config, cap=args.cap).spent - spent_before:.2f} (ledger {LEDGER})"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y-%m-%d_%H%M}_advisor_compare.md"
    out.write_text(f"# Financial Advisor: Sol vs Grok\n\n{table}{cost}\n\n" + "\n".join(sections), encoding="utf-8")
    print(table + cost)
    print(f"\nDetails: {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
