"""The Telegram summary in plain English: the summary editor rewrites the full summary for a reader with no
finance background, keeping only what decides buy or not (the full detail stays in the PDF), and the summary
judge evaluates each draft before anything is sent (`evaluated_summary`; see evals/eval.md).

The model never writes the call or the header (Python does, from the report), and every number it writes
must be in the full summary it was given; an item that breaks a rule is dropped. Only a draft the judge
rates fully faithful is sent; otherwise, or when the calls fail or hit the advisor ledger's cap, the caller
sends the full summary. Either way one summary is sent.

`problems()` is the rulebook: deterministic checks on any finished text, run next to the judge.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ipo_agents.calc import upper_band
from ipo_agents.contracts import PlainSummary, Report
from ipo_agents.grounding import numbers, unsupported
from ipo_agents.report.labels import CALL_LABELS, VERDICT_LABELS
from ipo_agents.summary_judge import FAITHFUL, SummaryScores, feedback, judge, passed
from ipo_agents.valuation import summary_line as valuation_line

log = logging.getLogger(__name__)

TASK = "summary_editor"
MAX_TOKENS = 8_000
MAX_REASONS = 3
MAX_WATCH_OUTS = 2
MAX_LINES = 15
MAX_ROUNDS = 2  # a first draft and, if the judge fails it, one revision with its feedback
MAX_LINE_WORDS = 30  # a bullet longer than this is not a quick read
PEER_MARK = "📊"  # the peer multiples line, copied as is from the full summary
PLAIN_HORIZONS = {"listing_gains": "to sell on the first trading day", "long_term": "to hold for years"}
FOOTER = "Full analysis in the attached PDF. Not investment advice."

FLAG_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
# Words a reader with no finance background does not know. Case-sensitive, so "pat" in "path" never matches.
JARGON = re.compile(r"\b(?:WACA|OFS|EBITDA|EV|P/E|P/B|P/S|PAT|QIBs?|NIIs?|HNIs?|RHP|DRHP|CAGR|RoE|RoNW|ROCE|"
                    r"GMP|EPS|NAV|Regulation \d|bps|YoY|[Pp]romoters?|[Uu]pper band|[Ll]isting gains?)\b|×")

SYSTEM = f"""You rewrite an IPO summary for an Indian retail reader who has never bought a share. They know
everyday words (IPO, share, ₹, lakh, crore, sales, profit, loan) but no finance jargon. They want to know one
thing: should I apply, and why. Use only what the summary says.

Rules:
- Keep only points that change the decision to apply or not; drop everything else (it is in the PDF).
- The comparison with similar listed companies (P/E and other multiples) is shown separately, exactly as in
  the summary. Do not repeat its figures; one plain reason about what it means (cheap, expensive, mixed) is
  fine when it matters to the decision.
- Never repeat a point: one_line_why gives the overall balance in a few words; the reasons give the detail
  and must not restate it. Merge points that say the same thing.
- Plain words only. Never write WACA, OFS, EBITDA, EV, P/E, PAT, QIB, RHP, GMP, CAGR, RoE, EPS, "promoter",
  "audit trail", "statutory", "listing gain", "upper band" or a code like OFFER_PRICE_FAR_ABOVE_WACA.
  Say what it means instead, for example:
  "insiders paid ₹72.2 a share on average; you would pay up to ₹289",
  "valued higher than similar listed companies for the profit it makes",
  "the owners are selling their own shares, so the company gets none of the money",
  "the company's owners and their firms" (not "promoters"),
  "people trading the shares unofficially before listing expect them to open about 7% above the price" (not
  "grey market" / "listing gain").
- Keep each fact's exact meaning while making it plain: who did what (a firm linked to the owners is not
  the owners themselves), what is pending vs done, what could not be checked. Plainer, never broader.
- Explain any market phrase in the same line, e.g. "people who apply" not "retail bids", "cash the business
  actually brought in" not "cash from operations".
- Write "x times" instead of "×". Keep figures exactly as written; never turn an average into an exact price.
- Short sentences, under 25 words each.
- Numbers: only ones written in the summary, exactly as written there. Round nothing, compute nothing.
- At most {MAX_REASONS} reasons and {MAX_WATCH_OUTS} watch-outs, most important first. The call is shown
  separately; do not restate it.
- listing_signal: what unofficial pre-listing trading suggests for the first day and how much demand there is
  so far; empty if the summary has neither."""


def plain_summary(report: Report, full_text: str, llm, revise: tuple[str, str] | None = None) -> str | None:
    """One plain-English draft, or None when the editor can't make one. `revise` is (previous draft, what to
    fix): the judge's feedback on it."""
    ipo_id = report.ipo.ipo_id
    if _call_line(report) is None:
        return None  # no call and no analyst view: nothing to explain plainly
    user = f"Summary:\n{full_text}"
    if revise is not None:
        user += (f"\n\nYour previous draft:\n{revise[0]}\n\nA reviewer found these problems; fix them all "
                 f"and keep what was good:\n{revise[1]}")
    try:
        result = llm.invoke_structured(task=TASK, system=SYSTEM, user=user, schema=PlainSummary,
                                       max_tokens=MAX_TOKENS)
    except Exception as exc:  # BudgetExceeded included: the full summary still goes out
        log.warning("%s: plain summary not made: %s", ipo_id, exc)
        return None
    kept = check(result.parsed, full_text, ipo_id)
    return render(report, kept) if kept else None


@dataclass
class Draft:
    text: str
    rules: list[str]               # the rulebook's problems; empty = clean
    scores: SummaryScores | None   # None: the judge could not score it

    @property
    def passed(self) -> bool:
        return passed(self.rules, self.scores)

    def as_dict(self) -> dict:
        return {"text": self.text, "rules": self.rules, "passed": self.passed,
                "scores": self.scores.model_dump() if self.scores else None}


def evaluated_summary(report: Report, full_text: str, llm, rounds: int = MAX_ROUNDS) -> tuple[str | None, list[Draft]]:
    """Draft, evaluate (rulebook + judge), revise with the feedback until a draft passes or `rounds` run out.
    Returns (the message to send, every draft): the first draft that passes, else the best one the judge
    rated fully faithful (fewest rule problems, then highest scores). None (send the full summary) when
    no draft is fully faithful, or the judge never scored one."""
    ipo_id = report.ipo.ipo_id
    drafts: list[Draft] = []
    revise = None
    for n in range(rounds):
        text = plain_summary(report, full_text, llm, revise)
        if text is None:
            break
        try:
            scores = judge(llm, full_text, text)
        except Exception as exc:  # BudgetExceeded included
            log.warning("%s: summary judge failed on draft %d: %s", ipo_id, n + 1, exc)
            scores = None
        draft = Draft(text, problems(text, full_text, report), scores)
        drafts.append(draft)
        log.info("%s: summary draft %d %s; scores %s; rules %s", ipo_id, n + 1, "passed" if draft.passed else "failed",
                 {k: v["score"] for k, v in scores.model_dump().items() if isinstance(v, dict)} if scores else None,
                 draft.rules or "clean")
        if draft.passed:
            return text, drafts
        if scores is None:
            break  # no feedback to revise with, and nothing to vouch for the draft
        revise = (text, feedback(draft.rules, scores))
    faithful = [d for d in drafts if d.scores is not None and d.scores.faithfulness.score == FAITHFUL]
    if not faithful:
        log.warning("%s: no plain draft was rated fully faithful; sending the full summary", ipo_id)
        return None, drafts
    best = min(faithful, key=lambda d: (len(d.rules), -d.scores.total()))
    return best.text, drafts


def check(s: PlainSummary, full_text: str, ipo_id: str = "") -> PlainSummary | None:
    """`s` with every item that breaks a rule dropped; None if no why or no reason survives."""
    allowed = numbers(full_text)

    def ok(text: str, what: str) -> bool:
        text = " ".join(text.split())
        if not text:
            return False
        if bad := unsupported(text, allowed):
            log.warning("plain summary %s: dropped a %s: %s not in the full summary", ipo_id, what, ", ".join(bad))
            return False
        if FLAG_CODE.search(text):
            log.warning("plain summary %s: dropped a %s: it has a flag code", ipo_id, what)
            return False
        return True

    why = s.one_line_why if ok(s.one_line_why, "why") else ""
    reasons = [r for r in s.reasons if ok(r, "reason")][:MAX_REASONS]
    watch = [w for w in s.watch_outs if ok(w, "watch-out")][:MAX_WATCH_OUTS]
    signal = s.listing_signal if ok(s.listing_signal, "listing signal") else ""
    if not why or not reasons:
        log.warning("plain summary %s: too little survived the checks; sending the full summary", ipo_id)
        return None
    return PlainSummary(one_line_why=why, reasons=reasons, watch_outs=watch, listing_signal=signal)


def _call_line(report: Report) -> str | None:
    a = report.advice
    if a is not None:
        horizon = f", {PLAIN_HORIZONS[a.horizon]}" if a.horizon in PLAIN_HORIZONS else ""
        return f"🧭 Our call: {CALL_LABELS[a.call]} ({a.conviction} confidence{horizon})"
    if report.synthesis is not None:
        return f"🧭 Analyst view: {VERDICT_LABELS[report.synthesis.verdict]} ({report.synthesis.confidence} confidence)"
    return None


def _header(report: Report) -> list[str]:
    ipo = report.ipo
    band_text = ipo.price_band if ipo.price_band.startswith("₹") else f"₹{ipo.price_band}"
    lines = [f"📄 {ipo.name} IPO", f"Apply {ipo.open_date:%-d %b}–{ipo.close_date:%-d %b} · {band_text} a share"]
    band = upper_band(ipo.price_band)
    if band and ipo.lot_size:
        lines.append(f"Smallest application: {ipo.lot_size} shares = ₹{band * ipo.lot_size:,.0f}")
    return lines


def render(report: Report, s: PlainSummary) -> str:
    lines = [*_header(report), "", _call_line(report), " ".join(s.one_line_why.split()), "", "Why:",
             *(f"• {' '.join(r.split())}" for r in s.reasons)]
    if s.watch_outs:
        lines += ["Watch out:", *(f"• {' '.join(w.split())}" for w in s.watch_outs)]
    if peer_line := valuation_line(report):
        lines += [f"{PEER_MARK} {peer_line.replace('Valuation:', 'Price vs similar listed companies:', 1)}"]
    if s.listing_signal:
        lines += ["", f"📈 {' '.join(s.listing_signal.split())}"]
    lines += ["", FOOTER]
    return "\n".join(lines)


def problems(text: str, full_text: str, report: Report) -> list[str]:
    """Why `text` is not a plain, decision-only summary; empty when it passes. Deterministic, no model."""
    out = []
    body = [line for line in text.splitlines() if line.strip()]
    if len(body) > MAX_LINES:
        out.append(f"{len(body)} lines (max {MAX_LINES})")
    if codes := sorted(set(FLAG_CODE.findall(text))):
        out.append(f"flag codes: {', '.join(codes)}")
    # The call line and the peer multiples line are Python's, kept as they are on purpose.
    written = "\n".join(line for line in body if not line.startswith(("🧭", PEER_MARK)))
    if terms := sorted(set(JARGON.findall(written))):
        out.append(f"jargon: {', '.join(terms)}")
    allowed = numbers(full_text) | numbers("\n".join(_header(report)))
    if bad := unsupported(text, allowed):
        out.append(f"numbers not in the full summary: {', '.join(sorted(set(bad)))}")
    long_lines = [line for line in body if len(line.split()) > MAX_LINE_WORDS and not line.startswith(PEER_MARK)]
    if long_lines:
        out.append(f"{len(long_lines)} line(s) over {MAX_LINE_WORDS} words")
    call = _call_line(report)
    if call is not None and call not in text:
        out.append(f"the call is not shown as: {call}")
    return out
