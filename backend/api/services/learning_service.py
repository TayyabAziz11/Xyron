"""
Learning Service — detects repeated command patterns and proposes workflow procedures.

After N repetitions of the same tool/intent pattern, the service:
  1. Flags the pattern as "learnable"
  2. Returns a suggestion string for Xyron to speak to the user
  3. On user confirmation (handled by caller), saves a named procedure

Storage: SQLite at ~/.ai-operator/learning.db
Tables:
  command_log   — every command with tool + transcript + timestamp
  procedures    — saved named procedures (name, trigger_keywords, steps)

Performance: all writes are synchronous but fast (SQLite local); reads < 10ms.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH       = Path.home() / ".ai-operator" / "learning.db"
_REPEAT_THRESH = 3     # suggest after this many repetitions
_WINDOW_HOURS  = 168   # look back 7 days for repeated patterns


@dataclass
class LearningResult:
    has_suggestion: bool
    suggestion: str = ""
    pattern: str = ""
    count: int = 0


class LearningService:

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS command_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    tool_name TEXT    NOT NULL,
                    pattern   TEXT    NOT NULL,
                    transcript TEXT   NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS procedures (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    name      TEXT    NOT NULL UNIQUE,
                    triggers  TEXT    NOT NULL DEFAULT '[]',
                    steps     TEXT    NOT NULL DEFAULT '[]',
                    created   REAL    NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tool ON command_log (tool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pattern ON command_log (pattern)")

    # ── Pattern normalisation ─────────────────────────────────────────────────

    @staticmethod
    def _to_pattern(tool_name: str, transcript: str) -> str:
        """
        Reduce a transcript to a canonical pattern for repetition detection.
        Strips specific file/folder names, preserving the verb + tool intent.
        """
        import re
        t = transcript.lower().strip()
        # Remove specific file/folder names (anything after open/close/play)
        t = re.sub(r'\b(open|play|launch|start|close|run|execute|install|download|get)\s+[\w\s\-\.]{3,40}',
                   lambda m: m.group(1) + " [target]", t)
        # Keep it short
        t = t[:60]
        return f"{tool_name}::{t}"

    # ── Public API ─────────────────────────────────────────────────────────────

    def record(self, transcript: str, tool_name: str) -> LearningResult:
        """
        Record a command execution. Returns a LearningResult with suggestion
        if this pattern has been repeated _REPEAT_THRESH or more times.
        """
        if not tool_name or not transcript:
            return LearningResult(has_suggestion=False)

        pattern  = self._to_pattern(tool_name, transcript)
        cutoff   = time.time() - _WINDOW_HOURS * 3600

        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO command_log (ts, tool_name, pattern, transcript) VALUES (?,?,?,?)",
                (time.time(), tool_name, pattern, transcript[:200]),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM command_log WHERE pattern=? AND ts>?",
                (pattern, cutoff),
            ).fetchone()[0]

        logger.debug("[LEARNING_PATTERN_DETECTED] tool=%s pattern=%r count=%d",
                     tool_name, pattern[:50], count)

        if count >= _REPEAT_THRESH:
            # Check if a procedure already covers this
            if self._has_procedure_for(pattern):
                return LearningResult(has_suggestion=False)
            suggestion = self._build_suggestion(tool_name, transcript, count)
            logger.info("[LEARNING_SUGGESTION] tool=%s count=%d suggestion=%r",
                        tool_name, count, suggestion[:80])
            return LearningResult(has_suggestion=True, suggestion=suggestion,
                                  pattern=pattern, count=count)

        return LearningResult(has_suggestion=False)

    def save_procedure(self, name: str, steps: list[str], triggers: list[str]) -> None:
        """Save a named procedure after user confirms learning."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO procedures (name, triggers, steps, created) VALUES (?,?,?,?)",
                (name.strip(), json.dumps(triggers), json.dumps(steps), time.time()),
            )
        logger.info("[LEARNING_SAVED] name=%r steps=%d triggers=%s", name, len(steps), triggers)

    def get_procedure(self, name: str) -> Optional[dict]:
        """Look up a saved procedure by name."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM procedures WHERE name=?", (name.strip(),)
            ).fetchone()
        if not row:
            return None
        return {
            "name":     row["name"],
            "triggers": json.loads(row["triggers"] or "[]"),
            "steps":    json.loads(row["steps"] or "[]"),
        }

    def find_procedure(self, transcript: str) -> Optional[dict]:
        """Return the first procedure whose trigger keywords appear in transcript."""
        t_low = transcript.lower()
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM procedures").fetchall()
        for row in rows:
            triggers = json.loads(row["triggers"] or "[]")
            if any(kw.lower() in t_low for kw in triggers):
                return {
                    "name":     row["name"],
                    "triggers": triggers,
                    "steps":    json.loads(row["steps"] or "[]"),
                }
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _has_procedure_for(self, pattern: str) -> bool:
        tool = pattern.split("::")[0]
        with self._conn() as conn:
            rows = conn.execute("SELECT triggers FROM procedures").fetchall()
        for row in rows:
            if tool in (row["triggers"] or ""):
                return True
        return False

    def _build_suggestion(self, tool_name: str, transcript: str, count: int) -> str:
        # Build a human-readable suggestion to speak
        import re
        # Extract target from transcript for friendlier suggestion
        m = re.search(
            r'\b(?:open|play|launch|start)\s+([\w\s\-\.]{3,30})',
            transcript, re.IGNORECASE,
        )
        target = m.group(1).strip().title() if m else transcript[:30]
        return (
            f"I've noticed you do this {count} times. "
            f"Should I remember '{target}' as a shortcut?"
        )


learning_service = LearningService()
