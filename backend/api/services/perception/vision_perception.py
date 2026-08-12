"""
vision_perception.py — Perception Engine: the LAST-resort fallback.

Per the Phase 2 brief: "Never use a Vision model if Browser Perception or
Desktop Perception already provides structured information." This module
is therefore never called unconditionally by event_dispatcher.py — only
when both of those returned nothing usable, and even then it's throttled
(_MIN_INTERVAL_SECONDS) so a perception loop tick can't spam vision calls.

Reuses screen_context_service.py's existing capture (System.Drawing via the
PowerShell bridge — already battle-tested, not mss/pywin32) and GPT-4o-mini
description call rather than standing up a second implementation. Capture
is monitor-scoped via multi_monitor_manager (capture only the monitor the
foreground window is actually on).

Local model note (why Qwen2.5-VL isn't wired in this phase): Qwen2.5-VL
needs several GB of VRAM even quantized, and this box's GPU (per project
history) is a low-VRAM mobile part already carrying Whisper + Kokoro +
SentenceTransformer. Downloading and serving a second multi-GB model as the
*rarest-used* perception tier is a bad trade against the boot/memory budget
until there's a concrete need for offline vision. The backend is a single
swappable function (_describe) so plugging in a local model later is a
one-function change, not an architecture change.

Logs: [VISION_PERCEPTION_SKIP] [VISION_PERCEPTION_CAPTURE] [VISION_PERCEPTION_THROTTLED]
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 30.0  # throttle — vision is the expensive, last-resort tier

_last_capture_at = 0.0
_lock = threading.Lock()


def _describe(b64: str, openai_key: str) -> str:
    """Swappable backend — reuses screen_context_service's existing GPT-4o-mini call."""
    from api.services.screen_context_service import _describe_screenshot
    return _describe_screenshot(b64, openai_key)


def maybe_capture(reason: str, openai_key: str, monitor_index: Optional[int] = None) -> Optional[dict]:
    """
    Capture + describe the screen — only called when Browser/Desktop
    Perception both yielded nothing. Returns None if throttled, no API key,
    or capture failed; never raises.
    """
    global _last_capture_at

    if not openai_key:
        logger.debug("[VISION_PERCEPTION_SKIP] reason=no_api_key")
        return None

    with _lock:
        now = time.monotonic()
        if now - _last_capture_at < _MIN_INTERVAL_SECONDS:
            logger.debug("[VISION_PERCEPTION_THROTTLED] since_last=%.1fs", now - _last_capture_at)
            return None
        _last_capture_at = now

    try:
        from api.services.screen_context_service import capture_screen_b64
        b64 = capture_screen_b64(monitor_index)
        if not b64:
            return None

        description = _describe(b64, openai_key)
        logger.info("[VISION_PERCEPTION_CAPTURE] reason=%s monitor=%s desc_len=%d",
                    reason, monitor_index, len(description))
        return {"description": description, "reason": reason, "monitor": monitor_index, "ts": time.time()}
    except Exception:
        logger.debug("[VISION_PERCEPTION] capture failed", exc_info=True)
        return None
