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


_env_roots = os.getenv("FS_SCAN_ROOTS", "")
if _env_roots:
    SCAN_ROOTS: List[Path] = [Path(p.strip()) for p in _env_roots.split(",") if p.strip()]
else:
    _candidates = [Path("/mnt/d"), Path("/mnt/e"), Path("/mnt/f"), Path("/mnt/g")]
    _win_home = _detect_win_user_home()
    SCAN_ROOTS = [p for p in _candidates if _is_readable(p)]
    if _win_home:
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

REBUILD_INTERVAL_SECONDS = 6 * 3600  # 6 hours
STARTUP_DELAY_SECONDS = 5            # wait before first build on startup

DDL = """
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY,
    path       TEXT    UNIQUE NOT NULL,
    name       TEXT    NOT NULL,
    type       TEXT    NOT NULL CHECK(type IN ('file', 'folder')),
    drive      TEXT    NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_name ON entries (name);
CREATE INDEX IF NOT EXISTS idx_entries_type ON entries (type);
"""

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

        name_pattern = f"%{query}%"
        conditions = ["name LIKE ? COLLATE NOCASE"]
        params: list = [name_pattern]

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

        import difflib
        q_lower = query.lower()
        scored: list[tuple[float, str]] = []
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
            logger.debug("[FS_INDEX_FUZZY_HIT] query=%r results=%d", query, len(results))
        else:
            logger.debug("[FS_INDEX_FUZZY_MISS] query=%r", query)
        return results

    # ------------------------------------------------------------------
    # Internal — DB setup
    # ------------------------------------------------------------------

    def _ensure_db_dir(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        conn = _get_thread_conn(self._db_path)
        conn.executescript(DDL)
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
            for entry_path, entry_type, entry_size in self._walk(root):
                rows.append((
                    str(entry_path),  # path
                    entry_path.name,  # name
                    entry_type,       # type
                    drive,            # drive
                    entry_size,       # size
                    now,              # indexed_at
                ))

        scan_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX] scan complete — entries=%d elapsed=%.1fs", len(rows), scan_elapsed)

        self._bulk_upsert(rows)

        total_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX] ready entries=%d total_elapsed=%.1fs", len(rows), total_elapsed)

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
                yield p, "folder", 0
                yield from self._walk(p)
            elif is_file:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    size = 0
                yield p, "file", size

    def _bulk_upsert(self, rows: List[tuple]) -> None:
        """Bulk-insert/replace all rows under a single write lock."""
        if not rows:
            return

        sql = (
            "INSERT OR REPLACE INTO entries "
            "(path, name, type, drive, size, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
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
