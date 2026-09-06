"""
voice_announcer.py — lets a background thread (outside the voice
WebSocket's asyncio event loop) ask the currently active voice session to
speak something out loud, using the session's own TTS pipeline.

This is a small, generic bridge — it doesn't know anything about WhatsApp.
The one active-session model matches this app: a single local desktop
assistant, one live voice session at a time. If a second session connects,
it simply replaces the registered one (last-connected wins), same as how
wake-word/session-active state already works elsewhere in this codebase.

Usage from the WebSocket session (api/routers/voice_ws.py):
    voice_announcer.register(asyncio.get_event_loop(), _announcement_queue)
    ...
    voice_announcer.unregister()   # in the session's finally block

Usage from any other thread (e.g. an SSE consumer thread):
    delivered = voice_announcer.announce({"text": "...", ...})
    # False if no voice session is currently connected — caller decides
    # what to do (WhatsApp auto-reply's incoming-message announcer, for
    # instance, just records context and skips speaking).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("voice_announcer")

_lock = threading.Lock()
_active_loop: Optional[asyncio.AbstractEventLoop] = None
_active_queue: Optional["asyncio.Queue[Dict[str, Any]]"] = None


def register(loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    global _active_loop, _active_queue
    with _lock:
        _active_loop = loop
        _active_queue = queue
    logger.info("[VOICE_ANNOUNCER] session registered")


def unregister(loop: asyncio.AbstractEventLoop) -> None:
    """Only clears the registration if it still belongs to *loop* — guards
    against a slow-closing old session clobbering a newer one's registration
    during the brief window where both could be shutting down/starting up."""
    global _active_loop, _active_queue
    with _lock:
        if _active_loop is loop:
            _active_loop = None
            _active_queue = None
    logger.info("[VOICE_ANNOUNCER] session unregistered")


def is_session_active() -> bool:
    with _lock:
        return _active_loop is not None and _active_queue is not None


def announce(payload: Dict[str, Any]) -> bool:
    """Thread-safe: callable from any thread. Returns True if a live voice
    session accepted the announcement, False if none is currently active
    (nothing is queued for later — the caller decides whether that matters)."""
    with _lock:
        loop, queue = _active_loop, _active_queue
    if loop is None or queue is None:
        return False
    try:
        loop.call_soon_threadsafe(queue.put_nowait, payload)
        return True
    except Exception as e:
        logger.warning(f"[VOICE_ANNOUNCER] failed to deliver announcement: {e}")
        return False
