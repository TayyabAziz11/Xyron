"""
Entity stack — session-scoped short-term entity memory.

Tracks folders, files, apps, and items that Xyron acts on so anaphoric
references ("that folder", "open it", "same one") can be resolved without
the user repeating themselves.

Wire-in:
  WRITE  — after any agent/tool creates, opens, or prepares an entity.
  RESOLVE — before routing, to expand "open it" → concrete entity reference.

Logs:
  [ENTITY_WRITE]   type=FOLDER path=...
  [ENTITY_RESOLVE] phrase="that folder" resolved=...
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Entity:
    type:         str               # FOLDER | FILE | APP | URL | CONTACT | ITEM
    value:        str               # path, app name, url, etc.
    source_agent: str               # which agent produced this
    action:       str               # create | open | prepare | close | delete | find
    timestamp:    str               = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra:        dict              = field(default_factory=dict)


# Keyword sets used for anaphora resolution
_TYPE_HINTS: dict[str, list[str]] = {
    "FOLDER":  ["folder", "directory", "dir"],
    "FILE":    ["file", "document", "doc", "script"],
    "APP":     ["app", "application", "program", "browser", "editor"],
    "URL":     ["link", "url", "site", "page", "tab"],
    "ITEM":    ["item", "thing", "one", "same", "again"],
}

_ANAPHORIC_TRIGGERS = [
    "that one", "same one", "open again", "open it", "launch it",
    "that folder", "that file", "that app", "it again", "same thing",
    "the folder", "the file", "the app", "this one",
]


class EntityStack:
    """Session-scoped LIFO entity stack with anaphora resolution."""

    def __init__(self, maxlen: int = 30) -> None:
        self._stack: list[Entity] = []
        self._maxlen = maxlen
        self._lock = threading.Lock()

    def push(self, entity: Entity) -> None:
        """Push an entity and log it."""
        with self._lock:
            self._stack.append(entity)
            if len(self._stack) > self._maxlen:
                self._stack.pop(0)
        logger.info("[ENTITY_WRITE] type=%s value=%r agent=%s action=%s",
                    entity.type, entity.value[:80], entity.source_agent, entity.action)
        try:
            from observability.xyron_logger import xlog
            xlog.latency(label=f"entity_write:{entity.type}", ms=0,
                         value=entity.value[:60], action=entity.action)
        except Exception:
            pass

    def push_from_result(
        self,
        *,
        agent_id: str,
        intent: str,
        spoken: str,
        entities: dict,
    ) -> None:
        """Extract and push entities from an agent result."""
        _pushed = False

        # Explicit entities dict from semantic frame
        for key in ("folder", "path", "directory"):
            if key in entities:
                self.push(Entity(type="FOLDER", value=str(entities[key]),
                                 source_agent=agent_id, action=_intent_to_action(intent)))
                _pushed = True

        for key in ("app", "application", "name"):
            if key in entities and intent in ("open_app", "work_mode", "chill_mode"):
                self.push(Entity(type="APP", value=str(entities[key]),
                                 source_agent=agent_id, action="open"))
                _pushed = True

        for key in ("file", "filename"):
            if key in entities:
                self.push(Entity(type="FILE", value=str(entities[key]),
                                 source_agent=agent_id, action=_intent_to_action(intent)))
                _pushed = True

        # Fallback: infer from intent when entities are empty
        if not _pushed and spoken:
            if intent in ("work_mode", "home_mode", "chill_mode"):
                self.push(Entity(type="APP", value=intent.replace("_mode", ""),
                                 source_agent=agent_id, action="prepare"))
            elif intent == "file_action" and entities.get("target"):
                self.push(Entity(type="FILE", value=str(entities["target"]),
                                 source_agent=agent_id, action=_intent_to_action(intent)))

    def resolve(self, phrase: str) -> Optional[Entity]:
        """
        Try to resolve an anaphoric reference in phrase.

        Returns the most recent matching Entity, or None.
        """
        phrase_low = phrase.lower()

        # Must look like an anaphoric phrase
        is_anaphoric = (
            any(trigger in phrase_low for trigger in _ANAPHORIC_TRIGGERS) or
            # very short phrases with pronouns are anaphoric
            (len(phrase.split()) <= 5 and any(w in phrase_low for w in ("it", "that", "the same", "again")))
        )
        if not is_anaphoric:
            return None

        with self._lock:
            if not self._stack:
                return None

            # Try type-specific match first
            for entity_type, keywords in _TYPE_HINTS.items():
                if any(kw in phrase_low for kw in keywords):
                    for e in reversed(self._stack):
                        if e.type == entity_type:
                            logger.info("[ENTITY_RESOLVE] phrase=%r resolved=Entity(type=%s value=%r)",
                                        phrase[:60], e.type, e.value[:60])
                            return e

            # Generic: return the most recently pushed entity
            e = self._stack[-1]
            logger.info("[ENTITY_RESOLVE] phrase=%r resolved=Entity(type=%s value=%r) [generic]",
                        phrase[:60], e.type, e.value[:60])
            return e

    def recent(self, n: int = 5) -> list[Entity]:
        with self._lock:
            return list(reversed(self._stack[-n:]))

    def clear(self) -> None:
        with self._lock:
            self._stack.clear()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {"type": e.type, "value": e.value, "action": e.action,
                 "agent": e.source_agent, "ts": e.timestamp}
                for e in reversed(self._stack[:10])
            ]


def _intent_to_action(intent: str) -> str:
    _MAP = {
        "file_action":   "open",
        "open_app":      "open",
        "work_mode":     "prepare",
        "home_mode":     "prepare",
        "chill_mode":    "prepare",
        "dev_help":      "edit",
        "screen_help":   "view",
    }
    return _MAP.get(intent, "act")


# Module singleton — shared across the voice pipeline session
entity_stack = EntityStack()
