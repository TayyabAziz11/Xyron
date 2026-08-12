"""
fs_index.py — File System Index Service for Xyron

Builds and maintains a fast SQLite index of all files/folders on the system
so that smart_open can find things in <5ms instead of running `find` (8s+).

DB location : ~/.ai-operator/fs_index.db
Rebuild cadence: on startup (30 s delay) + every 6 hours thereafter
Thread safety : threading.Lock guards all DB writes; per-thread connections
                via module-level threading.local avoid cross-thread sharing.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".ai-operator"
DB_PATH = DB_DIR / "fs_index.db"

# Build scan roots from env var or fall back to sensible defaults.
# On WSL2, Windows drives appear as /mnt/d, /mnt/e etc — include only those that exist.
# On native Linux/macOS, only ~/ is used unless FS_SCAN_ROOTS overrides.

def _is_readable(p: Path) -> bool:
    """Return True only for accessible directories — filters dead WSL mounts."""
    try:
        return p.is_dir() and bool(os.listdir(str(p)))
    except OSError:
        return False


def _detect_win_user_home() -> "Path | None":
    """Return Windows user home (e.g. /mnt/c/Users/muham) via %USERPROFILE%."""
    try:
        r = subprocess.run(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo", "%USERPROFILE%"],
            capture_output=True, text=True, timeout=3,
        )
        raw = r.stdout.strip()
        if raw and "%" not in raw:
            # raw = "C:\Users\muham" → /mnt/c/Users/muham
            wsl = "/mnt/" + raw[0].lower() + "/" + raw[3:].replace("\\", "/")
            p = Path(wsl)
            if p.is_dir():
                return p
    except Exception:
        pass
    return None


def _discover_all_drives() -> List[Path]:
    """
    Dynamically detect every mounted Windows drive on WSL2.

    Scans /mnt/<letter> for all single-letter directories — covers
    drives A–Z regardless of which letters the user actually has mounted.
    Falls back to ~/.  Logs [DRIVE_DISCOVERY], [DRIVE_FOUND], [DRIVE_UNAVAILABLE].
    """
    found: List[Path] = []
    logger.info("[DRIVE_DISCOVERY] scanning /mnt/ for mounted drives")
    try:
        mnt = Path("/mnt")
        candidates = sorted(
            d for d in mnt.iterdir()
            if d.is_dir() and len(d.name) == 1 and d.name.isalpha()
        )
        for d in candidates:
            if _is_readable(d):
                found.append(d)
                logger.info("[DRIVE_FOUND] drive=%s path=%s", d.name.upper(), d)
            else:
                logger.debug("[DRIVE_UNAVAILABLE] drive=%s path=%s (empty or dead mount)", d.name.upper(), d)
    except Exception as exc:
        logger.warning("[DRIVE_DISCOVERY] /mnt/ scan failed: %s — falling back to known letters", exc)
        for letter in "cdefghij":
            p = Path(f"/mnt/{letter}")
            if _is_readable(p):
                found.append(p)
                logger.info("[DRIVE_FOUND] drive=%s path=%s (fallback)", letter.upper(), p)
    return found


_env_roots = os.getenv("FS_SCAN_ROOTS", "")
if _env_roots:
    SCAN_ROOTS: List[Path] = [Path(p.strip()) for p in _env_roots.split(",") if p.strip()]
else:
    _win_home = _detect_win_user_home()
    SCAN_ROOTS = _discover_all_drives()
    if _win_home and _win_home not in SCAN_ROOTS:
        SCAN_ROOTS.append(_win_home)
    if not SCAN_ROOTS:
        SCAN_ROOTS.append(Path.home())

PRUNE_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".next",
    ".venv",
    "venv",
    "AppData",
    "Windows",
    "ProgramData",
    "$RECYCLE.BIN",
    "System Volume Information",
}

# ---------------------------------------------------------------------------
# Semantic content roots (Phase 1 — System Intelligence)
#
# Content extraction + embeddings are expensive (parse + GPU encode), so we
# scope them to folders that actually hold user work instead of every file
# on every drive. Plain filename indexing above still covers the whole disk.
# ---------------------------------------------------------------------------

_CONTENT_FOLDER_NAMES = {
    "desktop", "documents", "downloads", "pictures", "videos", "music",
    "onedrive", "google drive",
}


def _discover_semantic_roots() -> List[Path]:
    """User-content folders under every scan root's home-equivalent dir."""
    roots: List[Path] = []
    candidates = [Path.home()]
    win_home = _detect_win_user_home()
    if win_home:
        candidates.append(win_home)
    for home in candidates:
        try:
            for child in home.iterdir():
                if child.is_dir() and child.name.lower() in _CONTENT_FOLDER_NAMES:
                    roots.append(child)
        except OSError:
            continue
    # Xyron's own repo is the active dev workspace — always content-worthy.
    try:
        from api.config import settings as _settings
        if _settings.repo_root.is_dir():
            roots.append(_settings.repo_root)
    except Exception:
        pass
    return roots


SEMANTIC_ROOTS: List[Path] = _discover_semantic_roots()


def is_content_worthy(path: Path) -> bool:
    """True if *path* sits under a semantic root or inside a git repo/VS Code workspace."""
    try:
        for root in SEMANTIC_ROOTS:
            if path == root or root in path.parents:
                return True
        # Any ancestor containing a .git dir marks the subtree as a project.
        for parent in path.parents:
            if (parent / ".git").is_dir():
                return True
    except OSError:
        pass
    return False


REBUILD_INTERVAL_SECONDS = 6 * 3600  # 6 hours
STARTUP_DELAY_SECONDS = 5            # wait before first build on startup

# Base table DDL — no indexes yet (indexes are created after migrations).
_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS entries (
    id             INTEGER PRIMARY KEY,
    path           TEXT    UNIQUE NOT NULL,
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL CHECK(type IN ('file', 'folder')),
    drive          TEXT    NOT NULL,
    size           INTEGER NOT NULL DEFAULT 0,
    indexed_at     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_name ON entries (name);
CREATE INDEX IF NOT EXISTS idx_entries_type ON entries (type);
"""

# Column migrations — always safe to run (errors = column already exists).
_MIGRATIONS = [
    "ALTER TABLE entries ADD COLUMN lowercase_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE entries ADD COLUMN modified_time  REAL NOT NULL DEFAULT 0",
    "ALTER TABLE entries ADD COLUMN accessible     INTEGER NOT NULL DEFAULT 1",
    # Phase 1 — semantic filesystem intelligence
    "ALTER TABLE entries ADD COLUMN content_hash   TEXT    NOT NULL DEFAULT ''",
    "ALTER TABLE entries ADD COLUMN keywords       TEXT    NOT NULL DEFAULT ''",
    "ALTER TABLE entries ADD COLUMN has_content    INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE entries ADD COLUMN last_opened    REAL    NOT NULL DEFAULT 0",
    "ALTER TABLE entries ADD COLUMN open_count     INTEGER NOT NULL DEFAULT 0",
]

# Index DDL — created AFTER migrations so all columns definitely exist.
_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_entries_lowercase_name ON entries (lowercase_name);
CREATE INDEX IF NOT EXISTS idx_entries_drive         ON entries (drive);
CREATE INDEX IF NOT EXISTS idx_entries_has_content   ON entries (has_content);
CREATE INDEX IF NOT EXISTS idx_entries_last_opened   ON entries (last_opened);
CREATE INDEX IF NOT EXISTS idx_entries_open_count    ON entries (open_count);
"""

# Phase 1.5 — Context-Aware Filesystem: usage learning tables.
# open_events feeds time-of-day/weekday/folder/app/project affinity scoring.
# learned_resolutions is the "tier 0" — an exact (query -> path) pairing the
# user has repeatedly confirmed, promoted above every context tier.
_AUX_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS open_events (
    id             INTEGER PRIMARY KEY,
    entry_id       INTEGER NOT NULL,
    ts             REAL    NOT NULL,
    hour           INTEGER NOT NULL,
    weekday        INTEGER NOT NULL,
    active_app     TEXT    NOT NULL DEFAULT '',
    active_folder  TEXT    NOT NULL DEFAULT '',
    active_project TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_open_events_entry ON open_events (entry_id);
CREATE INDEX IF NOT EXISTS idx_open_events_ts    ON open_events (ts);

CREATE TABLE IF NOT EXISTS learned_resolutions (
    query_norm  TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 1,
    last_used   REAL    NOT NULL,
    PRIMARY KEY (query_norm, path)
);
"""

# Legacy alias so existing callers that reference DDL still work.
DDL = _TABLE_DDL

# Module-level thread-local storage for SQLite connections.
# Each OS thread gets its own sqlite3.Connection so we never share
# connections across threads (SQLite connections are not thread-safe).
_tls = threading.local()


# ---------------------------------------------------------------------------
# Module-level connection helper
# ---------------------------------------------------------------------------

def _get_thread_conn(db_path: Path) -> sqlite3.Connection:
    """
    Return a SQLite connection that belongs to the *calling thread*.

    The connection is created once per thread and reused thereafter.
    WAL mode and NORMAL synchronous are set for better concurrent performance.
    """
    conn: Optional[sqlite3.Connection] = getattr(_tls, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _tls.conn = conn
    return conn


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _drive_for(path: Path) -> str:
    """Return a short drive label (e.g. '/mnt/e', '~') for a given path."""
    try:
        home = Path.home()
        if path == home or home in path.parents:
            return "~"
    except Exception:
        pass
    # For /mnt/x paths return the mount point up to depth 2
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt":
        return f"/{parts[1]}/{parts[2]}"
    return parts[0] if parts else "/"


def _quick_hash(path: Path) -> Optional[str]:
    """
    Cheap change-detection fingerprint (size:mtime) — avoids re-reading and
    re-embedding file content that hasn't changed since the last pass.
    Not cryptographic; only used to short-circuit unnecessary re-extraction.
    """
    try:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime}"
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class FSIndex:
    """Singleton file-system index backed by SQLite."""

    def __init__(self) -> None:
        self._lock = threading.Lock()       # guards all DB writes
        self._ready = threading.Event()     # set after first build
        self._db_path = DB_PATH
        self._ensure_db_dir()
        self._init_db()
        self._start_background_thread()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True once the first index build has completed."""
        return self._ready.is_set()

    def search(
        self,
        query: str,
        type_filter: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 5,
    ) -> List[Path]:
        """
        Case-insensitive substring search across all indexed paths.

        Parameters
        ----------
        query       : substring to search for in the *name* column
        type_filter : 'file', 'folder', or None (both)
        drive       : single drive letter ('E', 'D', etc.) to restrict search
        limit       : max results to return

        Returns
        -------
        List of Path objects, ordered by path length (shorter = shallower).
        Logs [FS_INDEX_HIT] on match or [FS_INDEX_MISS] on empty result.
        """
        if not query:
            return []

        # Use lowercase_name index when possible (avoids COLLATE NOCASE full-scan).
        name_pattern = f"%{query.lower()}%"
        conditions = ["(lowercase_name LIKE ? OR name LIKE ? COLLATE NOCASE)"]
        params: list = [name_pattern, f"%{query}%"]

        if type_filter in ("file", "folder"):
            conditions.append("type = ?")
            params.append(type_filter)

        if drive:
            drive_prefix = f"/mnt/{drive.lower()}/%"
            conditions.append("(path LIKE ? OR drive = ?)")
            params.extend([drive_prefix, f"/mnt/{drive.lower()}"])

        where = " AND ".join(conditions)
        sql = (
            f"SELECT path FROM entries WHERE {where} "
            f"ORDER BY length(path) LIMIT {int(limit)}"
        )

        try:
            conn = _get_thread_conn(self._db_path)
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            results = [Path(row[0]) for row in rows]
            if results:
                logger.debug("[FS_INDEX_HIT] query=%r drive=%s type=%s results=%d first=%s",
                             query, drive or "any", type_filter or "any", len(results), results[0])
            else:
                logger.debug("[FS_INDEX_MISS] query=%r drive=%s type=%s",
                             query, drive or "any", type_filter or "any")
            return results
        except sqlite3.Error as exc:
            logger.error("fs_index search error: %s", exc)
            return []

    def search_fuzzy(
        self,
        query: str,
        type_filter: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 5,
    ) -> List[Path]:
        """
        Try exact substring first; fall back to fuzzy matching via difflib.

        Returns list of (path, score) where score is 0–1 similarity.
        """
        exact = self.search(query, type_filter=type_filter, drive=drive, limit=limit)
        if exact:
            return exact

        # Fuzzy fallback — pull candidate names and score them
        conditions = ["1=1"]
        params: list = []
        if type_filter in ("file", "folder"):
            conditions.append("type = ?")
            params.append(type_filter)
        if drive:
            conditions.append("(path LIKE ? OR drive = ?)")
            params.extend([f"/mnt/{drive.lower()}/%", f"/mnt/{drive.lower()}"])
        where = " AND ".join(conditions)
        sql = f"SELECT path, name FROM entries WHERE {where} ORDER BY length(path) LIMIT 50000"

        try:
            conn = _get_thread_conn(self._db_path)
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []

        q_lower = query.lower()
        scored: list[tuple[float, str]] = []

        try:
            from rapidfuzz import fuzz as _rfuzz
            for path_str, name in rows:
                name_l = name.lower()
                if q_lower in name_l:
                    score = 1.0
                else:
                    # Combine token_set_ratio (handles word reordering) with partial_ratio
                    s1 = _rfuzz.token_set_ratio(q_lower, name_l) / 100.0
                    s2 = _rfuzz.partial_ratio(q_lower, name_l) / 100.0
                    score = max(s1, s2)
                if score >= 0.55:
                    scored.append((score, path_str))
            logger.debug("[FS_RANK] rapidfuzz scored %d candidates for query=%r", len(scored), query)
        except ImportError:
            import difflib
            for path_str, name in rows:
                name_l = name.lower()
                if q_lower in name_l:
                    score = 1.0
                else:
                    score = difflib.SequenceMatcher(None, q_lower, name_l).ratio()
                if score >= 0.6:
                    scored.append((score, path_str))

        scored.sort(key=lambda x: (-x[0], len(x[1])))
        results = [Path(p) for _, p in scored[:limit]]
        if results:
            logger.info("[FS_MATCH] query=%r top=%s score=%.2f",
                        query, results[0].name, scored[0][0] if scored else 0)
            logger.debug("[FS_INDEX_FUZZY_HIT] query=%r results=%d", query, len(results))
        else:
            logger.debug("[FS_INDEX_FUZZY_MISS] query=%r", query)
        return results

    def get_discovered_drives(self) -> List[str]:
        """Return list of drive letters that were indexed (e.g. ['D', 'E'])."""
        drives: List[str] = []
        for root in SCAN_ROOTS:
            parts = root.parts
            if len(parts) >= 3 and parts[1] == "mnt":
                drives.append(parts[2].upper())
        return drives

    def search_ranked(
        self,
        query: str,
        type_filter: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 10,
    ) -> List[tuple]:
        """
        Search + rank results using rapidfuzz scoring.
        Returns list of (score, Path) tuples, highest score first.
        Logs [FS_TOP_RESULT] on success.
        """
        candidates = self.search(query, type_filter=type_filter, drive=drive, limit=200)
        if not candidates:
            return []

        q_lower = query.lower()
        scored: list[tuple[float, Path]] = []

        try:
            from rapidfuzz import fuzz as _rf
            for p in candidates:
                name_l = p.name.lower()
                if name_l == q_lower:
                    score = 1.0
                elif q_lower in name_l:
                    score = 0.95
                else:
                    score = max(
                        _rf.token_set_ratio(q_lower, name_l) / 100.0,
                        _rf.partial_ratio(q_lower, name_l) / 100.0,
                    )
                # Boost shallower paths (prefer top-level folders)
                depth_penalty = len(p.parts) * 0.002
                scored.append((max(0.0, score - depth_penalty), p))
        except ImportError:
            import difflib
            for p in candidates:
                name_l = p.name.lower()
                score = 1.0 if q_lower in name_l else difflib.SequenceMatcher(None, q_lower, name_l).ratio()
                scored.append((score, p))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]
        if top:
            logger.info("[FS_TOP_RESULT] query=%r top=%s score=%.2f drive=%s",
                        query, top[0][1].name, top[0][0], drive or "any")
        return top

    # ------------------------------------------------------------------
    # Internal — DB setup
    # ------------------------------------------------------------------

    def _ensure_db_dir(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        conn = _get_thread_conn(self._db_path)
        # 1. Create base table + original indexes (idempotent).
        conn.executescript(_TABLE_DDL)
        # 2. Add new columns (fail silently if already present).
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        # 3. Create indexes that depend on new columns (now guaranteed to exist).
        conn.executescript(_INDEX_DDL)
        # 4. Phase 1.5 usage-learning tables (independent of the migrations above).
        conn.executescript(_AUX_TABLE_DDL)
        conn.commit()

    # ------------------------------------------------------------------
    # Internal — background thread
    # ------------------------------------------------------------------

    def _start_background_thread(self) -> None:
        t = threading.Thread(
            target=self._worker_loop,
            name="fs-index-worker",
            daemon=True,
        )
        t.start()
        logger.info("fs_index: background worker thread started")

    def _worker_loop(self) -> None:
        """Wait STARTUP_DELAY_SECONDS, build index, then rebuild every 6 h."""
        logger.info("fs_index: first build in %s seconds", STARTUP_DELAY_SECONDS)
        time.sleep(STARTUP_DELAY_SECONDS)

        while True:
            try:
                from api.services.background_scheduler import scheduler as _sched
                # wait_for_idle_window only blocks for `timeout` seconds and its
                # return value was previously ignored — a rebuild would fire even
                # while a voice session was actively mid-conversation (observed
                # live: full C:/E: rebuild kicked off mid-turn, correlated with a
                # 200%+ CPU event-loop-blocker and a stalled STT stage). Keep
                # re-polling for a real idle window instead of forcing through,
                # with a generous cap so the 6h rebuild cadence can't stall forever.
                _waited_s = 0.0
                _max_wait_s = 1800.0  # 30 min safety valve
                while not _sched.wait_for_idle_window(timeout=120.0):
                    _waited_s += 120.0
                    if _waited_s >= _max_wait_s:
                        logger.warning(
                            "[FS_INDEX] idle window never opened after %.0fs — "
                            "proceeding with rebuild anyway", _waited_s,
                        )
                        break
                    logger.info(
                        "[FS_INDEX] still waiting for idle window (voice active) — "
                        "waited=%.0fs", _waited_s,
                    )
            except Exception:
                pass
            try:
                self._rebuild()
            except Exception as exc:  # noqa: BLE001
                logger.exception("fs_index: rebuild failed: %s", exc)
            finally:
                if not self._ready.is_set():
                    self._ready.set()

            logger.info(
                "fs_index: next rebuild in %.0f hours",
                REBUILD_INTERVAL_SECONDS / 3600,
            )
            time.sleep(REBUILD_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Internal — index build
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Full scan of SCAN_ROOTS and (re)populate the SQLite table."""
        logger.info("[FS_INDEX] starting full rebuild — roots=%s", [str(r) for r in SCAN_ROOTS])
        start = time.monotonic()
        now = time.time()

        rows: List[tuple] = []
        for root in SCAN_ROOTS:
            if not root.exists():
                continue
            drive = _drive_for(root)
            logger.info("[FS_INDEX] indexing drive=%s root=%s", drive, root)
            for entry_path, entry_type, entry_size, entry_mtime in self._walk(root):
                rows.append((
                    str(entry_path),          # path
                    entry_path.name,          # name
                    entry_path.name.lower(),  # lowercase_name
                    entry_type,               # type
                    drive,                    # drive
                    entry_size,               # size
                    entry_mtime,              # modified_time
                    1,                        # accessible
                    now,                      # indexed_at
                ))

        scan_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX] scan complete — entries=%d elapsed=%.1fs", len(rows), scan_elapsed)

        self._bulk_upsert(rows)

        total_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX] ready entries=%d total_elapsed=%.1fs", len(rows), total_elapsed)

        try:
            self._content_pass()
        except Exception:
            logger.exception("[FS_INDEX] content/embedding pass failed")

        try:
            self._prune_old_events()
        except Exception:
            logger.exception("[FS_INDEX] open_events prune failed")

    def _content_pass(self) -> None:
        """
        Second pass: extract text + build embeddings for files under
        SEMANTIC_ROOTS / git repos only (see is_content_worthy). Runs after
        the cheap filename scan so plain path search is never delayed by it.
        Skips files whose content_hash hasn't changed since last pass.
        """
        from api.services.content_extractor import extract_text, is_supported
        from api.services.semantic_index import semantic_index

        conn = _get_thread_conn(self._db_path)
        candidates = conn.execute(
            "SELECT id, path, content_hash FROM entries WHERE type = 'file'"
        ).fetchall()

        start = time.monotonic()
        batch: List[tuple] = []
        embed_batch: List[tuple] = []
        scanned = 0

        for entry_id, path_str, old_hash in candidates:
            p = Path(path_str)
            if not is_supported(p) or not is_content_worthy(p):
                continue

            try:
                from api.services.background_scheduler import scheduler as _sched
                _sched.wait_for_idle_window(timeout=30.0)
            except Exception:
                pass

            scanned += 1
            new_hash = _quick_hash(p)
            if new_hash is None:
                continue
            if new_hash == old_hash:
                continue  # unchanged since last content pass

            text = extract_text(p)
            if not text:
                continue

            batch.append((new_hash, text[:2000], 1, entry_id))
            embed_batch.append((entry_id, text))

            if len(embed_batch) >= 64:
                self._flush_content_batch(batch, embed_batch)
                batch, embed_batch = [], []

        if batch or embed_batch:
            self._flush_content_batch(batch, embed_batch)

        semantic_index.save()
        logger.info(
            "[FS_CONTENT_PASS] scanned=%d embedded=%d elapsed=%.1fs",
            scanned, semantic_index.count, time.monotonic() - start,
        )

    def _flush_content_batch(self, batch: List[tuple], embed_batch: List[tuple]) -> None:
        from api.services.semantic_index import semantic_index
        if batch:
            with self._lock:
                conn = _get_thread_conn(self._db_path)
                conn.executemany(
                    "UPDATE entries SET content_hash = ?, keywords = ?, has_content = ? WHERE id = ?",
                    batch,
                )
                conn.commit()
        if embed_batch:
            semantic_index.add_batch(embed_batch)

    # ------------------------------------------------------------------
    # Incremental updates — used by fs_watcher for real-time indexing.
    # No full rescan: each call touches only the affected row(s).
    # ------------------------------------------------------------------

    def upsert_single(self, path: Path, index_content: bool = True) -> Optional[int]:
        """
        Insert or refresh a single file/folder. Returns the entry id.

        index_content=False skips the inline content-extraction/embedding
        step — used by fs_watcher, which defers that work to its own
        idle-window-gated queue so watchdog callbacks stay fast.
        """
        try:
            is_dir = path.is_dir()
            st = path.stat()
        except OSError:
            return None

        drive = _drive_for(path)
        now = time.time()
        entry_type = "folder" if is_dir else "file"
        size = 0 if is_dir else st.st_size

        with self._lock:
            conn = _get_thread_conn(self._db_path)
            conn.execute(
                "INSERT INTO entries (path, name, lowercase_name, type, drive, size, "
                "modified_time, accessible, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, "
                "modified_time=excluded.modified_time, indexed_at=excluded.indexed_at, "
                "accessible=1",
                (str(path), path.name, path.name.lower(), entry_type, drive, size, st.st_mtime, now),
            )
            conn.commit()
            row = conn.execute("SELECT id FROM entries WHERE path = ?", (str(path),)).fetchone()

        entry_id = row[0] if row else None
        logger.debug("[FS_INDEX_UPSERT] path=%s id=%s", path, entry_id)

        if entry_id is not None and not is_dir and index_content:
            self._maybe_index_content(entry_id, path)
        return entry_id

    def _maybe_index_content(self, entry_id: int, path: Path) -> None:
        """Extract + embed content for a single file if it qualifies."""
        from api.services.content_extractor import extract_text, is_supported
        from api.services.semantic_index import semantic_index

        if not is_supported(path) or not is_content_worthy(path):
            return
        new_hash = _quick_hash(path)
        if new_hash is None:
            return
        text = extract_text(path)
        if not text:
            return

        with self._lock:
            conn = _get_thread_conn(self._db_path)
            conn.execute(
                "UPDATE entries SET content_hash = ?, keywords = ?, has_content = 1 WHERE id = ?",
                (new_hash, text[:2000], entry_id),
            )
            conn.commit()
        semantic_index.add(entry_id, text)
        semantic_index.save()
        logger.debug("[FS_INDEX_CONTENT] path=%s embedded", path)

    def remove_path(self, path: Path) -> None:
        """Delete a single path (and its semantic embedding) from the index."""
        from api.services.semantic_index import semantic_index

        with self._lock:
            conn = _get_thread_conn(self._db_path)
            row = conn.execute("SELECT id FROM entries WHERE path = ?", (str(path),)).fetchone()
            conn.execute("DELETE FROM entries WHERE path = ?", (str(path),))
            # Directory deletes also remove every descendant row.
            conn.execute("DELETE FROM entries WHERE path LIKE ?", (f"{path}/%",))
            conn.commit()

        if row:
            semantic_index.remove(row[0])
        logger.debug("[FS_INDEX_REMOVE] path=%s", path)

    def rename_path(self, old_path: Path, new_path: Path) -> None:
        """
        Rewrite path/name for a moved/renamed file or folder. Handles
        directory renames by rewriting every descendant's path prefix
        in one statement instead of walking the subtree again.
        """
        old_s, new_s = str(old_path), str(new_path)
        with self._lock:
            conn = _get_thread_conn(self._db_path)
            conn.execute(
                "UPDATE entries SET path = ?, name = ?, lowercase_name = ? WHERE path = ?",
                (new_s, new_path.name, new_path.name.lower(), old_s),
            )
            # Descendants: replace the old prefix with the new one.
            conn.execute(
                "UPDATE entries SET path = ? || substr(path, ?) "
                "WHERE path LIKE ?",
                (new_s, len(old_s) + 1, f"{old_s}/%"),
            )
            conn.commit()
        logger.debug("[FS_INDEX_RENAME] old=%s new=%s", old_path, new_path)

    def mark_opened(self, path: Path, context: Optional[dict] = None) -> None:
        """
        Record that *path* was actually opened — feeds frequency ranking and,
        when *context* is supplied, the Phase 1.5 usage-learning model
        (time-of-day / weekday / active app / active folder / active project
        affinity — see usage_model.py).
        """
        now = time.time()
        with self._lock:
            conn = _get_thread_conn(self._db_path)
            cur = conn.execute(
                "UPDATE entries SET last_opened = ?, open_count = open_count + 1 WHERE path = ?",
                (now, str(path)),
            )
            if context is not None and cur.rowcount:
                row = conn.execute("SELECT id FROM entries WHERE path = ?", (str(path),)).fetchone()
                if row:
                    lt = time.localtime(now)
                    conn.execute(
                        "INSERT INTO open_events (entry_id, ts, hour, weekday, active_app, "
                        "active_folder, active_project) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (row[0], now, lt.tm_hour, lt.tm_wday,
                         context.get("active_app", ""), context.get("active_folder", ""),
                         context.get("active_project", "")),
                    )
            conn.commit()

    # ------------------------------------------------------------------
    # Phase 1.5 — candidate lookups for the context-priority cascade.
    # Every method here is a single indexed SQLite query — cheap enough to
    # run unconditionally on the resolution hot path without hurting latency.
    # ------------------------------------------------------------------

    def get_recent_files(self, limit: int = 300, max_age_days: float = 30.0) -> List[tuple]:
        """(id, path, last_opened) for recently opened files, newest first."""
        cutoff = time.time() - max_age_days * 86400
        conn = _get_thread_conn(self._db_path)
        return conn.execute(
            "SELECT id, path, last_opened FROM entries "
            "WHERE type = 'file' AND last_opened > ? ORDER BY last_opened DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()

    def get_frequent_files(self, limit: int = 300) -> List[tuple]:
        """(id, path, open_count) for the most-opened files, highest first."""
        conn = _get_thread_conn(self._db_path)
        return conn.execute(
            "SELECT id, path, open_count FROM entries "
            "WHERE type = 'file' AND open_count > 0 ORDER BY open_count DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def get_candidates_under_root(
        self, root: Path, type_filter: Optional[str] = None,
        name_hint: Optional[str] = None, limit: int = 2000,
    ) -> List[tuple]:
        """
        (id, path) for entries inside *root* — used by the workspace/folder
        tiers. Without name_hint, a large project (thousands of entries)
        can silently truncate before the actual match under an arbitrary
        LIMIT — so callers that already have a query should always pass its
        significant words as name_hint: this pushes an OR'd LIKE filter
        into SQL so the right file is never dropped by the row cap,
        regardless of project size.
        """
        conditions = ["(path = ? OR path LIKE ?)"]
        params: list = [str(root), f"{root}/%"]
        if type_filter in ("file", "folder"):
            conditions.append("type = ?")
            params.append(type_filter)
        if name_hint:
            tokens = [t for t in name_hint.lower().split() if len(t) >= 3][:5]
            if tokens:
                or_clause = " OR ".join(["lowercase_name LIKE ?"] * len(tokens))
                conditions.append(f"({or_clause})")
                params.extend(f"%{t}%" for t in tokens)
        where = " AND ".join(conditions)
        conn = _get_thread_conn(self._db_path)
        return conn.execute(
            f"SELECT id, path FROM entries WHERE {where} LIMIT ?", (*params, limit),
        ).fetchall()

    def get_by_ids(self, ids: List[int]) -> List[tuple]:
        """(id, path, modified_time, last_opened, open_count) for a specific id list."""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        conn = _get_thread_conn(self._db_path)
        return conn.execute(
            f"SELECT id, path, modified_time, last_opened, open_count "
            f"FROM entries WHERE id IN ({placeholders})",
            ids,
        ).fetchall()

    def get_usage_affinity(self, entry_ids: List[int], now_ctx: dict) -> dict:
        """
        Batch-compute a 0..1 affinity score per entry_id from open_events —
        blends time-of-day, weekday, active-app, active-folder and
        active-project match rates against this entry's own history.
        Returns {} immediately if there's no context or no candidates —
        keeps the common cold-start case free.
        """
        if not entry_ids:
            return {}
        placeholders = ",".join("?" * len(entry_ids))
        conn = _get_thread_conn(self._db_path)
        rows = conn.execute(
            f"SELECT entry_id, hour, weekday, active_app, active_folder, active_project "
            f"FROM open_events WHERE entry_id IN ({placeholders})",
            entry_ids,
        ).fetchall()
        if not rows:
            return {}

        by_entry: dict[int, list] = {}
        for entry_id, hour, weekday, app, folder, project in rows:
            by_entry.setdefault(entry_id, []).append((hour, weekday, app, folder, project))

        cur_hour = now_ctx.get("hour")
        cur_weekday = now_ctx.get("weekday")
        cur_app = now_ctx.get("active_app") or ""
        cur_folder = now_ctx.get("active_folder") or ""
        cur_project = now_ctx.get("active_project") or ""

        affinity: dict[int, float] = {}
        for entry_id, events in by_entry.items():
            n = len(events)
            parts, weights = [], []

            if cur_hour is not None:
                hour_hits = sum(1 for h, *_ in events if min((h - cur_hour) % 24, (cur_hour - h) % 24) <= 2)
                parts.append(hour_hits / n); weights.append(0.25)
            if cur_weekday is not None:
                wd_hits = sum(1 for _, w, *_ in events if w == cur_weekday)
                parts.append(wd_hits / n); weights.append(0.15)
            if cur_app:
                app_hits = sum(1 for *_, a, _f, _p in events if a == cur_app)
                parts.append(app_hits / n); weights.append(0.15)
            if cur_folder:
                folder_hits = sum(1 for *_, _a, f, _p in events if f == cur_folder)
                parts.append(folder_hits / n); weights.append(0.25)
            if cur_project:
                proj_hits = sum(1 for *_, _a, _f, p in events if p == cur_project)
                parts.append(proj_hits / n); weights.append(0.20)

            if parts:
                total_w = sum(weights)
                affinity[entry_id] = sum(p * w for p, w in zip(parts, weights)) / total_w

        return affinity

    # ------------------------------------------------------------------
    # Phase 1.5 — learned query -> path resolutions ("tier 0").
    # A pairing is written only when the user has actually confirmed a
    # candidate (accepted a medium-confidence guess or picked one from a
    # disambiguation list) — see file_resolver.record_confirmed_choice().
    # ------------------------------------------------------------------

    def record_learned_resolution(self, query_norm: str, path: str) -> None:
        if not query_norm or not path:
            return
        with self._lock:
            conn = _get_thread_conn(self._db_path)
            conn.execute(
                "INSERT INTO learned_resolutions (query_norm, path, hits, last_used) "
                "VALUES (?, ?, 1, ?) "
                "ON CONFLICT(query_norm, path) DO UPDATE SET "
                "hits = hits + 1, last_used = excluded.last_used",
                (query_norm, path, time.time()),
            )
            conn.commit()
        logger.info("[FS_LEARNED_RESOLUTION] query=%r path=%s", query_norm, path)

    def get_learned_resolution(self, query_norm: str) -> Optional[tuple]:
        """Return (path, hits) for the strongest learned match, or None."""
        if not query_norm:
            return None
        conn = _get_thread_conn(self._db_path)
        row = conn.execute(
            "SELECT path, hits FROM learned_resolutions WHERE query_norm = ? "
            "ORDER BY hits DESC, last_used DESC LIMIT 1",
            (query_norm,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def _prune_old_events(self, max_age_days: float = 180.0) -> None:
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            conn = _get_thread_conn(self._db_path)
            conn.execute("DELETE FROM open_events WHERE ts < ?", (cutoff,))
            conn.commit()

    # ------------------------------------------------------------------
    # Semantic + ranked search
    # ------------------------------------------------------------------

    def search_semantic_ranked(
        self,
        query: str,
        limit: int = 10,
        active_folder: Optional[str] = None,
    ) -> List[tuple]:
        """
        Rank candidates by a blend of semantic similarity, recency,
        open frequency, and active-folder relevance.

        Returns list of (score, Path, breakdown_dict), best first.
        Logs [FS_SEMANTIC_RANK] with the winning candidate's breakdown.
        """
        from api.services.semantic_index import semantic_index

        hits = semantic_index.search(query, k=max(limit * 5, 40))
        if not hits:
            return []

        ids = [h[0] for h in hits]
        sims = dict(hits)
        placeholders = ",".join("?" * len(ids))
        conn = _get_thread_conn(self._db_path)
        rows = conn.execute(
            f"SELECT id, path, modified_time, last_opened, open_count "
            f"FROM entries WHERE id IN ({placeholders})",
            ids,
        ).fetchall()

        now = time.time()
        scored: List[tuple] = []
        for entry_id, path_str, mtime, last_opened, open_count in rows:
            sim = max(0.0, sims.get(entry_id, 0.0))

            age_days = max(0.0, now - max(mtime, last_opened)) / 86400.0
            recency = 0.5 ** (age_days / 14.0)  # 14-day half-life

            import math
            frequency = min(1.0, math.log1p(open_count) / math.log1p(20))

            folder_relevance = 0.0
            if active_folder:
                if active_folder.lower() in path_str.lower():
                    folder_relevance = 1.0

            score = (
                0.55 * sim
                + 0.20 * recency
                + 0.15 * frequency
                + 0.10 * folder_relevance
            )
            scored.append((score, Path(path_str), {
                "sim": round(sim, 3), "recency": round(recency, 3),
                "frequency": round(frequency, 3), "folder": folder_relevance,
            }))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]
        if top:
            logger.info(
                "[FS_SEMANTIC_RANK] query=%r top=%s score=%.3f breakdown=%s",
                query, top[0][1].name, top[0][0], top[0][2],
            )
        return top

    def _walk(self, root: Path):
        """
        Recursively walk *root*, yielding (Path, type_str, size) tuples.
        Directories whose name appears in PRUNE_DIRS are skipped entirely.
        Symlinks are not followed to avoid cycles.
        """
        try:
            with os.scandir(str(root)) as it:
                entries = list(it)
        except PermissionError:
            return
        except OSError as exc:
            logger.debug("fs_index: scandir error at %s: %s", root, exc)
            return

        for entry in entries:
            if entry.name in PRUNE_DIRS:
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue

            p = Path(entry.path)

            if is_dir:
                yield p, "folder", 0, 0.0
                yield from self._walk(p)
            elif is_file:
                try:
                    st = entry.stat(follow_symlinks=False)
                    size = st.st_size
                    mtime = st.st_mtime
                except OSError:
                    size = 0
                    mtime = 0.0
                yield p, "file", size, mtime

    def _bulk_upsert(self, rows: List[tuple]) -> None:
        """Bulk-insert/replace all rows under a single write lock."""
        if not rows:
            return

        sql = (
            "INSERT OR REPLACE INTO entries "
            "(path, name, lowercase_name, type, drive, size, modified_time, accessible, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )

        with self._lock:
            conn = _get_thread_conn(self._db_path)
            try:
                conn.execute("BEGIN")
                conn.executemany(sql, rows)
                conn.execute("COMMIT")
            except sqlite3.Error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

fs_index = FSIndex()
