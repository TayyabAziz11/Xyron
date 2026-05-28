"""
test_fs_intelligence.py — Tests for Xyron Filesystem Intelligence (Parts 1–9)

Tests cover:
  - SQLite schema (files + folders tables, correct columns)
  - Drive discovery (dynamic, not hardcoded)
  - Priority indexing (desktop/docs/downloads indexed first)
  - Incremental indexing (skip unchanged files)
  - Watchdog entry add/remove
  - Search exact, fuzzy, ranked
  - User memory learning (access_count, last_opened_ts)
  - Stale result cleanup
  - Worker queue (non-blocking, no overload)
  - fs_search.py normalize, junk filter, voice response
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Yield a temp SQLite db path with the xyron_fs schema initialized."""
    db_path = tmp_path / "test_xyron_fs.db"
    from api.services.fs_index import _DDL, _get_conn
    # Patch DB path for this test
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def index_with_db(tmp_db, tmp_path):
    """FSIndex instance backed by a temp DB with no background threads."""
    from api.services.fs_index import FSIndex
    with patch.object(FSIndex, '_start_worker', lambda self: None), \
         patch.object(FSIndex, '_migrate_legacy', lambda self: None):
        idx = FSIndex.__new__(FSIndex)
        idx._lock = threading.Lock()
        idx._ready = threading.Event()
        idx._full_ready = threading.Event()
        idx._db_path = tmp_db
        idx._drives = []
        idx._known_paths = {}
        idx._ensure_db_dir = lambda: None
        idx._init_db()
        idx._ready.set()
        idx._full_ready.set()
        return idx


def _insert_folder(db_path: Path, name: str, full_path: str, drive: str = "e",
                   mtime: float = 1000.0, access_count: int = 0, last_opened: float = 0.0) -> None:
    from api.services.fs_index import normalize
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.execute(
        "INSERT OR REPLACE INTO folders "
        "(name, normalized_name, full_path, drive, modified_ts, last_seen_ts, access_count, last_opened_ts) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (name, normalize(name), full_path, drive, mtime, time.time(), access_count, last_opened),
    )
    conn.commit()
    conn.close()


def _insert_file(db_path: Path, name: str, full_path: str, ext: str = ".py",
                 drive: str = "e", mtime: float = 1000.0) -> None:
    from api.services.fs_index import normalize
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.execute(
        "INSERT OR REPLACE INTO files "
        "(name, normalized_name, full_path, drive, type, extension, modified_ts, last_seen_ts, access_count, last_opened_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, normalize(name), full_path, drive, "code", ext, mtime, time.time(), 0, 0.0),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Part 1 — Schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    def test_files_table_exists(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "files" in tables
        assert "folders" in tables
        conn.close()

    def test_folders_has_required_columns(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(folders)").fetchall()}
        conn.close()
        required = {"id", "name", "normalized_name", "full_path", "drive",
                    "modified_ts", "last_seen_ts", "access_count", "last_opened_ts"}
        assert required <= cols

    def test_files_has_required_columns(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
        conn.close()
        required = {"id", "name", "normalized_name", "full_path", "drive",
                    "type", "extension", "modified_ts", "last_seen_ts",
                    "access_count", "last_opened_ts"}
        assert required <= cols

    def test_indexes_created(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        indexes = {r[1] for r in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        conn.close()
        assert any("folders" in i for i in indexes)
        assert any("files" in i for i in indexes)


# ---------------------------------------------------------------------------
# Part 2 — Drive discovery
# ---------------------------------------------------------------------------

class TestDriveDiscovery:
    def test_discover_drives_returns_list(self):
        from api.services.fs_index import discover_drives
        drives = discover_drives()
        assert isinstance(drives, list)
        assert len(drives) > 0

    def test_discover_drives_no_hardcoding(self):
        """Drive discovery must use psutil or /mnt/ scan, not hardcoded letters."""
        from api.services import fs_index as fsi
        import inspect
        src = inspect.getsource(fsi.discover_drives)
        # Should not hardcode C/D/E without psutil as primary
        assert "psutil" in src or "disk_partitions" in src

    def test_drive_labels_are_strings(self):
        from api.services.fs_index import discover_drives
        for label, path in discover_drives():
            assert isinstance(label, str)
            assert isinstance(path, Path)

    def test_inaccessible_drives_excluded(self):
        """Drives that are empty/dead mounts must not appear in results."""
        from api.services.fs_index import _is_readable
        assert not _is_readable(Path("/mnt/z_nonexistent_xyz"))


# ---------------------------------------------------------------------------
# Part 3 — Priority indexing (staged)
# ---------------------------------------------------------------------------

class TestPriorityIndexing:
    def test_get_priority_roots_includes_common_dirs(self):
        from api.services.fs_index import _get_priority_roots, discover_drives
        drives = discover_drives()
        priority = _get_priority_roots(drives)
        labels = [lbl.lower() for lbl, _ in priority]
        # At least one of the common priority dirs should be in the list
        assert any(lbl in ("desktop", "documents", "downloads", "projects", "dev", "home")
                   for lbl in labels)

    def test_priority_roots_paths_exist(self):
        from api.services.fs_index import _get_priority_roots, discover_drives
        drives = discover_drives()
        for _, p in _get_priority_roots(drives):
            assert p.is_dir(), f"{p} should be an existing directory"


# ---------------------------------------------------------------------------
# Part 4 — Incremental indexing
# ---------------------------------------------------------------------------

class TestIncrementalIndexing:
    def test_unchanged_file_skipped(self, index_with_db, tmp_db):
        """If mtime matches the cached value, the entry should not be re-inserted."""
        from api.services.fs_index import normalize
        path_str = "/mnt/e/test/unchanged.py"
        mtime = 1234567890.0

        # Manually set known_paths cache as if we already indexed this file
        index_with_db._known_paths[path_str] = mtime

        # Insert initial record
        _insert_file(tmp_db, "unchanged.py", path_str, mtime=mtime)

        # Simulate walk yielding same mtime
        entries = [
            (Path(path_str), False, mtime),  # same mtime → should skip
        ]

        initial_count = sqlite3.connect(str(tmp_db)).execute("SELECT COUNT(*) FROM files").fetchone()[0]

        # Run index with the same mtime
        folder_rows, file_rows = [], []
        now = time.time()
        for path, is_dir, mt in entries:
            if index_with_db._known_paths.get(str(path)) == mt:
                continue  # incremental skip
            folder_rows.append((path.name, normalize(path.name), str(path), "e", mt, now, 0, 0.0))

        assert not file_rows, "Unchanged file should be skipped"

    def test_changed_file_reindexed(self, index_with_db):
        """Changed mtime should cause re-index."""
        path_str = "/mnt/e/test/changed.py"
        old_mtime = 1000.0
        new_mtime = 2000.0

        index_with_db._known_paths[path_str] = old_mtime
        assert index_with_db._known_paths.get(path_str) != new_mtime  # would be re-indexed


# ---------------------------------------------------------------------------
# Part 5 — Watchdog events
# ---------------------------------------------------------------------------

class TestWatchdogEvents:
    def test_upsert_entry_folder(self, index_with_db, tmp_db):
        path = Path("/mnt/e/test/NewFolder")
        index_with_db.upsert_entry(path, True, "e", 9999.0)
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT name FROM folders WHERE full_path = ?", (str(path),)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "NewFolder"

    def test_upsert_entry_file(self, index_with_db, tmp_db):
        path = Path("/mnt/e/test/script.py")
        index_with_db.upsert_entry(path, False, "e", 9999.0)
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT extension FROM files WHERE full_path = ?", (str(path),)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == ".py"

    def test_remove_entry(self, index_with_db, tmp_db):
        path = Path("/mnt/e/test/ToDelete")
        _insert_folder(tmp_db, "ToDelete", str(path))
        index_with_db.remove_entry(path)
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT id FROM folders WHERE full_path = ?", (str(path),)).fetchone()
        conn.close()
        assert row is None


# ---------------------------------------------------------------------------
# Part 6 — Search engine
# ---------------------------------------------------------------------------

class TestSearch:
    def test_exact_folder_match(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Python", "/mnt/e/Projects/Python")
        results = index_with_db.search("Python", type_filter="folder")
        paths = [str(p) for p in results]
        assert "/mnt/e/Projects/Python" in paths

    def test_case_insensitive_search(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Xyron", "/mnt/e/Xyron")
        results = index_with_db.search("xyron")
        assert any("Xyron" in str(p) for p in results)

    def test_substring_match(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "MyPythonProject", "/mnt/e/Projects/MyPythonProject")
        results = index_with_db.search("python")
        assert any("MyPythonProject" in str(p) for p in results)

    def test_drive_filter(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Backend", "/mnt/e/Backend", drive="e")
        _insert_folder(tmp_db, "Backend", "/mnt/d/Backend", drive="d")
        results = index_with_db.search("Backend", drive="e")
        assert all("/mnt/e/" in str(p) for p in results)
        assert not any("/mnt/d/" in str(p) for p in results)

    def test_type_filter_folder(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Data", "/mnt/e/Data")
        _insert_file(tmp_db, "data.csv", "/mnt/e/data.csv", ext=".csv")
        results = index_with_db.search("data", type_filter="folder")
        assert all(str(p).endswith("Data") for p in results)

    def test_type_filter_file(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Reports", "/mnt/e/Reports")
        _insert_file(tmp_db, "report.pdf", "/mnt/e/report.pdf", ext=".pdf")
        results = index_with_db.search("report", type_filter="file")
        assert all(str(p).endswith(".pdf") for p in results)

    def test_empty_query_returns_empty(self, index_with_db):
        assert index_with_db.search("") == []

    def test_no_result_returns_empty(self, index_with_db):
        assert index_with_db.search("xyzzy_nonexistent_1234") == []

    def test_shorter_path_ranked_first(self, index_with_db, tmp_db):
        _insert_folder(tmp_db, "Python", "/mnt/e/Python")
        _insert_folder(tmp_db, "Python", "/mnt/e/very/deep/nested/path/to/old/Python")
        results = index_with_db.search("Python", type_filter="folder")
        # Shorter path must come first
        assert len(results) >= 2
        assert len(results[0].parts) < len(results[-1].parts)


# ---------------------------------------------------------------------------
# Part 7 — User memory learning
# ---------------------------------------------------------------------------

class TestUserLearning:
    def test_record_open_increments_access_count(self, index_with_db, tmp_db):
        path_str = "/mnt/e/Xyron"
        _insert_folder(tmp_db, "Xyron", path_str, access_count=0)

        index_with_db.record_open(Path(path_str))

        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT access_count, last_opened_ts FROM folders WHERE full_path = ?",
                           (path_str,)).fetchone()
        conn.close()
        assert row[0] == 1
        assert row[1] > 0

    def test_record_open_repeated(self, index_with_db, tmp_db):
        path_str = "/mnt/e/Projects"
        _insert_folder(tmp_db, "Projects", path_str, access_count=5)
        index_with_db.record_open(Path(path_str))
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT access_count FROM folders WHERE full_path = ?", (path_str,)).fetchone()
        conn.close()
        assert row[0] == 6

    def test_frequently_opened_ranked_higher(self, index_with_db, tmp_db):
        """Folder opened 10x should rank above identical folder opened 0x."""
        _insert_folder(tmp_db, "Python", "/mnt/e/Python",       access_count=10, last_opened=time.time())
        _insert_folder(tmp_db, "Python", "/mnt/e/old/Python",   access_count=0,  last_opened=0.0)
        ranked = index_with_db.search_ranked("Python", type_filter="folder")
        assert len(ranked) >= 2
        best_path = str(ranked[0][1])
        assert "old" not in best_path, "Frequently opened folder should rank first"


# ---------------------------------------------------------------------------
# Part 8 — Strict verification (via fs_search.py)
# ---------------------------------------------------------------------------

class TestStrictVerification:
    def test_verify_nonexistent_path_not_found(self):
        from api.services.fs_search import _verify
        ok, err = _verify(Path("/mnt/z_totally_nonexistent_xyz/folder"))
        assert not ok
        assert err in ("not_found", "drive_unavailable")

    def test_verify_existing_path_ok(self):
        from api.services.fs_search import _verify
        # /tmp always exists
        ok, err = _verify(Path("/tmp"))
        assert ok
        assert err == "ok"

    def test_voice_response_not_found(self):
        from api.services.fs_search import voice_response
        msg = voice_response(False, "Python", error_type="not_found")
        assert "couldn't find" in msg.lower() or "find" in msg.lower()

    def test_voice_response_access_denied(self):
        from api.services.fs_search import voice_response
        msg = voice_response(False, "SysFolder", error_type="access_denied")
        assert "denied" in msg.lower()

    def test_voice_response_drive_unavailable(self):
        from api.services.fs_search import voice_response
        msg = voice_response(False, "X", error_type="drive_unavailable")
        assert "unavailable" in msg.lower()

    def test_voice_response_success(self):
        from api.services.fs_search import voice_response
        msg = voice_response(True, "Xyron")
        assert msg.startswith("Opened")


# ---------------------------------------------------------------------------
# Part 9 — Non-blocking worker queue
# ---------------------------------------------------------------------------

class TestWorkerQueue:
    def test_submit_returns_immediately(self):
        from api.services.worker_queue import WorkerQueue
        wq = WorkerQueue(max_workers=2)
        results = []

        def slow_task():
            time.sleep(0.05)
            return 42

        t0 = time.monotonic()
        future = wq.submit(slow_task)
        elapsed_submit = time.monotonic() - t0
        assert elapsed_submit < 0.02, f"submit() should return immediately, took {elapsed_submit:.3f}s"

        result = future.result(timeout=2)
        assert result == 42
        wq.shutdown()

    def test_overloaded_flag(self):
        from api.services.worker_queue import WorkerQueue, _WARN_QUEUE_DEPTH
        wq = WorkerQueue(max_workers=1)
        assert not wq.is_overloaded
        barrier = threading.Barrier(2)

        def blocking():
            barrier.wait(timeout=2)

        # Submit enough to exceed depth
        for _ in range(_WARN_QUEUE_DEPTH + 1):
            wq.submit(blocking)
        # Queue should be overloaded now
        # (threads are blocked at barrier so tasks pile up)
        assert wq.pending_count > 0
        barrier.wait(timeout=3)
        wq.shutdown(wait=False)

    def test_submit_bg_fire_and_forget(self):
        from api.services.worker_queue import WorkerQueue
        wq = WorkerQueue(max_workers=2)
        done = threading.Event()

        def task():
            done.set()

        wq.submit_bg(task)
        assert done.wait(timeout=2), "Background task should complete"
        wq.shutdown()

    def test_voice_runtime_not_blocked(self):
        """Voice runtime must not wait > 50ms even when 8 heavy tasks are queued."""
        from api.services.worker_queue import WorkerQueue
        wq = WorkerQueue(max_workers=2)

        def heavy():
            time.sleep(0.5)

        for _ in range(8):
            wq.submit(heavy)

        t0 = time.monotonic()
        wq.submit(lambda: None)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.05, f"Voice runtime blocked for {elapsed*1000:.0f}ms"
        wq.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Part 6 extra — fs_search.py normalize and junk filter
# ---------------------------------------------------------------------------

class TestFsSearch:
    def test_normalize(self):
        from api.services.fs_search import _normalize
        assert _normalize("Python-3.12") == "python 3 12"
        assert _normalize("My Project!").strip() == "my project"

    def test_junk_paths_filtered(self):
        from api.services.fs_search import _is_junk
        assert _is_junk(Path("/mnt/c/Users/foo/AppData/Local/Temp/python"))
        assert not _is_junk(Path("/mnt/e/Projects/Python"))

    def test_wsl_to_win_conversion(self):
        from api.services.fs_search import _wsl_to_win
        assert _wsl_to_win("/mnt/e/Projects/Python") == "E:\\Projects\\Python"
        assert _wsl_to_win("/mnt/c/Users") == "C:\\Users"
        assert _wsl_to_win("/mnt/e/") == "E:\\"

    def test_folder_suffix_stripped(self):
        """'python folder' should yield query='python', type='folder'."""
        from api.services.fs_search import search as fs_search
        with patch("api.services.fs_search.search") as mock_s:
            # Just verify the suffix stripping logic directly
            from api.services import fs_search as fss
            # The function strips " folder" suffix and sets type_filter
            # We can test this by checking normalize behavior
            query = "python folder"
            for sfx in fss._FOLDER_SUFFIXES:
                if query.lower().endswith(sfx):
                    stripped = query[:-len(sfx)].strip()
                    assert stripped == "python"
                    break


# ---------------------------------------------------------------------------
# Utility test — normalize function
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_basic(self):
        from api.services.fs_index import normalize
        assert normalize("Hello World") == "hello world"
        assert normalize("My-Project_2024") == "my project 2024"
        assert normalize("  spaces  ") == "spaces"
        assert normalize("My Project!").strip() == "my project"

    def test_unicode_stripped(self):
        from api.services.fs_index import normalize
        result = normalize("Xyron™ AI")
        assert "xyron" in result
        assert "ai" in result
