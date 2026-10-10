"""Offer & Objects red flags (plan 5.2, spec story 23). Plain Python over calc.py's Derived numbers.

A flag needs verified inputs: a calculation that is not computable raises no flag.
"""
from __future__ import annotations

from ipo_agents.contracts import Derived, Flag, OfferObjects, Report

OFS_HEAVY_PCT = 50.0
GCP_CAP_PCT = 25.0  # SEBI ICDR cap on general corporate purposes, % of the fresh issue
GCP_NEAR_PCT = 20.0
PROMOTER_MIN_PCT = 50.0
RED = ("warning", "error")  # severities shown in the summary; "info" stays in the report


def offer_flags(offer: OfferObjects | None, calcs: dict[str, Derived]) -> list[Flag]:
    if offer is None:
        return []
    flags = []
    ofs = calcs.get("ofs_pct")
    if ofs and ofs.value is not None and ofs.value > OFS_HEAVY_PCT:
        flags.append(Flag(code="OFS_HEAVY", severity="warning", evidence_pages=ofs.pages,
                          message=f"Offer for sale is {ofs.value:.1f}% of the issue: most money goes to "
                                  "selling shareholders, not the company"))
    for s in offer.selling_shareholders:
        # Category is how the agent read the RHP's label ("Investor Selling Shareholder"); the holding is a verified Fact.
        if s.category == "investor" and s.holding_after.value == 0:
            flags.append(Flag(code="PE_VC_FULL_EXIT", severity="warning", evidence_pages=[s.holding_after.page],
                              message=f"Investor selling shareholder {s.name} exits fully (holding after the offer: nil)"))
    gcp = calcs.get("gcp_pct_of_fresh")
    if gcp and gcp.value is not None:
        if gcp.value > GCP_CAP_PCT:
            flags.append(Flag(code="GCP_OVER_CAP", severity="error", evidence_pages=gcp.pages,
                              message=f"General corporate purposes are {gcp.value:.1f}% of the fresh issue, "
                                      f"over the {GCP_CAP_PCT:g}% cap"))
        elif gcp.value >= GCP_NEAR_PCT:
            flags.append(Flag(code="GCP_NEAR_CAP", severity="warning", evidence_pages=gcp.pages,
                              message=f"General corporate purposes are {gcp.value:.1f}% of the fresh issue, "
                                      f"close to the {GCP_CAP_PCT:g}% cap"))
    post = offer.promoter_holding_post_pct
    if post.value is not None and post.value < PROMOTER_MIN_PCT:
        flags.append(Flag(code="PROMOTER_BELOW_50", severity="info", evidence_pages=[post.page],
                          message=f"Promoter holding after the issue is {post.value:g}%, under 50%"))
    return flags


def summary_line(report: Report) -> str | None:
    """E.g. "Offer: OFS 55.8% (at upper band) · ⚠️ OFS_HEAVY"; None when there is nothing to show."""
    parts = []
    ofs = report.calculations.get("ofs_pct")
    if ofs and ofs.value is not None:
        parts.append(f"OFS {ofs.value:.1f}%" + (" (at upper band)" if "upper band" in ofs.basis else ""))
    red = [f.code for f in report.flags if f.severity in RED]
    if red:
        parts.append("⚠️ " + ", ".join(red))
    return "Offer: " + " · ".join(parts) if parts else None
