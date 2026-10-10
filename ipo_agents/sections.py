"""Section agent registry: one SectionSpec (prompt + schema) per section group. The graph runs every entry."""
from __future__ import annotations

from ipo_agents.contracts import OfferObjects
from ipo_agents.section_agent import SectionSpec

OFFER_OBJECTS_PROMPT = (
    "You extract the Offer & Objects section of an Indian IPO red herring prospectus (RHP): the offer "
    "summary, capital structure, objects of the offer and offer structure. Extract the total offer size, "
    "the fresh issue and the offer for sale (OFS) as rupee amounts where stated, and the OFS as a number of "
    "shares; equity shares outstanding before and after the offer; the promoter holding % before and after "
    "the offer; each object of the fresh issue with its amount, and the general corporate purposes amount "
    "separately; the anchor, QIB, non-institutional and retail portions as %; and each selling shareholder "
    "with the shares they offer and their holding after the offer. Do not extract the price band or lot "
    "size."
)

SECTIONS: dict[str, SectionSpec] = {
    spec.group: spec for spec in [
        SectionSpec(group="offer_objects", schema=OfferObjects, prompt=OFFER_OBJECTS_PROMPT, report_field="offer"),
    ]
}
