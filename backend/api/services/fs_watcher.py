"""
fs_watcher.py — Real-time filesystem watcher for Phase 1 System Intelligence.

Watches SEMANTIC_ROOTS (Desktop/Documents/Downloads/Pictures/Videos/Music +
the Xyron repo) and patches fs_index incrementally on create/delete/rename/
modify instead of waiting for the 6-hour full rebuild.

Scope decision: WSL2 Windows drives (/mnt/*) are DrvFs-mounted and do not
support inotify, so a full-drive recursive watch would silently miss events
or require expensive polling across millions of files. We therefore watch
only the user-content roots that already receive content extraction +
embeddings (see fs_index.SEMANTIC_ROOTS) using watchdog's PollingObserver,
which works uniformly on ext4 and DrvFs. The rest of the filesystem keeps
its existing 6-hour full-rescan cadence — unchanged, still correct, just
not instant.

Heavy work (content extraction + embedding) is never done on the watchdog
callback thread: created/modified files are queued and drained by a
background worker that waits for the scheduler's idle window, so a large
file drop never competes with an active voice session for CPU/GPU.

Logs: [FS_WATCHER_START] [FS_WATCHER_EVENT] [FS_WATCHER_QUEUE] [FS_WATCHER_STOP]
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum seconds between processing repeated "modified" events for the same
# path — avoids re-extracting content on every fsync during a long write.
_MODIFY_DEBOUNCE_SECONDS = 3.0

# Poll interval for watchdog's PollingObserver.
_POLL_TIMEOUT_SECONDS = 5.0


class _FSEventHandler:
    """watchdog event handler that delegates to fs_index incremental methods."""

    def __init__(self, content_queue: "queue.Queue[Path]") -> None:
        self._content_queue = content_queue
        self._last_modified: dict[str, float] = {}
        self._lock = threading.Lock()

    def _is_pruned(self, path: Path) -> bool:
        from api.services.fs_index import PRUNE_DIRS
        return any(part in PRUNE_DIRS for part in path.parts)

    def dispatch(self, event) -> None:
        try:
            self._handle(event)
        except Exception:
            logger.exception("[FS_WATCHER] event handling failed for %s", event)

    def _handle(self, event) -> None:
        from api.services.fs_index import fs_index

        src = Path(event.src_path)
        if self._is_pruned(src):
            return

        etype = event.event_type  # 'created' | 'deleted' | 'modified' | 'moved'

        if etype == "moved":
            dest = Path(getattr(event, "dest_path", event.src_path))
            if self._is_pruned(dest):
                return
            fs_index.rename_path(src, dest)
            logger.info("[FS_WATCHER_EVENT] moved %s -> %s", src, dest)
            if not event.is_directory:
                self._enqueue_content(dest)
            return

        if etype == "deleted":
            fs_index.remove_path(src)
            logger.info("[FS_WATCHER_EVENT] deleted %s", src)
            return

        if etype in ("created", "modified"):
            if etype == "modified" and not event.is_directory:
                with self._lock:
                    last = self._last_modified.get(str(src), 0.0)
                    now = time.monotonic()
                    if now - last < _MODIFY_DEBOUNCE_SECONDS:
                        return
                    self._last_modified[str(src)] = now

            fs_index.upsert_single(src, index_content=False)
            logger.debug("[FS_WATCHER_EVENT] %s %s", etype, src)
            if not event.is_directory:
                self._enqueue_content(src)

    def _enqueue_content(self, path: Path) -> None:
        from api.services.fs_index import is_content_worthy
        from api.services.content_extractor import is_supported
        if is_supported(path) and is_content_worthy(path):
            self._content_queue.put(path)
            logger.debug("[FS_WATCHER_QUEUE] queued %s", path)


class FSWatcher:
    """Owns the watchdog observer + the idle-gated content-embedding worker."""

    def __init__(self) -> None:
        self._observer = None
        self._content_queue: "queue.Queue[Path]" = queue.Queue()
        self._handler = _FSEventHandler(self._content_queue)
        self._worker_thread: Optional[threading.Thread] = None
        self._started = False

    def start(self) -> None:
        """Kick off watcher setup on a background thread and return immediately.

        watchdog's PollingObserver.schedule(recursive=True) builds a full
        synchronous directory snapshot (stat every entry) before returning.
        One of SEMANTIC_ROOTS is the Xyron repo itself, which contains
        node_modules/.git trees with tens of thousands of files on a WSL2
        DrvFs mount (/mnt/e) — that snapshot alone was observed taking
        minutes. This method used to run that snapshot inline inside
        FastAPI's async startup handler, so the whole app never finished
        booting (no listening socket, no "Application startup complete.").
        Setup now always happens off the caller's thread so it can never
        block startup, regardless of how large a future root gets.
        """
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._start_impl, name="fs-watcher-setup", daemon=True).start()

    def _start_impl(self) -> None:
        try:
            from watchdog.observers.polling import PollingObserver
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("[FS_WATCHER] watchdog not installed — real-time indexing disabled")
            self._started = False
            return

        from api.services.fs_index import SEMANTIC_ROOTS, PRUNE_DIRS

        outer = self._handler

        class _Adapter(FileSystemEventHandler):
            def on_any_event(self, event):
                outer.dispatch(event)

        observer = PollingObserver(timeout=_POLL_TIMEOUT_SECONDS)
        watched = 0
        for root in SEMANTIC_ROOTS:
            if not root.exists():
                continue
            # Watch build-artifact-heavy subtrees (node_modules, .git, …) by
            # scheduling only their non-pruned children instead of the whole
            # root recursively — avoids snapshotting/polling tens of
            # thousands of dependency files that are never content-worthy.
            try:
                children = [c for c in root.iterdir() if c.name not in PRUNE_DIRS]
            except OSError:
                children = []
            if children:
                for child in children:
                    observer.schedule(_Adapter(), str(child), recursive=True)
            else:
                observer.schedule(_Adapter(), str(root), recursive=True)
            watched += 1
        if watched == 0:
            logger.warning("[FS_WATCHER] no existing semantic roots to watch — watcher idle")
            self._started = False
            return

        observer.daemon = True
        observer.start()
        self._observer = observer

        self._worker_thread = threading.Thread(
            target=self._content_worker_loop, name="fs-watcher-content", daemon=True
        )
        self._worker_thread.start()

        logger.info("[FS_WATCHER_START] watching %d root(s) via PollingObserver(%.0fs)",
                    watched, _POLL_TIMEOUT_SECONDS)

    def _content_worker_loop(self) -> None:
        from api.services.fs_index import fs_index

        while True:
            path = self._content_queue.get()
            try:
                try:
                    from api.services.background_scheduler import scheduler as _sched
                    _sched.wait_for_idle_window(timeout=60.0)
                except Exception:
                    pass

                entry_id = fs_index.upsert_single(path, index_content=False)
                if entry_id is not None:
                    fs_index._maybe_index_content(entry_id, path)
            except Exception:
                logger.exception("[FS_WATCHER] content indexing failed for %s", path)
            finally:
                self._content_queue.task_done()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("[FS_WATCHER_STOP]")
        self._started = False


fs_watcher = FSWatcher()
