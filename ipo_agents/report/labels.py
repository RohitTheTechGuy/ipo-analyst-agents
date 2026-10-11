"""Wording shared by the Telegram summary and the PDF report."""
from __future__ import annotations

from ipo_agents.contracts import Advice, SubscriptionStatus

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


def advice_text(advice: Advice) -> str:
    """e.g. "BUY · conviction high · listing gains"; a no_buy has no horizon."""
    parts = [CALL_LABELS[advice.call], f"conviction {advice.conviction}"]
    if advice.horizon in HORIZON_LABELS:
        parts.append(HORIZON_LABELS[advice.horizon])
    return " · ".join(parts)
