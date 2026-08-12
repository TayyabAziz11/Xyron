"""
worker_queue.py — Non-Blocking Background Worker for Xyron (Part 9)

All heavy operations (filesystem indexing, fuzzy ranking, embedding generation,
disk scans, PowerShell metric queries) run through this queue so the voice
runtime is NEVER blocked waiting for them.

Usage:
    from api.services.worker_queue import worker_queue
    future = worker_queue.submit(expensive_fn, arg1, arg2)
    # voice runtime continues immediately; result available later via future.result()

    # Fire-and-forget (no result needed):
    worker_queue.submit_bg(fn, arg)

    # Check before scheduling heavy work:
    if not worker_queue.is_overloaded:
        worker_queue.submit(heavy_task)

Log prefixes: [WORKER_QUEUE] [PERF_WARN] [MAIN_LOOP_BLOCK]
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Maximum concurrent worker threads
_MAX_WORKERS     = 4
# Queue depth warning threshold
_WARN_QUEUE_DEPTH = 8


class WorkerQueue:
    """
    Thread-pool backed task queue for heavy background work.

    Voice runtime should NEVER call submit() during active TTS playback —
    check is_overloaded first, or use submit_bg() for fire-and-forget.
    """

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._pool    = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="xyron-worker")
        self._lock    = threading.Lock()
        self._pending = 0
        self._total_submitted = 0
        self._total_completed = 0

    @property
    def is_overloaded(self) -> bool:
        """True when more than WARN_QUEUE_DEPTH tasks are pending."""
        return self._pending >= _WARN_QUEUE_DEPTH

    @property
    def pending_count(self) -> int:
        return self._pending

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        """
        Submit a task to the worker pool. Returns a Future.
        The calling thread (voice runtime) returns IMMEDIATELY.
        """
        with self._lock:
            self._pending += 1
            self._total_submitted += 1
            depth = self._pending

        if depth >= _WARN_QUEUE_DEPTH:
            logger.warning("[WORKER_QUEUE] [PERF_WARN] queue depth=%d — consider throttling", depth)

        def _wrapped():
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                elapsed = (time.monotonic() - t0) * 1000
                if elapsed > 500:
                    logger.warning("[PERF_WARN] slow worker fn=%s elapsed=%.0fms",
                                   getattr(fn, "__name__", "?"), elapsed)
                return result
            finally:
                with self._lock:
                    self._pending -= 1
                    self._total_completed += 1
                logger.debug("[WORKER_QUEUE] fn=%s done elapsed=%.0fms pending=%d",
                             getattr(fn, "__name__", "?"),
                             (time.monotonic() - t0) * 1000, self._pending)

        return self._pool.submit(_wrapped)

    def submit_bg(self, fn: Callable, *args, **kwargs) -> None:
        """Fire-and-forget. Errors are logged but not propagated."""
        def _safe():
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                logger.error("[WORKER_QUEUE] bg task error fn=%s: %s",
                             getattr(fn, "__name__", "?"), exc)
        self.submit(_safe)

    def stats(self) -> dict:
        return {
            "pending":   self._pending,
            "submitted": self._total_submitted,
            "completed": self._total_completed,
            "overloaded": self.is_overloaded,
        }

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait)
        logger.info("[WORKER_QUEUE] shutdown complete")


# Singleton
worker_queue = WorkerQueue()
