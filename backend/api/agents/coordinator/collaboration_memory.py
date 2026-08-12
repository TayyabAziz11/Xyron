"""
CollaborationMemory — shared key-value store for cross-agent data passing.

Agents write their outputs here so other agents in the same workflow can read them.
Also persists cross-session workflow learnings.

Storage: SQLite at ~/.xyron/collaboration_memory.db
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("api.agents.coordinator.memory")

_DB_PATH = Path.home() / ".xyron" / "collaboration_memory.db"


class CollaborationMemory:
    """
    Thread-safe SQLite-backed key-value store for agent collaboration.

    Two tables:
    1. session_memory — per-session ephemeral data (cleared on restart)
    2. persistent_memory — cross-session workflow summaries and learnings
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._session: dict[str, Any] = {}  # fast in-memory session store
        self._init_db()

    def _init_db(self) -> None:
        """Create DB and tables if not exist."""
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(_DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS persistent_memory (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_pattern TEXT,
                    delegation_type TEXT,
                    duration_s REAL,
                    verified INTEGER,
                    notes TEXT,
                    created_at REAL
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("[COLLAB_MEMORY] DB init failed (using in-memory only): %s", e)

    def write(self, key: str, value: Any, persist: bool = False) -> None:
        """Write to session memory (and optionally persist to DB)."""
        with self._lock:
            self._session[key] = value
            logger.info("[COLLAB_MEMORY_WRITE] key=%s persist=%s", key, persist)
            if persist:
                self._write_db(key, value)

    def read(self, key: str, default: Any = None) -> Any:
        """Read from session memory, falling back to persistent DB."""
        with self._lock:
            if key in self._session:
                logger.info("[COLLAB_MEMORY_HIT] key=%s source=session", key)
                return self._session[key]
        # Try DB
        val = self._read_db(key)
        if val is not None:
            logger.info("[COLLAB_MEMORY_HIT] key=%s source=db", key)
            return val
        logger.info("[COLLAB_MEMORY_MISS] key=%s", key)
        return default

    def _write_db(self, key: str, value: Any) -> None:
        """Persist to SQLite."""
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            now = time.time()
            conn.execute("""
                INSERT INTO persistent_memory (key, value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """, (key, json.dumps(value), now, now))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("[COLLAB_MEMORY] DB write failed: %s", e)

    def _read_db(self, key: str) -> Optional[Any]:
        """Read from SQLite."""
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            row = conn.execute(
                "SELECT value FROM persistent_memory WHERE key=?", (key,)
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception:
            pass
        return None

    def save_learning(
        self,
        goal_pattern: str,
        delegation_type: str,
        duration_s: float,
        verified: bool,
        notes: str = "",
    ) -> None:
        """Save a workflow learning for future reference."""
        logger.info(
            "[REFLECTION_LEARNING_SAVED] pattern=%r delegation=%s verified=%s",
            goal_pattern[:60],
            delegation_type,
            verified,
        )
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            conn.execute("""
                INSERT INTO workflow_learnings
                    (goal_pattern, delegation_type, duration_s, verified, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (goal_pattern, delegation_type, duration_s, int(verified), notes, time.time()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("[COLLAB_MEMORY] learning save failed: %s", e)

    def get_similar_workflow(self, goal: str) -> Optional[dict]:
        """Find a past successful workflow similar to this goal (simple keyword match)."""
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            rows = conn.execute("""
                SELECT goal_pattern, delegation_type, duration_s, verified, notes
                FROM workflow_learnings
                WHERE verified=1
                ORDER BY created_at DESC
                LIMIT 20
            """).fetchall()
            conn.close()
            goal_lower = goal.lower()
            for row in rows:
                pattern = (row[0] or "").lower()
                # Simple word overlap check
                pattern_words = set(pattern.split())
                goal_words = set(goal_lower.split())
                if len(pattern_words & goal_words) >= 2:
                    return {
                        "goal_pattern": row[0],
                        "delegation_type": row[1],
                        "duration_s": row[2],
                        "verified": bool(row[3]),
                        "notes": row[4],
                    }
        except Exception:
            pass
        return None

    def clear_session(self) -> None:
        """Clear ephemeral session data."""
        with self._lock:
            self._session.clear()

    def get_all_session(self) -> dict:
        """Return all session memory (for debugging)."""
        with self._lock:
            return dict(self._session)


collaboration_memory = CollaborationMemory()
