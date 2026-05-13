"""
Goal tracking — persistent goal store backed by SQLite.

  goal_tracker.set_goal(description, priority=3, deadline=None) → Goal
  goal_tracker.complete_goal(goal_id)                           → None
  goal_tracker.get_active_goals()                               → list[Goal]
  goal_tracker.prioritize()                                     → Goal | None
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".ai-operator" / "goals.db"


@dataclass
class Goal:
    id:          str
    description: str
    created_at:  float
    deadline:    Optional[float]
    priority:    int             # 1 (low) – 5 (urgent)
    status:      str             # "active" | "completed" | "cancelled"
    sub_goals:   list[str]       # child goal ids


class GoalTracker:

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id          TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    deadline    REAL,
                    priority    INTEGER NOT NULL DEFAULT 3,
                    status      TEXT NOT NULL DEFAULT 'active',
                    sub_goals   TEXT NOT NULL DEFAULT '[]'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON goals (status)")

    @staticmethod
    def _row_to_goal(row: sqlite3.Row) -> Goal:
        return Goal(
            id=row["id"],
            description=row["description"],
            created_at=row["created_at"],
            deadline=row["deadline"],
            priority=row["priority"],
            status=row["status"],
            sub_goals=json.loads(row["sub_goals"] or "[]"),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def set_goal(
        self,
        description: str,
        priority:    int            = 3,
        deadline:    float | None   = None,
    ) -> Goal:
        """Create and persist a new active goal."""
        goal = Goal(
            id=str(uuid.uuid4()),
            description=description.strip(),
            created_at=time.time(),
            deadline=deadline,
            priority=max(1, min(5, priority)),
            status="active",
            sub_goals=[],
        )
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO goals (id, description, created_at, deadline, priority, status, sub_goals) "
                "VALUES (?,?,?,?,?,?,?)",
                (goal.id, goal.description, goal.created_at, goal.deadline,
                 goal.priority, goal.status, json.dumps(goal.sub_goals)),
            )
        logger.info("[GoalTracker] set goal id=%s priority=%d %r", goal.id, goal.priority, description[:60])
        return goal

    def complete_goal(self, goal_id: str) -> None:
        """Mark a goal as completed."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE goals SET status='completed' WHERE id=?", (goal_id,)
            )
        logger.info("[GoalTracker] completed goal id=%s", goal_id)

    def cancel_goal(self, goal_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE goals SET status='cancelled' WHERE id=?", (goal_id,)
            )

    def get_active_goals(self) -> list[Goal]:
        """Return all active goals ordered by priority desc, then created_at asc."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status='active' ORDER BY priority DESC, created_at ASC"
            ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
        return self._row_to_goal(row) if row else None

    def prioritize(self) -> Optional[Goal]:
        """Return the highest-priority active goal, or None if no active goals."""
        goals = self.get_active_goals()
        return goals[0] if goals else None


goal_tracker = GoalTracker()
