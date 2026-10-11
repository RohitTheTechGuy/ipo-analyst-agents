"""Wording shared by the Telegram summary and the PDF report."""
from __future__ import annotations

from ipo_agents.calc import upper_band
from ipo_agents.contracts import Advice, Sentiment, SubscriptionStatus

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
