"""
Background job scheduler — pauses idle-only jobs during active voice sessions.

Priority levels
───────────────
  CRITICAL_REALTIME — voice session, STT, TTS, router, direct tool execution.
                       Never paused by this scheduler.
  BACKGROUND_IDLE_ONLY — fs index full rebuild, media deep scan, reflection,
                          dev observer, large memory jobs, semantic classifier.
                          Paused while any voice session or command is active.
                          Also delayed 90 s after READY state on boot.

Usage (registering a job):
    from api.services.background_scheduler import scheduler, JobPriority
    scheduler.register("fs_rebuild", JobPriority.BACKGROUND_IDLE_ONLY)
    scheduler.should_run("fs_rebuild")   → True / False

    # or: check inline without registration
    scheduler.should_run_inline()        → True / False

Usage (blocking until cleared to run):
    scheduler.wait_for_idle_window(timeout=10)  → True if window opened
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Delay all idle-only jobs for this many seconds after READY to let the
# voice pipeline stabilize before background work begins.
_BOOT_IDLE_DELAY_S = 90.0


class JobPriority(str, Enum):
    CRITICAL_REALTIME   = "CRITICAL_REALTIME"
    BACKGROUND_IDLE_ONLY = "BACKGROUND_IDLE_ONLY"


class BackgroundScheduler:
    """Coordinates background job execution around voice activity."""

    def __init__(self) -> None:
        self._lock              = threading.Lock()
        self._jobs: Dict[str, JobPriority] = {}
        self._ready_t: Optional[float] = None   # when READY was declared
        self._started_t         = time.monotonic()
        logger.info("[BACKGROUND_SCHEDULER_READY]")

    # ── Registration ────────────────────────────────────────────────────────

    def register(self, name: str, priority: JobPriority) -> None:
        with self._lock:
            self._jobs[name] = priority
        logger.info("[BACKGROUND_JOB_REGISTERED] name=%s priority=%s", name, priority.value)

    def on_ready(self) -> None:
        """Call this when readiness_service reaches READY state."""
        with self._lock:
            if self._ready_t is None:
                self._ready_t = time.monotonic()
        logger.info("[BACKGROUND_SCHEDULER_READY] boot_delay=%.0fs starts_at=READY+%.0fs",
                    time.monotonic() - self._started_t, _BOOT_IDLE_DELAY_S)

    # ── Voice activity bridge ────────────────────────────────────────────────

    @staticmethod
    def _voice_is_active() -> bool:
        try:
            from api.services.voice_activity import is_active as _ia
            return _ia()
        except Exception:
            return False

    # ── Decision API ────────────────────────────────────────────────────────

    def should_run(self, name: str) -> bool:
        """Return True if job *name* is cleared to run right now."""
        with self._lock:
            priority = self._jobs.get(name, JobPriority.BACKGROUND_IDLE_ONLY)
            ready_t  = self._ready_t

        if priority == JobPriority.CRITICAL_REALTIME:
            return True

        # Boot delay — don't start idle jobs until 90 s after READY
        if ready_t is None:
            logger.debug("[BACKGROUND_JOB_DELAYED] name=%s reason=not_ready_yet", name)
            return False
        elapsed_since_ready = time.monotonic() - ready_t
        if elapsed_since_ready < _BOOT_IDLE_DELAY_S:
            remaining = _BOOT_IDLE_DELAY_S - elapsed_since_ready
            logger.debug("[BACKGROUND_JOB_DELAYED] name=%s reason=boot_delay remaining=%.0fs",
                         name, remaining)
            return False

        # Voice gate
        if self._voice_is_active():
            logger.info("[BACKGROUND_JOB_SKIPPED_ACTIVE_VOICE] name=%s", name)
            return False

        return True

    def should_run_inline(self) -> bool:
        """Quick inline check — used by loops that don't have a job name."""
        with self._lock:
            ready_t = self._ready_t
        if ready_t is None:
            return False
        if time.monotonic() - ready_t < _BOOT_IDLE_DELAY_S:
            return False
        return not self._voice_is_active()

    def wait_for_idle_window(self, timeout: float = 30.0) -> bool:
        """Block current thread until an idle window opens. Returns True if cleared."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.should_run_inline():
                return True
            remaining = deadline - time.monotonic()
            time.sleep(min(5.0, remaining))
        return False

    def pause_log(self, name: str) -> None:
        logger.info("[BACKGROUND_JOB_PAUSED] name=%s", name)

    def resume_log(self, name: str) -> None:
        logger.info("[BACKGROUND_JOB_RESUMED] name=%s", name)


scheduler = BackgroundScheduler()
