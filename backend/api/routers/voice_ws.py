"""
WebSocket endpoints for real-time wake detection and voice sessions.

Endpoints
─────────
  WS /api/v1/voice/ws/wake     — continuous OWW wake detection
  WS /api/v1/voice/ws/session  — full voice session (STT → LLM → TTS streaming)

Wake protocol (binary frames)
──────────────────────────────
  Client → Server:  5120 bytes = 1280 float32 samples @ 16kHz (one 80ms frame)
  Server → Client:  JSON text frames
    {"type": "ready",  "models": [...], "thresholds": {...}}
    {"type": "wake",   "model": "hey_xyron", "confidence": 0.87, "ts": 1234}
    {"type": "ping"}
    {"type": "error",  "message": "..."}

Session protocol
────────────────
  Client → Server:
    {"type":"config","voice":"nova","speed":1.0}   ← first text frame
    <binary float32 PCM frames, continuous>
    {"type":"end_of_speech"}                        ← optional client-VAD trigger
    {"type":"tts_done"}                             ← client finished playing TTS
  Server → Client:
    {"type":"ack",       "text":"Yes?",  "audio":"<b64 wav>"}  ← instant on session start
    {"type":"listening"}                                         ← mic is active
    {"type":"transcript","text":"...","final":false}
    {"type":"transcript","text":"...","final":true}
    {"type":"response",  "text":"...","chunk":1}
    {"type":"audio",     "data":"<b64 wav>","chunk":1,"final":false}
    {"type":"audio",     "data":"<b64 wav>","chunk":N,"final":true}
    {"type":"done"}
    {"type":"error",     "message":"..."}
"""
from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

# Phase 5.3: content-free filler spoken the instant speech is finalized —
# requires no transcript, no classification. Kept deliberately generic so
# it's honest regardless of what the command turns out to be.
_IMMEDIATE_ACK_PHRASES = ["Got it.", "Sure.", "One moment.", "On it."]

# Disabled by default — the generic filler ("Sure."/"Got it." etc.) spoken
# before STT/routing complete. Does not affect the wake greeting, final
# response TTS, narration, or per-tool acks like "Opening Calculator."
# (those are a separate mechanism — _build_ack_text — left untouched).
# Re-enable with XYRON_IMMEDIATE_ACK_ENABLED=true.
_IMMEDIATE_ACK_ENABLED = os.getenv("XYRON_IMMEDIATE_ACK_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _pick_immediate_ack() -> str:
    return random.choice(_IMMEDIATE_ACK_PHRASES)


# Lazy import — tracer available after backend package is on sys.path
def _tracer():
    from api.services.tracer import trace_store, new_trace_id
    return trace_store, new_trace_id


def _flight_session_active() -> bool:
    """Cheap check enabling short-command VAD mode. Single-word commands
    ("cancel", "Emirates") need a lower minimum-speech-frame threshold
    than full sentences, but only within an already-active, gated flight
    conversation — never globally, so background noise still can't
    trigger anything outside of that narrow context."""
    try:
        from api.agents.browser_agent import flight_session_state as _fss
        return _fss.get_active() is not None
    except Exception:
        return False

router = APIRouter(prefix="/api/v1/voice", tags=["voice-ws"])

FRAME_BYTES = 1280 * 4  # 5120 bytes per 80ms frame

# Whisper transcripts to drop unconditionally — known silence/TTS-bleed artifacts.
_KNOWN_HALLUCINATIONS: frozenset[str] = frozenset({
    "voice assistant command, urdu aur english mixed.",
    "voice assistant command urdu aur english mixed",
    "voice assistant command",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "like and subscribe",
    "subscribe and like",
    "thanks for listening",
})

# Ensure backend/ is on sys.path for voice.* imports
_BACKEND = str(Path(__file__).parent.parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── Safe send helper ──────────────────────────────────────────────────────────

async def _send(ws: WebSocket, payload: dict) -> bool:
    """Send JSON; returns False if the connection is already closed.

    Phase 4.12: the state check above is a check-then-act race — many
    concurrent tasks (timeout watcher, health monitor, TTS watchdogs,
    narration speaker loop, the main turn handler) can all be mid-send
    at once, so `client_state` can read CONNECTED here and then flip to
    CLOSING/DISCONNECTED microseconds later, before `send_text()` actually
    runs. Previously only `(WebSocketDisconnect, RuntimeError)` were
    caught — Starlette's own "Cannot call \"send\" once a close message
    has been sent" is a plain RuntimeError so that specific case *was*
    already covered, but the underlying ASGI transport/`websockets`
    library can raise other exception types during the same race
    (e.g. connection-reset errors) that would otherwise propagate
    uncaught out of a fire-and-forget task. `_send()`'s whole contract is
    "best-effort, return False on any failure" — narrowing what counts as
    "any failure" was the bug."""
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception as exc:
        logger.debug("[WS_SEND_FAILED] error=%r", str(exc)[:120])
        return False


async def _safe_close(ws: WebSocket, code: int = 1000, reason: str = "unspecified", tag: str = "WAKE_WS") -> None:
    """Idempotent close — the only function allowed to call `ws.close()`.

    Starlette's `WebSocket.close()` is just `send({"type": "websocket.close", ...})`
    under the hood, and `send()` flips `application_state` to DISCONNECTED
    the instant that message goes out — including when the *send itself*
    fails (e.g. `_send()` swallowing an OSError from a client that already
    vanished still leaves `application_state` DISCONNECTED). Any second
    caller hitting `.close()` after that lands in `send()`'s terminal
    `else` branch and raises "Cannot call \"send\" once a close message
    has been sent." — an uncaught RuntimeError out of a fire-and-forget
    task/handler. Checking `application_state` first and swallowing that
    specific race makes closing safe to call from multiple watchers
    (timeout watcher, health monitor, main handler, finally block) without
    them coordinating who "owns" the close."""
    logger.info("[%s_CLOSE_REQUESTED] reason=%s", tag, reason)
    if ws.application_state not in (WebSocketState.CONNECTING, WebSocketState.CONNECTED):
        logger.info("[%s_ALREADY_CLOSED] reason=%s", tag, reason)
        return
    try:
        await ws.close(code=code)
        logger.info("[%s_CLOSED] reason=%s", tag, reason)
    except (WebSocketDisconnect, RuntimeError) as exc:
        logger.info("[%s_ALREADY_CLOSED] reason=%s error=%r", tag, reason, str(exc)[:100])


# ── Wake Word WebSocket ───────────────────────────────────────────────────────

@router.websocket("/ws/wake")
async def ws_wake(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        from voice.wake_word_service import wake_word_service as _wws
    except Exception as exc:
        await _send(websocket, {"type": "error", "message": f"WakeWordService unavailable: {exc}"})
        await _safe_close(websocket, reason="service_unavailable")
        return

    # Wait up to 5s for OWW models to finish loading
    for _ in range(50):
        if _wws.oww_ready:
            break
        await asyncio.sleep(0.1)

    if not _wws.oww_ready:
        await _send(websocket, {"type": "error", "message": "Wake word models not loaded"})
        await _safe_close(websocket, reason="models_not_loaded")
        return

    # Reset singleton state so a stuck session gate from a previous connection
    # never blocks wake detection on reconnect.
    _wws.set_session_active(False)
    _wws.set_tts_playing(False)

    await _send(websocket, {
        "type":       "ready",
        "models":     _wws.model_names,
        "thresholds": {n: _wws._thresholds.get(n, 0.5) for n in _wws.model_names},
        "cooldown_s": 3.0,
    })
    logger.info("[WS/wake] connected — models: %s (session/tts gates reset)", _wws.model_names)
    logger.info("[WakeWord] session_active=False")

    PING_EVERY = 8.0   # shorter interval → detect dead connections faster

    # Rolling audio buffer: last 2.56s of PCM (32 × 1280 samples @ 16kHz).
    # Used for Whisper second-stage verification when OWW fires.
    _BUFFER_FRAMES  = 32   # full buffer kept for session audio
    _WHISPER_FRAMES = 15   # last 15 × 80ms = 1.2s sent to Whisper (isolates wake phrase)
    audio_buf: collections.deque[np.ndarray] = collections.deque(maxlen=_BUFFER_FRAMES)

    loop = asyncio.get_event_loop()

    try:
        while True:
            if websocket.client_state != WebSocketState.CONNECTED:
                break

            try:
                data = await asyncio.wait_for(websocket.receive(), timeout=PING_EVERY)
            except asyncio.TimeoutError:
                if not await _send(websocket, {"type": "ping"}):
                    break
                continue
            except WebSocketDisconnect:
                break

            msg_type = data.get("type")
            if msg_type == "websocket.disconnect":
                break

            raw = data.get("bytes")
            if raw and len(raw) == FRAME_BYTES:
                pcm = np.frombuffer(raw, dtype=np.float32).copy()
                audio_buf.append(pcm)   # always buffer — used for Whisper verify

                triggered, model_name, confidence = _wws.detect_frame(pcm)
                if triggered:
                    # ── Second-stage Whisper verification ────────────────────
                    # OWW models produce false positives from background noise.
                    # Whisper confirms a wake keyword was actually spoken before
                    # we send the wake event to the frontend.
                    clip = np.concatenate(list(audio_buf)[-_WHISPER_FRAMES:])
                    try:
                        from voice.whisper_service import verify_wake_phrase, _model_ready
                        if not _model_ready.is_set():
                            # Wait briefly rather than failing open — reject if Whisper
                            # doesn't become ready within 2s to prevent false wake triggers.
                            logger.info("[WS/wake] Whisper not ready — waiting up to 2s")
                            _ready = await loop.run_in_executor(
                                None, lambda: _model_ready.wait(timeout=2.0)
                            )
                            if not _ready:
                                logger.info("[WS/wake] WAKE_REJECTED_NOT_READY — Whisper unavailable after 2s")
                                matched, transcript = False, ""
                            else:
                                matched, transcript = await loop.run_in_executor(
                                    None, verify_wake_phrase, clip
                                )
                        else:
                            matched, transcript = await loop.run_in_executor(
                                None, verify_wake_phrase, clip
                            )
                    except Exception as exc:
                        logger.warning("[WS/wake] Whisper verify error: %s — rejecting wake", exc)
                        matched, transcript = False, ""

                    if not matched:
                        logger.info(
                            "[WS/wake] WAKE_REJECTED_WHISPER model=%s conf=%.3f transcript=%r",
                            model_name, confidence, transcript[:60],
                        )
                        continue

                    logger.info("[WakeWord] WAKE_ACCEPTED")
                    if not await _send(websocket, {
                        "type":       "wake",
                        "model":      model_name,
                        "confidence": round(confidence, 4),
                        "ts":         int(time.time() * 1000),
                    }):
                        break
                    logger.info("[WS/wake] WAKE model=%s conf=%.3f transcript=%r",
                                model_name, confidence, transcript[:60])
                    logger.info("[VOICE_STATE_CHANGE] from=IDLE_WAKE_LISTENING to=WAKE_DETECTED")
                continue

            text = data.get("text")
            if text:
                try:
                    msg = json.loads(text)
                    if msg.get("type") == "reset_cooldown":
                        _wws.reset_cooldown()
                    elif msg.get("type") == "tts_start":
                        _wws.set_tts_playing(True)
                    elif msg.get("type") == "tts_end":
                        _wws.set_tts_playing(False)
                    elif msg.get("type") == "session_start":
                        _wws.set_session_active(True)
                        logger.debug("[WS/wake] session gate OPEN")
                    elif msg.get("type") == "session_end":
                        # Do NOT call reset_cooldown() here — set_session_active(False)
                        # already resets _last_wake_t so TTS echo gets the 2s debounce.
                        _wws.set_session_active(False)
                        logger.debug("[WS/wake] session gate CLOSED")
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[WS/wake] unexpected error: %s", exc)
    finally:
        logger.info("[WS/wake] connection closed")


# ── Voice Session WebSocket ───────────────────────────────────────────────────

# VAD constants
_SILENCE_RMS       = 0.008   # RMS below this = silence
_SILENCE_FRAMES    = 9       # 9 × 80ms = 720ms silence → end of speech
_MIN_SPEECH_FRAMES = 9       # < 720ms → too short, discard (9 × 80ms frames)
_SHORT_MIN_SPEECH_FRAMES = 3 # 240ms — single-word commands ("cancel") during an active flight session
_INTERRUPT_RMS     = 0.020   # RMS above this during TTS = user interrupt

# Session constants
SESSION_TIMEOUT  = 45.0       # seconds of silence before session auto-ends

# TTS chunking: split response at sentence boundaries, max N chars per chunk
_TTS_MAX_CHARS   = 80


def _normalize_clock_for_tts(text: str) -> str:
    """
    Normalize clock/time responses for Kokoro TTS.
    Phonemizer struggles with contractions and colon-notation times.
    "It's 9:47 AM." → "It is nine forty-seven A M."
    """
    # Expand common contractions
    text = re.sub(r"\bIt's\b", "It is", text)
    text = re.sub(r"\bit's\b", "it is", text)
    text = re.sub(r"\bI'm\b",  "I am",  text)
    text = re.sub(r"\bI've\b", "I have", text)

    _ONES  = ['', 'one', 'two', 'three', 'four', 'five', 'six',
              'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']
    _TEENS = ['ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen',
              'sixteen', 'seventeen', 'eighteen', 'nineteen']
    _TENS  = ['', '', 'twenty', 'thirty', 'forty', 'fifty']

    def _spell_time(m: re.Match) -> str:
        h   = int(m.group(1))
        mn  = int(m.group(2))
        per = (m.group(3) or "").strip().upper()

        h_str = _ONES[h] if 1 <= h <= 12 else str(h)

        if mn == 0:
            mn_str = "o'clock"
        elif mn < 10:
            mn_str = f"oh {_ONES[mn]}"
        elif mn < 20:
            mn_str = _TEENS[mn - 10]
        else:
            t, o   = divmod(mn, 10)
            mn_str = _TENS[t] + (" " + _ONES[o] if o else "")

        result = f"{h_str} {mn_str}" if mn else h_str
        if per in ("AM", "PM"):
            result += " " + " ".join(per)   # "AM" → "A M", "PM" → "P M"
        return result

    text = re.sub(r'\b(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?\b', _spell_time, text)
    # Standalone AM/PM left after substitution
    text = re.sub(r'\bAM\b', 'A M', text)
    text = re.sub(r'\bPM\b', 'P M', text)
    return text


def _split_for_tts(text: str) -> list[str]:
    """
    Split response into TTS-sized chunks at sentence boundaries.
    Short text stays as one chunk for lower latency.
    """
    if len(text) <= _TTS_MAX_CHARS:
        return [text]
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if not s:
            continue
        if len(current) + len(s) + 1 <= _TTS_MAX_CHARS:
            current = (current + " " + s).strip() if current else s
        else:
            if current:
                chunks.append(current)
            # Sentence longer than max — hard split at word boundary
            if len(s) > _TTS_MAX_CHARS:
                words = s.split()
                current = ""
                for w in words:
                    if len(current) + len(w) + 1 <= _TTS_MAX_CHARS:
                        current = (current + " " + w).strip() if current else w
                    else:
                        if current:
                            chunks.append(current)
                        current = w
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks or [text[:_TTS_MAX_CHARS]]


async def _synthesize_chunk(text: str, voice: str, speed: float) -> Optional[bytes]:
    """Synthesize one TTS chunk via Kokoro; retry once on None or exception.

    Phase 4.15: every narration line (stage narration, conversation-layer
    lines, final responses) went through this function calling Kokoro
    directly, entirely bypassing tts_cache_service — so pre-caching phrases
    at startup had no effect on the narration path, only on the separate
    immediate-ack code path. Live-measured consequence: once browser
    actions became near-instant (Phase 4.14's pre-warmed workspace), the
    ~200-800ms of *uncached* synthesis latency here meant the visible
    action consistently finished before its own narration audio started —
    "voice describes actions after they've already happened". Routing
    through the same cache used elsewhere fixes both problems at once:
    cache hits for pre-warmed/repeated phrases are ~10-200ms, and any
    miss self-populates the cache for next time (already `synthesize_or_cached`'s
    existing behavior, just never called from here before).
    """
    for attempt in range(2):
        try:
            from api.routers.voice import _kokoro_to_wav
            # The cache is built for a single voice (main.py's warmup) —
            # only use it when the session's voice actually matches, same
            # guard already used for the ACK-cache path elsewhere, otherwise
            # a cache hit would silently play the wrong voice.
            from api.services.tts_cache_service import tts_cache as _tcc_chunk
            _cache_voice = getattr(_tcc_chunk, "_build_voice", "nova")
            if _cache_voice == voice:
                wav = await asyncio.wait_for(
                    asyncio.to_thread(_tcc_chunk.synthesize_or_cached, text, voice, speed),
                    timeout=25.0,
                )
            else:
                wav = await asyncio.wait_for(
                    asyncio.to_thread(_kokoro_to_wav, text, voice, speed),
                    timeout=25.0,
                )
            if wav:
                return wav
            if attempt == 0:
                logger.warning("[WS/session] Kokoro returned None, retrying...")
                await asyncio.sleep(0.15)
        except asyncio.TimeoutError:
            logger.warning("[WS/session] Kokoro timed out (attempt %d) text=%r",
                           attempt + 1, text[:40])
            if attempt == 0:
                await asyncio.sleep(0.15)
            else:
                return None
        except Exception as exc:
            if attempt == 0:
                logger.warning("[WS/session] Kokoro attempt 1 failed, retrying: %s", exc)
                await asyncio.sleep(0.15)
            else:
                logger.error("[WS/session] Kokoro failed after retry: %s", exc)
    return None


@router.websocket("/ws/session")
async def ws_session(websocket: WebSocket) -> None:
    await websocket.accept()

    # ── Phase 3: Readiness gate — reject session if boot not complete ─────────
    try:
        from api.services.readiness_service import readiness_service as _rs
        if not _rs.is_core_ready:
            logger.info("[SESSION_REJECTED_NOT_READY] state=%s", _rs.state.value)
            await _send(websocket, _rs.not_ready_payload())
            await _safe_close(websocket, code=1013, reason="not_ready", tag="SESSION_WS")
            return
    except Exception as _re:
        logger.debug("[READINESS_CHECK] skipped: %s", _re)

    voice          = "nova"
    speed          = 1.0
    preferred_name = ""  # set via config frame; used in greeting + responses

    # Config frame (first message — wait up to 5s)
    _cfg_lang_hint: str | None = None   # e.g. "ur", "ar" — pre-seeds STT model selection
    try:
        first = await asyncio.wait_for(websocket.receive(), timeout=5.0)
        if first.get("text"):
            cfg = json.loads(first["text"])
            if cfg.get("type") == "config":
                voice          = cfg.get("voice", voice)
                speed          = float(cfg.get("speed", speed))
                preferred_name = (cfg.get("preferred_name") or "").strip()
                _cfg_lang_hint = cfg.get("language") or None  # "ur"/"ar" → skip tiny.en
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        pass

    logger.info("[SESSION_CREATE] voice=%s speed=%.1f name=%r lang_hint=%s",
                voice, speed, preferred_name, _cfg_lang_hint)
    logger.info("[SESSION_WS_CONNECT] remote=%s", getattr(websocket, 'client', 'unknown'))

    # Notify debug API that a voice session is now active
    try:
        from api.routers.debug import update_session_state as _upd
        _upd(voice_connected=True, session_start_ts=time.time(), current_state="greeting")
    except Exception:
        pass
    try:
        from api.routers.debug import update_audio_state as _upd_audio
        _upd_audio(voice_ws_connected=True, frontend_connected=True, mic_active=False,
                   audio_chunks_received=0, last_audio_timestamp=None)
    except Exception:
        pass

    # Block wake word for the duration of this session
    logger.info("[VOICE_STATE_CHANGE] from=WAKE_DETECTED to=SESSION_ACTIVE")
    try:
        from voice.wake_word_service import wake_word_service as _wws
        _wws.set_session_active(True)
        logger.info("[WakeWord] session_active=True")
    except Exception:
        pass


    # ── Session state ─────────────────────────────────────────────────────────
    pcm_buffer: list[np.ndarray] = []
    silence_count           = 0
    speech_started          = False
    is_speaking             = False      # True while TTS is streaming
    demo_mode               = False      # True during intro demo — disables VAD
    last_activity_t         = time.time()
    interrupt_event         = asyncio.Event()
    _tts_playback_done_event = asyncio.Event()   # set by tts_done from client; waited on by showcase
    _tts_playback_done_event.set()               # starts ready (no pending playback)
    last_response_text: str = ""         # for CLARIFY repetition
    current_turn_id         = 0          # monotonic counter; stale tasks self-abort when this advances
    _audio_chunks_received  = 0          # total binary PCM frames received from frontend
    _last_audio_ts: Optional[float] = None
    _last_pcm_rms: float    = 0.0        # latest RMS from main loop; used by mic re-arm watchdog
    _post_tts_flush_until: float = 0.0  # discard mic frames until this timestamp after TTS ends

    # Pending confirmation: set when a tool returns error="confirm_required".
    # pending_store_candidates: set when install_store_app returns error="store_disambiguation".
    # Mutable dict avoids nonlocal in nested closures.
    _session_state: dict = {
        "pending_confirmation":       None,
        "pending_control_confirmation": None,  # set when a short utterance looked like a
                                                # misheard control word (Phase 5.4B)
        "pending_store_candidates":   None,
        "pending_open_after_install": None,   # set after install_store_app_exec succeeds
        "ml_detected_lang":           _cfg_lang_hint or "en",  # pre-seed from config; else "en"
        "ml_resp_lang":               "en",   # language to use for TTS response
    }
    if _cfg_lang_hint and _cfg_lang_hint not in ("en",):
        logger.info("[SESSION_LANG_HINT] pre-seeded ml_detected_lang=%s → multilingual STT active", _cfg_lang_hint)

    import uuid as _uuid
    _session_id = str(_uuid.uuid4())  # stable ID for context_resolver within this WS session

    from brain.memory_manager import new_session_memory
    memory = new_session_memory()

    # ── Sentinel — start background health monitor for this session ───────────
    try:
        from api.services.sentinel_service import sentinel_service as _sentinel
        from api.services.background_scheduler import scheduler as _sched_sent, JobPriority as _JP_sent
        _sched_sent.register("sentinel", _JP_sent.BACKGROUND_IDLE_ONLY)
        _sentinel.start()
    except Exception:
        pass

    # ── Session timeout watcher ───────────────────────────────────────────────

    async def _timeout_watcher() -> None:
        while websocket.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(1.0)
            idle_s = time.time() - last_activity_t
            if not is_speaking and idle_s > SESSION_TIMEOUT:
                await _send(websocket, {
                    "type":   "session_timeout",
                    "reason": "inactivity",
                    "idle_s": round(idle_s, 1),
                })
                logger.info("[SESSION_DESTROY] reason=inactivity idle_s=%.1f", idle_s)
                logger.info("[SESSION_DESTROY_REASON] inactivity timeout after %.1fs — no speech detected", idle_s)
                logger.info("[SESSION_WS_DISCONNECT] code=1000 reason=inactivity")
                await _safe_close(websocket, code=1000, reason="inactivity", tag="SESSION_WS")
                break

    asyncio.create_task(_timeout_watcher())

    # ── Session health monitor — logs every 5 s for freeze/stuck detection ────

    async def _health_monitor() -> None:
        while websocket.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(5.0)
            try:
                _ts, _ = _tracer()
                _tid = _ts.current_trace_id() or "none"
            except Exception:
                _tid = "unavailable"
            logger.info(
                "[SESSION_HEALTH] session_active=True voice_connected=True "
                "mic_active=%s audio_queue_depth=%d speaking=%s current_trace=%s",
                speech_started,
                len(pcm_buffer),
                is_speaking,
                _tid,
            )

    asyncio.create_task(_health_monitor())

    # ── Event loop lag + resource monitor ─────────────────────────────────────
    async def _lag_monitor() -> None:
        import psutil as _psutil
        import os as _os_lag
        _proc = _psutil.Process(_os_lag.getpid())
        while websocket.client_state == WebSocketState.CONNECTED:
            _t0 = time.monotonic()
            await asyncio.sleep(1.0)
            lag_ms = (time.monotonic() - _t0 - 1.0) * 1000
            try:
                cpu  = _proc.cpu_percent(interval=None)
                mem  = _proc.memory_info().rss / (1024 * 1024)
            except Exception:
                cpu = mem = 0.0
            logger.debug(
                "[EVENT_LOOP_LAG] lag_ms=%.1f [CPU_USAGE] cpu=%.1f%% [MEMORY_USAGE] mem_mb=%.0f",
                lag_ms, cpu, mem,
            )
            if lag_ms > 100:
                # Identify which tasks are running to pinpoint the blocker
                try:
                    _all_tasks = asyncio.all_tasks()
                    _task_names = [
                        (t.get_name(), getattr(t.get_coro(), '__qualname__', '?'))
                        for t in _all_tasks if not t.done()
                    ]
                    _task_str = "; ".join(f"{n}/{q}" for n, q in _task_names[:6])
                except Exception:
                    _task_str = "unavailable"
                logger.warning(
                    "[EVENT_LOOP_BLOCKER] lag=%.0fms cpu=%.1f%% mem=%.0fMB tasks=[%s]",
                    lag_ms, cpu, mem, _task_str,
                )

    asyncio.create_task(_lag_monitor())

    # ── Multilingual TTS helper — XTTS-v2 for non-English responses ─────────

    async def _tts_ml(text: str, lang: str) -> bool:
        """Synthesize text using multilingual TTS (XTTS-v2).
        Falls back to Kokoro English if XTTS is unavailable or synthesis fails.
        Sends a single audio frame (XTTS is not chunked like Kokoro).
        """
        _tts_state["audio_sent"] = False
        _tts_playback_done_event.clear()
        _ml_t0 = time.time()
        logger.info("[TTS_ML_ENTER] lang=%s chars=%d", lang, len(text))
        wav = None
        try:
            from voice.tts_router import synthesize as _route_synth
            wav = await asyncio.wait_for(
                asyncio.to_thread(_route_synth, text, lang),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("[XTTS_FALLBACK_TO_TEXT] lang=%s error=30s_timeout", lang)
        except Exception as _ml_exc:
            logger.error("[XTTS_FALLBACK_TO_TEXT] lang=%s error=%s", lang, _ml_exc)

        if not wav:
            # Both XTTS and Kokoro fallback within tts_router already attempted;
            # if still None, there is no audio — signal no audio sent and return.
            logger.warning("[XTTS_FALLBACK_TO_TEXT] lang=%s no audio produced", lang)
            _tts_playback_done_event.set()
            return False

        sent = await _send(websocket, {
            "type":  "audio",
            "data":  base64.b64encode(wav).decode(),
            "chunk": 1,
            "total": 1,
            "final": True,
            "text":  text,
        })
        if sent:
            _tts_state["audio_sent"] = True
            logger.info("[TTS_ML_DONE] lang=%s ms=%.0f bytes=%d",
                        lang, (time.time() - _ml_t0) * 1000, len(wav))
        else:
            _tts_playback_done_event.set()

        if not _tts_state["audio_sent"]:
            _tts_playback_done_event.set()
        return False

    # ── TTS helper: sequential synthesis for short (tool/memory) responses ────

    # Mutable flag: did the most recent _tts_sequential call send a final audio chunk?
    # Checked after each call to decide whether to send "done" or fall back to "listening".
    _tts_state: dict = {"audio_sent": False}

    # Phase 5.3: holds the in-flight immediate-ack task (if any) so every
    # other _tts_sequential call — the contextual ack, a tool response, an
    # LLM reply, narration fallback — waits for it to finish sending before
    # starting its own audio stream. Prevents two concurrent _tts_sequential
    # calls from racing on _tts_state/_tts_playback_done_event, which is
    # only ever safe for one caller at a time (see _narration_queue comment
    # below for the same constraint in the narration path).
    _immediate_ack_state: dict = {"task": None}

    # ── Live narration queue (travel-consultant "thinking aloud") ─────────────
    # A long-running background task (e.g. flight_search_agent.search_and_compare)
    # cannot call _tts_sequential directly — it mutates shared _tts_state /
    # _tts_playback_done_event, which is only safe for one caller at a time,
    # and blocking on TTS playback there would stall the actual browser
    # automation. Instead, narration-worthy stage messages are pushed onto
    # this queue (non-blocking put_nowait, from _coord_ws_send below) and
    # spoken one at a time by _narration_speaker_loop, running for the life
    # of this WS connection, fully decoupled from whatever async task
    # produced the narration.
    _narration_queue: "asyncio.Queue[str]" = asyncio.Queue()

    async def _narration_speaker_loop() -> None:
        while True:
            text = await _narration_queue.get()
            try:
                # Phase 4.15: if browser events fire faster than they can be
                # spoken (common now that browser actions are near-instant —
                # see Phase 4.14), several narration lines can queue up
                # before this loop gets to the first one. Speaking all of
                # them in order would mean describing a page state that's
                # long since moved on ("I'm opening Google Flights..." said
                # after the page already loaded and results are showing).
                # Collapse the backlog to just the most recent line — it
                # reflects the browser's actual current state.
                _dropped = 0
                while not _narration_queue.empty():
                    try:
                        text = _narration_queue.get_nowait()
                        _narration_queue.task_done()
                        _dropped += 1
                    except asyncio.QueueEmpty:
                        break
                if _dropped:
                    logger.info("[VOICE_NARRATION_SUPERSEDED] dropped=%d kept=%r", _dropped, text[:80])
                if is_speaking:
                    # A turn-response is already mid-speech — don't talk over
                    # it; drop this narration line rather than queue up a
                    # backlog that reads out stale status updates later.
                    logger.info("[VOICE_NARRATION_SKIPPED] reason=already_speaking text=%r", text[:80])
                    continue
                logger.info("[VOICE_NARRATION] text=%r", text[:200])
                _narr_t0 = time.time()
                logger.info("[NARRATION_START] text=%r", text[:80])
                await _tts_with_fallback(text)
                logger.info("[NARRATION_END] ms=%.0f", (time.time() - _narr_t0) * 1000)
            except Exception as exc:
                logger.debug("[VOICE_NARRATION] speak error (ignored): %r", exc)
            finally:
                _narration_queue.task_done()

    async def _tts_sequential(
        text: str, _voice_override: str | None = None, _speed_override: float | None = None,
        _is_immediate_ack: bool = False,
    ) -> bool:
        """Synthesize `text` chunk-by-chunk and stream to client. Interruption disabled."""
        # Phase 5.3: every call except the immediate-ack's own waits for that
        # ack to finish sending first — it may still be in flight since it
        # was queued at speech-end, before STT even started, deliberately
        # concurrent with everything up to this point.
        if not _is_immediate_ack:
            _pending_ack = _immediate_ack_state["task"]
            if _pending_ack is not None and not _pending_ack.done():
                _wait_t0 = time.time()
                try:
                    await _pending_ack
                except Exception:
                    pass
                logger.info("[MICRO_PROFILE] op=wait_for_immediate_ack wait_ms=%.1f",
                            (time.time() - _wait_t0) * 1000)
        # ── Multilingual routing: delegate to XTTS if response language is non-English ──
        _resp_lang = _session_state.get("ml_resp_lang", "en")
        if _resp_lang != "en":
            # Localize English response text to the target language before speaking
            try:
                from api.services.response_localizer import localize_response as _loc_fn
                _loc = _loc_fn(text, _resp_lang)
                if _loc:
                    logger.info("[RESP_LOCALIZED_FOR_TTS] %r → %r", text[:40], _loc[:40])
                    text = _loc
            except Exception:
                pass
            return await _tts_ml(text, _resp_lang)
        # ── English Kokoro path (unchanged) ──────────────────────────────────────────────
        _tts_state["audio_sent"] = False
        _tts_playback_done_event.clear()  # playback is now pending
        _v = _voice_override or voice
        _tts_t0 = time.time()
        logger.info("[TTS_STATE_ENTER] chars=%d voice=%s", len(text), _v)
        _spd = _speed_override if _speed_override is not None else speed
        chunks = _split_for_tts(text)
        n = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            wav = await _synthesize_chunk(chunk, _v, _spd)
            if wav:
                sent = await _send(websocket, {
                    "type":  "audio",
                    "data":  base64.b64encode(wav).decode(),
                    "chunk": i,
                    "total": n,
                    "final": (i == n),
                    "text":  chunk,
                })
                if sent and (i == n):
                    _tts_state["audio_sent"] = True
                if not sent:
                    logger.info("[TTS_STATE_EXIT] ms=%.0f chunks=%d reason=send_failed audio_sent=%s",
                                (time.time() - _tts_t0) * 1000, i, _tts_state["audio_sent"])
                    if not _tts_state["audio_sent"]:
                        _tts_playback_done_event.set()  # no audio sent → no tts_done will arrive
                    return False
            if websocket.client_state != WebSocketState.CONNECTED:
                logger.info("[TTS_STATE_EXIT] ms=%.0f chunks=%d reason=disconnected audio_sent=%s",
                            (time.time() - _tts_t0) * 1000, i, _tts_state["audio_sent"])
                if not _tts_state["audio_sent"]:
                    _tts_playback_done_event.set()
                return False
        logger.info("[TTS_STATE_EXIT] ms=%.0f chunks=%d audio_sent=%s",
                    (time.time() - _tts_t0) * 1000, n, _tts_state["audio_sent"])
        if not _tts_state["audio_sent"]:
            _tts_playback_done_event.set()  # synthesis produced no audio → no tts_done will arrive
        return False

    async def _tts_with_fallback(text: str) -> bool:
        """_tts_sequential with one retry on a fallback voice if no audio was sent."""
        _interrupted = await _tts_sequential(text)
        if not _tts_state["audio_sent"]:
            _fb = "alloy" if voice != "alloy" else "nova"
            logger.warning("[TTS_FALLBACK_ATTEMPT] primary_voice=%s no_audio — retrying with fallback_voice=%s text=%r",
                           voice, _fb, text[:40])
            _interrupted = await _tts_sequential(text, _fb)
            if _tts_state["audio_sent"]:
                logger.info("[TTS_FALLBACK_SUCCESS] fallback_voice=%s", _fb)
            else:
                logger.error("[TTS_FALLBACK_FAILED] all voices failed — sending listening text=%r", text[:40])
        return _interrupted

    # Started once per WS connection, now that _tts_with_fallback exists in
    # this closure — drains _narration_queue for the life of the session.
    _narration_task = asyncio.create_task(_narration_speaker_loop())

    async def _tts_await_playback(text: str) -> bool:
        """_tts_with_fallback + blocks until client signals tts_done (showcase sequencing).

        Guarantees narration is fully played back before returning, so showcase
        actions only execute after the viewer has heard the narration.
        """
        _interrupted = await _tts_with_fallback(text)
        if _tts_state["audio_sent"]:
            try:
                await asyncio.wait_for(_tts_playback_done_event.wait(), timeout=8.0)
                logger.info("[TTS_PLAYBACK_CONFIRMED] tts_done received — showcase may proceed")
            except asyncio.TimeoutError:
                logger.warning("[TTS_PLAYBACK_WAIT_TIMEOUT] tts_done not received within 8s — proceeding anyway")
                _tts_playback_done_event.set()  # reset for next step
        return _interrupted

    # Human-readable names for settings pages used in ACK text
    _SETTINGS_PAGE_NAMES: dict[str, str] = {
        "wifi":            "Wi-Fi",
        "network":         "Network",
        "bluetooth":       "Bluetooth",
        "display":         "Display",
        "sound":           "Sound",
        "privacy":         "Privacy",
        "apps":            "Apps",
        "update":          "Windows Update",
        "power":           "Power",
        "storage":         "Storage",
        "accounts":        "Accounts",
        "time":            "Date and Time",
        "language":        "Language",
        "accessibility":   "Accessibility",
        "notifications":   "Notifications",
        "personalization": "Personalization",
        "themes":          "Themes",
        "taskbar":         "Taskbar",
        "startup":         "Startup Apps",
        "mouse":           "Mouse",
        "keyboard":        "Keyboard",
        "camera":          "Camera",
        "home":            "Settings",
    }

    def _build_ack_text(tool_name: str, tool_params: dict) -> str:
        """Return a command-aware acknowledgement phrase for the given tool."""
        if tool_name == "open_application":
            app = (tool_params.get("app") or tool_params.get("app_name") or
                   tool_params.get("name") or "").strip()
            text = f"Opening {app.title()}." if app else "Opening it."
        elif tool_name == "open_system_settings":
            page = (tool_params.get("page") or "").strip().lower()
            nice = _SETTINGS_PAGE_NAMES.get(page, page.replace("-", " ").replace("_", " ").title())
            text = f"Opening {nice} Settings." if (nice and page != "home") else "Opening Settings."
        elif tool_name == "open_drive":
            drive = (tool_params.get("drive") or "").upper().replace("DRIVE", "").strip()
            text = f"Opening {drive} Drive." if drive else "Opening the drive."
        elif tool_name in ("open_directory", "smart_open"):
            raw = (tool_params.get("query") or tool_params.get("path") or "").strip()
            name = Path(raw).name if ("/" in raw or "\\" in raw) else raw
            text = f"Opening {name.title()}." if name else "Opening it."
        elif tool_name == "search_youtube":
            q = (tool_params.get("query") or "").strip()
            text = f"Playing {q[:35].title()}." if q else "Opening YouTube."
        elif tool_name == "search_web":
            text = "Searching the web."
        elif tool_name == "open_url":
            text = "Opening it."
        elif tool_name == "play_media_file":
            q = (tool_params.get("query") or "").strip()
            text = f"Playing {q[:35]}." if q else "Playing it."
        elif tool_name == "install_store_app":
            app = (tool_params.get("app_name") or "").strip()
            text = f"Searching for {app.title()} in the Store." if app else "Searching the Store."
        else:
            text = "On it."
        logger.info("[COMMAND_ACK_SELECTED] tool=%s params=%s text=%r", tool_name, tool_params, text)
        return text

    # ── Tool execution helper ─────────────────────────────────────────────────

    async def _run_tool(tool_name: str, tool_params: dict, goal: str = "") -> str:
        # Operator mode is disabled — route directly to registered tools only
        logger.info("[DIRECT_TOOL_EXECUTION] tool=%s", tool_name)

        from api.tools import registry as _registry
        from api.config import settings as _cfg
        try:
            from api.services.window_context import window_context as _wctx
            # Run in thread — on WSL2 this may call PS session (~30ms); must not block event loop
            _aw = await asyncio.to_thread(_wctx.get_active_window)
        except Exception:
            _aw = None
        _ctx = {"openai_key": _cfg.openai_api_key, "active_window": _aw}
        # ── Trace instrumentation ─────────────────────────────────────────────
        try:
            _ts, _ = _tracer()
            _t_id = _ts.current_trace_id() or "?"
        except Exception:
            _t_id, _ts = "?", None
        _safe_p = {k: v for k, v in tool_params.items() if k != "openai_key"}
        logger.info("[TRACE %s] [TOOL_START] tool=%s params=%s", _t_id, tool_name, _safe_p)
        # Phase 4.11 Part 8: same canonical progress-event family the
        # flight workflow uses, so simple direct commands ("open
        # settings") get equally specific status instead of an implicit
        # "Listening" the whole time — traceable in logs even without a
        # wired frontend consumer.
        logger.info("[PROGRESS_EVENT_CREATED] tool=%s label=%r", tool_name, f"Opening {tool_name.replace('_', ' ')}")
        _tool_t0 = time.time()
        result = await asyncio.to_thread(_registry.execute, tool_name, tool_params, _ctx)
        _tool_ms = (time.time() - _tool_t0) * 1000
        logger.info("[PERF_TOOL] tool=%s ms=%.0f success=%s", tool_name, _tool_ms, result.success)
        if result.success:
            logger.info("[TRACE %s] [TOOL_SUCCESS] tool=%s ms=%.0f",
                        _t_id, tool_name, _tool_ms)
            logger.info("[PROGRESS_EVENT_CREATED] tool=%s label=%r", tool_name, "Done")
        else:
            logger.warning("[TRACE %s] [TOOL_FAIL] type=TOOL_FAILURE tool=%s ms=%.0f error=%r",
                           _t_id, tool_name, _tool_ms, (result.text or "")[:80])
            # Record tool failure with sentinel
            try:
                from api.services.sentinel_service import sentinel_service as _sent
                _sent.record_tool_failure(tool_name)
            except Exception:
                pass
        # ── Async verifier — fire-and-forget, never blocks voice output ───────
        _BLOCKING_VERIFY_TOOLS = {"install_store_app", "delete_file", "format_drive"}
        try:
            from api.services.verifier_v2 import verify as _vfy, log_verify_result as _log_vfy
            _vresult_data = result.data or {}
            if tool_name in _BLOCKING_VERIFY_TOOLS:
                logger.info("[VERIFY_BLOCKING_REQUIRED] tool=%s — running blocking verify", tool_name)
                _vr_blocking = await asyncio.to_thread(
                    _vfy, tool_name, tool_params, result.success, _vresult_data
                )
                _log_vfy(_vr_blocking)
                logger.info("[BRAIN_PIPELINE] stage=verify tool=%s verified=%s method=%s",
                            tool_name, _vr_blocking.verified, _vr_blocking.verification_method)
            else:
                logger.info("[VERIFY_ASYNC_STARTED] tool=%s — running async (non-blocking)", tool_name)
                async def _do_verify() -> None:
                    try:
                        _vr = await asyncio.to_thread(
                            _vfy, tool_name, tool_params, result.success, _vresult_data
                        )
                        _log_vfy(_vr)
                        logger.info("[VERIFY_ASYNC_SUCCESS] tool=%s verified=%s method=%s ms=%.0f",
                                    tool_name, _vr.verified, _vr.verification_method,
                                    _vr.latency_ms or 0)
                        logger.info("[BRAIN_PIPELINE] stage=verify tool=%s verified=%s method=%s",
                                    tool_name, _vr.verified, _vr.verification_method)
                    except Exception as _ve:
                        logger.debug("[VERIFY_ASYNC_ERROR] tool=%s: %s", tool_name, _ve)
                asyncio.create_task(_do_verify())
        except Exception:
            pass
        if _ts and _t_id != "?":
            _rec = _ts.get(_t_id)
            if _rec:
                _rec.tools_executed.append({
                    "tool":    tool_name,
                    "params":  _safe_p,
                    "ms":      round(_tool_ms, 1),
                    "success": result.success,
                    "result":  (result.spoken or result.text or "")[:120],
                })
        # Persist to context memory for pronoun resolution next turn
        try:
            from api.services.context_memory import context_memory as _cm
            _data  = result.data or {}
            _paths = [str(p) for p in _data.get("paths", [])]
            _ents  = [str(e) for e in _data.get("entities", [])]
            if not _paths and tool_params.get("path"):
                _paths = [str(tool_params["path"])]
            _cm.record_action(tool_name, _ents, _paths)
        except Exception:
            pass
        try:
            from api.services.memory_service import memory_service as _ms
            logger.debug("[MEMORY_LOAD_SKIPPED_COMMAND_PATH] memory_service already imported — no disk I/O")
            # Run in thread: set_last_action → _save_facts() does file I/O; must not block event loop
            _rdata2 = result.data or {}
            async def _persist_memory() -> None:
                try:
                    _ms.set_last_action(tool_name, tool_params, result.text)
                    if _rdata2.get("error_type") == "multiple_matches":
                        _ms.set_disambiguation_matches(
                            _rdata2.get("matches", []),
                            _rdata2.get("query", ""),
                            tool_params,
                        )
                except Exception:
                    pass
            asyncio.create_task(_persist_memory())
        except Exception:
            pass
        # ── Confirmation gate — tool requires user yes/no before executing ───
        if result.error == "confirm_required":
            _session_state["pending_confirmation"] = {
                "tool":   result.data.get("tool", ""),
                "params": result.data.get("params", {}),
                "prompt": result.data.get("prompt", "Should I proceed? Say yes or no."),
            }
            logger.info("[CONFIRMATION_PENDING] tool=%s prompt=%r",
                        result.data.get("tool"), result.data.get("prompt", "")[:60])
            return result.data.get("prompt", "Should I proceed? Say yes or no.")

        # ── Store disambiguation gate — multiple similar candidates ───────────
        if result.error == "store_disambiguation":
            _cands = (result.data or {}).get("candidates", [])
            _src_q = (result.data or {}).get("source_query", "")
            _session_state["pending_store_candidates"] = {
                "candidates":   _cands,
                "source_query": _src_q,
                "created_at":   time.time(),
            }
            logger.info("[STORE_SELECTION_PENDING] source_query=%r candidates=%d",
                        _src_q, len(_cands))
            return result.data.get("prompt", "Which one would you like to install?")

        # ── Active context update — track current platform/goal/folder ────────
        if result.success:
            try:
                from api.services.active_context import active_context as _actx
                _rdata_ctx = result.data or {}
                # Run off event loop — may do minor dict work but keep it fast
                asyncio.create_task(asyncio.to_thread(
                    _actx.update_from_tool, tool_name, tool_params, _rdata_ctx, True
                ))
            except Exception:
                pass
            # ── ContextStack update — entity history for V2 follow-up resolution ─
            try:
                from api.services.context_stack import context_stack as _cstack
                _rdata_cs = result.data or {}
                asyncio.create_task(asyncio.to_thread(
                    _cstack.update_from_tool, tool_name, tool_params, _rdata_cs, True
                ))
            except Exception:
                pass

        # ── Open-after-install offer — store pending state so Tier 0d2 can act ─
        if result.success and (result.data or {}).get("open_offer"):
            _oai_app = (result.data or {}).get("app_name", "")
            _oai_id  = (result.data or {}).get("app_id", "")
            if _oai_app:
                _session_state["pending_open_after_install"] = {
                    "app_name":   _oai_app,
                    "app_id":     _oai_id,
                    "created_at": time.time(),
                }
                logger.info("[STORE_OPEN_FOLLOWUP_CREATED] app=%r id=%r", _oai_app, _oai_id)

        return (result.spoken or result.text or "Done.").strip()

    # ── LLM streaming path: overlapped generation + TTS ──────────────────────

    async def _run_llm_stream(transcript: str, history: list[dict]) -> tuple[str, bool]:
        """
        Stream LLM tokens → sentence chunks → Kokoro TTS in parallel.
        Returns (full_response_text, interrupted).
        """
        from api.services.response_pipeline import stream_response_with_tts
        logger.info("[TTS_STARTED] streaming LLM+TTS pipeline")
        full_text  = ""
        interrupted = False
        try:
            async for sentence, wav, chunk_idx, is_final in stream_response_with_tts(
                transcript, history, voice=voice, speed=speed
            ):
                full_text += sentence + " "
                await _send(websocket, {"type": "response", "text": sentence, "chunk": chunk_idx})
                if wav:
                    await _send(websocket, {
                        "type":  "audio",
                        "data":  base64.b64encode(wav).decode(),
                        "chunk": chunk_idx,
                        "final": is_final,
                        "text":  sentence,
                    })
                if websocket.client_state != WebSocketState.CONNECTED:
                    interrupted = True
                    break
        except Exception as exc:
            logger.warning("[WS/session] LLM stream error: %s", exc)
            fallback = "I ran into an issue. Please try again."
            await _send(websocket, {"type": "response", "text": fallback, "chunk": 1})
            await _tts_sequential(fallback)
            full_text = fallback
        return full_text.strip(), interrupted

    # ── Utterance processor ───────────────────────────────────────────────────

    async def process_utterance(frames: list[np.ndarray], my_turn: int) -> None:
        nonlocal is_speaking, last_activity_t, last_response_text

        async def _spawn_tts_watchdog(route: str = "generic") -> None:
            """Send listening if tts_done never arrives within 2 s after audio is sent."""
            nonlocal is_speaking, last_activity_t
            await asyncio.sleep(2.0)
            if websocket.client_state == WebSocketState.CONNECTED:
                if is_speaking:
                    logger.warning("[TTS_DONE_WATCHDOG] is_speaking stuck 2s route=%s — force clearing", route)
                    is_speaking = False
                    last_activity_t = time.time()
                    logger.info("[TTS_DONE_MISSING_FAST_CLEAR] route=%s reason=watchdog_2s", route)
                    logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_done_watchdog route=%s", route)
                else:
                    logger.info("[TTS_DONE_MISSING_FAST_CLEAR] route=%s reason=already_cleared", route)
                await _send(websocket, {"type": "listening"})
                logger.info("[VOICE_LISTENING_READY] state=listening reason=tts_done_watchdog")
            asyncio.create_task(_mic_rearm_watchdog(route))

        async def _mic_rearm_watchdog(route: str = "generic") -> None:
            """After TTS, if mic RMS stays near 0 for 3s while user should be speaking, re-arm."""
            nonlocal _last_pcm_rms
            # Brief settle delay so frontend can arm mic and first chunks arrive
            await asyncio.sleep(0.5)
            _silence_start = time.time()
            _rearm_threshold = 0.001
            _rearm_window    = 3.0
            while websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(0.25)
                if _last_pcm_rms > _rearm_threshold:
                    # Mic is live — no action needed
                    return
                if time.time() - _silence_start >= _rearm_window:
                    break
            if websocket.client_state != WebSocketState.CONNECTED:
                return
            if _last_pcm_rms <= _rearm_threshold:
                logger.warning(
                    "[MIC_SILENCE_AFTER_TTS] route=%s rms=%.5f silence=%.1fs — re-arming mic",
                    route, _last_pcm_rms, _rearm_window,
                )
                logger.info("[MIC_REARM_AFTER_TTS] route=%s sending mic_required", route)
                sent = await _send(websocket, {
                    "type":    "mic_required",
                    "message": "Mic silent after response — please check microphone",
                })
                if sent:
                    logger.info("[MIC_REARM_SUCCESS] route=%s mic_required sent", route)

        if my_turn != current_turn_id:
            logger.info("[STALE_RESPONSE_DROPPED] turn=%d stale on entry (current=%d) — discarding",
                        my_turn, current_turn_id)
            is_speaking = False
            return

        # ── Trace ID — unique per utterance, stamped on every log ────────────
        try:
            _ts, _new_tid = _tracer()
            _trace_id = _new_tid()
            _trace = _ts.start(_trace_id)
        except Exception:
            _trace_id, _trace, _ts = "VX-ERR", None, None
        logger.info("[TRACE %s] [COMMAND_START] turn=%d frames=%d",
                    _trace_id, my_turn, len(frames))

        _turn_t0 = time.time()
        last_activity_t = time.time()
        audio = np.concatenate(frames).astype(np.float32)
        # Per-turn latency accumulator — filled as each stage completes
        _lat: dict[str, float] = {
            "stt": 0.0, "normalize": 0.0, "followup": 0.0,
            "screen_context": 0.0, "router": 0.0,
            "tool": 0.0, "tts": 0.0,
        }
        # Timeline checkpoints — ms from _turn_t0, to locate hidden gaps
        _tl: dict[str, float] = {}
        def _cp(name: str) -> None:
            _tl[name] = (time.time() - _turn_t0) * 1000

        # Signal background services to throttle while processing this command
        try:
            from api.services.voice_activity import set_active as _va_set
            _va_set(True)
        except Exception:
            _va_set = None  # type: ignore[assignment]

        # ── Pre-STT energy gate — skip Whisper for silence/noise ─────────────
        # Whisper hallucinates command lists when given audio with very low energy.
        # RMS < 0.010 reliably indicates no real speech was recorded.
        _pre_rms = float(np.sqrt(np.mean(audio ** 2)))
        if _pre_rms < 0.010:
            logger.info("[STT_SKIPPED_SILENCE] rms=%.5f (threshold=0.010)", _pre_rms)
            is_speaking = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return
        logger.debug("[STT_AUDIO_RMS] rms=%.5f frames=%d", _pre_rms, len(frames))

        # ── Audio trimming — strip leading/trailing silence; never trim below 700ms ─
        _MIN_AUDIO_SAMPLES = 700 * 16  # 700ms at 16kHz = 11200 samples
        _orig_len = len(audio)
        try:
            _TRIM_CHUNK = 160   # 10ms at 16kHz
            _TRIM_RMS = 0.005
            _trim_start = 0
            for _ci in range(0, len(audio) - _TRIM_CHUNK, _TRIM_CHUNK):
                if np.sqrt(np.mean(audio[_ci:_ci + _TRIM_CHUNK] ** 2)) > _TRIM_RMS:
                    _trim_start = max(0, _ci - _TRIM_CHUNK)
                    break
            _trim_end = len(audio)
            for _ci in range(len(audio) - _TRIM_CHUNK, _TRIM_CHUNK, -_TRIM_CHUNK):
                if np.sqrt(np.mean(audio[_ci - _TRIM_CHUNK:_ci] ** 2)) > _TRIM_RMS:
                    _trim_end = min(len(audio), _ci + _TRIM_CHUNK)
                    break
            if _trim_start > 0 or _trim_end < len(audio):
                _trimmed = audio[_trim_start:_trim_end]
                if len(_trimmed) >= _MIN_AUDIO_SAMPLES:
                    audio = _trimmed
                    _trim_ms = int((_orig_len - len(audio)) / 16)
                    logger.info("[AUDIO_TRIM_MS] trimmed=%dms orig_frames=%d new_frames=%d",
                                _trim_ms, _orig_len, len(audio))
                else:
                    logger.info("[AUDIO_TRIM_SKIPPED] result_would_be=%.0fms < 700ms — keeping original",
                                len(_trimmed) / 16.0)
        except Exception as _te:
            logger.debug("[AUDIO_TRIM] skipped: %s", _te)

        _audio_dur_ms = len(audio) / 16.0  # 16kHz mono → ms
        logger.info("[STT_AUDIO_DURATION] ms=%.0f", _audio_dur_ms)
        if _audio_dur_ms > 15000:
            logger.warning("[STT_BUDGET_WARNING] audio_duration=%.0fms > 15000ms cap", _audio_dur_ms)

        # ── Minimum duration gate — drop utterances too short to be real commands ─
        if _audio_dur_ms < 700.0:
            logger.info("[VAD_TOO_SHORT] dur_ms=%.0f min=700ms — discarding utterance", _audio_dur_ms)
            is_speaking = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return

        # ── Speech ratio gate — drop clips that are mostly silence/noise ─────────
        _RATIO_CHUNK = 160   # 10ms at 16kHz
        _RATIO_RMS   = 0.012
        _active_c = sum(1 for _ri in range(0, len(audio) - _RATIO_CHUNK, _RATIO_CHUNK)
                        if np.sqrt(np.mean(audio[_ri:_ri + _RATIO_CHUNK] ** 2)) > _RATIO_RMS)
        _total_c  = max(len(audio) // _RATIO_CHUNK, 1)
        _ratio    = _active_c / _total_c
        logger.info("[VAD_SPEECH_RATIO] ratio=%.2f active=%d total=%d", _ratio, _active_c, _total_c)
        if _ratio < 0.30:
            logger.info("[VAD_LOW_RATIO] ratio=%.2f < 0.30 — discarding utterance", _ratio)
            is_speaking = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return

        # ── Phase 4.12: immediate <1s acknowledgement ─────────────────────────
        # Fired here — after every real-speech validation gate has passed, but
        # BEFORE STT (which can take 3-4s: STT_ROUTER_DECISION + a low-
        # confidence retry, as measured live) and before the intelligence
        # pipeline (entity correction/candidate scoring, another ~1-2s) even
        # start. Previously the ONLY acknowledgement was the coordinator's
        # `_tts_sequential(_coordinator_ack)` call much further down, which
        # could not fire until routing had already resolved — i.e. after STT
        # + intelligence pipeline had both fully completed. Measured live:
        # first spoken word arrived ~6.6s after speech ended. This ack is
        # content-free (doesn't need the transcript) and uses a pre-warmed
        # tts_cache_service entry (see api/main.py's warmup phrase list) —
        # a cache hit is an in-memory dict lookup, not a synthesis call, so
        # dispatching it costs no real time and never blocks the STT call
        # that follows on the very next line.
        _ack_t0 = time.time()
        async def _send_immediate_ack() -> None:
            try:
                from api.services.tts_cache_service import tts_cache as _tcc_ack
                _ack_wav = await asyncio.to_thread(_tcc_ack.synthesize_or_cached, "Sure.", voice, speed)
                if _ack_wav and websocket.client_state == WebSocketState.CONNECTED:
                    await _send(websocket, {
                        "type": "audio", "data": base64.b64encode(_ack_wav).decode(),
                        "chunk": 1, "total": 1, "final": False, "text": "Sure.",
                    })
                    logger.info("[ACK_SENT_MS] ms=%.0f", (time.time() - _ack_t0) * 1000)
            except Exception as exc:
                logger.debug("[ACK_SENT_FAILED] error=%r", str(exc)[:120])
        if _IMMEDIATE_ACK_ENABLED:
            asyncio.create_task(_send_immediate_ack())
        else:
            logger.info("[IMMEDIATE_ACK_SKIPPED] reason=disabled")

        # ── Perf budget tracking ──────────────────────────────────────────────
        try:
            from api.services.perf_budget import perf_budget as _pb
            _perf_rec = _pb.start(transcript="")
        except Exception:
            _perf_rec = None

        # ── Hybrid STT Router ─────────────────────────────────────────────────
        # Selects tiny.en (fast, <500ms) or small (accurate, ~3s) per utterance.
        # Decision: audio_dur_ms + session_state → mode → model → optional retry.
        _stt_t0 = time.time()
        logger.info("[STT_START] audio_ms=%.0f", _audio_dur_ms)
        _stt_secondary = None  # Phase 2.1: secondary model result (for N-best)
        try:
            from voice.hybrid_stt_router import route as _stt_route
            _stt_route_out = await asyncio.to_thread(
                _stt_route, audio, _audio_dur_ms, _session_state
            )
            # Unpack 3-tuple (result, model, secondary); secondary may be None
            result, _stt_model = _stt_route_out[0], _stt_route_out[1]
            _stt_secondary = _stt_route_out[2] if len(_stt_route_out) > 2 else None
            transcript = result.get("text", "").strip()
            if not transcript and _flight_session_active():
                # Fast/tiny model returned nothing for a short in-session
                # command — retry once with the more accurate model rather
                # than silently dropping the turn (this was the exact
                # failure mode "Cancel" hit: empty transcript, no retry).
                logger.info("[FLIGHT_SHORT_COMMAND_RETRY] reason=empty_transcript prior_model=%s", _stt_model)
                try:
                    _retry_state = dict(_session_state) if isinstance(_session_state, dict) else {}
                    _retry_state["force_accurate"] = True
                    _retry_out = await asyncio.to_thread(_stt_route, audio, _audio_dur_ms, _retry_state)
                    retry_result, _retry_model = _retry_out[0], _retry_out[1]
                    retry_transcript = retry_result.get("text", "").strip()
                    if retry_transcript:
                        transcript = retry_transcript
                        result = retry_result
                        _stt_model = _retry_model
                        logger.info("[FLIGHT_SHORT_COMMAND_STT] transcript=%r model=%s (retry)",
                                    transcript[:80], _stt_model)
                except Exception as _retry_exc:
                    logger.debug("[FLIGHT_SHORT_COMMAND_RETRY] retry failed (ignored): %r", _retry_exc)
            _stt_ms = (time.time() - _stt_t0) * 1000
            _lat["stt"] = _stt_ms
            logger.info("[STT_MS] ms=%.0f model=%s", _stt_ms, _stt_model)
            if _stt_ms < 5000:
                logger.info("[STT_WARM_HIT] ms=%.0f", _stt_ms)
            else:
                logger.info("[STT_COLD_HIT] ms=%.0f — model loading or GPU unavailable", _stt_ms)
            logger.info("[STT_REALTIME_FACTOR] audio_ms=%.0f stt_ms=%.0f rtf=%.2f model=%s",
                        _audio_dur_ms, _stt_ms, _stt_ms / max(_audio_dur_ms, 1), _stt_model)
            _cp("stt_done")
            logger.info("[VOICE_SESSION_LATENCY] stage=stt ms=%.0f", _stt_ms)
            logger.info("[TRACE %s] [STT_END] ms=%.0f transcript=%r",
                        _trace_id, _stt_ms, transcript[:80])
            logger.info("[STT_END] ms=%.0f", _stt_ms)
            if _stt_ms > 1000:
                logger.warning("[SLOW_STAGE] stage=stt ms=%.0f budget=1000", _stt_ms)
            if _flight_session_active():
                logger.info("[FLIGHT_SHORT_COMMAND_STT] transcript=%r model=%s", transcript[:80], _stt_model)
            if _trace:
                _trace.transcript = transcript
                _trace.timings_ms["stt"] = _stt_ms
            if _perf_rec:
                _perf_rec.transcript = transcript
                _perf_rec.set("stt", _stt_ms)
        except Exception as exc:
            logger.warning("[TRACE %s] [STT_FAILURE] type=STT_FAILURE error=%s",
                           _trace_id, exc)
            logger.warning("[WS/session] STT error: %s", exc)
            if _ts and _trace:
                _ts.finish(_trace_id, status="error", result="")
                _trace.error_type = "STT_FAILURE"
                _trace.error_detail = str(exc)
            await _send(websocket, {"type": "error", "message": "STT failed"})
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        # ── STT language retry — if non-English detected with English command words ──
        # Whisper sometimes auto-detects hi/ur/pt for garbled English voice commands.
        # Retry with language="en" and keep whichever transcript has more command hits.
        _EN_CMD_WORDS = {
            "play", "open", "find", "drive", "folder", "movie", "video", "file",
            "settings", "volume", "close", "search", "create", "delete",
            "screenshot", "shutdown", "restart", "lock", "show", "get",
        }
        _detected_lang = result.get("language", "en") or "en"
        # Phase 2.4: skip English retry for script-based languages — normalizer handles them.
        # Arabic/Urdu script audio must stay native until ml_normalizer converts it.
        _SCRIPT_LANGS = {"ar", "ur", "fa", "ps"}
        if _detected_lang not in ("en",) and _detected_lang not in _SCRIPT_LANGS and transcript and len(transcript.split()) >= 2:
            _orig_hits = sum(1 for w in transcript.lower().split() if w in _EN_CMD_WORDS)
            # Phase 7: strong English signal (≥2 command words) → trust first transcript,
            # skip second Whisper call to save ~400ms latency.
            if _orig_hits >= 2:
                logger.info("[STT_SKIP_RETRY] strong_english orig_hits=%d transcript=%r — no retry",
                            _orig_hits, transcript[:60])
            elif _orig_hits >= 1:
                logger.info("[STT_RETRY_ENGLISH] detected_lang=%s transcript=%r",
                            _detected_lang, transcript[:60])
                try:
                    _result_en = await asyncio.to_thread(
                        transcribe_audio, audio, fast=True, language="en"
                    )
                    _en_text = (_result_en.get("text") or "").strip()
                    if _en_text:
                        _en_hits = sum(1 for w in _en_text.lower().split() if w in _EN_CMD_WORDS)
                        if _en_hits >= _orig_hits:
                            logger.info("[STT_SELECTED_TRANSCRIPT] lang=%s original=%r en=%r → using en",
                                        _detected_lang, transcript[:60], _en_text[:60])
                            transcript = _en_text
                            result = _result_en
                        else:
                            logger.info("[STT_SELECTED_TRANSCRIPT] lang=%s → keeping original (en_hits=%d orig_hits=%d)",
                                        _detected_lang, _en_hits, _orig_hits)
                except Exception as _re:
                    logger.debug("[STT_RETRY_ENGLISH] retry failed: %s", _re)

        if not transcript:
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        # ── Exact-phrase hallucination filter — known Whisper artifacts ──────
        _t_norm = transcript.lower().strip().rstrip('.!?,')
        if _t_norm in _KNOWN_HALLUCINATIONS or transcript.lower().strip() in _KNOWN_HALLUCINATIONS:
            logger.info("[TRANSCRIPT_DROPPED_SILENCE_HALLUCINATION] exact_match text=%r", transcript[:80])
            is_speaking = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return

        # ── Hallucination filter — reject command-list garbage from Whisper ──
        # Whisper hallucinates sorted command lists when processing ambient TTS
        # audio or mic noise during the post-wake stabilization window.
        # Two patterns caught:
        #   1. Comma-separated list: "Close, screenshot, volume up, shutdown."
        #   2. Space-separated chain: "Close screenshot volume down folder settings"
        _CMD_WORDS = {
            'close', 'screenshot', 'volume', 'folder', 'settings', 'lock',
            'shutdown', 'restart', 'sleep', 'open', 'launch', 'start', 'stop',
            'play', 'pause', 'mute', 'search', 'create', 'delete', 'copy',
            'paste', 'save', 'print', 'minimize', 'maximize', 'scroll',
            'refresh', 'undo', 'redo', 'cut', 'select', 'send', 'cancel',
        }
        _hallu = False
        # Pattern 1: comma-separated (3+ segments, each 1-3 words, 3+ cmd hits)
        _segments = [s.strip().rstrip('.!?').lower() for s in transcript.split(',')]
        if len(_segments) >= 3:
            _cmd_hits = sum(
                1 for seg in _segments
                if 1 <= len(seg.split()) <= 3 and any(w in _CMD_WORDS for w in seg.split())
            )
            if _cmd_hits >= 3:
                _hallu = True
        # Pattern 2: space-separated command chain (5+ words, 4+ are cmd words, >60% density)
        if not _hallu:
            _words = transcript.rstrip('.!?,').lower().split()
            if len(_words) >= 5:
                _hits = sum(1 for w in _words if w in _CMD_WORDS)
                if _hits >= 4 and (_hits / len(_words)) >= 0.60:
                    _hallu = True
        if _hallu:
            logger.info("[TRANSCRIPT_DROPPED_SILENCE_HALLUCINATION] text=%r", transcript[:80])
            is_speaking = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return

        # Staleness check — a new utterance may have arrived while Whisper was running
        if my_turn != current_turn_id:
            logger.info("[STALE_RESPONSE_DROPPED] turn=%d dropped after STT (current=%d)",
                        my_turn, current_turn_id)
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        # ── Phase 4.14 Part 3: Parallel pipeline — fast-path agent dispatch ───
        # A confident "book a flight/hotel/ticket" match is unambiguous enough
        # to skip the ~4-8s intelligence-pipeline + tier waterfall entirely and
        # launch BrowserAgent on the raw transcript immediately — Chrome opens
        # and Google Flights starts loading while the rest of the reasoning
        # (destination/date parsing, planning, recommendation) proceeds inside
        # the agent's own pipeline, not blocked behind voice_ws.py's generic
        # entity-corrector/n-best rescoring (which mainly helps app/tool-name
        # commands, not travel free text anyway). Skipped entirely when a
        # flight follow-up session is already active — Tier 0e/0f1 further
        # down must keep priority so "only Emirates" never spawns a second
        # agent instead of filtering the existing one.
        if not _flight_session_active():
            try:
                from api.services.agent_intent_detector import agent_intent_detector as _aid_fast
                _fast_intent = _aid_fast.detect(transcript)
            except Exception:
                _fast_intent = None
            if (
                _fast_intent is not None
                and _fast_intent.is_agent_command
                and _fast_intent.agent_type == "browser"
                and _fast_intent.reason == "booking_pattern"
            ):
                _fast_coordinated = _fast_intent.execution_mode == "COORDINATED_WORKFLOW"
                logger.info("[FAST_PATH_AGENT_DISPATCH] turn=%d reason=%s mode=%s transcript=%r",
                            my_turn, _fast_intent.reason, _fast_intent.execution_mode, transcript[:60])
                from api.agents.agent_runtime import agent_runtime as _art_fast
                from api.agents.agent_types import AgentType as _AT_fast

                _fast_ack = "On it. I'll research that and report back."
                try:
                    from api.agents.personality.personality_engine import personality_engine as _pe_fast
                    _fast_ack = _pe_fast.polish_response(_fast_ack)
                except Exception:
                    pass

                await _send(websocket, {"type": "response", "text": _fast_ack, "chunk": 1})
                _fast_interrupted = await _tts_sequential(_fast_ack)
                memory.add_assistant(_fast_ack, tool_name="coordinator_launch")
                last_response_text = _fast_ack
                last_activity_t = time.time()
                if not _fast_interrupted and _tts_state["audio_sent"]:
                    await _send(websocket, {"type": "done"})
                    asyncio.create_task(_spawn_tts_watchdog("coordinator_launch"))
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})

                async def _fast_coord_ws_send(payload: dict) -> bool:
                    if payload.get("type") == "narration":
                        _text = payload.get("message", "")
                        if _text:
                            _narration_queue.put_nowait(_text)
                    return await _send(websocket, payload)

                _fast_coord_task = await _art_fast.launch(
                    goal=transcript,
                    agent_type=(_AT_fast.COORDINATOR if _fast_coordinated else _AT_fast.BROWSER),
                    ws_send_fn=_fast_coord_ws_send,
                    context={
                        "turn_id":         my_turn,
                        "trace_id":        _trace_id,
                        "primary_type":    "browser",
                        "history":         memory.history_for_llm()[-6:],
                        "turn_started_at": _turn_t0,
                    },
                )
                logger.info("[ACTIVE_WORKFLOW_SET] task_id=%s goal=%r mode=%s",
                            _fast_coord_task.task_id, transcript[:60], _fast_intent.execution_mode)
                logger.info("[TOTAL_PIPELINE] turn=%d ms=%.0f path=%s",
                            my_turn, (time.time() - _turn_t0) * 1000,
                            "fast_dispatch_coordinator" if _fast_coordinated else "fast_dispatch_direct_agent")
                return

        # ── Phase 2: Intelligence Pipeline ────────────────────────────────────
        # N-best → entity correction → tool prediction → mixed-language → vote
        # Runs async, <50ms added on warm GPU. Fail-safe: falls back to original.
        _intel_t0 = time.time()
        try:
            from api.services.intelligence_pipeline import process as _intel_process
            _session_state["session_id"] = _session_id  # expose for contextual repair
            _intel_result = await _intel_process(
                stt_result=result,
                session_state=_session_state,
                secondary_result=_stt_secondary,
                audio_dur_ms=_audio_dur_ms,
            )
            if _intel_result and _intel_result.winner_text:
                if _intel_result.corrected:
                    logger.info(
                        "[INTEL_APPLIED] %r → %r (%.1fms)",
                        transcript[:60], _intel_result.winner_text[:60],
                        _intel_result.latency_ms,
                    )
                transcript = _intel_result.winner_text
                result["text"] = transcript
        except Exception as _ip_exc:
            logger.debug("[INTEL_PIPELINE] skipped: %s", _ip_exc)
        _lat["intel"] = (time.time() - _intel_t0) * 1000
        logger.info("[INTEL_PIPELINE_MS] ms=%.0f", _lat["intel"])

        # ── Context resolution — replace vague pronouns before routing ──────
        _ctx_t0 = time.time()
        try:
            from api.services.context_resolver import resolve as _ctx_resolve
            resolved = _ctx_resolve(transcript, _session_id)
            if resolved != transcript:
                logger.info("[CTX_RESOLVED] %r → %r", transcript[:60], resolved[:60])
                transcript = resolved
        except Exception as _exc:
            logger.debug("[CTX_RESOLVE] skipped: %s", _exc)

        _cp("ctx_resolve_done")

        # ── Multilingual language detection + command normalization ───────────
        # Runs before English normalizer.
        # Non-English input → transcript converted to English for intent routing.
        # Response language stored in _session_state["ml_resp_lang"] for TTS.
        try:
            import os as _ml_os
            from api.services.language_detector import detect as _lang_detect
            from api.services.ml_normalizer import normalize as _ml_normalize
            from api.services.response_language import (
                check_preference_update as _check_lang_pref,
                select_response_language as _select_resp_lang,
            )
            _raw_stt_lang = (result.get("language") or "en") if isinstance(result, dict) else "en"
            _lang_info    = _lang_detect(transcript, _raw_stt_lang)
            _detected_ml  = _lang_info["lang"]
            _session_state["ml_detected_lang"] = _detected_ml
            # Check if user is setting a language preference ("always reply in Urdu" etc.)
            _check_lang_pref(transcript, _session_id)
            # Choose TTS output language
            _resp_lang_mode = _ml_os.getenv("RESPONSE_LANGUAGE_MODE", "auto")
            _ml_resp = _select_resp_lang(_detected_ml, _session_id, _resp_lang_mode)
            _session_state["ml_resp_lang"] = _ml_resp
            # Normalize non-English command to English for intent routing
            if _detected_ml not in ("en",):
                _ml_en_cmd = _ml_normalize(transcript, _detected_ml)
                if _ml_en_cmd and _ml_en_cmd.strip() and _ml_en_cmd != transcript:
                    logger.info("[ML_NORMALIZE_INPUT→OUTPUT] %r → %r",
                                transcript[:60], _ml_en_cmd[:60])
                    transcript = _ml_en_cmd
            logger.info(
                "[STT_LANG_ROUTE] mode=%s reason=lang_%s resp_lang=%s",
                "multilingual" if _detected_ml != "en" else "english_fast",
                _detected_ml, _ml_resp,
            )
            logger.info(
                "[STT_MULTILINGUAL_RESULT] lang=%s transcript=%r ms=%.0f",
                _detected_ml, transcript[:60], _lat.get("stt", 0),
            )
        except Exception as _ml_exc:
            logger.debug("[ML_LANG_DETECT] skipped: %s", _ml_exc)

        # ── Text normalization — STT cleanup before routing ───────────────────
        _normalize_t0 = time.time()
        try:
            from api.services.normalizer import normalize as _normalize
            _norm = _normalize(transcript)
            if _norm and _norm.strip():
                if _norm != transcript.lower().strip():
                    logger.info("[VOICE_NORMALIZE] %r → %r", transcript[:60], _norm[:60])
                transcript = _norm
        except Exception as _ne:
            logger.debug("[VOICE_NORMALIZE] skipped: %s", _ne)
        _norm_ms = (time.time() - _normalize_t0) * 1000
        _lat["normalize"] = _norm_ms
        logger.info("[NORMALIZE_MS] ms=%.0f", _norm_ms)
        if _trace:
            _trace.normalized = transcript
            _trace.timings_ms["normalize"] = _norm_ms
        if _perf_rec:
            _perf_rec.set("normalize", _norm_ms)

        # ── Media title correction — fix Whisper phonetic mishearings ─────────
        try:
            from api.services.voice_title_corrector import correct_media_title
            _corrected = correct_media_title(transcript)
            if _corrected != transcript:
                transcript = _corrected
        except Exception as _ce:
            logger.debug("[MEDIA_TITLE_CORRECT] skipped: %s", _ce)

        _cp("transcript_sent")
        await _send(websocket, {"type": "transcript", "text": transcript, "final": True})
        logger.info("[WS/session] transcript: %r", transcript)
        memory.add_user(transcript)
        logger.info("[VOICE_TRACE] stage=stt transcript=%r", transcript[:80])
        logger.info(
            "[BRAIN_PIPELINE] stage=normalized input=%r turn=%d",
            transcript[:80], my_turn,
        )

        # ── Tier 0d: Pending confirmation handler — yes/no before any routing ─
        # Fires when a prior tool returned error="confirm_required" (e.g. install_store_app).
        # Must run before Tier 0 clock so "yes" / "no" answers the pending action.
        _pending = _session_state.get("pending_confirmation")
        if _pending:
            _YES_RE = re.compile(
                r'\b(yes|yeah|yep|yup|sure|go ahead|do it|confirm|install it|'
                r'proceed|ok|okay|please|affirmative|absolutely)\b',
                re.IGNORECASE,
            )
            _NO_RE = re.compile(
                r'\b(no|nope|cancel|stop|don\'?t|never mind|nevermind|abort|'
                r'reject|skip|forget it|actually no)\b',
                re.IGNORECASE,
            )
            if _YES_RE.search(transcript):
                logger.info("[CONFIRMATION_ACCEPTED] tool=%s", _pending["tool"])
                _session_state["pending_confirmation"] = None
                _conf_resp = await _run_tool(_pending["tool"], _pending["params"], goal=transcript)
                memory.add_assistant(_conf_resp, tool_name=_pending["tool"])
                last_response_text = _conf_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _conf_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_conf_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=confirm")
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("confirm"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            elif _NO_RE.search(transcript):
                logger.info("[CONFIRMATION_REJECTED] tool=%s", _pending["tool"])
                _session_state["pending_confirmation"] = None
                _cancel_resp = "Alright, cancelled."
                memory.add_assistant(_cancel_resp, tool_name="confirmation_cancelled")
                last_response_text = _cancel_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _cancel_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_cancel_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("confirm_cancel"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            else:
                # Not a clear yes/no — re-ask
                _reprompt = _pending.get("prompt", "Should I proceed? Say yes or no.")
                logger.info("[CONFIRMATION_PENDING] re-prompting transcript=%r", transcript[:40])
                await _send(websocket, {"type": "response", "text": _reprompt, "chunk": 1})
                _interrupted = await _tts_with_fallback(_reprompt)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("confirm_reprompt"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return

        # ── Tier 0d-control: ambiguous control-word confirmation (Phase 5.4B) ─
        # Fires when Tier 0g (further below, previous turn) detected a short
        # utterance that looked like a misheard control word ("console" for
        # "cancel") while an operation was active, and asked "Did you say
        # cancel?" instead of guessing. Resolves that question here — never
        # falls through to executing something unrelated on a misheard word.
        _pending_ctrl = _session_state.get("pending_control_confirmation")
        if _pending_ctrl:
            _CTRL_YES_RE = re.compile(r'\b(yes|yeah|yep|yup|correct|right|that\'?s right)\b', re.IGNORECASE)
            _CTRL_NO_RE = re.compile(r'\b(no|nope|not|wrong|never\s?mind)\b', re.IGNORECASE)
            _ctrl_action = _pending_ctrl["action"]
            _session_state["pending_control_confirmation"] = None
            if _CTRL_YES_RE.search(transcript):
                from api.agents.agent_runtime import agent_runtime as _art_ctrl
                _active_ctrl = _art_ctrl.get_active()
                if _ctrl_action == "cancel" and _active_ctrl is not None:
                    await _art_ctrl.cancel(_active_ctrl.task_id)
                    # Cancelling the runtime task does not clear
                    # flight_session_state — they're separate pieces of
                    # state. Leaving the session "active" here silently
                    # suppressed every subsequent identical search via
                    # BROWSER_DISPATCH_SUPPRESSED (found live, Phase 5.4B
                    # controlled benchmark: only 5 of 10 identical searches
                    # actually launched a new task after a cancel).
                    if _flight_session_active():
                        from api.agents.browser_agent import flight_session_state as _fss_ctrl2
                        _fss_ctrl2.clear()
                    _ctrl_resp = "Cancelled."
                elif _ctrl_action in ("pause",) and _active_ctrl is not None:
                    await _art_ctrl.pause(_active_ctrl.task_id)
                    _ctrl_resp = "Paused."
                elif _ctrl_action in ("continue", "resume") and _active_ctrl is not None:
                    await _art_ctrl.resume(_active_ctrl.task_id)
                    _ctrl_resp = "Resuming."
                elif _ctrl_action == "cancel" and _flight_session_active():
                    from api.agents.browser_agent import flight_session_state as _fss_ctrl
                    _fss_ctrl.clear()
                    _ctrl_resp = "Cancelled. The browser stays open, but I've cleared the active search."
                else:
                    _ctrl_resp = f"There's nothing active to {_ctrl_action} anymore."
                logger.info("[AMBIGUOUS_CONTROL_CONFIRMED] action=%s", _ctrl_action)
            elif _CTRL_NO_RE.search(transcript):
                _ctrl_resp = "Okay, ignoring that."
                logger.info("[AMBIGUOUS_CONTROL_REJECTED] action=%s", _ctrl_action)
            else:
                # Neither a clear yes nor no — treat this turn as a fresh
                # command rather than re-prompting forever; safer to drop
                # the stale clarification than to keep blocking normal use.
                _ctrl_resp = None
                logger.info("[AMBIGUOUS_CONTROL_UNCLEAR] action=%s transcript=%r", _ctrl_action, transcript[:60])
            if _ctrl_resp is not None:
                memory.add_assistant(_ctrl_resp, tool_name="ambiguous_control_resolved")
                last_response_text = _ctrl_resp
                last_activity_t = time.time()
                await _send(websocket, {"type": "response", "text": _ctrl_resp, "chunk": 1})
                _interrupted = await _tts_sequential(_ctrl_resp)
                if not _interrupted and _tts_state["audio_sent"]:
                    await _send(websocket, {"type": "done"})
                    asyncio.create_task(_spawn_tts_watchdog("ambiguous_control_resolved"))
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            # _ctrl_resp is None → fall through, let this transcript be
            # processed as a normal new command.

        # ── Tier 0d2: Open-after-install handler ─────────────────────────────
        # Fires when install_store_app_exec just succeeded and user says yes/no/open it.
        _pending_oai = _session_state.get("pending_open_after_install")
        if _pending_oai:
            _OAI_YES_RE = re.compile(
                r'^\s*(?:yes|yeah|yep|sure|ok|okay|open|launch|start|run)'
                r'(?:\s+(?:it|up|now|please|instagram|whatsapp|spotify|tiktok|telegram'
                r'|snapchat|netflix|youtube|chatgpt|discord|facebook|twitter|zoom'
                r'|reddit|linkedin|pinterest|uber|lyft|amazon|twitch))?\s*[.!]?\s*$',
                re.IGNORECASE,
            )
            _OAI_NO_RE = re.compile(
                r'\b(?:no|nope|not\s+now|later|skip|cancel|nevermind|never\s+mind|nah)\b',
                re.IGNORECASE,
            )
            _oai_app      = _pending_oai["app_name"]
            _oai_expired  = (time.time() - _pending_oai.get("created_at", 0)) > 300
            _oai_name_pat = re.compile(
                rf'\b(?:open|launch|start|run)\s+{re.escape(_oai_app)}\b',
                re.IGNORECASE,
            )
            if _oai_expired:
                logger.info("[STORE_OPEN_FOLLOWUP_DISMISSED] reason=expired app=%r", _oai_app)
                _session_state["pending_open_after_install"] = None
            elif _OAI_YES_RE.search(transcript) or _oai_name_pat.search(transcript):
                logger.info("[STORE_OPEN_FOLLOWUP_RESOLVED] app=%r transcript=%r",
                            _oai_app, transcript[:60])
                _session_state["pending_open_after_install"] = None
                await _run_tool("open_application", {"app_name": _oai_app}, goal=transcript)
                _open_spoken = f"Opening {_oai_app}."
                logger.info("[STORE_OPEN_AFTER_INSTALL] app=%r", _oai_app)
                memory.add_assistant(_open_spoken, tool_name="open_application")
                last_response_text = _open_spoken
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _open_spoken, "chunk": 1})
                _interrupted = await _tts_with_fallback(_open_spoken)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("oai_open"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            elif _OAI_NO_RE.search(transcript):
                logger.info("[STORE_OPEN_FOLLOWUP_DISMISSED] app=%r transcript=%r",
                            _oai_app, transcript[:60])
                _session_state["pending_open_after_install"] = None
                _no_resp = "No problem."
                memory.add_assistant(_no_resp, tool_name="open_after_install_dismissed")
                last_response_text = _no_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _no_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_no_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("oai_dismiss"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return

        # ── Tier 0d3: Store install cancel handler ───────────────────────────
        # Fires when a Microsoft Store install flow is active (product page
        # open, candidates pending, or open-after-install offer pending) and
        # the user says "cancel"/"never mind"/"stop"/"forget it". Previously
        # there was no dedicated cancel path for this state — "cancel install"
        # would fall through to Tier 0g's generic agent-cancel control action,
        # which has nothing to cancel (no running AgentRuntime task) and
        # leaves the store context dangling. Must run before Tier 0e so a
        # bare "cancel" isn't mistaken for an unresolved follow-up.
        try:
            from api.services.store_agent import (
                is_cancel_phrase as _sa_is_cancel,
                store_context_active as _sa_store_active,
                cancel_install_context as _sa_cancel_ctx,
            )
            from api.services.active_context import active_context as _sa_actx
            if _sa_store_active(_sa_actx.get(), _session_state) and _sa_is_cancel(transcript):
                _sa_cancel_ctx(_sa_actx, _session_state)
                _cancel_resp = "No problem, cancelled."
                logger.info("[STORE_INSTALL_CANCELLED] transcript=%r", transcript[:80])
                memory.add_assistant(_cancel_resp, tool_name="store_install_cancelled")
                last_response_text = _cancel_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _cancel_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_cancel_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("store_cancel"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
        except Exception as _sc_exc:
            logger.debug("[STORE_CANCEL] skipped: %s", _sc_exc)

        # ── Tier 0e: Follow-up resolver V2 — 5-tier context-aware expansion ────
        # Tiers: ContextStack → ScreenContext → SessionState → Memory → V1
        # Resolves pronouns + platform-context commands:
        #   "open it"    → last app/folder from context stack or screen
        #   "install it" → last store_app from stack
        #   "download whatsapp" → install_store_app when store is active
        #   "play believer" → search_youtube when YouTube is active
        # Always runs so context_stack resolution works without active_context.
        try:
            from api.services.follow_up_resolver_v2 import resolve_v2 as _fur_resolve
            from api.services.active_context import active_context as _actx
            from api.services.context_stack import context_stack as _cstack
            _actx_snap = _actx.get()
            _fur_t0 = time.time()
            _fur = _fur_resolve(transcript, _actx_snap, _cstack, _session_state)
            _fur_ms = (time.time() - _fur_t0) * 1000
            _lat["followup"] = _fur_ms
            logger.info("[FOLLOWUP_RESOLVER_MS] ms=%.1f resolved=%s tool=%s",
                        _fur_ms, _fur.was_resolved, _fur.tool_name or "none")
            _actx.log_current()
            if _fur.needs_clarification:
                _clarif = _fur.clarification_prompt
                logger.info("[FOLLOWUP_NEEDS_CLARIFICATION] prompt=%r", _clarif[:60])
                memory.add_assistant(_clarif, tool_name="followup_clarify")
                last_response_text = _clarif
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _clarif, "chunk": 1})
                _interrupted = await _tts_with_fallback(_clarif)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("followup_clarify"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            if _fur.tool_name:
                logger.info("[FOLLOWUP_DIRECT_TOOL] tool=%s params=%s",
                            _fur.tool_name, _fur.tool_params)
                # ── ACK for install_store_app_exec — speak before winget blocks ──
                if _fur.tool_name == "install_store_app_exec":
                    _ack_app  = _fur.tool_params.get("app_name", "the app")
                    _ack_text = f"Installing {_ack_app} now."
                    logger.info("[STORE_INSTALL_ACK] app=%r ack=%r", _ack_app, _ack_text)
                    logger.info("[STORE_INSTALL_STARTED] app=%r", _ack_app)
                    from api.services.store_agent import set_store_state as _sa_set_state2
                    from api.services.store_agent import StoreInstallState as _SIS2
                    _sa_set_state2(_session_state, _SIS2.INSTALLING)
                    await _send(websocket, {"type": "response", "text": _ack_text, "chunk": 1})
                    await _tts_with_fallback(_ack_text)
                    # Do NOT send done/listening — install is still running
                _fur_tool_t0 = time.time()
                _fur_resp = await _run_tool(_fur.tool_name, _fur.tool_params, goal=transcript)
                _lat["tool"] = (time.time() - _fur_tool_t0) * 1000
                # If the tool set a pending confirmation, _run_tool returns the prompt text.
                # Continue to TTS whether it's a confirmation prompt or a real response.
                memory.add_assistant(_fur_resp, tool_name=_fur.tool_name)
                last_response_text = _fur_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _fur_resp, "chunk": 1})
                _fur_tts_t0 = time.time()
                _interrupted = await _tts_with_fallback(_fur_resp)
                _lat["tts"] = (time.time() - _fur_tts_t0) * 1000
                # Emit per-stage breakdown for this early-return path
                _fur_total_ms = (time.time() - _turn_t0) * 1000
                logger.info(
                    "[V_LATENCY] stt_ms=%.0f normalize_ms=%.0f followup_ms=%.0f "
                    "screen_context_ms=%.0f router_ms=%.0f tool_ms=%.0f tts_ms=%.0f total_ms=%.0f",
                    _lat["stt"], _lat["normalize"], _lat["followup"],
                    _lat["screen_context"], _lat["router"], _lat["tool"], _lat["tts"],
                    _fur_total_ms,
                )
                _bn = max(_lat, key=_lat.__getitem__)
                if _lat[_bn] > 200:
                    logger.info("[V_LATENCY_BOTTLENECK] stage=%s ms=%.0f", _bn, _lat[_bn])
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=followup")
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("followup"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
            if _fur.was_resolved:
                logger.info("[FOLLOWUP_TEXT_REWRITE] %r → %r",
                            transcript[:50], _fur.resolved[:50])
                transcript = _fur.resolved
        except Exception as _fur_exc:
            logger.debug("[FOLLOWUP_RESOLVER] skipped: %s", _fur_exc)

        # ── Tier 0x: Screen awareness — "what am I looking at?" / "what app?" ─
        # Answers questions about the current desktop context without LLM.
        # Uses ScreenContextAgent (system APIs: window_context + title parsing).
        _SCREEN_QUERY_RE = re.compile(
            r'\b(?:'
            r'what(?:\'s|\s+is)\s+(?:(?:currently|now)\s+)?'
            r'(?:on\s+(?:my\s+)?(?:screen|display|monitor)|'
            r'(?:the\s+)?(?:active|current)\s+(?:app(?:lication)?|window|program))'
            r'|what\s+(?:app(?:lication)?|window|program)\s+'
            r'(?:am\s+i\s+(?:on|using|in|looking\s+at)|is\s+(?:open|active|running|focused))'
            r'|what\s+am\s+i\s+(?:looking\s+at|working\s+on|doing)'
            r'|what\s+(?:is|are)\s+(?:currently\s+)?(?:open|running|active|showing|focused)'
            r'|what\s+project\s+(?:am\s+i|is)\s+(?:open|active|running|showing)?'
            r'|(?:describe|tell\s+me\s+about)\s+(?:my\s+)?(?:screen|display|current\s+(?:app|window|view))'
            r')\b',
            re.IGNORECASE,
        )
        try:
            if _SCREEN_QUERY_RE.search(transcript):
                from api.services.screen_context_agent import screen_context_agent as _sca
                _screen_t0 = time.time()
                _screen_snap = await asyncio.to_thread(_sca.get_fresh)
                _lat["screen_context"] = (time.time() - _screen_t0) * 1000
                _screen_resp = _screen_snap.describe()
                logger.info("[SCREEN_QUERY_RESPONSE] response=%r", _screen_resp)
                memory.add_assistant(_screen_resp, tool_name="screen_context_query")
                last_response_text = _screen_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _screen_resp, "chunk": 1})
                _sq_tts_t0 = time.time()
                _interrupted = await _tts_with_fallback(_screen_resp)
                _lat["tts"] = (time.time() - _sq_tts_t0) * 1000
                # Emit latency breakdown for this early-return path
                _sq_total_ms = (time.time() - _turn_t0) * 1000
                logger.info(
                    "[V_LATENCY] stt_ms=%.0f normalize_ms=%.0f followup_ms=%.0f "
                    "screen_context_ms=%.0f router_ms=%.0f tool_ms=%.0f tts_ms=%.0f total_ms=%.0f",
                    _lat["stt"], _lat["normalize"], _lat["followup"],
                    _lat["screen_context"], _lat["router"], _lat["tool"], _lat["tts"],
                    _sq_total_ms,
                )
                _sq_bn = max(_lat, key=_lat.__getitem__)
                if _lat[_sq_bn] > 200:
                    logger.info("[V_LATENCY_BOTTLENECK] stage=%s ms=%.0f", _sq_bn, _lat[_sq_bn])
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=screen_query")
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("screen_query"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
        except Exception as _sq_exc:
            logger.debug("[SCREEN_QUERY] skipped: %s", _sq_exc)

        # ── Tier 0: Local clock — instant, offline, no LLM ───────────────────
        try:
            from api.services.intent_router import _local_clock_route
            _clock = _local_clock_route(transcript.lower().strip())
            if _clock and _clock.tool_name and _clock.tool_name.startswith("local_clock_"):
                _clock_response = _clock.params.get("response", "")
                if _clock_response:
                    logger.info("[LOCAL_CLOCK_RESPONSE] tool=%s response=%r", _clock.tool_name, _clock_response)
                    _clock_tts = _normalize_clock_for_tts(_clock_response)
                    await _send(websocket, {"type": "response", "text": _clock_response, "chunk": 1})
                    _interrupted = await _tts_sequential(_clock_tts)
                    logger.info("[VOICE_TRACE] stage=audio_stream done response=%r", _clock_response[:60])
                    logger.info("[TTS_STOPPED] interrupted=%s audio_sent=%s", _interrupted, _tts_state["audio_sent"])
                    memory.add_assistant(_clock_response, tool_name=_clock.tool_name)
                    last_response_text  = _clock_response
                    last_activity_t     = time.time()
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=clock")
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("clock"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                            logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_no_audio route=clock")
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                        logger.info("[SPEAKING_FLAG_CLEARED] reason=interrupted route=clock")
                        logger.info("[VOICE_LISTENING_READY] state=listening reason=interrupted route=clock")
                    return
        except Exception as _ce:
            logger.debug("[LOCAL_CLOCK] skipped: %s", _ce)

        # ── Tier 0b: Live system metrics — instant, no LLM ───────────────────
        try:
            from api.services.intent_router import intent_router as _ir
            _sys_route = _ir.route(transcript.lower().strip())
            if _sys_route.tool_name == "get_live_system_metrics":
                from api.services.system_monitor_service import system_monitor as _sysmon
                snap = _sysmon.get_snapshot()
                metric = _sys_route.params.get("metric", "all")
                if metric == "cpu":
                    _sr = f"CPU is at {snap.get('cpu_pct', 0):.0f}%."
                    if snap.get("cpu_freq_mhz"):
                        _sr = _sr.rstrip('.') + f", running at {snap['cpu_freq_mhz']/1000:.1f} GHz."
                elif metric == "ram":
                    _sr = (f"RAM usage is {snap.get('ram_pct', 0):.0f}% — "
                           f"{snap.get('ram_used_gb', 0):.1f} of {snap.get('ram_total_gb', 0):.1f} GB used.")
                elif metric == "gpu":
                    gpu_pct = snap.get("gpu_pct")
                    gpu_name = snap.get("gpu_name", "")
                    if gpu_pct is not None:
                        _sr = f"GPU is at {gpu_pct:.0f}%"
                        if gpu_name:
                            _sr += f" on {gpu_name}"
                        _sr += "."
                    else:
                        _sr = "GPU data isn't available right now."
                elif metric == "disk":
                    _sr = (f"Disk is {snap.get('disk_pct', 0):.0f}% full — "
                           f"{snap.get('disk_used_gb', 0):.0f} of {snap.get('disk_total_gb', 0):.0f} GB used.")
                elif metric == "network":
                    _sr = (f"Network: uploading at {snap.get('net_up_str', '0 KB/s')}, "
                           f"downloading at {snap.get('net_down_str', '0 KB/s')}.")
                elif metric == "battery":
                    batt = snap.get("battery_pct")
                    charging = snap.get("battery_charging")
                    if batt is not None:
                        _sr = f"Battery is at {batt:.0f}%"
                        _sr += ", charging." if charging else ", not charging."
                    else:
                        _sr = "No battery detected — probably running on AC power."
                else:  # "all"
                    _sr = (f"CPU {snap.get('cpu_pct', 0):.0f}%, "
                           f"RAM {snap.get('ram_pct', 0):.0f}%, "
                           f"disk {snap.get('disk_pct', 0):.0f}%.")
                    if snap.get("gpu_pct") is not None:
                        _sr = _sr.rstrip('.') + f", GPU {snap['gpu_pct']:.0f}%."
                logger.info("[SYSTEM_METRICS_VOICE] metric=%s response=%r", metric, _sr)
                await _send(websocket, {"type": "response", "text": _sr, "chunk": 1})
                _interrupted = await _tts_sequential(_sr)
                logger.info("[VOICE_TRACE] stage=audio_stream done response=%r", _sr[:60])
                logger.info("[TTS_STOPPED] interrupted=%s audio_sent=%s", _interrupted, _tts_state["audio_sent"])
                memory.add_assistant(_sr, tool_name="get_live_system_metrics")
                last_response_text = _sr
                last_activity_t    = time.time()
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=metrics")
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("metrics"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                        logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_no_audio route=metrics")
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                    logger.info("[SPEAKING_FLAG_CLEARED] reason=interrupted route=metrics")
                    logger.info("[VOICE_LISTENING_READY] state=listening reason=interrupted route=metrics")
                return
        except Exception as _se:
            logger.debug("[SYSTEM_METRICS_VOICE] skipped: %s", _se)

        # ── Tier 0c: Identity / persona queries — instant, no LLM ───────────
        # Catches "who is your founder", "introduce yourself", "who made you", etc.
        # Uses existing identity_policy.py facts and self_intro_engine.py templates.
        # Must fire BEFORE orchestrator so the LLM never invents creator names.
        _IDENTITY_INTERCEPT_RE = re.compile(
            r'\b(?:'
            r'who\s+(?:made|created|built|developed|owns?)\s+you\b'
            r'|who\s+is\s+your\s+(?:founder|co[-\s]?founder|creator|developer|maker)\b'
            r'|who\s+are\s+your\s+(?:founders?|co[-\s]?founders?)\b'
            r'|introduce\s+yourself\b'
            r'|tell\s+me\s+about\s+yourself\b'
            r'|what\s+are\s+you\b'
            r'|who\s+are\s+you\b'
            r'|show\s+(?:me\s+)?what\s+you\s+can\s+do\b'
            r'|showcase\s+yourself\b'
            r'|social\s+media\s+demo\b'
            r'|demo(?:nstrate)?\s+yourself\b'
            r')\b',
            re.IGNORECASE,
        )
        try:
            if _IDENTITY_INTERCEPT_RE.search(transcript):
                logger.info("[IDENTITY_QUERY_DETECTED] transcript=%r", transcript[:80])
                logger.info("[LLM_BYPASS_IDENTITY] transcript=%r", transcript[:60])
                logger.info("[PERSONA_SOURCE_FOUND] source=identity_policy+self_intro_engine")

                _t_low = transcript.lower()
                _has_intro = bool(re.search(
                    r'\b('
                    r'introduce\s+yourself'
                    r'|tell\s+me\s+about\s+yourself'
                    r'|who\s+are\s+you'
                    r'|what\s+are\s+you'
                    r'|show\s+(?:me\s+)?what\s+you\s+can\s+do'
                    r'|showcase\s+yourself'
                    r'|social\s+media\s+demo'
                    r'|demo(?:nstrate)?\s+yourself'
                    r')\b',
                    _t_low,
                ))
                _has_co = bool(re.search(r'\bco[-\s]?founder\b', _t_low))
                # Strip "co-founder" before testing for standalone "founder" so that
                # "who is your co-founder" doesn't also set _has_fnd.
                _t_no_co = re.sub(r'\bco[-\s]?founder\b', '', _t_low)
                _has_fnd = bool(re.search(r'\bfounder\b', _t_no_co))

                if _has_intro and not _has_co and not _has_fnd:
                    # "introduce yourself" — run full action demo mode
                    logger.info("[INTRO_QUERY_DETECTED] routing to introduce_self demo")

                    def _set_demo_mode(val: bool) -> None:
                        nonlocal demo_mode
                        demo_mode = val
                        logger.info("[DEMO_MODE_SET] demo_mode=%s", val)

                    try:
                        from api.services.introduce_self import run_intro_demo as _intro_demo
                        await _intro_demo(_tts_await_playback, _run_tool, memory, logger, _set_demo_mode)
                    except Exception as _ide:
                        logger.warning("[INTRO_DEMO_FAIL] %s — falling back to text", _ide)
                        _fb = (
                            "I'm Xyron, a local-first AI assistant built by Tayyab Aziz "
                            "and Muhammad Qasim. Voice, memory, and system control — "
                            "all running on your machine."
                        )
                        await _tts_with_fallback(_fb)
                    last_activity_t = time.time()
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                    return
                elif _has_co and _has_fnd:
                    # "who is your founder and co-founder"
                    _id_resp = (
                        "Xyron was built by Tayyab Aziz, the founder, "
                        "and Muhammad Qasim, the co-founder."
                    )
                    logger.info("[IDENTITY_MEMORY_HIT] founder=Tayyab_Aziz co_founder=Muhammad_Qasim")
                    logger.info("[PERSONA_FACT_LOADED] both_founders")
                elif _has_co:
                    # "who is your co-founder"
                    _id_resp = "The co-founder of Xyron is Muhammad Qasim."
                    logger.info("[IDENTITY_MEMORY_HIT] co_founder=Muhammad_Qasim")
                    logger.info("[PERSONA_FACT_LOADED] co_founder")
                elif _has_fnd:
                    # "who is your founder" / "who made you" etc.
                    _id_resp = "Tayyab Aziz is the founder of Xyron."
                    logger.info("[IDENTITY_MEMORY_HIT] founder=Tayyab_Aziz")
                    logger.info("[PERSONA_FACT_LOADED] founder")
                else:
                    # Generic "who made you" / "who created you"
                    _id_resp = (
                        "Xyron was built by Tayyab Aziz, the founder, "
                        "and Muhammad Qasim, the co-founder."
                    )
                    logger.info("[PERSONA_FACT_LOADED] generic_creator")

                logger.info("[IDENTITY_RESPONSE_FINAL] text=%r", _id_resp[:80])
                await _send(websocket, {"type": "response", "text": _id_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_id_resp)
                memory.add_assistant(_id_resp, tool_name="identity_query")
                last_response_text = _id_resp
                last_activity_t    = time.time()
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=identity")
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("identity"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                        logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_no_audio route=identity")
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                    logger.info("[SPEAKING_FLAG_CLEARED] reason=interrupted route=identity")
                return
        except Exception as _id_exc:
            logger.debug("[IDENTITY_CHECK] skipped: %s", _id_exc)

        # ── Tier 0f: Store candidate ordinal selection ───────────────────────
        # Fires when pending_store_candidates exist and user says "first one" etc.
        # Must run BEFORE emotion/orchestrator so "first one" selects a candidate,
        # not triggers a web search or LLM response.
        _pending_store = _session_state.get("pending_store_candidates")
        if _pending_store:
            _STORE_ORDINAL_RE = re.compile(
                r'(?:^|\b)'
                r'(?:(?:download|install|get|choose|select|pick|take|go\s+with|the)\s+)?'
                r'(?P<ord>first|second|third|'
                r'number\s+(?:one|two|three)|'
                r'(?:the\s+)?(?:1(?:st)?|2(?:nd)?|3(?:rd)?)'
                r')'
                r'(?:\s+one)?\b',
                re.IGNORECASE,
            )
            _ORDINAL_IDX = {
                "first": 0, "1": 0, "1st": 0, "number one": 0,
                "second": 1, "2": 1, "2nd": 1, "number two": 1,
                "third": 2, "3": 2, "3rd": 2, "number three": 2,
            }

            _store_expired = (time.time() - _pending_store.get("created_at", 0)) > 300
            if _store_expired:
                logger.info("[STORE_SELECTION_EXPIRED] source_query=%r",
                            _pending_store.get("source_query"))
                _session_state["pending_store_candidates"] = None
            else:
                _ord_m = _STORE_ORDINAL_RE.search(transcript)
                if _ord_m:
                    _ord_raw = _ord_m.group("ord").lower().strip()
                    _ord_idx = None
                    for _k, _v in _ORDINAL_IDX.items():
                        if _k in _ord_raw or _ord_raw in _k:
                            _ord_idx = _v
                            break

                    if _ord_idx is not None:
                        _s_cands = _pending_store.get("candidates", [])
                        if _ord_idx < len(_s_cands):
                            _s_sel = _s_cands[_ord_idx]
                            logger.info("[STORE_SELECTION_ORDINAL_DETECTED] ord=%r idx=%d name=%r",
                                        _ord_raw, _ord_idx, _s_sel["name"])
                            logger.info("[STORE_SELECTION_SELECTED] name=%r id=%r source=%r",
                                        _s_sel["name"], _s_sel["id"], _s_sel.get("source"))
                            _session_state["pending_store_candidates"] = None
                            _ord_resp = await _run_tool("install_store_app", {
                                "app_name": _s_sel["name"],
                                "source":   _s_sel.get("source", "msstore"),
                            }, goal=transcript)
                            memory.add_assistant(_ord_resp, tool_name="install_store_app")
                            last_response_text = _ord_resp
                            last_activity_t    = time.time()
                            await _send(websocket, {"type": "response", "text": _ord_resp, "chunk": 1})
                            _interrupted = await _tts_with_fallback(_ord_resp)
                            if not _interrupted:
                                if _tts_state["audio_sent"]:
                                    logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=store_ordinal")
                                    await _send(websocket, {"type": "done"})
                                    asyncio.create_task(_spawn_tts_watchdog("store_ordinal"))
                                else:
                                    is_speaking = False
                                    await _send(websocket, {"type": "listening"})
                            else:
                                is_speaking = False
                                await _send(websocket, {"type": "listening"})
                            return

        # ── Tier 0g: Store install intent bypass ─────────────────────────────
        # Catches "download X from microsoft store" AND "open microsoft store
        # and install X" BEFORE the orchestrator/LLM. Detection now lives in
        # store_agent.py (single source of truth, also used by intent_router.py)
        # so the compound phrasing can no longer fall through to open_application
        # and hit the app_finder fuzzy-match bug.
        # Safety net: intent_router Tier 2 regex handles most cases; this is the
        # belt-and-suspenders for anything that slips through Tier 2/3.
        try:
            from api.services.store_agent import detect_install_intent as _sa_detect
            _s_intent = _sa_detect(transcript)
            if _s_intent:
                _s_app = _s_intent.product
                if _s_app:
                    logger.info("[STORE_INSTALL_INTENT_DETECTED] transcript=%r", transcript[:80])
                    logger.info("[STORE_INSTALL_QUERY_EXTRACTED] app_name=%r", _s_app)
                    logger.info("[STORE_INSTALL_ROUTE_SELECTED] tool=install_store_app app=%r", _s_app)
                    logger.info("[LLM_BYPASS_STORE_INSTALL] app=%r", _s_app)
                    _store_ack = _build_ack_text("install_store_app", {"app_name": _s_app})
                    await _send(websocket, {"type": "response", "text": _store_ack, "chunk": 1})
                    _interrupted_ack = await _tts_with_fallback(_store_ack)
                    _s_resp = await _run_tool("install_store_app", {
                        "app_name": _s_app,
                        "source":   "msstore",
                    }, goal=transcript)
                    from api.services.store_agent import set_store_state as _sa_set_state
                    from api.services.store_agent import StoreInstallState as _SIS
                    _sa_set_state(_session_state, _SIS.WAITING_INSTALL)
                    memory.add_assistant(_s_resp, tool_name="install_store_app")
                    last_response_text = _s_resp
                    last_activity_t    = time.time()
                    await _send(websocket, {"type": "response", "text": _s_resp, "chunk": 1})
                    _interrupted = await _tts_with_fallback(_s_resp)
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=store_bypass")
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("store_bypass"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _s_exc:
            logger.exception("[STORE_BYPASS] skipped: %s", _s_exc)

        _cp("tier_checks_done")
        # ── Emotion detection + mood update (runs before ANY routing) ─────────
        _emo          = None
        _current_mood = "CALM"
        try:
            from cognition.emotion_engine import emotion_engine as _ee
            from cognition.mood_state_machine import mood_machine as _mm, MoodContext, MoodState
            from cognition.cognitive_state import cognitive_state as _cs
            import datetime as _dt
            _emo  = _ee.detect_text(transcript)
            _hour = _dt.datetime.now().hour
            _mm.update(MoodContext(
                emotion       = _emo.emotion,
                energy        = _emo.energy,
                is_late_night = (_hour >= 23 or _hour < 5),
            ))
            _cs.mood_state     = _mm.state.value
            _cs.emotion_label  = _emo.emotion
            _cs.emotion_energy = _emo.energy
            _current_mood      = _mm.state.value
            logger.info("[VOICE_TRACE] stage=emotion_detect emotion=%s energy=%.2f mood=%s",
                        _emo.emotion, _emo.energy, _current_mood)
        except Exception as _exc:
            logger.warning("[VOICE_TRACE] emotion_detect failed: %s", _exc)

        _cp("emotion_detect_done")
        # ── Emotional intent guard — intercepts BEFORE orchestrator ───────────
        _guard = None
        try:
            from cognition.emotional_intent_guard import emotional_intent_guard as _eig, IntentClass
            _guard = _eig.classify(transcript, transcript)
            logger.info("[VOICE_TRACE] stage=emotional_guard intent=%s conf=%.2f reason=%s",
                        _guard.intent_class.value, _guard.confidence, _guard.reason)
        except Exception as _exc:
            logger.warning("[VOICE_TRACE] emotional_guard failed: %s", _exc)

        # ── Emotional branch — full bypass of orchestrator + tool routing ─────
        if _guard and _guard.intent_class.value in ("EMOTIONAL_EVENT", "CONVERSATION"):
            _emotional_response_text = ""
            try:
                from cognition.self_upgrade_detector import self_upgrade_detector as _sud
                from cognition.expression_engine import expression_engine as _expr
                from voice.emotion_tts_mapper import emotion_tts_mapper as _etm
                from api.services.response_pipeline import quick_response as _qr
                from cognition.mood_state_machine import MoodState as _MS

                _su = _sud.detect(transcript)

                if _su.is_self_upgrade:
                    _mm.force(_MS.HYPED)
                    try:
                        _cs.mood_state = "HYPED"
                    except Exception:
                        pass
                    _current_mood = "HYPED"
                    _sys = (
                        "You are Xyron, a voice-first AI built by Tayyab Aziz. "
                        + _mm.get_personality_addendum() + " "
                        + f"The user just told you about a {_su.upgrade_type} upgrade to your system. "
                        "React with genuine excitement. Reference the specific upgrade type. "
                        "1-2 punchy sentences. No markdown, no lists."
                    )
                    logger.info("[VOICE_TRACE] stage=self_upgrade type=%s mood=HYPED", _su.upgrade_type)
                elif _guard.reason == "frustration_pattern":
                    _mm.force(_MS.PROTECTIVE)
                    try:
                        _cs.mood_state = "PROTECTIVE"
                    except Exception:
                        pass
                    _current_mood = "PROTECTIVE"
                    _sys = (
                        "You are Xyron, a voice-first AI built by Tayyab Aziz. "
                        + _mm.get_personality_addendum() + " "
                        "The user is expressing frustration with a bug or issue. "
                        "Acknowledge it directly. Offer specific help. "
                        "1-2 sentences. No filler. No generic chatbot warmth."
                    )
                    logger.info("[VOICE_TRACE] stage=frustration mood=PROTECTIVE")
                else:
                    _sys = (
                        "You are Xyron, a voice-first AI built by Tayyab Aziz. "
                        + _mm.get_personality_addendum() + " "
                        "Respond naturally. 1-2 sentences max. No markdown."
                    )

                # Generate emotional response with specialized system prompt
                _raw = await _qr(transcript, memory.history_for_llm(), system_override=_sys)
                logger.info("[VOICE_TRACE] stage=response_generation raw=%r", _raw[:80])

                # Shape with expression engine
                _shaped = _expr.shape(
                    _raw, _current_mood,
                    _emo.emotion if _emo else "calmness",
                    _emo.energy  if _emo else 0.5,
                    turn_count = 1,
                    importance = _emo.importance if _emo else 0.5,
                )
                _emotional_response_text = _shaped

                # Apply emotion TTS transform
                _tts_r = _etm.transform(_shaped, _current_mood)
                logger.info("[TTS_EMOTION] state=%s speed=%.2f transform_applied=true",
                            _current_mood, _tts_r.speed_hint)
                logger.info("[VOICE_TRACE] stage=emotion_tts text=%r speed=%.2f",
                            _tts_r.text[:60], _tts_r.speed_hint)

                await _send(websocket, {"type": "response", "text": _shaped, "chunk": 1})

                # Same synth/send/state-tracking path every other route uses —
                # was previously a hand-rolled chunk loop here that duplicated
                # _tts_sequential's synth+send logic without its audio_sent/
                # playback-done bookkeeping, so the SPEAKING_FLAG_SET/watchdog
                # logic right after this branch was reading stale state left
                # over from whatever the last _tts_sequential call had set.
                await _tts_sequential(_tts_r.text, _speed_override=_tts_r.speed_hint)

            except Exception as _exc:
                logger.warning("[VOICE_TRACE] emotional_response error: %s", _exc)
                _OFFLINE = {
                    "self_upgrade_pattern": "That upgrade just landed. System noted — keep building.",
                    "frustration_pattern":  "I hear you. Send me the error and we'll tear it apart.",
                    "achievement_pattern":  "That's it. Done. Onto the next.",
                }
                _emotional_response_text = _OFFLINE.get(_guard.reason, "Got it.")
                await _send(websocket, {"type": "response", "text": _emotional_response_text, "chunk": 1})
                await _tts_sequential(_emotional_response_text)

            # Emit live emotion state update to frontend
            await _send(websocket, {
                "type":    "emotion_state",
                "mood":    _current_mood,
                "emotion": getattr(_emo, "emotion", "calmness") if _emo else "calmness",
                "energy":  getattr(_emo, "energy",  0.5)        if _emo else 0.5,
            })
            logger.info("[UI_EMOTION_EVENT] state=%s", _current_mood)

            if _emotional_response_text:
                last_response_text = _emotional_response_text
            memory.add_assistant(_emotional_response_text, tool_name=None)
            last_activity_t = time.time()
            if _tts_state["audio_sent"]:
                logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=emotional")
                await _send(websocket, {"type": "done"})
                asyncio.create_task(_spawn_tts_watchdog("emotional"))
            else:
                is_speaking = False
                await _send(websocket, {"type": "listening"})
                logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_no_audio route=emotional")
            return

        _cp("guard_done")

        # ── Tier 0f1: Flight follow-up conversation (Phase 4.8) ──────────────
        # Checked BEFORE the flight-decision tier and the generic intent
        # detector. If a flight session is active, phrases like "check
        # Emirates" / "sort by cheapest" / "which one do you recommend"
        # operate directly on the persistent browser workspace page instead
        # of spawning a brand-new agent task through the Coordinator.
        try:
            from api.agents.browser_agent import flight_session_state as _fss
            if _fss.get_active() is not None:
                from api.agents.browser_agent.flight_conversation import FlightConversationManager
                _followup_reply = await FlightConversationManager.handle_followup_logged(transcript)
                if _followup_reply:
                    logger.info("[VOICE_APPROVAL_RESOLVED] path=flight_followup text=%r", _followup_reply[:120])
                    await _send(websocket, {"type": "response", "text": _followup_reply, "chunk": 1})
                    _fu_interrupted = await _tts_sequential(_followup_reply)
                    memory.add_assistant(_followup_reply, tool_name="flight_followup")
                    last_response_text = _followup_reply
                    last_activity_t = time.time()
                    if not _fu_interrupted and _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("flight_followup"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _fu_exc:
            logger.warning("[FLIGHT_FOLLOWUP_ROUTING] error (falling through): %s", _fu_exc)

        # ── Tier 0f2: Flight decision voice approval ─────────────────────────
        # Checked BEFORE the generic intent detector — phrases like "continue"
        # or "cancel" are only meaningful here while a flight search is
        # actually waiting on a decision (flight_search_agent.request_decision).
        try:
            from api.agents.agent_runtime import agent_runtime as _art_fd
            _active_fd = _art_fd.get_active()
            if _active_fd and _active_fd.metadata.get("awaiting_flight_decision"):
                from api.agents.browser_agent.flight_search_agent import parse_flight_decision
                _fd_decision = parse_flight_decision(transcript)
                if _fd_decision:
                    logger.info(
                        "[VOICE_APPROVAL_DETECTED] task=%s decision=%r",
                        _active_fd.task_id, _fd_decision,
                    )
                    _active_fd.metadata["flight_decision"] = _fd_decision
                    _fd_ack = "Got it — one moment."
                    await _send(websocket, {"type": "response", "text": _fd_ack, "chunk": 1})
                    _fd_interrupted = await _tts_sequential(_fd_ack)
                    memory.add_assistant(_fd_ack, tool_name="flight_voice_decision")
                    last_response_text = _fd_ack
                    last_activity_t = time.time()
                    if not _fd_interrupted and _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("flight_decision"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _fd_exc:
            logger.warning("[FLIGHT_VOICE_DECISION] error (falling through): %s", _fd_exc)

        # ── Tier 0f3: Cleanup selection / approval voice command ─────────────
        # Checked BEFORE the generic intent detector — phrases like "clean
        # only temp files" or "don't touch browser cache" are only actioned
        # here while a PC-cleanup task is actually waiting on a decision.
        try:
            from api.agents.agent_runtime import agent_runtime as _art_cd
            _active_cd = _art_cd.get_active()
            if _active_cd and _active_cd.metadata.get("awaiting_cleanup_decision"):
                from api.agents.automation_agent.automation_agent import parse_cleanup_command
                _cd = parse_cleanup_command(transcript)
                _cd_handled = (
                    _cd["cancel"] or _cd["approved"] or _cd["include"] or _cd["exclude"]
                    or _cd["show_large"] or _cd["show_duplicates"] or _cd["query_safe"]
                )
                if _cd_handled:
                    logger.info("[VOICE_APPROVAL_DETECTED] task=%s decision=%r", _active_cd.task_id, _cd)
                    if _cd["cancel"]:
                        _active_cd.metadata["approved"] = False
                        _cd_ack = "Understood — cancelling. Nothing will be deleted."
                        logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=cancel", _active_cd.task_id)
                    elif _cd["query_safe"]:
                        _cd_ack = "Safe to delete right now: " + _active_cd.metadata.get(
                            "safe_categories_summary", "nothing significant right now")
                        logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=query_safe", _active_cd.task_id)
                    elif _cd["show_duplicates"] and not (_cd["approved"] or _cd["include"] or _cd["exclude"]):
                        _dg = _active_cd.metadata.get("duplicate_groups_list") or []
                        if _dg:
                            _cd_ack = f"I found {sum(len(g) for g in _dg)} duplicate file(s) across {len(_dg)} group(s)."
                        else:
                            _cd_ack = "I didn't find any duplicate files in this scan."
                        logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=show_duplicates", _active_cd.task_id)
                    elif _cd["show_large"] and not (_cd["approved"] or _cd["include"] or _cd["exclude"]):
                        _lf = _active_cd.metadata.get("large_files_list") or []
                        if _lf:
                            _lf_lines = [f"{f.get('size_human','?')} — {f.get('path','?')}" for f in _lf[:5]]
                            _cd_ack = "Here are your largest files: " + "; ".join(_lf_lines)
                        else:
                            _cd_ack = "I didn't find any large files over 100 MB in this scan."
                        logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=show_large", _active_cd.task_id)
                    else:
                        if _cd["include"] is not None or _cd["exclude"] is not None:
                            _active_cd.metadata["cleanup_selection"] = {
                                "include": _cd["include"], "exclude": _cd["exclude"],
                            }
                        if _cd["approved"]:
                            _active_cd.metadata["approved"] = True
                            _cd_ack = "Got it — cleaning that now."
                            logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=approve", _active_cd.task_id)
                        else:
                            _cd_ack = "Got it — updated what I'll clean. Still waiting for your go-ahead."
                            logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=update_selection", _active_cd.task_id)
                    await _send(websocket, {"type": "response", "text": _cd_ack, "chunk": 1})
                    _cd_interrupted = await _tts_sequential(_cd_ack)
                    memory.add_assistant(_cd_ack, tool_name="cleanup_voice_decision")
                    last_response_text = _cd_ack
                    last_activity_t = time.time()
                    if not _cd_interrupted and _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("cleanup_decision"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _cd_exc:
            logger.warning("[CLEANUP_VOICE_DECISION] error (falling through): %s", _cd_exc)

        # ── Tier 0g: Phase 3 Agent Dispatch ──────────────────────────────────
        # Detect long-running agent commands BEFORE the orchestrator.
        # Runs async in background — voice pipeline stays responsive.
        try:
            _tier0g_t0 = time.time()
            logger.info("[INTENT_START] turn=%d source=tier0g", my_turn)
            from api.services.agent_intent_detector import agent_intent_detector as _aid
            _agent_intent = _aid.detect(transcript)
            _tier0g_ms = (time.time() - _tier0g_t0) * 1000
            logger.info("[INTENT_END] turn=%d ms=%.0f source=tier0g", my_turn, _tier0g_ms)
            if _tier0g_ms > 300:
                logger.warning("[SLOW_STAGE] stage=intent_tier0g ms=%.0f budget=300", _tier0g_ms)
            logger.info("[AGENT_INTENT] is_agent=%s type=%s reason=%s",
                        _agent_intent.is_agent_command, _agent_intent.agent_type, _agent_intent.reason)
            if not (_agent_intent.is_agent_command and _agent_intent.agent_type == "browser"):
                logger.info("[BROWSER_LAZY_INIT_SKIPPED] reason=no_browser_command turn=%d", my_turn)

            # ── Phase 5.4B: short-command safety ──────────────────────────────
            # Nothing matched, but the transcript looks like a misheard
            # control word ("console" for "cancel"). Only worth asking about
            # when there's actually something to cancel/pause/resume —
            # contextual validation against the active operation, not a
            # blind guess from confidence/text alone. Never falls through to
            # an unrelated action on a misheard short word.
            if not _agent_intent.is_agent_command and _agent_intent.ambiguous_control:
                from api.agents.agent_runtime import agent_runtime as _art_amb
                _active_amb = _art_amb.get_active()
                _flight_active_amb = _flight_session_active()
                if _active_amb is not None or _flight_active_amb:
                    _amb_word = _agent_intent.ambiguous_control
                    logger.info(
                        "[AMBIGUOUS_CONTROL_DETECTED] word=%s transcript=%r active_task=%s flight_active=%s",
                        _amb_word, transcript[:60], _active_amb is not None, _flight_active_amb,
                    )
                    _session_state["pending_control_confirmation"] = {"action": _amb_word}
                    _clarify_text = f"Did you say {_amb_word}?"
                    await _send(websocket, {"type": "response", "text": _clarify_text, "chunk": 1})
                    _interrupted = await _tts_sequential(_clarify_text)
                    memory.add_assistant(_clarify_text, tool_name="ambiguous_control_clarify")
                    last_response_text = _clarify_text
                    last_activity_t = time.time()
                    if not _interrupted and _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("ambiguous_control"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return

            if _agent_intent.is_agent_command:
                from api.agents.agent_runtime import agent_runtime as _art
                from api.agents.agent_types import AgentType as _AT

                if _agent_intent.agent_type == "control":
                    # ── Agent control commands ────────────────────────────────
                    _active = _art.get_active()
                    _ctrl_action = _agent_intent.control_action

                    if not _active or _ctrl_action == "progress":
                        # Progress query — check even if no active task
                        _prog_text = _art.get_progress()
                        logger.info("[AGENT_CONTROL] action=progress text=%r", _prog_text[:60])
                        logger.info("[WORKFLOW_PROGRESS_REQUEST] text=%r", _prog_text[:60])
                        await _send(websocket, {"type": "response", "text": _prog_text, "chunk": 1})
                        _interrupted = await _tts_sequential(_prog_text)
                        memory.add_assistant(_prog_text, tool_name="agent_progress")
                        last_response_text = _prog_text
                        last_activity_t = time.time()
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("agent_control"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

                    if _ctrl_action == "cancel":
                        _tid = _active.task_id
                        await _art.cancel(_tid)
                        _cancel_resp = "I've cancelled that task."
                        logger.info("[AGENT_CONTROL] action=cancel task_id=%s", _tid)
                        logger.info("[WORKFLOW_CANCEL] task_id=%s", _tid)
                        await _send(websocket, {"type": "response", "text": _cancel_resp, "chunk": 1})
                        _interrupted = await _tts_sequential(_cancel_resp)
                        memory.add_assistant(_cancel_resp, tool_name="agent_cancel")
                        last_response_text = _cancel_resp
                        last_activity_t = time.time()
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("agent_control"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

                    if _ctrl_action == "pause":
                        await _art.pause(_active.task_id)
                        _pause_resp = "I've paused that task. Say resume when you're ready."
                        logger.info("[AGENT_CONTROL] action=pause task_id=%s", _active.task_id)
                        logger.info("[WORKFLOW_PAUSE] task_id=%s", _active.task_id)
                        await _send(websocket, {"type": "response", "text": _pause_resp, "chunk": 1})
                        _interrupted = await _tts_sequential(_pause_resp)
                        memory.add_assistant(_pause_resp, tool_name="agent_pause")
                        last_response_text = _pause_resp
                        last_activity_t = time.time()
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("agent_control"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

                    if _ctrl_action == "resume":
                        await _art.resume(_active.task_id)
                        _resume_resp = "Resuming. I'll keep you updated."
                        logger.info("[AGENT_CONTROL] action=resume task_id=%s", _active.task_id)
                        logger.info("[WORKFLOW_RESUME] task_id=%s", _active.task_id)
                        await _send(websocket, {"type": "response", "text": _resume_resp, "chunk": 1})
                        _interrupted = await _tts_sequential(_resume_resp)
                        memory.add_assistant(_resume_resp, tool_name="agent_resume")
                        last_response_text = _resume_resp
                        last_activity_t = time.time()
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("agent_control"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

                elif _agent_intent.agent_type == "personality":
                    # ── Personality mode switch — instant, no background task ─
                    try:
                        from api.agents.personality.personality_engine import (
                            personality_engine as _pe, PersonalityMode as _PM,
                        )
                        _pm = _PM(_agent_intent.personality_mode or "default")
                        _mode_resp = _pe.set_mode(_pm)
                        logger.info("[AGENT_ROUTE_SELECTED] type=personality mode=%s", _pm.value)
                        await _send(websocket, {
                            "type":          "agent_progress",
                            "task_id":       "personality",
                            "message":       f"Mode set: {_pm.value}",
                            "status":        "completed",
                            "personality_mode": _pm.value,
                        })
                        await _send(websocket, {"type": "response", "text": _mode_resp, "chunk": 1})
                        _interrupted = await _tts_sequential(_mode_resp)
                        memory.add_assistant(_mode_resp, tool_name="personality_mode_switch")
                        last_response_text = _mode_resp
                        last_activity_t = time.time()
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("personality"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    except Exception as _pe_exc:
                        logger.warning("[PERSONALITY_MODE_ERROR] %s", _pe_exc)
                        _fallback_pm = f"Switching to {_agent_intent.personality_mode} mode."
                        await _send(websocket, {"type": "response", "text": _fallback_pm, "chunk": 1})
                        await _tts_sequential(_fallback_pm)
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return

                elif _agent_intent.agent_type == "browser" and _flight_session_active():
                    # Phase 4.15: a garbled STT transcript during an active flight
                    # conversation can still match a *different* agent-intent
                    # pattern (compare/research/job) than the flight-followup
                    # detector's own patterns — live-measured spawning a second,
                    # fully redundant Coordinator/BrowserAgent task concurrently
                    # with the one already running, doubling event-loop load and
                    # contributing to WS keepalive timeouts. If we reach here
                    # with an active session, Tier 0e/0f1 already had first
                    # chance and didn't recognize it — don't compound that
                    # miss by launching a second, unrelated browser workflow.
                    logger.info("[BROWSER_DISPATCH_SUPPRESSED] reason=flight_session_active type=%s transcript=%r",
                                _agent_intent.agent_type, transcript[:60])

                else:
                    # ── Phase 5: direct dispatch for single-domain agent goals ──
                    # execution_mode was decided once, upstream, by
                    # agent_intent_detector — COORDINATED_WORKFLOW only when
                    # the goal genuinely spans >1 domain (e.g. "research this
                    # and build a site from it"). Everything else — a plain
                    # flight search, a PC cleanup, a single browser task —
                    # launches the specialist directly and never touches
                    # TaskGraph/DelegationPlanner/verifier/reflection/
                    # collaboration-memory.
                    _direct_type_map = {
                        "browser":    _AT.BROWSER,
                        "coding":     _AT.CODING,
                        "automation": _AT.AUTOMATION,
                    }
                    _is_coordinated = _agent_intent.execution_mode == "COORDINATED_WORKFLOW"
                    _launch_type = (
                        _AT.COORDINATOR if _is_coordinated
                        else _direct_type_map.get(_agent_intent.agent_type, _AT.COORDINATOR)
                    )

                    _coordinator_ack = "On it — I'll take care of that."
                    _ack_by_type = {
                        "browser":    "On it. I'll research that and report back.",
                        "coding":     "I'll start building that now. I'll keep you updated.",
                        "automation": "Starting the scan now. I'll let you know what I find.",
                    }
                    _coordinator_ack = _ack_by_type.get(_agent_intent.agent_type, _coordinator_ack)
                    logger.info("[%s_ROUTE_SELECTED] type=%s mode=%s transcript=%r",
                                "COORDINATOR" if _is_coordinated else "DIRECT_AGENT",
                                _agent_intent.agent_type, _agent_intent.execution_mode, transcript[:60])

                    # Personality-polish the ack
                    try:
                        from api.agents.personality.personality_engine import personality_engine as _pe2
                        _coordinator_ack = _pe2.polish_response(_coordinator_ack)
                    except Exception:
                        pass

                    await _send(websocket, {"type": "response", "text": _coordinator_ack, "chunk": 1})
                    _interrupted = await _tts_sequential(_coordinator_ack)
                    memory.add_assistant(_coordinator_ack, tool_name="coordinator_launch")
                    last_response_text = _coordinator_ack
                    last_activity_t = time.time()
                    if not _interrupted and _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("coordinator_launch"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})

                    # WebSocket send fn shared with coordinator and all sub-agents
                    async def _coord_ws_send(payload: dict) -> bool:
                        if payload.get("type") == "narration":
                            _text = payload.get("message", "")
                            if _text:
                                _narration_queue.put_nowait(_text)
                        return await _send(websocket, payload)

                    # Launch Coordinator (multi-domain) or the specialist agent
                    # directly (single-domain) in the background.
                    _coord_task = await _art.launch(
                        goal=transcript,
                        agent_type=_launch_type,
                        ws_send_fn=_coord_ws_send,
                        context={
                            "turn_id":         my_turn,
                            "trace_id":        _trace_id,
                            "primary_type":    _agent_intent.agent_type,
                            "history":         memory.history_for_llm()[-6:],
                            "turn_started_at": _turn_t0,
                        },
                    )
                    logger.info("[ACTIVE_WORKFLOW_SET] task_id=%s goal=%r mode=%s",
                                _coord_task.task_id, transcript[:60], _agent_intent.execution_mode)
                    # This branch returns before the shared tail-end timing
                    # block (PERF_TOTAL/TOTAL_PIPELINE further down) — log
                    # the same total-turn-handling measurement here too, so
                    # this path (what every flight/cleanup/single-agent
                    # command takes) isn't invisible to pipeline timing.
                    _coord_turn_ms = (time.time() - _turn_t0) * 1000
                    logger.info("[TOTAL_PIPELINE] turn=%d ms=%.0f path=%s", my_turn, _coord_turn_ms,
                                "coordinator_launch" if _is_coordinated else "direct_agent_launch")
                    return

        except Exception as _agi_exc:
            logger.warning("[TIER_0G_AGENT_DISPATCH] error (falling through): %s", _agi_exc)

        _cp("agent_dispatch_done")
        logger.info("[VOICE_TRACE] stage=intent_router — passing to orchestrator")
        logger.info("[BRAIN_PIPELINE] stage=routing input=%r turn=%d", transcript[:60], my_turn)

        # ── Orchestrator decision ─────────────────────────────────────────────
        from brain.orchestrator import orchestrator as _orch, ActionType
        logger.info("[TURN_START] turn=%d routing transcript=%r", my_turn, transcript[:60])
        _intent_t0 = time.time()
        logger.info("[INTENT_START] turn=%d", my_turn)
        decision = await _orch.decide(transcript, memory.history_for_llm())
        _router_ms = (time.time() - _intent_t0) * 1000
        _lat["router"] = _router_ms
        logger.info("[INTENT_END] turn=%d ms=%.0f", my_turn, _router_ms)
        if _router_ms > 300:
            logger.warning("[SLOW_STAGE] stage=intent ms=%.0f budget=300", _router_ms)
        logger.info("[ROUTER_MS] turn=%d ms=%.0f action=%s tool=%s",
                    my_turn, _router_ms, decision.action.name, decision.tool_name or "none")
        logger.info("[PERF_INTENT] turn=%d ms=%.0f action=%s tool=%s",
                    my_turn, _router_ms, decision.action.name, decision.tool_name or "none")
        logger.info("[ORCHESTRATOR] action=%s reason=%s", decision.action.name, decision.reason)
        logger.info("[VOICE_TRACE] stage=tool_route action=%s tool=%s",
                    decision.action.name, decision.tool_name)
        logger.info(
            "[TRACE %s] [ROUTER_DECISION] action=%s tier=%d tool=%s confidence=%.2f reason=%s",
            _trace_id, decision.action.name, decision.tier,
            decision.tool_name or "none", decision.confidence, decision.reason,
        )
        if _trace:
            _trace.action          = decision.action.name
            _trace.route_tier      = decision.tier
            _trace.route_tool      = decision.tool_name or ""
            _trace.route_confidence = decision.confidence
            _trace.timings_ms["router"] = _router_ms
        if _perf_rec:
            _perf_rec.set("router", _router_ms)
            if decision.tool_name:
                _perf_rec.tool_name    = decision.tool_name
                from api.services.perf_budget import _classify_command as _pbc
                _perf_rec.command_type = _pbc(decision.tool_name)
        # Log fast vs LLM path selection
        if decision.tier <= 2:
            logger.info("[FAST_PATH_SELECTED] tool=%s tier=%d", decision.tool_name or "none", decision.tier)
        elif decision.tier == 3:
            logger.info("[INDEX_HIT] tool=%s tier=semantic", decision.tool_name or "none")
        else:
            logger.info("[LLM_FALLBACK_SELECTED] reason=%s", decision.reason)

        if my_turn != current_turn_id:
            logger.info("[STALE_RESPONSE_DROPPED] turn=%d dropped after orchestrator (current=%d)",
                        my_turn, current_turn_id)
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        response_text: str = ""
        interrupted:   bool = False
        _ack_spoken:   bool = False   # set by TOOL branch; read in post-dispatch

        # ── STOP ──────────────────────────────────────────────────────────────
        if decision.action == ActionType.STOP:
            response_text = "Goodbye! Have a great day."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            await _tts_sequential(response_text)
            await _send(websocket, {"type": "done"})
            await _safe_close(websocket, code=1000, reason="stop_command", tag="SESSION_WS")
            return

        # ── INTERRUPT — soft cancel, keep session alive ────────────────────────
        elif decision.action == ActionType.INTERRUPT:
            is_speaking     = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "listening"})
            return

        # ── CLARIFY — repeat last response ────────────────────────────────────
        elif decision.action == ActionType.CLARIFY:
            response_text = last_response_text or "I didn't catch that. Could you say it again?"
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_with_fallback(response_text)

        # ── MEMORY_REF — pronoun resolved to prior action ─────────────────────
        elif decision.action == ActionType.MEMORY_REF:
            tool = decision.tool_name
            try:
                if tool == "delete_file":
                    from api.tools import registry as _registry
                    paths = decision.tool_params.get("paths", [])
                    logger.info("[MEMORY_USED] delete_ref paths=%s", paths)
                    deleted, failed = [], []
                    for p in paths:
                        try:
                            r = await asyncio.to_thread(
                                _registry.execute,
                                "delete_file", {"path": p, "confirmed": True}, {},
                            )
                            (deleted if r.success else failed).append(p)
                        except Exception:
                            failed.append(p)
                    if deleted and not failed:
                        response_text = f"Deleted {len(deleted)} item{'s' if len(deleted) > 1 else ''}."
                    elif deleted:
                        response_text = f"Deleted {len(deleted)}, but {len(failed)} failed."
                    else:
                        response_text = "I couldn't delete those — they may no longer exist."
                elif tool:
                    logger.info("[MEMORY_USED] %s ref tool=%s", decision.reason, tool)
                    response_text = await _run_tool(tool, decision.tool_params, goal=transcript)
                else:
                    response_text = "I couldn't resolve what you're referring to."
            except Exception as exc:
                logger.warning("[WS/session] memory_ref exec error: %s", exc)
                response_text = "I had trouble with that reference."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_with_fallback(response_text)

        # ── TOOL — matched tool execution ─────────────────────────────────────
        elif decision.action == ActionType.TOOL:
            _slow_tools = {
                "open_application", "smart_open", "open_directory", "open_drive",
                "open_system_settings", "search_youtube", "search_web", "open_url",
                "play_media_file", "install_store_app",
            }
            # ACK is the only spoken response for these launch/navigate tools.
            # install_store_app is excluded — its response IS the confirmation prompt.
            _ACK_ONLY_TOOLS = {
                "open_application", "smart_open", "open_directory", "open_drive",
                "open_system_settings", "open_url", "search_youtube",
            }

            # Fix 3: Start tool execution IMMEDIATELY as a background task —
            # don't block on ACK synthesis before calling the OS.
            _tool_t0 = time.time()
            _tool_task = asyncio.create_task(
                _run_tool(decision.tool_name, decision.tool_params, goal=transcript)
            )

            # Fix 2: Build command-aware ACK and send while tool runs in parallel
            # Phase 5.3: skip this tool-specific cached ack when the universal
            # immediate ack ("Got it."/"Sure.") already fired this turn —
            # otherwise the user hears two acks back to back ("Got it....
            # Opening Calculator.") where one used to be enough. The tool's
            # own completion message still gets spoken normally below via
            # response_text once _ack_spoken stays False.
            if decision.tool_name in _slow_tools and _immediate_ack_state["task"] is None:
                try:
                    from api.services.tts_cache_service import tts_cache as _tcc
                    _ack_text = _build_ack_text(decision.tool_name, decision.tool_params)
                    # ── Localize ACK for non-English sessions ─────────────────────────
                    _ack_ml_lang = _session_state.get("ml_resp_lang", "en")
                    if _ack_ml_lang != "en":
                        try:
                            from api.services.response_localizer import localize_response as _ack_loc
                            _loc_ack = _ack_loc(_ack_text, _ack_ml_lang)
                            if _loc_ack:
                                logger.info("[ACK_LOCALIZED] %r → %r", _ack_text, _loc_ack)
                                _ack_text = _loc_ack
                        except Exception:
                            pass
                        # Non-English ACK: synthesize via XTTS (bypass Kokoro cache)
                        _ack_wav: Optional[bytes] = None
                        try:
                            from voice.tts_router import synthesize as _ack_route
                            _ack_synth_t0 = time.time()
                            _ack_wav = await asyncio.wait_for(
                                asyncio.to_thread(_ack_route, _ack_text, _ack_ml_lang),
                                timeout=30.0,
                            )
                            _ack_synth_ms = (time.time() - _ack_synth_t0) * 1000
                            logger.info("[ACK_SYNTH_ML_MS] ms=%.0f lang=%s text=%r",
                                        _ack_synth_ms, _ack_ml_lang, _ack_text)
                            _lat["tts"] = _ack_synth_ms
                        except Exception as _xa:
                            logger.warning("[ACK_ML_TTS_FAIL] lang=%s err=%s — no ACK audio", _ack_ml_lang, _xa)
                    else:
                        # English ACK: Voice-match check, then Kokoro cache
                        _cache_voice = getattr(_tcc, "_build_voice", "nova")
                        _voice_match = (_cache_voice == voice)
                        if _voice_match:
                            logger.info("[TTS_CACHE_VOICE_MATCH] cache_voice=%s session_voice=%s — using cache", _cache_voice, voice)
                            _ack_wav = _tcc.get_by_text(_ack_text)
                        else:
                            logger.info("[TTS_CACHE_DISABLED_MISMATCH] cache_voice=%s session_voice=%s — bypassing cache", _cache_voice, voice)
                            _ack_wav = None
                        if not _ack_wav:
                            _ack_synth_t0 = time.time()
                            _ack_wav = await asyncio.to_thread(
                                _tcc.synthesize_or_cached, _ack_text, voice, speed
                            )
                            _ack_synth_ms = (time.time() - _ack_synth_t0) * 1000
                            logger.info("[ACK_SYNTH_MS] ms=%.0f text=%r cache=miss", _ack_synth_ms, _ack_text)
                            _lat["tts"] = _ack_synth_ms
                        else:
                            logger.info("[ACK_SYNTH_MS] ms=0 text=%r cache=hit", _ack_text)
                    if _ack_wav:
                        await _send(websocket, {
                            "type":  "audio",
                            "data":  base64.b64encode(_ack_wav).decode(),
                            "chunk": 0,
                            "total": 1,
                            "final": False,
                            "text":  _ack_text,
                            "ack":   True,
                        })
                        logger.info("[ACK_SENT] tool=%s text=%r voice=%s", decision.tool_name, _ack_text, voice)
                        logger.info("[FAST_ACK_SENT] tool=%s text=%r elapsed_ms=%.0f",
                                    decision.tool_name, _ack_text, (time.time() - _turn_t0) * 1000)
                        if decision.tool_name in _ACK_ONLY_TOOLS:
                            _ack_spoken = True
                            logger.info("[ACK_ONLY_MODE] tool=%s", decision.tool_name)
                except Exception as _ace:
                    logger.debug("[ACK_SENT] skipped: %s", _ace)

            # Await tool result (likely already running or done)
            try:
                response_text = await _tool_task
            except Exception as exc:
                logger.warning("[WS/session] tool exec error: %s", exc)
                response_text = "That action ran into an issue."
            _lat["tool"] = (time.time() - _tool_t0) * 1000
            if _perf_rec:
                _perf_rec.set("tool", _lat["tool"])

            # Some tools signal the frontend to trigger a UI sequence.
            _FE_ACTIONS: dict[str, str] = {"takeover_mode": "TAKEOVER_START"}
            if decision.tool_name in _FE_ACTIONS:
                await _send(websocket, {
                    "type":   "frontend_action",
                    "action": _FE_ACTIONS[decision.tool_name],
                })
            if _ack_spoken:
                logger.info("[FINAL_RESPONSE_SKIPPED] reason=ack_already_spoken tool=%s", decision.tool_name)
                interrupted = False
            else:
                logger.info("[FINAL_RESPONSE_SENT] text=%r", response_text[:60])
                await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
                _tts_tool_t0 = time.time()
                interrupted = await _tts_with_fallback(response_text)
                _lat["tts"] = (time.time() - _tts_tool_t0) * 1000
                if _perf_rec:
                    _perf_rec.set("tts", _lat["tts"])

        # ── MULTI_STEP — compound command via planner ─────────────────────────
        elif decision.action == ActionType.MULTI_STEP:
            from brain.planner import planner as _planner
            from brain.orchestrator import orchestrator as _o2, ActionType as _AT

            async def _step_fn(step_text: str, hist: list[dict]) -> str:
                step_dec = await _o2.decide(step_text, hist)
                if step_dec.action == _AT.TOOL:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params, goal=step_text)
                elif step_dec.action == _AT.MEMORY_REF and step_dec.tool_name:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params, goal=step_text)
                else:
                    from api.services.response_pipeline import quick_response
                    return await quick_response(step_text, hist)

            plan = _planner.build(transcript)
            if plan:
                if _trace:
                    _trace.plan_steps = [s.text for s in plan.steps]
                _plan_t0 = time.time()
                response_text = await _planner.execute(plan, _step_fn, memory.history_for_llm())
                _plan_ms = (time.time() - _plan_t0) * 1000
                logger.info("[TRACE %s] [PLANNER_MS] ms=%.0f steps=%d",
                            _trace_id, _plan_ms, len(plan.steps))
                if _trace:
                    _trace.timings_ms["planner"] = _plan_ms
            else:
                response_text = "I had trouble parsing those steps."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_with_fallback(response_text)

        # ── LLM — overlapped streaming generation + TTS ───────────────────────
        else:
            response_text, interrupted = await _run_llm_stream(
                transcript, memory.history_for_llm()
            )

        _cp("dispatch_done")
        # ── Post-dispatch bookkeeping ─────────────────────────────────────────
        if response_text:
            last_response_text = response_text
        memory.add_assistant(response_text, tool_name=decision.tool_name)

        # ── Brain pipeline completion trace ───────────────────────────────────
        logger.info(
            "[BRAIN_PIPELINE] stage=complete action=%s tool=%s output=%r turn=%d",
            decision.action.name, decision.tool_name or "none",
            (response_text or "")[:60], my_turn,
        )

        # ── Async: learning service + episodic recording (non-blocking) ───────
        _bp_transcript = transcript
        _bp_tool       = decision.tool_name or ""
        _bp_response   = response_text or ""
        async def _post_dispatch_brain() -> None:
            # Learning pattern detection
            if _bp_tool:
                try:
                    from api.services.learning_service import learning_service as _ls
                    _lr = _ls.record(_bp_transcript, _bp_tool)
                    if _lr.has_suggestion:
                        logger.info("[LEARNING_PATTERN_DETECTED] tool=%s count=%d", _bp_tool, _lr.count)
                        logger.info("[LEARNING_SUGGESTION] %s", _lr.suggestion)
                        # Future: speak suggestion when appropriate (not intrusive for now)
                except Exception:
                    pass
            # Episodic memory recording
            try:
                from api.services.episodic_memory import episodic_memory as _em
                await asyncio.to_thread(
                    _em.save,
                    _session_id,
                    "assistant",
                    _bp_response[:200],
                    _bp_tool or None,
                    True,
                )
            except Exception:
                pass
        asyncio.create_task(_post_dispatch_brain())

        # Emit emotion state so frontend orb stays in sync after tool/LLM turns
        await _send(websocket, {
            "type":    "emotion_state",
            "mood":    _current_mood,
            "emotion": getattr(_emo, "emotion", "calmness") if _emo else "calmness",
            "energy":  getattr(_emo, "energy",  0.5)        if _emo else 0.5,
        })
        logger.info("[UI_EMOTION_EVENT] state=%s", _current_mood)
        logger.info("[VOICE_TRACE] stage=audio_stream done response=%r", (response_text or "")[:60])

        _cp("perf_total")
        logger.info("[TTS_STOPPED] interrupted=%s", interrupted)
        _turn_total_ms = _tl["perf_total"]
        logger.info("[PERF_TOTAL] turn=%d ms=%.0f interrupted=%s", my_turn, _turn_total_ms, interrupted)
        logger.info("[TOTAL_PIPELINE] turn=%d ms=%.0f", my_turn, _turn_total_ms)
        # Timeline: show ms-from-turn-start at each checkpoint to find hidden gaps
        _tl_parts = " ".join(f"{k}={v:.0f}" for k, v in _tl.items())
        logger.info("[V_TIMELINE] %s", _tl_parts)
        logger.info("[TRACE %s] [TOTAL_MS] ms=%.0f", _trace_id, _turn_total_ms)
        # ── Per-stage latency breakdown ────────────────────────────────────────
        logger.info(
            "[V_LATENCY] stt_ms=%.0f normalize_ms=%.0f followup_ms=%.0f "
            "screen_context_ms=%.0f router_ms=%.0f tool_ms=%.0f tts_ms=%.0f total_ms=%.0f",
            _lat["stt"], _lat["normalize"], _lat["followup"],
            _lat["screen_context"], _lat["router"], _lat["tool"], _lat["tts"],
            _turn_total_ms,
        )
        _bottleneck_stage = max(_lat, key=_lat.__getitem__)
        _bottleneck_ms    = _lat[_bottleneck_stage]
        if _bottleneck_ms > 200:
            logger.info("[V_LATENCY_BOTTLENECK] stage=%s ms=%.0f", _bottleneck_stage, _bottleneck_ms)
        if _turn_total_ms > 2000:
            logger.warning("[TRACE %s] [SLOW_COMMAND_WARNING] ms=%.0f threshold=2000",
                           _trace_id, _turn_total_ms)
        if _trace:
            _trace.timings_ms["total"] = _turn_total_ms
        # Finish perf budget record
        if _perf_rec:
            try:
                from api.services.perf_budget import perf_budget as _pb
                _pb.finish(_perf_rec)
            except Exception:
                pass
        if _ts and _trace_id != "VX-ERR":
            _ts.finish(_trace_id, status="success", result=(response_text or "")[:200])
        # Release voice activity lock so background services resume
        try:
            from api.services.voice_activity import set_active as _va_done
            _va_done(False)
        except Exception:
            pass
        last_activity_t = time.time()
        # Clear before logging so [VOICE_STATE_AFTER_TTS] always reflects the post-TTS state.
        is_speaking = False
        logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_exit route=dispatch")
        logger.info(
            "[VOICE_STATE_AFTER_TTS] is_speaking=%s audio_sent=%s response=%r",
            is_speaking, _tts_state["audio_sent"], (response_text or "")[:60],
        )
        last_activity_t = time.time()
        logger.info("[TTS_FLAG_ORDER] is_speaking cleared before VOICE_STATE_AFTER_TTS route=dispatch")

        if not interrupted:
            if _tts_state["audio_sent"]:
                # Audio was sent — tell frontend stream is complete and arm 2s watchdog.
                await _send(websocket, {"type": "done"})
                asyncio.create_task(_spawn_tts_watchdog("dispatch"))
            elif _ack_spoken:
                # ACK audio was already sent for this fast-action command.
                # Listening is restored by the main loop's tts_done handler when
                # the frontend acknowledges the ACK. Send "done" so the watchdog
                # fires as fallback if tts_done never arrives.
                logger.info("[ACK_ONLY_COMPLETE] tool=%s — sending done for tts_done handshake",
                            getattr(decision, "tool_name", "?"))
                await _send(websocket, {"type": "done"})
                asyncio.create_task(_spawn_tts_watchdog("dispatch"))
            else:
                # Kokoro failed to synthesize audio — immediately restore listening.
                logger.warning("[TTS_NO_AUDIO_SENT] synthesis failed — sending listening immediately")
                await _send(websocket, {"type": "listening"})
                logger.info("[VOICE_LISTENING_READY] state=listening reason=tts_no_audio")
        else:
            # Interrupted: frontend received no/partial audio — clear gate immediately.
            await _send(websocket, {"type": "listening"})
            logger.info("[VOICE_LISTENING_READY] state=listening reason=interrupted")

    # ── Opening greeting ──────────────────────────────────────────────────────
    import datetime as _dt
    _hour = _dt.datetime.now().hour
    _tod  = "morning" if _hour < 12 else "afternoon" if _hour < 18 else "evening"
    _greet_name = preferred_name or "boss"
    _greeting_text = (
        f"Good {_tod}, {_greet_name}. I'm Xyron, ready and at your service. Just give the word."
    )
    logger.info("[GREETING_STARTED] name=%r text=%r", _greet_name, _greeting_text)
    is_speaking = True
    # Try TTS cache first (pre-synthesized during warmup)
    _g_wav: Optional[bytes] = None
    try:
        from api.services.tts_cache_service import tts_cache as _tc
        _g_wav = _tc.get_by_text(_greeting_text)
        if _g_wav:
            logger.info("[TTS_CACHE_HIT] greeting")
    except Exception:
        pass
    if not _g_wav:
        _g_wav = await _synthesize_chunk(_greeting_text, "onyx", 1.0)
    if _g_wav:
        await _send(websocket, {
            "type":  "audio",
            "data":  base64.b64encode(_g_wav).decode(),
            "chunk": 1,
            "total": 1,
            "final": True,
            "text":  _greeting_text,
        })
        logger.info("[GREETING_SENT] bytes=%d", len(_g_wav))
        _deadline = time.time() + 15.0
        while websocket.client_state == WebSocketState.CONNECTED and time.time() < _deadline:
            try:
                _d = await asyncio.wait_for(websocket.receive(), timeout=2.0)
                if _d.get("type") == "websocket.disconnect":
                    break
                _txt = _d.get("text")
                if _txt:
                    try:
                        _m = json.loads(_txt)
                        if _m.get("type") == "tts_done":
                            logger.info("[GREETING_DONE]")
                            break
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        pass
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    else:
        logger.warning("[GREETING] synthesis failed — proceeding to listen")
    is_speaking = False
    last_activity_t = time.time()  # reset idle timer after greeting completes
    await _send(websocket, {"type": "listening"})

    # ── Phase 6: PCM watchdog — if no mic frames arrive within 15s, warn frontend ──
    async def _pcm_watchdog() -> None:
        await asyncio.sleep(15.0)
        if websocket.client_state != WebSocketState.CONNECTED:
            return
        if _audio_chunks_received == 0:
            logger.warning(
                "[SESSION_MIC_WATCHDOG] no PCM frames received 15s after greeting — "
                "mic may not have armed on the frontend"
            )
            logger.warning(
                "[MIC_REQUIRED_SENT] sending mic_required to frontend — "
                "chunks_received=0 ws_state=%s",
                websocket.client_state,
            )
            await _send(websocket, {
                "type":    "mic_required",
                "message": "No mic input detected — please check microphone permissions",
            })

    asyncio.create_task(_pcm_watchdog())

    # ── Main receive loop ─────────────────────────────────────────────────────
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                data = await asyncio.wait_for(
                    websocket.receive(), timeout=SESSION_TIMEOUT + 5.0
                )
            except asyncio.TimeoutError:
                if not await _send(websocket, {"type": "ping"}):
                    break
                continue
            except WebSocketDisconnect:
                break

            if data.get("type") == "websocket.disconnect":
                break

            # Text control messages
            text = data.get("text")
            if text:
                try:
                    msg = json.loads(text)
                    t   = msg.get("type")
                    if t == "end_of_speech":
                        _min_frames = _MIN_SPEECH_FRAMES
                        if _flight_session_active():
                            _min_frames = _SHORT_MIN_SPEECH_FRAMES
                            logger.info("[FLIGHT_SHORT_COMMAND_MODE] min_frames=%d buffer_len=%d",
                                        _min_frames, len(pcm_buffer))
                        if speech_started and len(pcm_buffer) >= _min_frames:
                            frames = list(pcm_buffer)
                            if _min_frames == _SHORT_MIN_SPEECH_FRAMES:
                                logger.info("[FLIGHT_SHORT_COMMAND_SELECTED] frames=%d", len(frames))
                            pcm_buffer.clear()
                            speech_started  = False
                            silence_count   = 0
                            is_speaking     = True
                            interrupt_event.clear()
                            current_turn_id += 1
                            logger.info("[MICRO_PROFILE] op=speech_end_finalized turn=%d ts=%.6f",
                                        current_turn_id, time.time())
                            # Phase 5.3: fire a content-free filler the instant speech
                            # ends — no transcript, no classification needed. Skipped
                            # for flight-followup short commands ("only Emirates",
                            # "cheapest") — that path already has its own fast,
                            # tuned acks (conversation_layer's ACK_PHRASES) and is
                            # deliberately snappy; stacking a second generic ack in
                            # front of it would just add a redundant beat.
                            if not _IMMEDIATE_ACK_ENABLED:
                                logger.info("[IMMEDIATE_ACK_SKIPPED] reason=disabled")
                            elif _min_frames != _SHORT_MIN_SPEECH_FRAMES:
                                _imm_ack_text = _pick_immediate_ack()
                                await _send(websocket, {"type": "response", "text": _imm_ack_text, "chunk": 1})
                                _immediate_ack_state["task"] = asyncio.create_task(
                                    _tts_sequential(_imm_ack_text, _is_immediate_ack=True)
                                )
                                logger.info("[MICRO_PROFILE] op=immediate_ack_queued turn=%d ts=%.6f",
                                            current_turn_id, time.time())
                            asyncio.create_task(process_utterance(frames, current_turn_id))
                    elif t == "tts_done":
                        logger.info("[TTS_DONE_RECEIVED] is_speaking=%s", is_speaking)
                        _tts_playback_done_event.set()  # unblock _tts_await_playback if waiting
                        is_speaking = False
                        last_activity_t = time.time()
                        logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_done_received")
                        # Post-TTS flush window: discard 700ms of mic frames to prevent
                        # TTS echo / room-noise false VAD triggers on the next listen pass.
                        _post_tts_flush_until = time.time() + 0.7
                        speech_started = False
                        silence_count  = 0
                        pcm_buffer.clear()
                        logger.info("[POST_TTS_MIC_FLUSH_START] flush_window_ms=700 resetting VAD state")
                        logger.info("[VAD_STATE_RESET_AFTER_TTS] speech_started=False silence_count=0 buffer_cleared=True")
                        await _send(websocket, {"type": "listening"})
                        logger.info("[VOICE_LISTENING_READY] state=listening reason=tts_done_received")
                except Exception:
                    pass
                continue

            # Binary PCM frames
            raw = data.get("bytes")
            if not raw or len(raw) != FRAME_BYTES:
                continue

            _audio_chunks_received += 1
            _last_audio_ts          = time.time()
            if _audio_chunks_received == 1:
                logger.info("[PCM_FIRST_CHUNK_RECEIVED] first PCM frame arrived bytes=%d ws_state=%s",
                            len(raw), websocket.client_state)
                logger.info("[VAD_THRESHOLD] silence_rms=%.4f silence_frames=%d min_speech_frames=%d",
                            _SILENCE_RMS, _SILENCE_FRAMES, _MIN_SPEECH_FRAMES)
            if _audio_chunks_received % 50 == 0:
                logger.info("[MIC_CHUNKS_RECEIVED] count=%d", _audio_chunks_received)
            try:
                from api.routers.debug import update_audio_state as _upd_audio
                _upd_audio(
                    mic_active=True,
                    audio_chunks_received=_audio_chunks_received,
                    last_audio_timestamp=_last_audio_ts,
                    voice_ws_connected=True,
                )
            except Exception:
                pass

            pcm = np.frombuffer(raw, dtype=np.float32).copy()
            rms = float(np.sqrt(np.mean(pcm ** 2)))
            _last_pcm_rms = rms

            if is_speaking:
                if _audio_chunks_received % 50 == 0:
                    logger.info("[VAD_IGNORED_IS_SPEAKING] chunk=%d rms=%.5f", _audio_chunks_received, rms)
                continue  # drop all mic frames during TTS — no interrupt detection

            # Post-TTS flush window: discard frames for 700ms after TTS ends to prevent
            # TTS echo or room noise triggering a false VAD event on the first listen pass.
            _now = time.time()
            if _now < _post_tts_flush_until:
                if _audio_chunks_received % 10 == 0:
                    logger.info("[VAD_IGNORED_POST_TTS_FLUSH] chunk=%d rms=%.5f flush_remaining_ms=%.0f",
                                _audio_chunks_received, rms, (_post_tts_flush_until - _now) * 1000)
                continue
            elif _post_tts_flush_until > 0.0 and _now >= _post_tts_flush_until:
                # Flush window just expired on this chunk — log once, then clear the gate
                logger.info("[POST_TTS_MIC_FLUSH_END] chunk=%d — VAD re-armed, listening resumes")
                logger.info("[LISTENING_REARMED_AFTER_TTS] chunk=%d rms=%.5f", _audio_chunks_received, rms)
                _post_tts_flush_until = 0.0

            # Periodic RMS diagnostic (every 25 frames ≈ 2s)
            if _audio_chunks_received % 25 == 0:
                logger.info("[VAD_RMS] chunk=%d rms=%.5f threshold=%.4f speech_started=%s",
                            _audio_chunks_received, rms, _SILENCE_RMS, speech_started)

            # ── Demo mode: skip all VAD processing during showcase ────────────
            if demo_mode:
                if _audio_chunks_received % 50 == 0:
                    logger.info("[VAD_DEMO_MODE_SKIP] chunk=%d demo_mode=True — ignoring mic input", _audio_chunks_received)
                continue

            # Normal VAD accumulation
            if rms > _SILENCE_RMS:
                if not speech_started:
                    logger.info("[VAD_SPEECH_START] rms=%.5f threshold=%.4f chunk=%d",
                                rms, _SILENCE_RMS, _audio_chunks_received)
                    _vad_speech_t = time.time()
                speech_started  = True
                silence_count   = 0
                last_activity_t = time.time()  # reset session idle timer on speech
                pcm_buffer.append(pcm)
                if len(pcm_buffer) == _MIN_SPEECH_FRAMES:
                    await _send(websocket, {"type": "transcript", "text": "…", "final": False})
            elif speech_started:
                pcm_buffer.append(pcm)
                silence_count += 1
                if silence_count >= _SILENCE_FRAMES:
                    _min_frames = _MIN_SPEECH_FRAMES
                    if _flight_session_active():
                        _min_frames = _SHORT_MIN_SPEECH_FRAMES
                        logger.info("[FLIGHT_SHORT_COMMAND_MODE] min_frames=%d buffer_len=%d",
                                    _min_frames, len(pcm_buffer))
                    if len(pcm_buffer) >= _min_frames:
                        frames = list(pcm_buffer)
                        _vad_ms = (time.time() - _vad_speech_t) * 1000 if '_vad_speech_t' in dir() else 0.0
                        logger.info("[VAD_MS] ms=%.0f frames=%d", _vad_ms, len(frames))
                        if _min_frames == _SHORT_MIN_SPEECH_FRAMES:
                            logger.info("[FLIGHT_SHORT_COMMAND_SELECTED] frames=%d", len(frames))
                        pcm_buffer.clear()
                        speech_started  = False
                        silence_count   = 0
                        is_speaking     = True
                        interrupt_event.clear()
                        current_turn_id += 1
                        asyncio.create_task(process_utterance(frames, current_turn_id))
                    else:
                        pcm_buffer.clear()
                        speech_started = False
                        silence_count  = 0
            else:
                # rms below threshold, no active speech segment — pure silence
                if _audio_chunks_received % 50 == 0:
                    logger.info("[VAD_IGNORED_LOW_RMS] chunk=%d rms=%.5f threshold=%.4f",
                                _audio_chunks_received, rms, _SILENCE_RMS)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[WS/session] unexpected error: %s", exc)
    finally:
        try:
            _narration_task.cancel()
        except Exception:
            pass
        import time as _t_diag
        _session_age_s = round(_t_diag.time() - (last_activity_t or 0), 1)
        logger.info(
            "[SESSION_DESTROY_DIAGNOSTIC] "
            "session_active=False "
            "voice_connected=%s "
            "mic_active=%s "
            "chunks_received=%d "
            "speech_started=%s "
            "is_speaking=%s "
            "pcm_buffer_depth=%d "
            "idle_age_s=%.1f "
            "ws_state=%s",
            websocket.client_state.name if hasattr(websocket.client_state, "name") else str(websocket.client_state),
            _audio_chunks_received > 0,
            _audio_chunks_received,
            speech_started,
            is_speaking,
            len(pcm_buffer),
            _session_age_s,
            str(websocket.client_state),
        )
        logger.info("[VOICE_STATE_CHANGE] from=SESSION_ACTIVE to=SESSION_ENDING")
        try:
            from voice.wake_word_service import wake_word_service as _wws_cleanup
            _wws_cleanup.set_session_active(False)
            _wws_cleanup.reset_cooldown()
            logger.info("[WAKE_LISTENING_RESUMED]")
        except Exception:
            pass
        logger.info("[VOICE_SESSION_CLOSED]")
        logger.info("[VOICE_STATE_CHANGE] from=SESSION_ENDING to=IDLE_WAKE_LISTENING")
        try:
            from api.routers.debug import update_session_state as _upd_end
            _upd_end(voice_connected=False, current_state="idle", session_start_ts=None)
        except Exception:
            pass
        try:
            from api.routers.debug import update_audio_state as _upd_audio_end
            _upd_audio_end(voice_ws_connected=False, frontend_connected=False, mic_active=False)
        except Exception:
            pass
        logger.info("[SESSION_DESTROY] reason=connection_closed chunks_received=%d", _audio_chunks_received)
        logger.info("[SESSION_WS_DISCONNECT] code=closed reason=connection_ended")
