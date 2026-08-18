"""
Boot readiness state machine for Xyron.

Two-level readiness:
    CORE_READY — wakeword + TTS + command STT + PS session + interop bridge
                 Frontend leaves "Preparing Xyron", voice sessions may start.
    FULL_READY — all services including Whisper large + semantic classifier +
                 Ollama + TTS cache + media index.

States (in order):
    BOOTING → LOADING_TTS → LOADING_WAKEWORD → CORE_READY
            → LOADING_WHISPER → LOADING_INTENT → AUDIO_READY → READY (FULL_READY)
    DEGRADED / ERROR can be set from any state.
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ReadyState(str, Enum):
    BOOTING          = "BOOTING"
    LOADING_TTS      = "LOADING_TTS"
    LOADING_WAKEWORD = "LOADING_WAKEWORD"
    CORE_READY       = "CORE_READY"
    LOADING_WHISPER  = "LOADING_WHISPER"
    LOADING_INTENT   = "LOADING_INTENT"
    AUDIO_READY      = "AUDIO_READY"
    READY            = "READY"
    DEGRADED         = "DEGRADED"
    ERROR            = "ERROR"


class ReadinessService:
    """Thread-safe two-level boot readiness gate."""

    def __init__(self) -> None:
        self._lock          = threading.Lock()
        self._state         = ReadyState.BOOTING
        self._core_ready    = False
        self._full_ready    = False
        self._core_ready_at: Optional[float] = None
        self._ready_at:      Optional[float] = None
        self._start_t       = time.monotonic()
        self._services: Dict[str, bool] = {
            "whisper":    False,
            "tts":        False,
            "intent":     False,
            "wakeword":   False,
            "app_finder": False,
            "audio":      False,
        }
        self._blockers: List[str] = []
        self._core_ready_event = threading.Event()
        self._ready_event      = threading.Event()
        logger.info("[READY_STATE] %s", ReadyState.BOOTING.value)

    # ── State transitions ───────────────────────────────────────────────────

    def set_state(self, state: ReadyState) -> None:
        with self._lock:
            self._state = state
            if state == ReadyState.CORE_READY:
                self._core_ready = True
                self._core_ready_at = time.monotonic()
                self._core_ready_event.set()
            elif state == ReadyState.READY:
                self._core_ready = True
                self._full_ready  = True
                self._ready_at    = time.monotonic()
                self._core_ready_event.set()
                self._ready_event.set()
            logger.info("[READY_STATE] %s", state.value)

    def is_service_ready(self, name: str) -> bool:
        """True only if `mark_service(name, True)` has been called — False for
        both "marked failed" and "never checked" (fail-closed, since a caller
        skipping work based on this should only do so on a confirmed failure)."""
        with self._lock:
            return self._services.get(name, False)

    def mark_service(self, name: str, ok: bool) -> None:
        with self._lock:
            self._services[name] = ok
            if not ok:
                lbl = f"{name}_failed"
                if lbl not in self._blockers:
                    self._blockers.append(lbl)
            else:
                blocker = f"{name}_failed"
                if blocker in self._blockers:
                    self._blockers.remove(blocker)

    def set_degraded(self, reason: str) -> None:
        with self._lock:
            self._state = ReadyState.DEGRADED
            if reason not in self._blockers:
                self._blockers.append(reason)
            logger.warning("[READY_STATE] DEGRADED reason=%s", reason)

    def set_core_ready(self) -> None:
        """Mark CORE_READY — voice sessions and wake word may start."""
        with self._lock:
            self._state      = ReadyState.CORE_READY
            self._core_ready = True
            self._core_ready_at = time.monotonic()
            self._core_ready_event.set()
        elapsed = (self._core_ready_at - self._start_t) * 1000  # type: ignore[operator]
        logger.info("[READY_STATE] CORE_READY core_boot_ms=%.0f", elapsed)

    def set_ready(self) -> None:
        """Mark FULL_READY — all background services loaded."""
        with self._lock:
            self._state      = ReadyState.READY
            self._core_ready = True
            self._full_ready = True
            self._ready_at   = time.monotonic()
            self._core_ready_event.set()
            self._ready_event.set()
        elapsed = (self._ready_at - self._start_t) * 1000  # type: ignore[operator]
        logger.info("[READY_STATE] FULL_READY boot_ms=%.0f", elapsed)
        logger.info("[READY_STATE] READY boot_ms=%.0f", elapsed)

    # ── Query API ───────────────────────────────────────────────────────────

    @property
    def is_core_ready(self) -> bool:
        with self._lock:
            return self._core_ready

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._full_ready

    @property
    def state(self) -> ReadyState:
        with self._lock:
            return self._state

    def wait_core_ready(self, timeout: float = 30.0) -> bool:
        return self._core_ready_event.wait(timeout=timeout)

    def wait_ready(self, timeout: float = 60.0) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def snapshot(self) -> dict:
        """Return the full readiness snapshot for /api/v1/ready."""
        with self._lock:
            boot_ms = (time.monotonic() - self._start_t) * 1000
            core_ms = None
            if self._core_ready_at:
                core_ms = round((self._core_ready_at - self._start_t) * 1000)
            ready_in_ms = None
            if self._ready_at:
                ready_in_ms = round((self._ready_at - self._start_t) * 1000)
            return {
                "ready":      self._core_ready,   # true once CORE_READY (frontend gate)
                "core_ready": self._core_ready,
                "full_ready": self._full_ready,
                "state":      self._state.value,
                "services":   dict(self._services),
                "blockers":   list(self._blockers),
                "boot_ms":    round(boot_ms),
                "core_ready_ms": core_ms,
                "ready_in_ms":   ready_in_ms,
            }

    def not_ready_payload(self) -> dict:
        with self._lock:
            return {
                "type":    "not_ready",
                "state":   self._state.value,
                "reason":  "Xyron is still preparing voice services",
                "blockers": list(self._blockers),
            }


readiness_service = ReadinessService()
