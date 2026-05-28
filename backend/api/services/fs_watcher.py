"""
fs_watcher.py — Live Filesystem Watcher for Xyron (Part 5)

Uses watchdog to monitor Desktop/Documents/Downloads and all indexed roots.
Updates the SQLite DB on create/rename/delete/move without triggering full rebuilds.

Start via: fs_watcher.start()
Stop via:  fs_watcher.stop()

Log prefixes: [WATCHDOG_EVENT] [WATCHDOG_START] [WATCHDOG_STOP]
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_PRUNE = frozenset({
    "node_modules", ".git", "__pycache__", ".next", ".venv", "venv",
    "AppData", "Windows", "ProgramData", "$RECYCLE.BIN",
})


def _should_skip(path: str) -> bool:
    parts = Path(path).parts
    return any(p in _PRUNE for p in parts)


class _XyronHandler:
    """Watchdog event handler that updates the fs_index SQLite DB."""

    def __init__(self, drive_label: str) -> None:
        self._drive = drive_label

    def _get_index(self):
        from api.services.fs_index import fs_index
        return fs_index

    def _mtime(self, path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def on_created(self, event) -> None:
        if _should_skip(event.src_path):
            return
        p = Path(event.src_path)
        is_dir = event.is_directory
        mtime = self._mtime(event.src_path)
        logger.debug("[WATCHDOG_EVENT] created path=%s", event.src_path)
        try:
            self._get_index().upsert_entry(p, is_dir, self._drive, mtime)
        except Exception as exc:
            logger.debug("[WATCHDOG_EVENT] upsert error: %s", exc)

    def on_deleted(self, event) -> None:
        if _should_skip(event.src_path):
            return
        logger.debug("[WATCHDOG_EVENT] deleted path=%s", event.src_path)
        try:
            self._get_index().remove_entry(Path(event.src_path))
        except Exception as exc:
            logger.debug("[WATCHDOG_EVENT] remove error: %s", exc)

    def on_moved(self, event) -> None:
        if _should_skip(event.src_path) and _should_skip(event.dest_path):
            return
        logger.debug("[WATCHDOG_EVENT] moved %s → %s", event.src_path, event.dest_path)
        idx = self._get_index()
        try:
            idx.remove_entry(Path(event.src_path))
        except Exception:
            pass
        try:
            p = Path(event.dest_path)
            mtime = self._mtime(event.dest_path)
            idx.upsert_entry(p, event.is_directory, self._drive, mtime)
        except Exception as exc:
            logger.debug("[WATCHDOG_EVENT] moved upsert error: %s", exc)

    def on_modified(self, event) -> None:
        if event.is_directory or _should_skip(event.src_path):
            return
        p = Path(event.src_path)
        mtime = self._mtime(event.src_path)
        try:
            self._get_index().upsert_entry(p, False, self._drive, mtime)
        except Exception:
            pass

    # watchdog calls dispatch() — we implement the individual methods above
    def dispatch(self, event) -> None:
        etype = event.event_type
        if etype == "created":
            self.on_created(event)
        elif etype == "deleted":
            self.on_deleted(event)
        elif etype == "moved":
            self.on_moved(event)
        elif etype == "modified":
            self.on_modified(event)


class FSWatcher:
    """
    Manages watchdog Observer instances for all indexed roots.
    Starts after fs_index is ready to avoid watcher/indexer conflicts.
    """

    def __init__(self) -> None:
        self._observers: list = []
        self._started = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="fs-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        for obs in self._observers:
            try:
                obs.stop()
                obs.join(timeout=2)
            except Exception:
                pass
        self._observers.clear()
        logger.info("[WATCHDOG_STOP] all observers stopped")

    def _run(self) -> None:
        # Wait for watchdog library
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("[WATCHDOG_START] watchdog not installed — live watcher disabled. "
                           "Install: pip install watchdog")
            return

        # Wait for the index to be ready before attaching watchers
        try:
            from api.services.fs_index import fs_index
            for _ in range(60):
                if fs_index.is_ready:
                    break
                time.sleep(1)
        except Exception:
            pass

        roots = self._get_watch_roots()
        if not roots:
            logger.warning("[WATCHDOG_START] no roots to watch")
            return

        logger.info("[WATCHDOG_START] watching %d roots", len(roots))

        # Create a proper watchdog handler adapter
        class _Adapter(FileSystemEventHandler):
            def __init__(self, handler: _XyronHandler):
                super().__init__()
                self._h = handler

            def on_created(self, event):
                self._h.on_created(event)

            def on_deleted(self, event):
                self._h.on_deleted(event)

            def on_moved(self, event):
                self._h.on_moved(event)

            def on_modified(self, event):
                self._h.on_modified(event)

        for drive_label, root_path in roots:
            if not root_path.exists():
                continue
            try:
                handler  = _XyronHandler(drive_label)
                adapter  = _Adapter(handler)
                observer = Observer()
                observer.schedule(adapter, str(root_path), recursive=True)
                observer.start()
                self._observers.append(observer)
                logger.info("[WATCHDOG_START] watching drive=%s path=%s", drive_label, root_path)
            except Exception as exc:
                logger.warning("[WATCHDOG_START] failed to watch %s: %s", root_path, exc)

        self._started.set()

        # Keep thread alive while observers run
        try:
            while True:
                alive = [obs for obs in self._observers if obs.is_alive()]
                if not alive:
                    break
                time.sleep(5)
        except Exception:
            pass

    def _get_watch_roots(self) -> List[tuple]:
        """Collect all roots worth watching — priority dirs + drives."""
        roots: List[tuple] = []
        try:
            from api.services.fs_index import fs_index, _get_win_user_dirs, _get_priority_roots
            drives = fs_index._drives or []
            # Priority: user dirs
            for label, p in _get_win_user_dirs():
                roots.append((label.lower(), p))
            # Linux home subdirs
            home = Path.home()
            for sub in ("Desktop", "Documents", "Downloads", "Projects", "dev"):
                p = home / sub
                if p.is_dir():
                    roots.append((sub.lower(), p))
            # Dev roots on all drives (shallow, non-recursive scanning handled by watchdog)
            for drive_label, drive_root in drives:
                for dev_dir in ("Projects", "dev", "code", "repos", "work"):
                    p = drive_root / dev_dir
                    if p.is_dir():
                        roots.append((drive_label.lower(), p))
        except Exception as exc:
            logger.debug("[WATCHDOG_START] get_roots error: %s", exc)
        return roots


# Singleton
fs_watcher = FSWatcher()
