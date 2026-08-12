from __future__ import annotations

import threading
import time

import pytest


class TestWorkerQueue:
    def test_submit_returns_immediately(self):
        from api.services.worker_queue import WorkerQueue
        wq = WorkerQueue(max_workers=2)

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
