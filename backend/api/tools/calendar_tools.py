"""Calendar tools — list events and create events via Google Calendar service."""
from __future__ import annotations

import logging
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)


def _exec_list_events(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    days_ahead = int(params.get("days_ahead", 1))
    label_map  = {1: "today", 2: "in the next two days", 7: "this week"}
    label      = label_map.get(days_ahead, f"in the next {days_ahead} days")
    try:
        from api.services.calendar_service import list_events, build_events_spoken, is_configured
        if not is_configured():
            return ToolResult(
                success=False,
                text="Calendar not configured.",
                spoken="Google Calendar isn't set up yet. Please add your OAuth credentials.",
            )
        events = list_events(days_ahead=days_ahead)
        spoken = build_events_spoken(events, label=label)
        return ToolResult(
            success=True,
            text=f"Fetched {len(events)} events ({days_ahead} days ahead)",
            spoken=spoken,
            data={"events": events, "count": len(events)},
        )
    except Exception as exc:
        logger.error("list_events failed: %s", exc)
        return ToolResult(
            success=False,
            text=str(exc),
            spoken="I had trouble reading your calendar right now. Make sure Google Calendar is authorized.",
        )


def _exec_create_event(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    summary          = (params.get("summary") or "").strip()
    start_iso        = (params.get("start_iso") or "").strip()
    duration_minutes = int(params.get("duration_minutes", 60))
    description      = (params.get("description") or "").strip()
    if not summary or not start_iso:
        return ToolResult(success=False, text="Missing summary or start time.", spoken="I need an event title and start time to create a calendar event.")
    try:
        from api.services.calendar_service import create_event, is_configured
        if not is_configured():
            return ToolResult(success=False, text="Calendar not configured.", spoken="Google Calendar isn't set up yet.")
        created = create_event(
            summary=summary,
            start_iso=start_iso,
            duration_minutes=duration_minutes,
            description=description,
        )
        event_url = created.get("htmlLink", "")
        return ToolResult(
            success=True,
            text=f"Event created: {summary} at {start_iso}",
            spoken=f"Done — I've added '{summary}' to your calendar.",
            action_url=event_url or None,
            data={"event": created},
        )
    except Exception as exc:
        logger.error("create_event failed: %s", exc)
        return ToolResult(
            success=False,
            text=str(exc),
            spoken=f"I couldn't create the calendar event. {exc}",
        )


# ── Register ──────────────────────────────────────────────────────────────────

registry.register(
    name="list_events",
    definition={
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "List upcoming Google Calendar events. Use for: 'what's on my calendar', 'any meetings today', 'what do I have today', 'my schedule'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 1 = today)", "default": 1},
                },
                "required": [],
            },
        },
    },
    executor=_exec_list_events,
    risk="low",
    category="productivity",
)

registry.register(
    name="create_event",
    definition={
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Create a new Google Calendar event. Use for: 'add meeting', 'schedule X', 'create event', 'put X on my calendar'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary":          {"type": "string", "description": "Event title/name"},
                    "start_iso":        {"type": "string", "description": "Start datetime in ISO 8601 format with timezone"},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 60)", "default": 60},
                    "description":      {"type": "string", "description": "Optional event description"},
                },
                "required": ["summary", "start_iso"],
            },
        },
    },
    executor=_exec_create_event,
    risk="medium",
    category="productivity",
)
