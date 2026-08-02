"""
activity_events.py — Phase 3.6, Task 9/10/11: structured activity events.

The backend already had a "PROGRESS_EVENT_CREATED" log-only concept (see
voice_ws.py's _run_tool — Phase 4.11 comment) that was never actually sent
to the frontend as a message; and a separate, older `{"type":"agent_progress"}`
shape used by the coordinator/flight/coding-agent flows. This module adds
the canonical `{"type":"activity"}` schema and actually emits it over the
session WebSocket, built from the already-resolved tool/params/result —
never from an LLM or TTS wait (must stay under ~5ms — it's a dict + one
websocket send).

Titles are deterministic, derived from the resolved action/object/tool,
never internal jargon (no "intent router", "tool call", "tier", "object
resolver", etc. — those never appear in user-facing text here).

Logs: [ACTIVITY_EVENT_EMITTED]
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# tool_name -> (stage, title_started, title_completed, title_failed)
_TOOL_ACTIVITY: dict[str, tuple[str, str, str, str]] = {
    "open_application":  ("opening_app",    "Opening {name}",          "{name} is open",              "Couldn't open {name}"),
    "open_directory":    ("opening_folder", "Opening {name}",          "{name} opened",                "Couldn't open {name}"),
    "open_drive":        ("opening_folder", "Opening {name} drive",    "{name} drive is open",         "Couldn't open {name} drive"),
    "smart_open":        ("opening_folder", "Finding {name}",          "Opened {name}",                "Couldn't find {name}"),
    "create_folder":     ("opening_folder", "Creating {name}",         "{name} created",                "Couldn't create {name}"),
    "open_file":         ("opening_folder", "Opening {name}",          "{name} opened",                 "Couldn't open {name}"),
    "search_web":        ("searching_web",  "Searching the web",       "Search results ready",          "Web search failed"),
    "search_youtube":    ("searching_web",  "Searching YouTube",       "Found it",                      "YouTube search failed"),
    "open_url":          ("opening_app",    "Opening the page",        "Page opened",                   "Couldn't open the page"),
    "install_store_app": ("running_tool",   "Searching the Store for {name}", "Found {name} in the Store", "Couldn't find {name}"),
    "read_screen":       ("reading_screen", "Checking your screen",    "Ready",                          "Couldn't read the screen"),
    "open_system_settings": ("opening_app", "Opening Settings",        "Settings is open",              "Couldn't open Settings"),
}

_DEFAULT_ACTIVITY = ("running_tool", "Working on it", "Done", "That didn't work")

STAGES = frozenset({
    "opening_app", "searching_web", "opening_folder", "reading_screen",
    "analyzing_page", "comparing_options", "running_tool", "waiting_approval",
    "generating_response", "completed", "failed",
})


def _display_name(tool_name: str, params: dict, result_data: Optional[dict] = None) -> str:
    result_data = result_data or {}
    for key in ("app_name", "query", "name", "page"):
        val = params.get(key)
        if val:
            return str(val).strip().title()[:40]
    for key in ("app_name", "name"):
        val = result_data.get(key)
        if val:
            return str(val).strip()[:40]
    return "it"


def title_for(tool_name: str, params: dict, status: str, result_data: Optional[dict] = None) -> tuple[str, str]:
    """Return (stage, title) for a tool at a given status
    ('started'|'completed'|'failed'), from deterministic templates only."""
    stage, t_started, t_done, t_failed = _TOOL_ACTIVITY.get(tool_name, _DEFAULT_ACTIVITY)
    name = _display_name(tool_name, params, result_data)
    template = {"started": t_started, "completed": t_done, "failed": t_failed}.get(status, t_started)
    return stage, template.format(name=name)


async def emit_activity(
    websocket,
    send_fn,
    *,
    trace_id: str,
    stage: str,
    status: str,
    title: str,
    detail: Optional[str] = None,
    tool: Optional[str] = None,
    progress: Optional[float] = None,
) -> None:
    """
    Send one activity event. *send_fn* is the caller's existing `_send`
    helper (already handles disconnect-safety) — this module never opens
    its own connection or duplicates that logic. Non-blocking: no LLM call,
    no TTS wait, just a dict literal and one send.
    """
    payload = {
        "type":      "activity",
        "trace_id":  trace_id,
        "stage":     stage if stage in STAGES else "running_tool",
        "status":    status,
        "title":     title,
        "detail":    detail,
        "progress":  progress,
        "tool":      tool,
        "timestamp": time.time(),
    }
    await send_fn(websocket, payload)
    logger.info("[ACTIVITY_EVENT_EMITTED] trace_id=%s stage=%s status=%s title=%r",
                trace_id, payload["stage"], status, title)
