from __future__ import annotations

"""
FlightSessionState — the single active travel-consultant conversation's
state, decoupled from any one AgentTask so voice follow-ups in later
turns ("check Emirates", "sort by cheapest") can find and update it.

Single active session for this process (matches the existing single-user
desktop-app pattern used by memory_service.py / travel_memory.json — no
multi-user session table needed here). Recovery after page reload is
just re-reading this same object; nothing is destroyed on navigation.

Log tags: [FLIGHT_SESSION_CREATED] [FLIGHT_SESSION_UPDATED]
[FLIGHT_SESSION_REUSED] [FLIGHT_FILTER_STATE] [FLIGHT_SELECTED_OPTION]
"""

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger("api.agents.browser_agent.flight_session")


@dataclass
class FlightSessionState:
    origin: str = ""
    destination: str = ""
    departure_date: str = ""
    return_date: str = ""
    one_way_or_return: str = "one_way"
    passengers: dict = field(default_factory=lambda: {"adults": 1, "children": 0, "infants": 0})
    cabin_class: str = "economy"
    preferred_airlines: list = field(default_factory=list)
    excluded_airlines: list = field(default_factory=list)
    stops_filter: Optional[str] = None  # "nonstop" | "1_stop" | None (any)
    departure_time_filter: Optional[str] = None  # "morning" | "afternoon" | "evening" | "night"
    arrival_time_filter: Optional[str] = None
    baggage_requirement: Optional[str] = None  # e.g. "20kg"
    refundable_only: bool = False
    current_sort: str = "best"  # "cheapest" | "fastest" | "best"
    selected_flight: Optional[dict] = None
    current_page_url: str = ""
    current_tab_id: Optional[int] = None
    expanded_result: Optional[dict] = None
    last_verified_options: list = field(default_factory=list)
    confidence: str = "low"
    approval_state: str = "none"  # "none" | "pending" | "accepted" | "rejected"
    task_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_active_session: Optional[FlightSessionState] = None

# Phase 4.15: an abandoned conversation (WS disconnects without ever saying
# "cancel") used to leave this session "active" forever — silently blocking
# voice_ws.py's fast-path dispatch for every future, unrelated command for
# the rest of the process's life (live-measured: turn 1 of a brand-new
# conversation fell back to the slow ~20s path because a session from an
# earlier, abandoned test was still considered active). Auto-expire instead.
_SESSION_STALE_AFTER_S = 600.0  # 10 minutes of inactivity


def create(task_id: str, origin: str = "", destination: str = "", departure_date: str = "") -> FlightSessionState:
    global _active_session
    _active_session = FlightSessionState(
        origin=origin, destination=destination, departure_date=departure_date, task_id=task_id,
    )
    logger.info(
        "[FLIGHT_SESSION_CREATED] task=%s origin=%r destination=%r date=%r",
        task_id, origin, destination, departure_date,
    )
    return _active_session


def get_active() -> Optional[FlightSessionState]:
    global _active_session
    if _active_session is not None:
        _age = time.time() - _active_session.updated_at
        if _age > _SESSION_STALE_AFTER_S:
            logger.info("[FLIGHT_SESSION_EXPIRED] task=%s age_s=%.0f", _active_session.task_id, _age)
            _active_session = None
            return None
        logger.info("[FLIGHT_SESSION_REUSED] task=%s origin=%r destination=%r",
                     _active_session.task_id, _active_session.origin, _active_session.destination)
    return _active_session


def update(**fields: Any) -> Optional[FlightSessionState]:
    """Merge *fields* into the active session, if any. Returns the
    updated session (or None if no session is active)."""
    session = _active_session
    if session is None:
        return None
    for k, v in fields.items():
        if hasattr(session, k):
            setattr(session, k, v)
    session.updated_at = time.time()
    logger.info("[FLIGHT_SESSION_UPDATED] task=%s fields=%s", session.task_id, sorted(fields.keys()))
    return session


def update_filter_state(**filters: Any) -> Optional[FlightSessionState]:
    session = update(**filters)
    if session is not None:
        logger.info(
            "[FLIGHT_FILTER_STATE] stops=%s dep_time=%s arr_time=%s airlines_pref=%s airlines_excl=%s sort=%s",
            session.stops_filter, session.departure_time_filter, session.arrival_time_filter,
            session.preferred_airlines, session.excluded_airlines, session.current_sort,
        )
    return session


def select_option(option: dict) -> Optional[FlightSessionState]:
    session = update(selected_flight=option)
    if session is not None:
        logger.info("[FLIGHT_SELECTED_OPTION] task=%s airline=%s price=%s",
                     session.task_id, option.get("airline"), option.get("price"))
    return session


def clear() -> None:
    global _active_session
    if _active_session is not None:
        logger.info("[FLIGHT_SESSION_UPDATED] task=%s action=cleared", _active_session.task_id)
    _active_session = None
