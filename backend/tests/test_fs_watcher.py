"""
Regression tests for the boot-blocking incident: FSWatcher.start() used to run
watchdog's synchronous directory snapshot (PollingObserver.schedule) inline,
inside FastAPI's async startup handler. On this machine one of SEMANTIC_ROOTS
is the Xyron repo itself, whose node_modules/.git trees (tens of thousands of
files on a WSL2 DrvFs mount) made that snapshot take minutes — so
"Application startup complete." was never printed and the app never bound its
listening socket.

Covers:
  - start() must return immediately regardless of how slow directory
    scheduling is (the fix moves it to a background thread)
  - build-artifact-heavy directories (node_modules, .git, ...) must not be
    watched whole — only their non-pruned children are scheduled
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.fs_watcher import FSWatcher


class _StubObserver:
    """Records schedule() calls; optionally sleeps to simulate a slow snapshot."""

    def __init__(self, sleep_seconds: float = 0.0):
        self._sleep_seconds = sleep_seconds
        self.scheduled_paths: list[str] = []

    def __call__(self, timeout=None):
        return self

    def schedule(self, handler, path, recursive=True):
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        self.scheduled_paths.append(path)

    def start(self):
        pass

    def stop(self):
        pass

    def join(self, timeout=None):
        pass


class TestFSWatcherStartIsNonBlocking(unittest.TestCase):
    def test_start_returns_immediately_even_when_schedule_is_slow(self):
        stub = _StubObserver(sleep_seconds=1.0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_text("x")

            watcher = FSWatcher()
            with patch("api.services.fs_index.SEMANTIC_ROOTS", [root]), \
                 patch("watchdog.observers.polling.PollingObserver", stub):
                t0 = time.monotonic()
                watcher.start()
                elapsed = time.monotonic() - t0

            self.assertLess(
                elapsed, 0.5,
                "start() must return immediately — the slow directory scan belongs "
                "on a background thread, not the caller (FastAPI's startup handler)",
            )
            # Let the background thread actually finish the slow schedule() call.
            time.sleep(1.5)
            self.assertTrue(stub.scheduled_paths)


class TestFSWatcherPrunesHeavyDirs(unittest.TestCase):
    def test_node_modules_and_git_are_not_watched_whole(self):
        stub = _StubObserver()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "node_modules").mkdir()
            (root / ".git").mkdir()
            (root / "src").mkdir()

            watcher = FSWatcher()
            with patch("api.services.fs_index.SEMANTIC_ROOTS", [root]), \
                 patch("watchdog.observers.polling.PollingObserver", stub):
                watcher.start()
                for _ in range(50):
                    if stub.scheduled_paths:
                        break
                    time.sleep(0.05)

            scheduled_names = {Path(p).name for p in stub.scheduled_paths}
            self.assertIn("src", scheduled_names)
            self.assertNotIn("node_modules", scheduled_names)
            self.assertNotIn(".git", scheduled_names)


if __name__ == "__main__":
    unittest.main()
