"""
Tests for gpu_coordinator.py — Phase 3.6 keepalive-timeout incident fix.

Covers the priority primitive used to keep SentenceTransformer/semantic-index
GPU loads from contending with active voice-session TTS/STT (confirmed root
cause: a background thread loading SentenceTransformer held the GIL long
enough to starve the asyncio event loop that also has to service uvicorn's
own WebSocket ping/pong, producing "code=1011 reason=keepalive ping timeout").
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services import gpu_coordinator as gc


class TestGPUCoordinator(unittest.TestCase):
    def setUp(self):
        # Coordinator is module-level singleton state — reset between tests.
        while gc.is_voice_active():
            gc.voice_priority_end("test_setup_reset")

    def test_idle_by_default(self):
        self.assertFalse(gc.is_voice_active())

    def test_begin_sets_active_end_clears(self):
        gc.voice_priority_begin("test")
        self.assertTrue(gc.is_voice_active())
        gc.voice_priority_end("test")
        self.assertFalse(gc.is_voice_active())

    def test_reentrant_begin_end_pairs(self):
        """Overlapping turns within one session must not clear the flag
        until every begin() has a matching end()."""
        gc.voice_priority_begin("turn_1")
        gc.voice_priority_begin("turn_2")
        gc.voice_priority_end("turn_1")
        self.assertTrue(gc.is_voice_active(), "cleared too early — turn_2 still active")
        gc.voice_priority_end("turn_2")
        self.assertFalse(gc.is_voice_active())

    def test_wait_for_voice_idle_returns_immediately_when_idle(self):
        t0 = time.monotonic()
        result = gc.wait_for_voice_idle("test_component", timeout=5.0)
        self.assertTrue(result)
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_wait_for_voice_idle_blocks_until_voice_ends(self):
        gc.voice_priority_begin("blocking_test")

        def _release_after_delay():
            time.sleep(0.3)
            gc.voice_priority_end("blocking_test")

        threading.Thread(target=_release_after_delay).start()
        t0 = time.monotonic()
        result = gc.wait_for_voice_idle("waiting_component", timeout=5.0)
        elapsed = time.monotonic() - t0
        self.assertTrue(result)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 2.0)

    def test_wait_for_voice_idle_gives_up_after_timeout(self):
        gc.voice_priority_begin("stuck")
        t0 = time.monotonic()
        result = gc.wait_for_voice_idle("impatient_component", timeout=0.3)
        elapsed = time.monotonic() - t0
        self.assertFalse(result, "must return False (not hang forever) when voice never idles")
        self.assertLess(elapsed, 1.0)
        gc.voice_priority_end("stuck")


if __name__ == "__main__":
    unittest.main()
