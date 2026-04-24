"""
History Service — Feature #2
Stores every voice command + result in a local SQLite database.
Supports full-text retrieval by date range or keyword.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_DIR  = Path.home() / ".ai-operator"
_DB_PATH = _DB_DIR / "history.db"

_CREATE_DDL = """
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,           -- Unix timestamp
    date_str   TEXT    NOT NULL,           -- YYYY-MM-DD (local)
    time_str   TEXT    NOT NULL,           -- HH:MM
    command    TEXT    NOT NULL,
    tool_used  TEXT,
    result     TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts);
CREATE INDEX IF NOT EXISTS idx_history_date ON history(date_str);
CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
    command, result, content='history', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS history_ai AFTER INSERT ON history BEGIN
    INSERT INTO history_fts(rowid, command, result) VALUES (new.id, new.command, new.result);
END;
"""


class HistoryService:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_DDL)
        self._conn.commit()
        logger.info("HistoryService ready at %s", _DB_PATH)

    def log(
        self,
        command: str,
        result: str = "",
        tool_used: str = "",
        session_id: str = "",
    ) -> int:
        """Insert a new history entry. Returns the inserted row id."""
        now    = datetime.now()
        ts     = now.timestamp()
        ds     = now.strftime("%Y-%m-%d")
        ts_str = now.strftime("%H:%M")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history(ts, date_str, time_str, command, tool_used, result, session_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (ts, ds, ts_str, command, tool_used or None, result or None, session_id or None),
            )
            self._conn.commit()
            return cur.lastrowid

    def query_by_date(self, date_str: str) -> list[dict]:
        """Return all entries for a given date (YYYY-MM-DD)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM history WHERE date_str = ? ORDER BY ts",
                (date_str,),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_keyword(self, keyword: str, limit: int = 20) -> list[dict]:
        """Full-text search over commands + results."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT h.* FROM history h "
                "JOIN history_fts f ON h.id = f.rowid "
                "WHERE history_fts MATCH ? "
                "ORDER BY h.ts DESC LIMIT ?",
                (keyword, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def query_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM history ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def summarize_for_speech(self, date_str: str) -> str:
        """Return a spoken summary of commands on a given date."""
        entries = self.query_by_date(date_str)
        if not entries:
            return f"I don't have any recorded activity for {date_str}."
        lines = [f"At {e['time_str']}: {e['command']}" for e in entries[:10]]
        summary = ". ".join(lines)
        total = len(entries)
        if total > 10:
            summary += f". And {total - 10} more commands."
        return summary

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


history_service = HistoryService()
