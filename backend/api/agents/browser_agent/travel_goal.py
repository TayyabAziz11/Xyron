from __future__ import annotations

"""
TravelGoal — structured travel request built BEFORE any browser
interaction, from repaired entities (TravelEntityResolver) + regex
parsing of preferences/passengers/dates. The browser only ever acts on
a TravelGoal, never on raw transcript text.

Log tags: [TRAVEL_GOAL_CREATED] [TRAVEL_GOAL_UPDATED]
[TRAVEL_GOAL_MISSING_FIELD] [TRAVEL_GOAL_CLARIFICATION_REQUIRED]
"""

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from api.agents.browser_agent.travel_entity_resolver import TravelEntityResolver, TravelLocation

logger = logging.getLogger("api.agents.browser_agent.travel_goal")

_WORD_NUM = {"a": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_TIME_WINDOWS = ("morning", "afternoon", "evening", "night")
_BUDGET_RE = re.compile(r"(?:under|below|less\s+than|budget\s+of|around|about|max(?:imum)?)\s*\$?\s*(\d{2,6})", re.IGNORECASE)
_BAGGAGE_RE = re.compile(r"(\d+)\s*k(?:g|ilograms?)\b", re.IGNORECASE)
_DIRECT_RE = re.compile(r"\bdirect\s+only\b|\bnonstop\s+only\b|\bno\s+stops?\b", re.IGNORECASE)
_MAX_STOPS_RE = re.compile(r"\ballow\s+(\d+|one|two)\s+stops?\b|\bup\s+to\s+(\d+|one|two)\s+stops?\b", re.IGNORECASE)
_CABIN_RE = re.compile(r"\b(economy|business|first|premium\s+economy)\s+class\b", re.IGNORECASE)
_REFUNDABLE_RE = re.compile(r"\brefundable\s+only\b|\bonly\s+refundable\b", re.IGNORECASE)
_RETURN_TRIP_RE = re.compile(r"\breturn\s+trip\b|\bround[\s-]?trip\b", re.IGNORECASE)
_SORT_RE = re.compile(r"\bsort\s+by\s+(cheapest|fastest|best)\b", re.IGNORECASE)
_PASSENGERS_RE = re.compile(r"\b(\w+)\s+adults?\s*(?:and\s+(\w+)\s+child(?:ren)?)?\b", re.IGNORECASE)


@dataclass
class TravelGoal:
    origin: Optional[str] = None
    destination: Optional[str] = None
    origin_iata: Optional[str] = None
    destination_iata: Optional[str] = None
    departure_date: str = ""
    return_date: str = ""
    trip_type: str = "one_way"
    adults: int = 1
    children: int = 0
    infants: int = 0
    cabin_class: str = "economy"
    preferred_airlines: list = field(default_factory=list)
    excluded_airlines: list = field(default_factory=list)
    direct_only: bool = False
    max_stops: Optional[int] = None
    departure_time_window: Optional[str] = None
    arrival_time_window: Optional[str] = None
    baggage_kg: Optional[int] = None
    budget: Optional[float] = None
    currency: str = "USD"
    refundable_only: bool = False
    flexible_dates: bool = False
    sort_preference: str = "best"

    # Resolver evidence, not part of the "clean" schema but useful for
    # honest reporting of how confidently each field was understood.
    origin_confidence: float = 0.0
    destination_confidence: float = 0.0
    needs_clarification: Optional[str] = None  # question text, or None

    def missing_required_fields(self) -> list[str]:
        missing = []
        if not self.destination:
            missing.append("destination")
        if not self.departure_date:
            missing.append("departure_date")
        return missing

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_date_phrase(goal_text: str) -> str:
    m = re.search(
        r"\b(next\s+month|next\s+week|tomorrow|today|this\s+weekend|"
        r"in\s+\d+\s+days?|next\s+(?:mon|tue|wed|thu|fri|sat|sun)\w*)\b",
        goal_text.strip(), re.IGNORECASE,
    )
    return m.group(0).strip() if m else ""


def build_travel_goal(
    text: str,
    origin_raw: str = "",
    destination_raw: str = "",
    memory_context: Optional[dict] = None,
) -> TravelGoal:
    """Builds a TravelGoal from a natural-language request. origin_raw/
    destination_raw are whatever the lightweight from/to phrase parser
    already extracted (still-unrepaired STT text) — this function is
    responsible for repairing them via TravelEntityResolver before
    anything touches a browser."""
    memory_context = memory_context or {}
    goal = TravelGoal()

    if origin_raw:
        loc = TravelEntityResolver.resolve_location(
            origin_raw, context={"existing_value": memory_context.get("default_origin")})
        _apply_location(goal, loc, field_prefix="origin")
    elif memory_context.get("default_origin"):
        goal.origin = memory_context["default_origin"]

    if destination_raw:
        loc = TravelEntityResolver.resolve_location(
            destination_raw, context={"existing_value": memory_context.get("last_destination")})
        _apply_location(goal, loc, field_prefix="destination")
    elif memory_context.get("last_destination"):
        goal.destination = memory_context["last_destination"]

    goal.departure_date = _parse_date_phrase(text) or memory_context.get("departure_date", "")

    if _DIRECT_RE.search(text):
        goal.direct_only = True
    m = _MAX_STOPS_RE.search(text)
    if m:
        raw = (m.group(1) or m.group(2) or "").lower()
        goal.max_stops = _WORD_NUM.get(raw, None) or (int(raw) if raw.isdigit() else None)

    for window in _TIME_WINDOWS:
        if re.search(rf"\b{window}\s+flights?\b", text, re.IGNORECASE):
            goal.departure_time_window = window
            break

    bag_m = _BAGGAGE_RE.search(text)
    if bag_m:
        goal.baggage_kg = int(bag_m.group(1))

    budget_m = _BUDGET_RE.search(text)
    if budget_m:
        goal.budget = float(budget_m.group(1))

    cabin_m = _CABIN_RE.search(text)
    if cabin_m:
        goal.cabin_class = cabin_m.group(1).lower().replace(" ", "_")

    if _REFUNDABLE_RE.search(text):
        goal.refundable_only = True
    if _RETURN_TRIP_RE.search(text):
        goal.trip_type = "return"

    sort_m = _SORT_RE.search(text)
    if sort_m:
        goal.sort_preference = sort_m.group(1).lower()

    pax_m = _PASSENGERS_RE.search(text)
    if pax_m and pax_m.group(1).lower() in _WORD_NUM:
        goal.adults = _WORD_NUM[pax_m.group(1).lower()]
        goal.children = _WORD_NUM.get((pax_m.group(2) or "").lower(), 0)

    missing = goal.missing_required_fields()
    if missing:
        logger.info("[TRAVEL_GOAL_MISSING_FIELD] fields=%s", missing)
    if goal.needs_clarification:
        logger.info("[TRAVEL_GOAL_CLARIFICATION_REQUIRED] question=%r", goal.needs_clarification)

    logger.info(
        "[TRAVEL_GOAL_CREATED] origin=%r destination=%r date=%r direct_only=%s baggage_kg=%s time_window=%s",
        goal.origin, goal.destination, goal.departure_date, goal.direct_only, goal.baggage_kg,
        goal.departure_time_window,
    )
    return goal


def _apply_location(goal: TravelGoal, loc: TravelLocation, field_prefix: str) -> None:
    if loc.evidence == "ambiguous_needs_clarification" and loc.candidates:
        question = TravelEntityResolver.clarification_question(loc)
        goal.needs_clarification = question
    elif loc.canonical_city:
        setattr(goal, field_prefix, loc.canonical_city)
        setattr(goal, f"{field_prefix}_iata", loc.iata_code)
        setattr(goal, f"{field_prefix}_confidence", loc.confidence)


def update_travel_goal(goal: TravelGoal, **fields: Any) -> TravelGoal:
    for k, v in fields.items():
        if hasattr(goal, k):
            setattr(goal, k, v)
    logger.info("[TRAVEL_GOAL_UPDATED] fields=%s", sorted(fields.keys()))
    return goal
