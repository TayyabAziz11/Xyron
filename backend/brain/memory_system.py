"""
Brain Memory System — 5 memory types with SQLite + ChromaDB storage.

Memory types:
  episodic     — what happened, when, with what outcome
  semantic     — facts, knowledge, concepts
  procedural   — how to do things, successful workflows
  relationship — user corrections, preferences, patterns
  project      — milestones, goals, active work

Only meaningful events are stored.
Everything is tagged with importance 0.0–1.0.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

MemoryType = Literal["episodic", "semantic", "procedural", "relationship", "project"]

_DB_PATH    = Path(__file__).parent.parent / "data" / "brain" / "memory.db"
_CHROMA_PATH = Path(__file__).parent.parent / "data" / "chroma"
_MIN_IMPORTANCE = 0.40  # ignore low-importance events


# ── Memory record ─────────────────────────────────────────────────────────────

@dataclass
class MemoryRecord:
    id:           str
    type:         MemoryType
    text:         str
    entities:     dict[str, Any]
    importance:   float            # 0.0–1.0
    created_at:   str
    last_accessed: str
    source:       str              # "voice" | "tool" | "agent" | "system"
    embedding_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_row(row: tuple) -> "MemoryRecord":
        return MemoryRecord(
            id=row[0], type=row[1], text=row[2],
            entities=json.loads(row[3] or "{}"),
            importance=row[4], created_at=row[5],
            last_accessed=row[6], source=row[7],
            embedding_id=row[8] if len(row) > 8 else None,
        )


# ── SQLite store ──────────────────────────────────────────────────────────────

class _SQLiteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    entities TEXT DEFAULT '{}',
                    importance REAL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    source TEXT DEFAULT 'system',
                    embedding_id TEXT
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON memories (type)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_importance ON memories (importance)")
            self._conn.commit()

    def insert(self, rec: MemoryRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?,?,?,?,?)",
                (rec.id, rec.type, rec.text, json.dumps(rec.entities),
                 rec.importance, rec.created_at, rec.last_accessed, rec.source, rec.embedding_id),
            )
            self._conn.commit()

    def recent(self, n: int = 20, mem_type: MemoryType | None = None) -> list[MemoryRecord]:
        with self._lock:
            if mem_type:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE type=? ORDER BY last_accessed DESC LIMIT ?",
                    (mem_type, n),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memories ORDER BY last_accessed DESC LIMIT ?", (n,)
                ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def search_text(self, query: str, n: int = 10) -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE text LIKE ? ORDER BY importance DESC LIMIT ?",
                (f"%{query}%", n),
            ).fetchall()
        return [MemoryRecord.from_row(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def touch(self, mem_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET last_accessed=? WHERE id=?",
                (datetime.now().isoformat(), mem_id),
            )
            self._conn.commit()


# ── ChromaDB semantic store ───────────────────────────────────────────────────

class _ChromaSemanticStore:
    def __init__(self) -> None:
        self._col   = None
        self._lock  = threading.Lock()

    def _get_col(self):
        with self._lock:
            if self._col is not None:
                return self._col
            try:
                import chromadb
                _CHROMA_PATH.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
                self._col = client.get_or_create_collection(
                    name="brain_memory",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as exc:
                logger.debug("[MEMORY] ChromaDB unavailable: %s", exc)
            return self._col

    def _embed(self, text: str) -> Optional[list[float]]:
        try:
            import urllib.request
            payload = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read())["embedding"]
        except Exception:
            return None

    def store(self, rec: MemoryRecord) -> Optional[str]:
        col = self._get_col()
        if col is None:
            return None
        vec = self._embed(rec.text)
        if vec is None:
            return None
        try:
            col.add(
                ids=[rec.id],
                embeddings=[vec],
                documents=[rec.text],
                metadatas=[{"type": rec.type, "importance": rec.importance, "source": rec.source}],
            )
            return rec.id
        except Exception as exc:
            logger.debug("[MEMORY] chroma store error: %s", exc)
            return None

    def search(self, query: str, n: int = 5) -> list[str]:
        col = self._get_col()
        if col is None or col.count() == 0:
            return []
        vec = self._embed(query)
        if vec is None:
            return []
        try:
            results = col.query(query_embeddings=[vec], n_results=n)
            return results["ids"][0] if results["ids"] else []
        except Exception:
            return []


# ── Public API ─────────────────────────────────────────────────────────────────

class BrainMemorySystem:
    """
    Unified memory system for the Xyron brain.

    Importance filter: only stores events with importance >= 0.40.
    Semantic search: ChromaDB + nomic-embed-text (falls back to SQLite text search).
    """

    def __init__(self) -> None:
        self._sql    = _SQLiteStore(_DB_PATH)
        self._chroma = _ChromaSemanticStore()

    def store(
        self,
        text:       str,
        mem_type:   MemoryType = "episodic",
        importance: float = 0.5,
        source:     str = "voice",
        entities:   dict | None = None,
    ) -> Optional[str]:
        """
        Store a memory record. Returns the record ID or None if filtered out.
        """
        if importance < _MIN_IMPORTANCE:
            return None
        if not text.strip():
            return None

        now = datetime.now().isoformat()
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            type=mem_type,
            text=text.strip(),
            entities=entities or {},
            importance=importance,
            created_at=now,
            last_accessed=now,
            source=source,
        )

        self._sql.insert(rec)

        # Store embedding async-style (non-blocking; embeddings are nice-to-have)
        import threading
        def _async_embed():
            eid = self._chroma.store(rec)
            if eid:
                rec.embedding_id = eid
                self._sql.insert(rec)  # update with embedding ID
        threading.Thread(target=_async_embed, daemon=True).start()

        logger.info("[MEMORY] stored type=%s importance=%.2f text=%r", mem_type, importance, text[:60])
        return rec.id

    def search(self, query: str, n: int = 5) -> list[MemoryRecord]:
        """Semantic search using ChromaDB; fallback to text search."""
        ids = self._chroma.search(query, n=n)
        if ids:
            results = []
            for mid in ids:
                hits = self._sql.search_text(mid, n=1)
                if hits:
                    results.extend(hits)
            if results:
                return results

        return self._sql.search_text(query, n=n)

    def recent(self, n: int = 20, mem_type: MemoryType | None = None) -> list[MemoryRecord]:
        return self._sql.recent(n=n, mem_type=mem_type)

    def count(self) -> int:
        return self._sql.count()

    def record_preference(self, key: str, value: str, source: str = "voice") -> None:
        self.store(
            f"User prefers: {key} = {value}",
            mem_type="relationship",
            importance=0.75,
            source=source,
            entities={"key": key, "value": value},
        )

    def record_workflow(self, name: str, steps: list[str], source: str = "agent") -> None:
        self.store(
            f"Successful workflow '{name}': {'; '.join(steps)}",
            mem_type="procedural",
            importance=0.80,
            source=source,
            entities={"name": name, "steps": steps},
        )

    def record_milestone(self, milestone: str, source: str = "system") -> None:
        self.store(
            milestone,
            mem_type="project",
            importance=0.85,
            source=source,
        )

    def record_upgrade(self, upgrade_desc: str) -> None:
        self.store(
            f"System upgrade: {upgrade_desc}",
            mem_type="project",
            importance=0.90,
            source="system",
            entities={"type": "upgrade"},
        )


brain_memory = BrainMemorySystem()
