"""
Voice activity signal — pauses heavy background work during active voice commands.

Usage:
    from api.services.voice_activity import voice_activity
    voice_activity.set_active(True)   # command started
    voice_activity.set_active(False)  # command finished

Background services import and check is_active() before starting heavy work.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_ACTIVE_LOCK = threading.Lock()
_active      = False
_active_since: float = 0.0


def set_active(is_active: bool) -> None:
    global _active, _active_since
    with _ACTIVE_LOCK:
        if is_active and not _active:
            _active_since = time.monotonic()
            _active = True
            logger.info("[BACKGROUND_PAUSE] reason=voice_active")
        elif not is_active and _active:
            held_ms = (time.monotonic() - _active_since) * 1000
            _active = False
            logger.info("[BACKGROUND_RESUME] reason=voice_idle held_ms=%.0f", held_ms)


def is_active() -> bool:
    with _ACTIVE_LOCK:
        return _active
