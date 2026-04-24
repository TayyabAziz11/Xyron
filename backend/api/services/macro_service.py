"""
Macro Service — Feature #5
Voice macros: "when I say 'start my day', open VS Code, read my emails, check calendar".
Stores trigger phrases → action sequences in SQLite.
Detected in the voice pipeline BEFORE GPT — zero LLM overhead.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_DIR  = Path.home() / ".ai-operator"
_DB_PATH = _DB_DIR / "macros.db"

_CREATE_DDL = """
CREATE TABLE IF NOT EXISTS macros (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT    NOT NULL,
    steps       TEXT    NOT NULL,   -- JSON list of {tool, params, description}
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL,
    run_count   INTEGER NOT NULL DEFAULT 0
);
"""

# Built-in macros shipped by default
_DEFAULT_MACROS = [
    {
        "trigger": "start my day",
        "name":    "Morning Routine",
        "steps": [
            {"tool": "open_application", "params": {"app_name": "VS Code"},    "description": "Open VS Code"},
            {"tool": "read_inbox",        "params": {"max_results": 5},          "description": "Read emails"},
            {"tool": "list_events",       "params": {"days_ahead": 1},           "description": "Check today's calendar"},
        ],
    },
    {
        "trigger": "focus mode",
        "name":    "Focus Mode",
        "steps": [
            {"tool": "close_window",     "params": {},                           "description": "Close distractions"},
            {"tool": "open_application", "params": {"app_name": "VS Code"},     "description": "Open VS Code"},
        ],
    },
    {
        "trigger": "end my day",
        "name":    "End of Day",
        "steps": [
            {"tool": "list_events",  "params": {"days_ahead": 1},  "description": "Review tomorrow's calendar"},
            {"tool": "read_inbox",   "params": {"max_results": 3}, "description": "Check unread emails"},
        ],
    },
]


class MacroService:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_DDL)
        self._conn.commit()
        self._seed_defaults()
        logger.info("MacroService ready at %s", _DB_PATH)

    def _seed_defaults(self) -> None:
        import time
        for m in _DEFAULT_MACROS:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO macros(trigger, name, steps, created_at) VALUES (?,?,?,?)",
                    (m["trigger"], m["name"], json.dumps(m["steps"]), time.time()),
                )
            except Exception:
                pass
        self._conn.commit()

    # ── Matching ──────────────────────────────────────────────────────────────

    def match(self, text: str) -> Optional[dict]:
        """Return the first active macro whose trigger phrase appears in text."""
        lower = text.lower().strip()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM macros WHERE active=1 ORDER BY length(trigger) DESC"
            ).fetchall()
        for row in rows:
            if row["trigger"].lower() in lower:
                return dict(row)
        return None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(self, trigger: str, name: str, steps: list[dict]) -> dict:
        import time
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO macros(trigger, name, steps, created_at) VALUES (?,?,?,?)",
                (trigger.lower().strip(), name, json.dumps(steps), time.time()),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM macros WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        return self._row_to_dict(row)

    def list_all(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM macros ORDER BY created_at DESC").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete(self, macro_id: int) -> bool:
        with self._lock:
            c = self._conn.execute("DELETE FROM macros WHERE id=?", (macro_id,))
            self._conn.commit()
        return c.rowcount > 0

    def toggle(self, macro_id: int, active: bool) -> bool:
        with self._lock:
            c = self._conn.execute(
                "UPDATE macros SET active=? WHERE id=?", (1 if active else 0, macro_id)
            )
            self._conn.commit()
        return c.rowcount > 0

    def increment_run(self, macro_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE macros SET run_count = run_count + 1 WHERE id=?", (macro_id,)
            )
            self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["steps"] = json.loads(d["steps"])
        except Exception:
            d["steps"] = []
        return d


macro_service = MacroService()
