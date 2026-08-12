"""
activity_timeline.py — World State Engine: chronological record of user actions.

A bounded, in-memory, human-readable action log — distinct from the
persistent SQLite history (episodic_memory/history_service) and from
context_stack's typed entity stack. This is the "what has Xyron been doing"
narrative feed consumed by the Reasoning Context API, not a durable audit
log — restart-safe persistence already exists elsewhere and isn't duplicated
here.

Logs: [ACTIVITY_TIMELINE_RECORD]
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_EVENTS = 200


@dataclass
class ActivityEvent:
    ts: float
    description: str
    tool: Optional[str] = None
    entity: Optional[str] = None
    success: bool = True
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "description": self.description, "tool": self.tool,
            "entity": self.entity, "success": self.success, "source": self.source,
        }


class ActivityTimeline:
    """Thread-safe bounded chronological log of important user actions."""

    def __init__(self, maxlen: int = MAX_EVENTS) -> None:
        self._lock = threading.Lock()
        self._events: deque[ActivityEvent] = deque(maxlen=maxlen)

    def record(
        self, description: str, tool: Optional[str] = None,
        entity: Optional[str] = None, success: bool = True, source: str = "",
    ) -> None:
        event = ActivityEvent(
            ts=time.time(), description=description, tool=tool,
            entity=entity, success=success, source=source,
        )
        with self._lock:
            self._events.append(event)
        logger.debug("[ACTIVITY_TIMELINE_RECORD] %s (tool=%s success=%s source=%s)",
                      description[:80], tool, success, source)

    def recent(self, n: int = 20) -> list[ActivityEvent]:
        with self._lock:
            items = list(self._events)
        return list(reversed(items))[:n]

    def to_list(self, n: int = 20) -> list[dict]:
        return [e.to_dict() for e in self.recent(n)]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


activity_timeline = ActivityTimeline()
