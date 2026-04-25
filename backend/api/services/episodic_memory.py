"""
SQLite-backed episodic memory — every conversation turn persists across restarts.

Complements the existing in-session MemoryService with:
  • Full history that survives server restarts
  • Tool-usage patterns for habit detection
  • Searchable conversation log
  • Human-readable recent-activity summaries
"""
from __future__ import annotations

import sqlite3
import time
import threading
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path.home() / ".ai-operator" / "episodes.db"


@dataclass
class Turn:
    session_id: str
    role: str            # 'user' | 'assistant'
    text: str
    tool_name: Optional[str]
    success: Optional[bool]
    timestamp: float


class EpisodicMemory:

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── DB setup ─────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS turns (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT    NOT NULL,
                    role        TEXT    NOT NULL CHECK(role IN ('user','assistant')),
                    text        TEXT    NOT NULL,
                    tool_name   TEXT,
                    success     INTEGER,
                    timestamp   REAL    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_session ON turns (session_id);
                CREATE INDEX IF NOT EXISTS idx_tool    ON turns (tool_name) WHERE tool_name IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_ts      ON turns (timestamp DESC);

                -- Usage pattern table: tracks what tool is used at what hour
                CREATE TABLE IF NOT EXISTS tool_patterns (
                    tool_name   TEXT    NOT NULL,
                    hour        INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
                    count       INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (tool_name, hour)
                );
            """)

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(
        self,
        session_id: str,
        role: str,
        text: str,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> None:
        """Persist a single conversation turn."""
        now = time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO turns (session_id, role, text, tool_name, success, timestamp) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, role, text, tool_name,
                 None if success is None else int(success), now),
            )
            if role == "user" and tool_name:
                hour = int(time.strftime("%H"))
                conn.execute("""
                    INSERT INTO tool_patterns (tool_name, hour, count) VALUES (?,?,1)
                    ON CONFLICT(tool_name, hour) DO UPDATE SET count = count + 1
                """, (tool_name, hour))

    # ── Read ──────────────────────────────────────────────────────────────────

    def recent(self, n: int = 20) -> list[Turn]:
        """Last N turns across all sessions, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM turns ORDER BY timestamp DESC LIMIT ?", (n,)
            ).fetchall()
        return self._rows_to_turns(rows)

    def session_history(self, session_id: str, n: int = 50) -> list[Turn]:
        """All turns for a specific session, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY timestamp DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        return self._rows_to_turns(rows)

    def update_last_tool(self, session_id: str, tool_name: str, success: bool = True) -> None:
        """Backfill tool_name onto the most recent user turn and update usage pattern."""
        hour = int(time.strftime("%H"))
        with self._lock, self._conn() as conn:
            conn.execute("""
                UPDATE turns SET tool_name=?, success=?
                WHERE id = (
                    SELECT id FROM turns
                    WHERE session_id=? AND role='user' AND tool_name IS NULL
                    ORDER BY timestamp DESC LIMIT 1
                )
            """, (tool_name, int(success), session_id))
            conn.execute("""
                INSERT INTO tool_patterns (tool_name, hour, count) VALUES (?,?,1)
                ON CONFLICT(tool_name, hour) DO UPDATE SET count = count + 1
            """, (tool_name, hour))

    def search(self, query: str, limit: int = 10) -> list[Turn]:
        """Case-insensitive full-text search across all turns."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM turns WHERE text LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return self._rows_to_turns(rows)

    # ── Patterns ──────────────────────────────────────────────────────────────

    def tool_frequency(self) -> dict[str, int]:
        """All-time count per tool, most used first."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT tool_name, SUM(count) as total
                FROM tool_patterns GROUP BY tool_name ORDER BY total DESC
            """).fetchall()
        return {r["tool_name"]: r["total"] for r in rows}

    def tools_at_hour(self, hour: int) -> dict[str, int]:
        """Which tools are typically used at a given hour of day."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT tool_name, count FROM tool_patterns WHERE hour=? ORDER BY count DESC",
                (hour,),
            ).fetchall()
        return {r["tool_name"]: r["count"] for r in rows}

    def top_tools_now(self, top_n: int = 3) -> list[str]:
        """Tools most likely to be used right now based on historical hour patterns."""
        hour = int(time.strftime("%H"))
        patterns = self.tools_at_hour(hour)
        return list(patterns.keys())[:top_n]

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary_since(self, hours: float = 24.0) -> str:
        """Human-readable activity summary for the last N hours."""
        cutoff = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT tool_name, COUNT(*) as cnt
                FROM turns
                WHERE timestamp > ? AND tool_name IS NOT NULL AND role='user'
                GROUP BY tool_name ORDER BY cnt DESC LIMIT 10
            """, (cutoff,)).fetchall()
        if not rows:
            return "No commands recorded in this period."
        parts = [f"{r['tool_name']} ({r['cnt']}×)" for r in rows]
        return "Recently used: " + ", ".join(parts)

    def conversation_context(self, session_id: str, n: int = 6) -> list[dict]:
        """Last N turns as {role, content} dicts — ready for GPT messages list."""
        turns = self.session_history(session_id, n)
        turns.reverse()  # chronological order
        return [{"role": t.role, "content": t.text} for t in turns]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _rows_to_turns(rows: list) -> list[Turn]:
        return [
            Turn(
                session_id=r["session_id"],
                role=r["role"],
                text=r["text"],
                tool_name=r["tool_name"],
                success=None if r["success"] is None else bool(r["success"]),
                timestamp=r["timestamp"],
            )
            for r in rows
        ]


# Singleton
episodic_memory = EpisodicMemory()
