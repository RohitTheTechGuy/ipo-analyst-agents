"""Wording shared by the Telegram summary and the PDF report."""
from __future__ import annotations

from ipo_agents.contracts import SubscriptionStatus

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
