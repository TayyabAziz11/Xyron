"""
fs_index.py — Production Filesystem Index for Xyron (Stabilization Phase)

Architecture
────────────
DB            : backend/data/fs/xyron_fs.db  (separate files + folders tables)
Drive disco   : psutil.disk_partitions() primary, /mnt/ scan fallback (WSL2)
Startup       : staged priority scan — Desktop/Docs/Downloads first, drives later
Incremental   : skip entries whose mtime is unchanged (no full rebuild)
Thread safety : threading.Lock for writes; per-thread connections via threading.local

Log prefixes  : [DRIVE_DISCOVERY] [DRIVE_FOUND] [DRIVE_REMOVED]
                [FS_INDEX_STAGE] [FS_INDEX_PROGRESS] [FS_INDEX_DONE]
                [INDEX_INCREMENTAL] [WATCHDOG_EVENT]
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB path — canonical location inside the repo
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent   # backend/../ → repo root
DB_DIR  = _REPO_ROOT / "backend" / "data" / "fs"
DB_PATH = DB_DIR / "xyron_fs.db"

# Legacy path kept for migration
_LEGACY_DB = Path.home() / ".ai-operator" / "fs_index.db"

REBUILD_INTERVAL_SECONDS = 6 * 3600   # 6 hours for full re-index
STARTUP_DELAY_SECONDS    = 8          # let models warm up first

PRUNE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".next", ".venv", "venv",
    "AppData", "Windows", "ProgramData", "$RECYCLE.BIN",
    "System Volume Information", "Recovery", "WinSxS", "SoftwareDistribution",
    "MSOCache", "Intel", "NVIDIA", "AMD",
})

# ---------------------------------------------------------------------------
# Schema — separate tables for files and folders as specified
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS folders (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    normalized_name  TEXT    NOT NULL DEFAULT '',
    full_path        TEXT    UNIQUE NOT NULL,
    drive            TEXT    NOT NULL DEFAULT '',
    modified_ts      REAL    NOT NULL DEFAULT 0,
    last_seen_ts     REAL    NOT NULL DEFAULT 0,
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_opened_ts   REAL    NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    normalized_name  TEXT    NOT NULL DEFAULT '',
    full_path        TEXT    UNIQUE NOT NULL,
    drive            TEXT    NOT NULL DEFAULT '',
    type             TEXT    NOT NULL DEFAULT '',
    extension        TEXT    NOT NULL DEFAULT '',
    modified_ts      REAL    NOT NULL DEFAULT 0,
    last_seen_ts     REAL    NOT NULL DEFAULT 0,
    access_count     INTEGER NOT NULL DEFAULT 0,
    last_opened_ts   REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_folders_name           ON folders (name);
CREATE INDEX IF NOT EXISTS idx_folders_normalized     ON folders (normalized_name);
CREATE INDEX IF NOT EXISTS idx_folders_drive          ON folders (drive);
CREATE INDEX IF NOT EXISTS idx_folders_access         ON folders (access_count DESC);
CREATE INDEX IF NOT EXISTS idx_folders_last_opened    ON folders (last_opened_ts DESC);
CREATE INDEX IF NOT EXISTS idx_files_name             ON files (name);
CREATE INDEX IF NOT EXISTS idx_files_normalized       ON files (normalized_name);
CREATE INDEX IF NOT EXISTS idx_files_drive            ON files (drive);
CREATE INDEX IF NOT EXISTS idx_files_ext              ON files (extension);
CREATE INDEX IF NOT EXISTS idx_files_access           ON files (access_count DESC);
CREATE INDEX IF NOT EXISTS idx_files_last_opened      ON files (last_opened_ts DESC);
"""

# ---------------------------------------------------------------------------
# Thread-local connection pool
# ---------------------------------------------------------------------------

_tls = threading.local()


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    # Keyed by db_path string so tests with temp DBs get their own connections.
    conns: Optional[dict] = getattr(_tls, "conns", None)
    if conns is None:
        conns = {}
        _tls.conns = conns
    key = str(db_path)
    conn = conns.get(key)
    if conn is None:
        conn = sqlite3.connect(key, check_same_thread=False, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-8000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conns[key] = conn
    return conn


# ---------------------------------------------------------------------------
# Drive discovery — psutil primary, /mnt/ fallback for WSL2
# ---------------------------------------------------------------------------

def _is_readable(p: Path) -> bool:
    try:
        return p.is_dir() and bool(os.listdir(str(p)))
    except OSError:
        return False


def discover_drives() -> List[Tuple[str, Path]]:
    """
    Return list of (drive_label, path) tuples for all available drives.
    Labels are uppercase letters: 'C', 'D', 'E', etc.

    Strategy:
      1. psutil.disk_partitions() — works anywhere, cross-platform
      2. /mnt/<letter> scan — WSL2 fallback
      3. Windows cmd fallback via wsl.exe path
    """
    logger.info("[DRIVE_DISCOVERY] starting drive discovery")
    found: List[Tuple[str, Path]] = []
    seen_paths: set[str] = set()

    # -- Strategy 1: psutil (cross-platform, works in WSL2 + native Linux)
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            mp = part.mountpoint
            # In WSL2 Windows drives appear as /mnt/c, /mnt/e, etc.
            p = Path(mp)
            label = ""
            if len(p.parts) >= 3 and p.parts[1] == "mnt" and len(p.parts[2]) == 1:
                label = p.parts[2].upper()
            elif mp and len(mp.rstrip("/\\")) <= 3 and mp[1:3] == ":\\":
                label = mp[0].upper()
            if label and str(p) not in seen_paths and _is_readable(p):
                found.append((label, p))
                seen_paths.add(str(p))
                logger.info("[DRIVE_FOUND] source=psutil drive=%s path=%s fs=%s", label, p, part.fstype)
    except Exception as exc:
        logger.warning("[DRIVE_DISCOVERY] psutil failed: %s — trying /mnt/ scan", exc)

    # -- Strategy 2: /mnt/<letter> scan for WSL2 (catches drives psutil misses)
    try:
        mnt = Path("/mnt")
        if mnt.is_dir():
            for d in sorted(mnt.iterdir()):
                if d.is_dir() and len(d.name) == 1 and d.name.isalpha():
                    label = d.name.upper()
                    if str(d) not in seen_paths and _is_readable(d):
                        found.append((label, d))
                        seen_paths.add(str(d))
                        logger.info("[DRIVE_FOUND] source=wsl_mnt drive=%s path=%s", label, d)
    except Exception as exc:
        logger.debug("[DRIVE_DISCOVERY] /mnt/ scan error: %s", exc)

    if not found:
        home = Path.home()
        found.append(("~", home))
        logger.info("[DRIVE_FOUND] fallback=home path=%s", home)

    logger.info("[DRIVE_DISCOVERY] complete — drives=%s", [lbl for lbl, _ in found])
    return found


def _get_win_user_dirs() -> List[Tuple[str, Path]]:
    """
    Detect Windows user profile directories (Desktop, Documents, Downloads, etc.).
    Returns list of (label, path) tuples for all that exist.
    """
    dirs: List[Tuple[str, Path]] = []
    try:
        r = subprocess.run(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo", "%USERPROFILE%"],
            capture_output=True, text=True, timeout=3,
        )
        raw = r.stdout.strip()
        if raw and "%" not in raw and len(raw) > 3:
            # "C:\Users\tayyab" → "/mnt/c/Users/tayyab"
            wsl = "/mnt/" + raw[0].lower() + "/" + raw[3:].replace("\\", "/")
            home = Path(wsl)
            if home.is_dir():
                for sub in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"):
                    p = home / sub
                    if p.is_dir():
                        dirs.append((sub, p))
                dirs.append(("Home", home))
    except Exception:
        pass
    return dirs


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy matching."""
    import re
    return re.sub(r"[^a-z0-9 ]", " ", name.lower()).strip()


def _drive_label(path: Path, drives: List[Tuple[str, Path]]) -> str:
    p_str = str(path)
    for label, root in drives:
        if p_str.startswith(str(root)):
            return label
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "mnt":
        return parts[2].upper()
    return "~"


# ---------------------------------------------------------------------------
# Core FSIndex
# ---------------------------------------------------------------------------

class FSIndex:
    """
    Staged, incremental SQLite filesystem index.

    Startup sequence:
      1. Init DB schema (instant)
      2. Priority scan — Desktop/Docs/Downloads (fast, ~0.5s)
      3. Full drive scan in background (slow, non-blocking)
      4. Incremental re-index every 6h (skip unchanged)
    """

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._ready   = threading.Event()     # set after priority scan
        self._full_ready = threading.Event()  # set after full drive scan
        self._db_path = DB_PATH
        self._drives: List[Tuple[str, Path]] = []
        self._known_paths: dict[str, float] = {}   # full_path → modified_ts cache

        self._ensure_db_dir()
        self._init_db()
        self._migrate_legacy()
        self._start_worker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True once the priority scan (Desktop/Docs/Downloads) has completed."""
        return self._ready.is_set()

    @property
    def is_full_ready(self) -> bool:
        """True once the full drive scan has completed."""
        return self._full_ready.is_set()

    def get_discovered_drives(self) -> List[str]:
        return [lbl for lbl, _ in self._drives]

    def search(
        self,
        query: str,
        type_filter: Optional[str] = None,   # "file" | "folder" | None
        drive: Optional[str] = None,
        limit: int = 10,
    ) -> List[Path]:
        """
        Fast substring search. Returns paths ordered by depth (shallow first).
        Logs [FS_SEARCH] [FS_SEARCH_HIT] [FS_SEARCH_MISS].
        """
        if not query:
            return []
        logger.debug("[FS_SEARCH] query=%r type=%s drive=%s", query, type_filter, drive)
        q_norm = normalize(query)
        results: List[Path] = []

        tables = self._tables_for(type_filter)
        for tbl in tables:
            rows = self._search_table(tbl, q_norm, query, drive, limit)
            for r in rows:
                p = Path(r)
                if p not in results:
                    results.append(p)

        results.sort(key=lambda p: len(p.parts))
        results = results[:limit]

        if results:
            logger.debug("[FS_SEARCH_HIT] query=%r count=%d first=%s", query, len(results), results[0])
        else:
            logger.debug("[FS_SEARCH_MISS] query=%r", query)
        return results

    def search_fuzzy(
        self,
        query: str,
        type_filter: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 5,
    ) -> List[Path]:
        """Exact substring first; rapidfuzz fallback. Logs [FS_SEARCH_FUZZY]."""
        exact = self.search(query, type_filter=type_filter, drive=drive, limit=limit)
        if exact:
            return exact

        logger.debug("[FS_SEARCH_FUZZY] falling back to fuzzy for query=%r", query)
        tables = self._tables_for(type_filter)
        q_lower = query.lower()
        candidates: List[Tuple[float, str]] = []

        for tbl in tables:
            drive_cond = self._drive_cond(drive, tbl)
            sql = f"SELECT full_path, name FROM {tbl} {drive_cond} LIMIT 40000"
            try:
                conn = _get_conn(self._db_path)
                rows = conn.execute(sql).fetchall()
            except sqlite3.Error:
                continue

            try:
                from rapidfuzz import fuzz as rf
                for path_str, name in rows:
                    nl = name.lower()
                    if q_lower in nl:
                        s = 1.0
                    else:
                        s = max(rf.token_set_ratio(q_lower, nl),
                                rf.partial_ratio(q_lower, nl)) / 100.0
                    if s >= 0.55:
                        candidates.append((s, path_str))
            except ImportError:
                import difflib
                for path_str, name in rows:
                    nl = name.lower()
                    s = 1.0 if q_lower in nl else difflib.SequenceMatcher(None, q_lower, nl).ratio()
                    if s >= 0.6:
                        candidates.append((s, path_str))

        candidates.sort(key=lambda x: (-x[0], len(x[1])))
        results = [Path(p) for _, p in candidates[:limit]]
        if results:
            logger.info("[FS_SEARCH_FUZZY] query=%r top=%s score=%.2f",
                        query, results[0].name, candidates[0][0] if candidates else 0)
        return results

    def search_ranked(
        self,
        query: str,
        type_filter: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 10,
    ) -> List[Tuple[float, Path]]:
        """
        Search + rank by: exact name > recency > access_count > shorter path.
        Returns [(score, Path), ...] sorted best-first.
        Logs [FS_SEARCH_RANK].
        """
        candidates = self.search(query, type_filter=type_filter, drive=drive, limit=200)
        if not candidates:
            return []

        q_lower = query.lower()
        scored: List[Tuple[float, Path]] = []

        # Pull usage stats from DB for ranking boost
        usage = self._get_usage_stats([str(p) for p in candidates], type_filter)

        try:
            from rapidfuzz import fuzz as rf
            for p in candidates:
                nl = p.name.lower()
                if nl == q_lower:
                    name_score = 1.0
                elif q_lower in nl:
                    name_score = 0.9
                else:
                    name_score = max(rf.token_set_ratio(q_lower, nl),
                                     rf.partial_ratio(q_lower, nl)) / 100.0
                stats = usage.get(str(p), (0, 0.0))
                access_boost = min(stats[0] * 0.01, 0.15)    # up to +15% for heavy use
                recency_boost = min(stats[1] * 0.001, 0.10)  # up to +10% for recent use
                depth_penalty = len(p.parts) * 0.003
                score = max(0.0, name_score + access_boost + recency_boost - depth_penalty)
                scored.append((score, p))
        except ImportError:
            import difflib
            for p in candidates:
                nl = p.name.lower()
                s = 1.0 if q_lower in nl else difflib.SequenceMatcher(None, q_lower, nl).ratio()
                scored.append((s, p))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]
        if top:
            logger.info("[FS_SEARCH_RANK] query=%r top=%s score=%.2f", query, top[0][1].name, top[0][0])
        return top

    def record_open(self, path: Path) -> None:
        """
        Increment access_count and update last_opened_ts for a successfully opened path.
        Implements Part 7 — user memory learning.
        """
        path_str = str(path)
        now = time.time()
        for tbl in ("folders", "files"):
            try:
                conn = _get_conn(self._db_path)
                with self._lock:
                    conn.execute(
                        f"UPDATE {tbl} SET access_count = access_count + 1, "
                        f"last_opened_ts = ? WHERE full_path = ?",
                        (now, path_str),
                    )
                    conn.commit()
                return
            except sqlite3.Error:
                pass

    def record_open_by_str(self, path_str: str) -> None:
        self.record_open(Path(path_str))

    def upsert_entry(self, path: Path, is_dir: bool, drive: str, mtime: float) -> None:
        """Insert or update a single entry. Used by watchdog watcher."""
        now = time.time()
        name = path.name
        norm = normalize(name)
        path_str = str(path)

        if is_dir:
            sql = (
                "INSERT INTO folders (name, normalized_name, full_path, drive, modified_ts, last_seen_ts) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(full_path) DO UPDATE SET "
                "name=excluded.name, normalized_name=excluded.normalized_name, "
                "drive=excluded.drive, modified_ts=excluded.modified_ts, last_seen_ts=excluded.last_seen_ts"
            )
            params = (name, norm, path_str, drive, mtime, now)
        else:
            ext = path.suffix.lower()
            ftype = _file_type(ext)
            sql = (
                "INSERT INTO files (name, normalized_name, full_path, drive, type, extension, modified_ts, last_seen_ts) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(full_path) DO UPDATE SET "
                "name=excluded.name, normalized_name=excluded.normalized_name, drive=excluded.drive, "
                "type=excluded.type, extension=excluded.extension, modified_ts=excluded.modified_ts, "
                "last_seen_ts=excluded.last_seen_ts"
            )
            params = (name, norm, path_str, drive, ftype, ext, mtime, now)

        try:
            with self._lock:
                conn = _get_conn(self._db_path)
                conn.execute(sql, params)
                conn.commit()
        except sqlite3.Error as exc:
            logger.debug("[WATCHDOG_EVENT] upsert error: %s", exc)

    def remove_entry(self, path: Path) -> None:
        """Remove a path from both tables. Used by watchdog watcher."""
        path_str = str(path)
        try:
            with self._lock:
                conn = _get_conn(self._db_path)
                conn.execute("DELETE FROM folders WHERE full_path = ?", (path_str,))
                conn.execute("DELETE FROM files   WHERE full_path = ?", (path_str,))
                conn.commit()
            logger.debug("[WATCHDOG_EVENT] removed path=%s", path_str)
        except sqlite3.Error as exc:
            logger.debug("[WATCHDOG_EVENT] remove error: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tables_for(self, type_filter: Optional[str]) -> List[str]:
        if type_filter == "folder":
            return ["folders"]
        if type_filter == "file":
            return ["files"]
        return ["folders", "files"]

    def _drive_cond(self, drive: Optional[str], tbl: str = "") -> str:
        if not drive:
            return ""
        dl = drive.lower()
        return f"WHERE drive = '{dl}' OR full_path LIKE '/mnt/{dl}/%'"

    def _search_table(
        self, tbl: str, q_norm: str, q_raw: str, drive: Optional[str], limit: int
    ) -> List[str]:
        conditions = [
            "(normalized_name LIKE ? OR name LIKE ? COLLATE NOCASE)"
        ]
        params: List = [f"%{q_norm}%", f"%{q_raw}%"]

        if drive:
            dl = drive.lower()
            conditions.append(f"(drive = ? OR full_path LIKE ?)")
            params.extend([dl, f"/mnt/{dl}/%"])

        where = " AND ".join(conditions)
        sql = (
            f"SELECT full_path FROM {tbl} WHERE {where} "
            f"ORDER BY length(full_path) LIMIT {int(limit * 4)}"
        )
        try:
            conn = _get_conn(self._db_path)
            return [r[0] for r in conn.execute(sql, params).fetchall()]
        except sqlite3.Error:
            return []

    def _get_usage_stats(self, paths: List[str], type_filter: Optional[str]) -> dict:
        """Return {path_str: (access_count, last_opened_ts)} for ranking."""
        if not paths:
            return {}
        placeholders = ",".join("?" * len(paths))
        stats: dict = {}
        for tbl in self._tables_for(type_filter):
            try:
                conn = _get_conn(self._db_path)
                rows = conn.execute(
                    f"SELECT full_path, access_count, last_opened_ts FROM {tbl} "
                    f"WHERE full_path IN ({placeholders})",
                    paths,
                ).fetchall()
                for path_str, ac, lo in rows:
                    stats[path_str] = (ac or 0, lo or 0.0)
            except sqlite3.Error:
                pass
        return stats

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _ensure_db_dir(self) -> None:
        DB_DIR.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        conn = _get_conn(self._db_path)
        conn.executescript(_DDL)
        conn.commit()
        logger.info("[FS_INDEX_STAGE] DB initialized at %s", self._db_path)

    def _migrate_legacy(self) -> None:
        """Copy entries from the old ~/.ai-operator/fs_index.db if it exists."""
        if not _LEGACY_DB.exists():
            return
        try:
            old = sqlite3.connect(str(_LEGACY_DB), timeout=5)
            rows = old.execute(
                "SELECT path, name, type, drive, indexed_at FROM entries LIMIT 50000"
            ).fetchall()
            old.close()
            if not rows:
                return
            now = time.time()
            folder_rows, file_rows = [], []
            for path_str, name, typ, drive, indexed_at in rows:
                norm = normalize(name)
                p = Path(path_str)
                if typ == "folder":
                    folder_rows.append((name, norm, path_str, drive, 0.0, now, 0, 0.0))
                else:
                    ext = p.suffix.lower()
                    folder_rows_append = False
                    file_rows.append((name, norm, path_str, drive, _file_type(ext), ext, 0.0, now, 0, 0.0))
            conn = _get_conn(self._db_path)
            with self._lock:
                conn.execute("BEGIN")
                conn.executemany(
                    "INSERT OR IGNORE INTO folders "
                    "(name, normalized_name, full_path, drive, modified_ts, last_seen_ts, access_count, last_opened_ts) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    folder_rows,
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO files "
                    "(name, normalized_name, full_path, drive, type, extension, modified_ts, last_seen_ts, access_count, last_opened_ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    file_rows,
                )
                conn.execute("COMMIT")
            logger.info("[INDEX_INCREMENTAL] migrated %d legacy entries", len(rows))
        except Exception as exc:
            logger.warning("[FS_INDEX_STAGE] legacy migration failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        t = threading.Thread(target=self._worker_loop, name="fs-index-worker", daemon=True)
        t.start()
        logger.info("[FS_INDEX_STAGE] background worker started")

    def _worker_loop(self) -> None:
        logger.info("[FS_INDEX_STAGE] waiting %ds before first scan", STARTUP_DELAY_SECONDS)
        time.sleep(STARTUP_DELAY_SECONDS)

        while True:
            try:
                self._drives = discover_drives()
                self._staged_index()
            except Exception as exc:
                logger.exception("[FS_INDEX_STAGE] build failed: %s", exc)
            finally:
                if not self._ready.is_set():
                    self._ready.set()
                if not self._full_ready.is_set():
                    self._full_ready.set()

            logger.info("[FS_INDEX_DONE] next rebuild in %.0f hours",
                        REBUILD_INTERVAL_SECONDS / 3600)
            time.sleep(REBUILD_INTERVAL_SECONDS)

    def _staged_index(self) -> None:
        """
        Stage 1 — priority locations (fast, fires ready event quickly)
        Stage 2 — remaining drive contents (slow, background)
        """
        start = time.monotonic()

        # Stage 1: priority roots
        logger.info("[FS_INDEX_STAGE] stage=1 priority_roots — Desktop/Docs/Downloads")
        priority_roots = _get_priority_roots(self._drives)
        self._index_roots(priority_roots, stage=1)

        self._ready.set()
        stage1_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX_STAGE] stage=1 done elapsed=%.1fs", stage1_elapsed)

        # Stage 2: remaining drive roots (exclude already-indexed priority paths)
        logger.info("[FS_INDEX_STAGE] stage=2 full_drives — remaining content")
        self._index_roots(self._drives, stage=2, priority_already=priority_roots)

        total_elapsed = time.monotonic() - start
        logger.info("[FS_INDEX_DONE] total_elapsed=%.1fs drives=%s",
                    total_elapsed, [lbl for lbl, _ in self._drives])

    def _index_roots(
        self,
        roots: List[Tuple[str, Path]],
        stage: int,
        priority_already: Optional[List[Tuple[str, Path]]] = None,
    ) -> None:
        already_str = {str(p) for _, p in (priority_already or [])}
        folder_rows: List[tuple] = []
        file_rows: List[tuple] = []
        now = time.time()
        count = 0

        for label, root in roots:
            if str(root) in already_str:
                continue
            if not root.exists():
                continue
            drive_label = label.lower()
            logger.info("[FS_INDEX_PROGRESS] stage=%d scanning drive=%s root=%s", stage, label, root)

            for path, is_dir, mtime in self._walk(root):
                path_str = str(path)
                name = path.name
                norm = normalize(name)

                # Incremental: skip if mtime unchanged
                cached_mtime = self._known_paths.get(path_str)
                if cached_mtime is not None and cached_mtime == mtime:
                    continue
                self._known_paths[path_str] = mtime

                if is_dir:
                    folder_rows.append((name, norm, path_str, drive_label, mtime, now, 0, 0.0))
                else:
                    p = Path(path_str)
                    ext = p.suffix.lower()
                    file_rows.append((name, norm, path_str, drive_label, _file_type(ext), ext, mtime, now, 0, 0.0))

                count += 1
                if count % 10000 == 0:
                    logger.info("[FS_INDEX_PROGRESS] stage=%d entries=%d", stage, count)
                    self._flush(folder_rows, file_rows)
                    folder_rows.clear()
                    file_rows.clear()

        self._flush(folder_rows, file_rows)
        self._clean_stale(now)
        logger.info("[FS_INDEX_PROGRESS] stage=%d complete entries=%d", stage, count)

    def _flush(self, folder_rows: List[tuple], file_rows: List[tuple]) -> None:
        if not folder_rows and not file_rows:
            return
        conn = _get_conn(self._db_path)
        with self._lock:
            try:
                conn.execute("BEGIN")
                if folder_rows:
                    conn.executemany(
                        "INSERT INTO folders "
                        "(name, normalized_name, full_path, drive, modified_ts, last_seen_ts, access_count, last_opened_ts) "
                        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(full_path) DO UPDATE SET "
                        "name=excluded.name, normalized_name=excluded.normalized_name, "
                        "drive=excluded.drive, modified_ts=excluded.modified_ts, last_seen_ts=excluded.last_seen_ts",
                        folder_rows,
                    )
                if file_rows:
                    conn.executemany(
                        "INSERT INTO files "
                        "(name, normalized_name, full_path, drive, type, extension, modified_ts, last_seen_ts, access_count, last_opened_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(full_path) DO UPDATE SET "
                        "name=excluded.name, normalized_name=excluded.normalized_name, drive=excluded.drive, "
                        "type=excluded.type, extension=excluded.extension, modified_ts=excluded.modified_ts, "
                        "last_seen_ts=excluded.last_seen_ts",
                        file_rows,
                    )
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.error("[FS_INDEX_PROGRESS] flush error: %s", exc)

    def _clean_stale(self, scan_ts: float) -> None:
        """Remove entries not seen in the current scan (deleted files)."""
        stale_cutoff = scan_ts - 60   # allow 60s grace
        try:
            conn = _get_conn(self._db_path)
            with self._lock:
                conn.execute("DELETE FROM folders WHERE last_seen_ts < ? AND access_count = 0",
                             (stale_cutoff,))
                conn.execute("DELETE FROM files WHERE last_seen_ts < ? AND access_count = 0",
                             (stale_cutoff,))
                conn.commit()
        except sqlite3.Error:
            pass

    def _walk(self, root: Path):
        """Yield (Path, is_dir, mtime) — skip PRUNE_DIRS, no symlink follow."""
        try:
            with os.scandir(str(root)) as it:
                entries = list(it)
        except (PermissionError, OSError):
            return

        for entry in entries:
            if entry.name in PRUNE_DIRS:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue

            p = Path(entry.path)
            try:
                st = entry.stat(follow_symlinks=False)
                mtime = st.st_mtime
            except OSError:
                mtime = 0.0

            if is_dir:
                yield p, True, mtime
                yield from self._walk(p)
            else:
                yield p, False, mtime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_priority_roots(drives: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
    """Return priority scan roots: Win user dirs + top-level dev folders."""
    priority: List[Tuple[str, Path]] = []

    # Windows user dirs (Desktop, Documents, Downloads, etc.)
    for label, p in _get_win_user_dirs():
        priority.append((label, p))

    # Linux home dirs
    home = Path.home()
    for sub in ("Desktop", "Documents", "Downloads", "Projects", "dev", "code", "src"):
        p = home / sub
        if p.is_dir():
            priority.append((sub, p))

    # Common project roots on all drives (top 2 levels only)
    for drive_label, drive_root in drives:
        for dev_dir in ("Projects", "dev", "code", "src", "repos", "work", "git"):
            p = drive_root / dev_dir
            if p.is_dir():
                priority.append((f"{drive_label}/{dev_dir}", p))

    return priority


def _file_type(ext: str) -> str:
    _MAP = {
        ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
        ".rs": "code", ".go": "code", ".java": "code", ".cpp": "code", ".c": "code",
        ".mp4": "video", ".mkv": "video", ".avi": "video", ".mov": "video",
        ".mp3": "audio", ".wav": "audio", ".flac": "audio",
        ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image", ".webp": "image",
        ".pdf": "document", ".docx": "document", ".doc": "document",
        ".xlsx": "document", ".xls": "document", ".pptx": "document",
        ".txt": "text", ".md": "text", ".rst": "text",
        ".zip": "archive", ".tar": "archive", ".gz": "archive", ".7z": "archive",
        ".exe": "executable", ".msi": "executable", ".bat": "executable",
        ".db": "database", ".sqlite": "database",
    }
    return _MAP.get(ext, "other")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

fs_index = FSIndex()
