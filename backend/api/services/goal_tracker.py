"""
goal_tracker.py — World State Engine: the user's active high-level objective.

active_context.py already infers a *fine-grained* goal per tool call
(file_management, app_installation, web_browsing, media_search, app_launch)
— GoalTracker doesn't re-derive that from scratch, it categorizes those
signals (plus a few tool-name hints active_context doesn't cover) into the
broad life-domains a reasoning layer actually cares about, and keeps a
short history so "how has the objective shifted" is answerable, not just
"what is it right now".

Logs: [GOAL_TRACKER_UPDATE]
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

MAX_HISTORY = 10

# Fine-grained active_context goal -> broad domain
_FROM_ACTIVE_CONTEXT_GOAL: dict[str, str] = {
    "file_management":  "file_management",
    "app_installation":  "app_management",
    "app_launch":        "app_management",
    "web_browsing":       "research",
    "web_search":         "research",
    "media_search":       "media",
}

# Tool-name hints not covered by active_context's goal inference
_FROM_TOOL: dict[str, str] = {
    "write_file": "writing", "create_note": "writing", "open_note": "writing",
    "search_youtube": "media", "play_media_file": "media",
    "install_store_app": "shopping", "open_store_app_page": "shopping",
    "search_web": "research",
}

# Coding signals — workspace app context (from workspace_context) is the
# strongest signal available, stronger than any single tool call.
_WORKSPACE_APP_GOAL: dict[str, str] = {
    "vscode": "coding", "visual_studio": "coding",
    "photoshop": "design", "illustrator": "design", "premiere": "design",
    "blender": "design", "figma": "design",
}

KNOWN_DOMAINS = frozenset({
    "coding", "writing", "shopping", "travel", "research", "communication",
    "media", "design", "system_admin", "file_management", "app_management", "unknown",
})


class GoalTracker:
    """Thread-safe tracker for the user's current broad objective + history."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[str] = None
        self._updated_at = 0.0
        self._history: deque[tuple[str, float, str]] = deque(maxlen=MAX_HISTORY)

    def update_from_active_context_goal(self, raw_goal: Optional[str], source: str = "active_context") -> None:
        if not raw_goal:
            return
        domain = _FROM_ACTIVE_CONTEXT_GOAL.get(raw_goal)
        if domain:
            self._set(domain, source)

    def update_from_tool(self, tool_name: str, source: str = "tool") -> None:
        domain = _FROM_TOOL.get(tool_name)
        if domain:
            self._set(domain, source)

    def update_from_workspace(self, app: Optional[str], source: str = "workspace_context") -> None:
        if not app:
            return
        domain = _WORKSPACE_APP_GOAL.get(app)
        if domain:
            self._set(domain, source)

    def set(self, domain: str, source: str = "manual") -> None:
        """Direct set — for callers (e.g. a future travel/shopping agent) that already know the domain."""
        self._set(domain, source)

    def _set(self, domain: str, source: str) -> None:
        with self._lock:
            if domain == self._current:
                return  # no-op, avoid noisy history churn
            self._current = domain
            self._updated_at = time.time()
            self._history.append((domain, self._updated_at, source))
        logger.info("[GOAL_TRACKER_UPDATE] goal=%s source=%s", domain, source)

    def get_goal(self) -> Optional[str]:
        with self._lock:
            return self._current

    def history(self) -> list[dict]:
        with self._lock:
            items = list(self._history)
        return [{"goal": g, "ts": ts, "source": s} for g, ts, s in reversed(items)]


goal_tracker = GoalTracker()
