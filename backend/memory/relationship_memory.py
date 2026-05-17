"""
Relationship memory — Phase 4 of emotional cognition.

Persists emotionally significant events, user patterns, and relational
context to SQLite. Enables Xyron to reference past moments authentically.

Examples of stored events:
  - "user upgraded the memory system" (feature_upgrade)
  - "consecutive late-night sessions" (pattern_late_night)
  - "third tool failure in a row" (frustration_streak)
  - "coding streak: 4 hours" (focus_streak)
  - "shipped a feature" (achievement)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_DIR  = Path.home() / ".ai-operator"
_DB_PATH = _DB_DIR / "relationship_memory.db"

# Event categories
EVENT_TYPES = frozenset({
    "achievement",       # user completed something significant
    "frustration",       # user hit repeated failures
    "feature_upgrade",   # Xyron's capabilities were upgraded
    "focus_streak",      # long coding/work session
    "late_night",        # after-midnight session
    "task_success",      # tool executed successfully
    "task_failure",      # tool failed
    "milestone",         # notable turn_count or session count
    "personal",          # user shared something personal
    "pattern",           # recurring behavioral pattern
})

# Comments Xyron can make when recalling past events (keyed by event_type)
_RECALL_LINES: dict[str, list[str]] = {
    "achievement": [
        "You knocked that out.",
        "That's the third one this week.",
        "Progress is compounding.",
    ],
    "frustration": [
        "You hit this wall before — we got through it.",
        "Same friction point. We fixed it last time.",
    ],
    "feature_upgrade": [
        "You keep making me better.",
        "Every upgrade sticks.",
        "You upgraded my memory system yourself.",
    ],
    "focus_streak": [
        "You've been in this for hours.",
        "Long session. You're locked in.",
        "Still here. Respect.",
    ],
    "late_night": [
        "Still coding at this hour.",
        "Late again. Consistent.",
        "You always push past midnight.",
    ],
    "milestone": [
        "We've been at this a while.",
        "That's a lot of turns.",
    ],
}


@dataclass
class RelationshipEvent:
    id:         int
    event_type: str
    description: str
    metadata:   dict
    ts:         float


class RelationshipMemory:
    """SQLite-backed emotional event log with threadsafe access."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._init_db()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        event_type:  str,
        description: str,
        metadata:    Optional[dict] = None,
    ) -> None:
        """Store a relationship event."""
        if event_type not in EVENT_TYPES:
            logger.warning("[RelMemory] unknown event type: %s", event_type)
        meta_json = json.dumps(metadata or {})
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_type, description, metadata, ts) VALUES (?,?,?,?)",
                (event_type, description, meta_json, time.time()),
            )
            self._conn.commit()
        logger.debug("[RelMemory] recorded %s: %s", event_type, description[:60])

    def recent(self, n: int = 10, event_type: Optional[str] = None) -> list[RelationshipEvent]:
        """Return the n most recent events, optionally filtered by type."""
        with self._lock:
            if event_type:
                rows = self._conn.execute(
                    "SELECT id,event_type,description,metadata,ts FROM events "
                    "WHERE event_type=? ORDER BY ts DESC LIMIT ?",
                    (event_type, n),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id,event_type,description,metadata,ts FROM events "
                    "ORDER BY ts DESC LIMIT ?",
                    (n,),
                ).fetchall()
        return [
            RelationshipEvent(
                id=r[0], event_type=r[1], description=r[2],
                metadata=json.loads(r[3] or "{}"), ts=r[4],
            )
            for r in rows
        ]

    def count_type(self, event_type: str, since_hours: float = 168.0) -> int:
        """Count events of a type in the last N hours (default 1 week)."""
        since = time.time() - since_hours * 3600
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type=? AND ts>=?",
                (event_type, since),
            ).fetchone()
        return row[0] if row else 0

    def get_recall_line(self, event_type: str) -> Optional[str]:
        """Return a contextual recall comment for a past event type, if any exist."""
        lines = _RECALL_LINES.get(event_type, [])
        if not lines:
            return None
        count = self.count_type(event_type)
        if count == 0:
            return None
        # Rotate through lines based on total count
        return lines[(count - 1) % len(lines)]

    def has_pattern(self, event_type: str, min_count: int = 3) -> bool:
        """True if the event type has occurred at least min_count times."""
        return self.count_type(event_type) >= min_count

    def get_context_string(self) -> str:
        """Return a 1-2 sentence context string for injecting into prompts."""
        parts: list[str] = []
        for etype, min_c in [
            ("late_night", 2),
            ("focus_streak", 3),
            ("feature_upgrade", 1),
            ("achievement", 2),
            ("frustration", 2),
        ]:
            if self.has_pattern(etype, min_c):
                line = self.get_recall_line(etype)
                if line:
                    parts.append(line)
                if len(parts) >= 2:
                    break
        return " ".join(parts)

    def export_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Return events as dicts for API exposure."""
        return [
            {
                "id": e.id, "event_type": e.event_type,
                "description": e.description, "metadata": e.metadata, "ts": e.ts,
            }
            for e in self.recent(n)
        ]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT    NOT NULL,
                description TEXT    NOT NULL,
                metadata    TEXT    DEFAULT '{}',
                ts          REAL    NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events (event_type, ts)"
        )
        self._conn.commit()
        logger.debug("[RelMemory] initialized at %s", _DB_PATH)


# Global singleton
relationship_memory = RelationshipMemory()
