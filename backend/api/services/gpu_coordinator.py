"""
gpu_coordinator.py — priority-based coordination for GPU/CPU-heavy workloads.

Root-cause context (voice-session 1011 "keepalive ping timeout" incident):
uvicorn's own protocol-level WebSocket keepalive (ping every 20s, expects a
pong within 20s — this is where the literal "keepalive ping timeout" string
comes from; it is not application code) runs on the same asyncio event loop
as everything else in this process. That loop needs the GIL to run at all.
A plain `threading.Thread` doing heavy, mostly-single-threaded CPU work
(observed: SentenceTransformer's model constructor — deserializing tensors,
building a tokenizer vocab) can hold the GIL for extended stretches without
yielding it back, starving the event loop hard enough that uvicorn's own
keepalive coroutine misses its window and the server closes the connection.
`asyncio.to_thread()` alone does not prevent this — it correctly yields at
the *asyncio* level, but two plain OS threads still contend for the same GIL
underneath it.

This module does not (and cannot) preempt a running CUDA kernel or GIL hold
— there is no safe way to interrupt either from Python. What it does is
cooperative: heavy, non-latency-critical GPU/CPU consumers check in here
before starting, and wait while a voice session's TTS/STT is actively in
flight, so they simply don't START a competing heavy load during the
window that matters. Priorities, highest first:

  1. Active voice TTS/STT           (never deferred — this is what waits for)
  2. Wake verification              (short, already bounded)
  3. Direct command execution       (tool calls)
  4. Background semantic/perception/indexing work   (defers here)
  5. Model warmups                  (defers here)

Logs: [GPU_JOB_WAIT] [GPU_JOB_ACQUIRED] [GPU_JOB_RELEASED]
      [GPU_BACKGROUND_DEFERRED] [VOICE_GPU_PRIORITY_ACTIVE]
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# Set for the duration of any voice-session TTS/STT work. Cleared as soon as
# that work finishes. Background GPU consumers wait for this to clear.
_voice_active = threading.Event()
_lock = threading.Lock()
_active_count = 0


def voice_priority_begin(reason: str = "") -> None:
    """Call when voice-critical GPU/CPU work starts (greeting synth, STT,
    response TTS). Re-entrant — safe to call from nested/overlapping turns
    within the same session."""
    global _active_count
    with _lock:
        _active_count += 1
        if _active_count == 1:
            _voice_active.set()
            logger.info("[VOICE_GPU_PRIORITY_ACTIVE] active=True reason=%s", reason)


def voice_priority_end(reason: str = "") -> None:
    """Call when that voice-critical work finishes. Must be paired with
    voice_priority_begin() — call from a finally block."""
    global _active_count
    with _lock:
        _active_count = max(0, _active_count - 1)
        if _active_count == 0:
            _voice_active.clear()
            logger.info("[VOICE_GPU_PRIORITY_ACTIVE] active=False reason=%s", reason)


def is_voice_active() -> bool:
    return _voice_active.is_set()


def wait_for_voice_idle(component: str, timeout: float = 30.0) -> bool:
    """
    Block the CALLING THREAD (never call from the asyncio event loop thread)
    until no voice session is actively doing TTS/STT, or *timeout* elapses.
    Returns True if it returned because voice went idle, False on timeout
    (caller proceeds anyway — this is cooperative deferral, not a hard gate,
    so a genuinely-stuck voice flag can never permanently block startup).
    """
    if not _voice_active.is_set():
        return True
    logger.info("[GPU_JOB_WAIT] component=%s", component)
    t0 = time.monotonic()
    while _voice_active.is_set():
        if time.monotonic() - t0 > timeout:
            logger.warning("[GPU_JOB_WAIT] component=%s timed out after %.0fs — proceeding anyway",
                           component, timeout)
            return False
        time.sleep(0.2)
    ms = (time.monotonic() - t0) * 1000
    logger.info("[GPU_JOB_ACQUIRED] component=%s waited_ms=%.0f", component, ms)
    return True


def defer_background_job(component: str, timeout: float = 30.0) -> None:
    """Convenience wrapper for background jobs (model warmups, semantic
    indexing) that just want to log-and-wait, then proceed regardless."""
    logger.info("[GPU_BACKGROUND_DEFERRED] component=%s", component)
    wait_for_voice_idle(component, timeout=timeout)
    logger.info("[GPU_JOB_RELEASED] component=%s", component)
