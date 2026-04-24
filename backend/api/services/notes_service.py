"""
Notes Service — Feature #10
Timestamped voice notes with semantic search via sentence embeddings.
Embeddings are stored as JSON blobs in SQLite (no extra deps required).
Falls back to keyword search if openai is unavailable.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_DIR  = Path.home() / ".ai-operator"
_DB_PATH = _DB_DIR / "notes.db"

_CREATE_DDL = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    date_str   TEXT    NOT NULL,
    time_str   TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    embedding  TEXT,               -- JSON float array, nullable
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_ts ON notes(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    text, content='notes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot  = sum(x * y for x, y in zip(a, b))
    na   = math.sqrt(sum(x * x for x in a))
    nb   = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed(text: str, openai_key: str) -> Optional[list[float]]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        resp = client.embeddings.create(model="text-embedding-3-small", input=text)
        return resp.data[0].embedding
    except Exception as exc:
        logger.debug("Embedding failed: %s", exc)
        return None


class NotesService:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_DDL)
        self._conn.commit()
        logger.info("NotesService ready at %s", _DB_PATH)

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(self, text: str, openai_key: str = "", session_id: str = "") -> dict:
        """Store a note. Embedding computed async if openai_key provided."""
        now   = time.time()
        from datetime import datetime
        dt    = datetime.fromtimestamp(now)
        ds    = dt.strftime("%Y-%m-%d")
        ts_s  = dt.strftime("%H:%M")
        emb   = None
        if openai_key:
            emb = _embed(text, openai_key)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO notes(ts, date_str, time_str, text, embedding, session_id) VALUES (?,?,?,?,?,?)",
                (now, ds, ts_s, text, json.dumps(emb) if emb else None, session_id or None),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._row_to_dict(row)

    # ── Search ────────────────────────────────────────────────────────────────

    def semantic_search(self, query: str, openai_key: str, limit: int = 5) -> list[dict]:
        """Cosine similarity search over stored embeddings, fallback to FTS."""
        q_emb = _embed(query, openai_key) if openai_key else None
        if q_emb:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM notes WHERE embedding IS NOT NULL ORDER BY ts DESC LIMIT 200"
                ).fetchall()
            scored = []
            for row in rows:
                try:
                    emb = json.loads(row["embedding"])
                    score = _cosine(q_emb, emb)
                    d = self._row_to_dict(row)
                    d["score"] = score
                    scored.append(d)
                except Exception:
                    pass
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]
        # Fallback: FTS keyword search
        return self.keyword_search(query, limit)

    def keyword_search(self, query: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT n.* FROM notes n "
                "JOIN notes_fts f ON n.id = f.rowid "
                "WHERE notes_fts MATCH ? ORDER BY n.ts DESC LIMIT ?",
                (query, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notes ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete(self, note_id: int) -> bool:
        with self._lock:
            c = self._conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
            self._conn.commit()
        return c.rowcount > 0

    def summarize_search_for_speech(self, results: list[dict]) -> str:
        if not results:
            return "I couldn't find any notes matching that."
        parts = [f"Note from {r['date_str']} at {r['time_str']}: {r['text']}" for r in results[:3]]
        return ". ".join(parts)

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d.pop("embedding", None)  # don't send raw embedding to frontend
        return d


notes_service = NotesService()
