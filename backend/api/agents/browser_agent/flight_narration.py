from __future__ import annotations

"""
Canonical flight-workflow stage/narration/frontend-status emitter
(Phase 4.11.3 — travel-consultant live narration).

Every call site across browser_agent.py / flight_search_agent.py /
flight_conversation.py that used to log an ad hoc [FLIGHT_STAGE] +
[FRONTEND_STATUS] pair and separately push a "narration" WS event now
goes through `speak_stage()` here instead, so the spoken line, the
frontend-facing state, and the log trail are always the same event,
never three independently-maintained call sites drifting apart.

Deliberately NOT a rewrite of BrowserWorkspace/FlightSessionState/the
follow-up resolver — this module only centralizes the
narration/state-sync plumbing those already call into.

Log tags: [FLIGHT_STAGE_CHANGED] [FLIGHT_NARRATION_SENT]
[FLIGHT_NARRATION_SKIPPED_DUPLICATE] [FRONTEND_FLIGHT_STATUS_SENT]
[FLIGHT_ACTION_NARRATION_SYNC]
"""

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("api.agents.browser_agent.flight_narration")


class FlightStage:
    UNDERSTANDING_FLIGHT_REQUEST = "UNDERSTANDING_FLIGHT_REQUEST"
    OPENING_GOOGLE_FLIGHTS = "OPENING_GOOGLE_FLIGHTS"
    SEARCHING_FLIGHTS = "SEARCHING_FLIGHTS"
    LOADING_RESULTS = "LOADING_RESULTS"
    EXTRACTING_OPTIONS = "EXTRACTING_OPTIONS"
    COMPARING_PRICE = "COMPARING_PRICE"
    COMPARING_DURATION = "COMPARING_DURATION"
    FILTERING_AIRLINE = "FILTERING_AIRLINE"
    FILTERING_STOPS = "FILTERING_STOPS"
    FILTERING_TIME = "FILTERING_TIME"
    CHECKING_BAGGAGE = "CHECKING_BAGGAGE"
    WAITING_FOR_PREFERENCE = "WAITING_FOR_PREFERENCE"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


# Process-wide "last spoken line" — matches the existing single-active-
# flight-session assumption used throughout flight_session_state.py;
# there is only ever one live travel-consultant conversation at a time.
_last_spoken: Optional[str] = None


def _resolve_ws_send(task: Optional[Any]) -> tuple[Optional[str], Optional[Any]]:
    if task is not None:
        return task.task_id, task.ws_send_fn
    try:
        from api.agents.agent_runtime import agent_runtime as _art
        active = _art.get_active()
        if active is not None:
            return active.task_id, active.ws_send_fn
    except Exception:
        pass
    return None, None


def speak_stage(stage: str, message: str, task: Optional[Any] = None, speak: bool = True) -> None:
    """Canonical stage-change + narration + frontend-status emission.

    `speak=True` (default): enqueues a live TTS narration event AND a
    frontend-status event — used for genuine mid-task progress updates
    ("I'm opening Google Flights", "I'm comparing price and duration").

    `speak=False`: emits only the frontend-status + log trail, no TTS —
    used when the human-readable text will be spoken anyway as the
    turn's normal final response (e.g. the proactive preference menu),
    so the user never hears the same content twice.
    """
    global _last_spoken
    logger.info("[FLIGHT_STAGE_CHANGED] stage=%s", stage)

    is_duplicate = speak and message == _last_spoken
    if is_duplicate:
        logger.info("[FLIGHT_NARRATION_SKIPPED_DUPLICATE] stage=%s message=%r", stage, message[:100])
    elif speak:
        _last_spoken = message
        logger.info("[FLIGHT_NARRATION_SENT] stage=%s message=%r", stage, message[:200])

    logger.info("[FRONTEND_FLIGHT_STATUS_SENT] state=%s message=%r", stage, message[:120])
    logger.info("[FLIGHT_ACTION_NARRATION_SYNC] stage=%s", stage)

    task_id, ws_send_fn = _resolve_ws_send(task)
    if ws_send_fn is None:
        return

    asyncio.create_task(ws_send_fn({
        "type": "flight_status", "task_id": task_id, "state": stage, "message": message,
    }))
    if speak and not is_duplicate:
        asyncio.create_task(ws_send_fn({
            "type": "narration", "task_id": task_id, "message": message,
        }))
