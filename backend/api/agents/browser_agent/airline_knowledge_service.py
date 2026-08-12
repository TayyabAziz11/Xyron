from __future__ import annotations

"""
AirlineKnowledgeService — distinguishes general airline policy (public
knowledge, not fare-specific) from fare-specific verified facts (only
ever obtained by actually reading a specific booking/fare page). Never
lets the former stand in as proof of the latter.

Example of the distinction this module exists to enforce:
  "Emirates generally allows 30kg checked baggage in Economy" (general —
  useful context, but NOT proof this specific search result's fare
  includes it)
  vs.
  "This FlyDubai fare page shows 20kg included" (fare-specific — actually
  read from the page in front of the user)

General policy is a small curated cache (public, widely-published
airline baggage policies — not a scrape, not fare data). Fare-specific
verification always delegates to a real page read
(flight_conversation.verify_baggage_on_official_site), never invented.

Log tags: [AIRLINE_POLICY_LOOKUP] [AIRLINE_POLICY_SOURCE]
[AIRLINE_POLICY_CACHED] [FARE_BAGGAGE_NOT_VERIFIED]
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("api.agents.browser_agent.airline_knowledge")

# Publicly published general baggage allowances (economy cabin, checked
# baggage) as of curation time — general policy only, never fare-specific.
#
# Curation note (expanded from the original 8): these figures and URLs
# are drawn from well-established, widely-published industry knowledge,
# not a live scrape of each airline's site — the same basis the original
# 8 entries already used. Airlines revise fare-bundle inclusions and
# occasionally restructure their baggage-info page URLs, so treat this
# as a "good general starting point" cache, not a guarantee of current
# accuracy. This is exactly why `explain_general_vs_fare_specific()`
# always appends the fare-specific caveat, and why
# `verify_baggage_on_official_site()` only ever reports what it actually
# reads on the page — a stale URL here degrades to an honest "couldn't
# find it" rather than a fabricated number.
_GENERAL_BAGGAGE_POLICY: dict[str, dict] = {
    # ── Middle East / Gulf ───────────────────────────────────────────
    "Emirates": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.emirates.com/english/before-fly/baggage/"},
    "FlyDubai": {"checked_kg": 20, "cabin_kg": 7, "source_url": "https://www.flydubai.com/en/plan/baggage"},
    "Air Arabia": {"checked_kg": 20, "cabin_kg": 10, "source_url": "https://www.airarabia.com/en/baggage-information"},
    "Qatar Airways": {"checked_kg": 23, "cabin_kg": 7, "source_url": "https://www.qatarairways.com/en/baggage.html"},
    "Etihad Airways": {"checked_kg": 23, "cabin_kg": 7, "source_url": "https://www.etihad.com/en-ae/fly-etihad/baggage"},
    "Saudia": {"checked_kg": 23, "cabin_kg": 7, "source_url": "https://www.saudia.com/before-flying/baggage"},
    "Kuwait Airways": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.kuwaitairways.com/en/baggage-information"},
    "Gulf Air": {"checked_kg": 30, "cabin_kg": 6, "source_url": "https://www.gulfair.com/travel-information/baggage"},
    "Oman Air": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.omanair.com/en/before-you-fly/baggage-information"},
    "SalamAir": {"checked_kg": 20, "cabin_kg": 7, "source_url": "https://www.salamair.com/en/baggage"},

    # ── South Asia ───────────────────────────────────────────────────
    "Pakistan International Airlines": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.piac.com.pk/baggage-information"},
    "Airblue": {"checked_kg": 20, "cabin_kg": 7, "source_url": "https://www.airblue.com/travel-information/baggage-policy.aspx"},
    "Serene Air": {"checked_kg": 20, "cabin_kg": 7, "source_url": "https://www.sereneair.com/baggage-policy"},
    "Air India": {"checked_kg": 23, "cabin_kg": 8, "source_url": "https://www.airindia.com/in/en/travel-information/baggage-allowance.html"},
    "IndiGo": {"checked_kg": 15, "cabin_kg": 7, "source_url": "https://www.goindigo.in/travel-information/baggage-services.html"},
    "SpiceJet": {"checked_kg": 15, "cabin_kg": 7, "source_url": "https://www.spicejet.com/en/plan-my-trip/baggage/"},

    # ── East / Southeast Asia ────────────────────────────────────────
    "Malaysia Airlines": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.malaysiaairlines.com/my/en/travel-info/baggage.html"},
    "Singapore Airlines": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.singaporeair.com/en_UK/us/travel-info/baggage/"},
    "Cathay Pacific": {"checked_kg": 23, "cabin_kg": 7, "source_url": "https://www.cathaypacific.com/cx/en_US/baggage.html"},
    "Thai Airways": {"checked_kg": 30, "cabin_kg": 7, "source_url": "https://www.thaiairways.com/en/travel_information/baggage_allowance.page"},

    # ── Europe ───────────────────────────────────────────────────────
    "Turkish Airlines": {"checked_kg": 20, "cabin_kg": 8, "source_url": "https://www.turkishairlines.com/en-int/any-questions/baggage-allowance/"},
    "British Airways": {"checked_kg": 23, "cabin_kg": 23, "source_url": "https://www.britishairways.com/en-gb/information/baggage-essentials"},
    "Lufthansa": {"checked_kg": 23, "cabin_kg": 8, "source_url": "https://www.lufthansa.com/us/en/baggage"},
    "Air France": {"checked_kg": 23, "cabin_kg": 12, "source_url": "https://wwws.airfrance.us/information/preparation/baggage"},
    "KLM": {"checked_kg": 23, "cabin_kg": 12, "source_url": "https://www.klm.com/en/information/baggage"},

    # ── Americas / Oceania ───────────────────────────────────────────
    "American Airlines": {"checked_kg": 23, "cabin_kg": 10, "source_url": "https://www.aa.com/i18n/travel-info/baggage/checked-baggage.jsp"},
    "United Airlines": {"checked_kg": 23, "cabin_kg": 10, "source_url": "https://www.united.com/en/us/fly/baggage/checked-bags.html"},
    "Delta Air Lines": {"checked_kg": 23, "cabin_kg": 10, "source_url": "https://www.delta.com/us/en/baggage/overview"},
    "Air Canada": {"checked_kg": 23, "cabin_kg": 10, "source_url": "https://www.aircanada.com/us/en/aco/home/plan/baggage.html"},
    "Qantas": {"checked_kg": 23, "cabin_kg": 7, "source_url": "https://www.qantas.com/au/en/travel-info/baggage.html"},
}

_cache: dict[str, dict] = {}


def get_general_policy(airline_name: str) -> Optional[dict]:
    """Returns cached general baggage policy for *airline_name*, or None
    if not in the curated set — never fabricates a number for an airline
    we don't actually have published data for."""
    logger.info("[AIRLINE_POLICY_LOOKUP] airline=%r", airline_name)
    policy = _GENERAL_BAGGAGE_POLICY.get(airline_name)
    if policy is None:
        logger.info("[AIRLINE_POLICY_LOOKUP] airline=%r result=not_in_curated_set", airline_name)
        return None

    logger.info("[AIRLINE_POLICY_SOURCE] airline=%r source=%s", airline_name, policy["source_url"])

    cached = _cache.get(airline_name)
    if cached is None:
        cached = {**policy, "cached_at": time.time()}
        _cache[airline_name] = cached
        logger.info("[AIRLINE_POLICY_CACHED] airline=%r checked_kg=%d", airline_name, policy["checked_kg"])
    return cached


def explain_general_vs_fare_specific(airline_name: str, requested_kg: Optional[int]) -> str:
    """Builds the honest, non-conflating explanation the spec requires:
    general policy is useful context, but is explicitly NOT presented as
    proof of what a specific search result's fare includes."""
    policy = get_general_policy(airline_name)
    logger.info("[FARE_BAGGAGE_NOT_VERIFIED] airline=%r requested_kg=%s", airline_name, requested_kg)

    if policy is None:
        return (
            f"I don't have {airline_name}'s general baggage policy on hand, and I can't verify "
            "the fare-specific allowance from this page either."
        )

    kg = policy["checked_kg"]
    meets = requested_kg is not None and kg >= requested_kg
    general_line = f"{airline_name} generally allows {kg} kilograms of checked baggage in economy"
    if requested_kg is not None:
        general_line += f", which would cover your {requested_kg} kilogram requirement" if meets else \
            f", which is less than the {requested_kg} kilograms you need"
    return general_line + " — but I can't verify that this specific fare includes it without checking the fare details."
