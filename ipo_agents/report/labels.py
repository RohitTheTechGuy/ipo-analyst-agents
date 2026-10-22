"""Wording shared by the Telegram summary and the PDF report."""
from __future__ import annotations

from ipo_agents.calc import upper_band
from ipo_agents.contracts import Advice, CheckedDemand, Sentiment, SubscriptionStatus

CALL_LABELS = {"buy": "BUY", "no_buy": "NO BUY"}
HORIZON_LABELS = {"listing_gains": "listing gains", "long_term": "long term"}
DISCLAIMER = "Not investment advice; generated from RHP + public sources."
VERDICT_LABELS = {
    "subscribe_long_term": "SUBSCRIBE (LONG TERM)",
    "subscribe_listing_gains": "SUBSCRIBE – LISTING GAINS ONLY",
    "neutral": "NEUTRAL",
    "avoid": "AVOID",
}


def subscription_text(sub: SubscriptionStatus) -> str:
    """e.g. "2.3× total (QIB 4.1×, NII 1.8×, Retail 1.2×)"."""
    parts = [f"{name} {f.value:.1f}×" for name, f in (("QIB", sub.qib), ("NII", sub.nii), ("Retail", sub.retail))
             if f and f.value is not None]
    return f"{sub.total.value:.1f}× total" + (f" ({', '.join(parts)})" if parts else "")


MARKET_HEADING = "What the market is saying"
# How a stance reads after "Most coverage ..." and in a tally.
STANCE_READS = {"subscribe": "says subscribe", "listing_gains_only": "says listing gains only",
                "avoid": "says avoid", "neutral": "is neutral"}


def _coverage(s: Sentiment) -> str:
    total = sum(s.tally.values())
    if not s.available:
        return "No press coverage could be read"
    if not total:
        return "No recent press coverage"
    stance, n = max(s.tally.items(), key=lambda kv: kv[1])
    if n * 2 > total:
        return f"Most coverage {STANCE_READS[stance]} ({n} of {total} articles)"
    counts = " / ".join(f"{k} {st.replace('_', ' ')}" for st, k in s.tally.items() if k)
    return f"Coverage is split ({counts})"


def market_read(s: Sentiment, price_band: str) -> str:
    """One plain sentence on what the market is saying: the articles' stances, the GMP as a listing
    gain or loss over the upper band, and the subscription so far. Python's counts only, no model."""
    parts = [_coverage(s)]
    band = upper_band(price_band)
    if s.gmp and s.gmp.value is not None and band:
        pct = s.gmp.value / band * 100
        if round(pct) == 0:
            parts.append(f"the grey market prices a flat listing at the ₹{band:,g} upper band (unofficial)")
        else:
            gain, side = ("gain", "over") if pct > 0 else ("loss", "below")
            parts.append(f"the grey market prices a {abs(pct):.0f}% listing {gain} {side} the ₹{band:,g} upper band (unofficial)")
    if s.subscription and s.subscription.total.value is not None:
        parts.append(f"bids so far are {s.subscription.total.value:.1f}× the shares on offer")
    if len(parts) == 1:
        return f"{parts[0]}, and no GMP or subscription data yet."
    return "; ".join(parts) + "."


def advice_text(advice: Advice) -> str:
    """e.g. "BUY · conviction high · listing gains"; a no_buy has no horizon."""
    parts = [CALL_LABELS[advice.call], f"conviction {advice.conviction}"]
    if advice.horizon in HORIZON_LABELS:
        parts.append(HORIZON_LABELS[advice.horizon])
    return " · ".join(parts)


DEMAND_LABELS = {"hot": "HIGH demand", "normal": "Normal demand", "weak": "WEAK demand"}


def demand_label(check: CheckedDemand) -> str:
    if check.devils_advocate:
        return "HIGH demand" if check.call == "no_buy" else "LOW demand"
    return DEMAND_LABELS[check.level]


def demand_heading(check: CheckedDemand) -> str:
    """e.g. "Why demand is high despite the NO BUY"; only for a check playing devil's advocate."""
    side = "high" if check.call == "no_buy" else "low"
    return f"Why demand is {side} despite the {CALL_LABELS[check.call]}"


def demand_lines(check: CheckedDemand | None) -> list[str]:
    """The demand check: why demand is where it is (led by a 🔥 heading when the market disagrees with
    the call), the retail reading, each reason of the call re-tested against the market, and the call against the demand; nothing without
    a check."""
    if check is None:
        return []
    head = (f"🔥 {demand_heading(check)} (the market as devil's advocate):" if check.devils_advocate
            else f"🔎 Demand check: {DEMAND_LABELS[check.level]}")
    lines = [head, *(f"• {r.text}" for r in check.demand_reasons)]
    if check.retail_reading:
        lines.append(f"Retail: {check.retail_reading}")
    if check.hypotheses:
        lines += ["Our reasons vs the market:",
                  *(f"{'✅ Holds' if h.holds else '⚠️ Weakened'}: {h.why}" for h in check.hypotheses)]
    why = f" {check.verdict_vs_demand}" if check.verdict_vs_demand else ""
    against = f"{demand_label(check)} vs {CALL_LABELS[check.call]}"
    if check.contradicts and check.call_holds:
        lines.append(f"⚠️ {against}: the call still holds.{why}")
    elif check.contradicts:
        lines.append(f"❗ {against}: the check thinks demand weakens the call.{why}")
    elif not check.call_holds:
        lines.append(f"❗ The check questions the call.{why}")
    else:
        lines.append(f"✅ Demand fits the call.{why}")
    return lines
