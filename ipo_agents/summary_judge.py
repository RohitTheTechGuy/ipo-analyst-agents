"""The summary judge: scores a plain-English Telegram message against the full summary it came from, for a
reader with no finance background. A different model from the editor (routing `summary_judge`, Sol), so the
editor never grades its own work. Used in the daily run before the summary is sent (plain_summary.
evaluated_summary) and by the offline eval (evals/summary_eval.py). See evals/eval.md.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

TASK = "summary_judge"
MAX_TOKENS = 16_000
SCORES = ("readability_non_finance", "decision_relevance", "coverage", "faithfulness", "no_repetition")
PASS_SCORE = 4  # every score at least this...
FAITHFUL = 5    # ...and faithfulness exactly this: a wrong message is worse than a hard one


class Score(BaseModel):
    score: int = Field(description="1 (bad) to 5 (excellent)")
    reason: str = Field(description="One sentence")


class SummaryScores(BaseModel):
    """The judge's answer. LLM-facing (strict mode): no defaults."""

    readability_non_finance: Score = Field(description="Would someone who has never bought a share understand every line?")
    decision_relevance: Score = Field(description="Is every line something that moves apply / don't apply? 5 = no filler")
    coverage: Score = Field(description="Does it have the decisive reasons, the biggest risk and the listing signal "
                                        "(if the full summary has one)?")
    faithfulness: Score = Field(description="5 = nothing contradicts, overstates or adds to the full summary")
    no_repetition: Score = Field(description="5 = no point is made twice")
    leftover_jargon: list[str] = Field(description="Words a non-finance reader would not know; empty if none")
    missing_decisive_points: list[str] = Field(description="Points in the full summary that would change the "
                                                           "decision but are missing; empty if none")
    filler: list[str] = Field(description="Lines that don't affect the decision; empty if none")

    def total(self) -> int:
        return sum(getattr(self, k).score for k in SCORES)


SYSTEM = """You review a short IPO message for an Indian retail reader with NO finance background. They
know everyday words (IPO, share, ₹, lakh, crore, revenue/sales, profit, loan, company owner) and basic market
words from the news (listed company, stock market, investors, subscribed x times, retail investors, auditor,
board, unofficial trading before listing); these are fine and are NOT jargon. They don't know accounting or
finance jargon (P/E, EBITDA, OFS, promoter, WACA, QIB, audit trail, listing gain, grey market, upper band,
goodwill, capital spending, working capital, board independence, related party...).
You get the FULL summary (the source of truth, written for experts) and the PLAIN message (the candidate).

Some lines are written by code from the report and are correct by construction; do not score or quote them:
the first lines (name, dates, price band, "Smallest application" = lot x upper price), the line starting
"🧭" (the call), the line starting "📊" (the peer multiples, kept as is on purpose for readers who want
them) and the last line (the PDF pointer and disclaimer).

Score the rest of the PLAIN message strictly, 1-5 per question:
5 excellent, 4 good with a nit, 3 noticeable problem, 2 serious problem, 1 fails.
- readability_non_finance: every word understandable to the reader above; a jargon word explained in plain
  words on the same line is fine. Do not mark down or list basic market words.
- decision_relevance: only what changes whether to apply; anything else is filler.
- coverage: the decisive reasons (including how the price compares with similar companies, when the FULL
  summary has that), the biggest risk, and the listing signal if the source has one. Not every detail.
- faithfulness: compared with FULL; any contradiction, overstatement or new claim scores at most 2. A plain
  rewording that keeps the meaning is faithful.
- no_repetition: the same point twice (in other words too) lowers it. An opening sentence that names the
  single biggest reason, which a bullet then explains in detail, is a summary, not a repeat.
Quote the exact words in leftover_jargon and filler."""


def judge(llm, full: str, plain: str) -> SummaryScores:
    user = f"FULL summary:\n{full}\n\nPLAIN message:\n{plain}"
    return llm.invoke_structured(task=TASK, system=SYSTEM, user=user, schema=SummaryScores,
                                 max_tokens=MAX_TOKENS).parsed


def passed(rules: list[str], s: SummaryScores | None) -> bool:
    """The pass bar: no rulebook problem, every score >= PASS_SCORE, faithfulness == FAITHFUL."""
    if rules or s is None:
        return False
    return all(getattr(s, k).score >= PASS_SCORE for k in SCORES) and s.faithfulness.score == FAITHFUL


def feedback(rules: list[str], s: SummaryScores | None) -> str:
    """What the editor must fix in its next draft: the rulebook's problems and the judge's low scores and lists."""
    lines = [f"- Rule broken: {r}" for r in rules]
    if s is not None:
        lines += [f"- {k} {getattr(s, k).score}/5: {getattr(s, k).reason}" for k in SCORES
                  if getattr(s, k).score < (FAITHFUL if k == "faithfulness" else 5)]
        for label, items in (("Explain or replace these words", s.leftover_jargon),
                             ("Add these decisive points", s.missing_decisive_points),
                             ("Drop these filler lines", s.filler)):
            if items:
                lines.append(f"- {label}: " + "; ".join(items))
    return "\n".join(lines)
