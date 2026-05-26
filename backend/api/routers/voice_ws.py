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
import re
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

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
    """Send JSON; returns False if the connection is already closed."""
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


# ── Wake Word WebSocket ───────────────────────────────────────────────────────

@router.websocket("/ws/wake")
async def ws_wake(websocket: WebSocket) -> None:
    await websocket.accept()

    try:
        from voice.wake_word_service import wake_word_service as _wws
    except Exception as exc:
        await _send(websocket, {"type": "error", "message": f"WakeWordService unavailable: {exc}"})
        await websocket.close()
        return

    # Wait up to 5s for OWW models to finish loading
    for _ in range(50):
        if _wws.oww_ready:
            break
        await asyncio.sleep(0.1)

    if not _wws.oww_ready:
        await _send(websocket, {"type": "error", "message": "Wake word models not loaded"})
        await websocket.close()
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
                        # If Whisper is still loading (startup warmup), skip verification
                        # and fail open — don't block or double-load the model.
                        if not _model_ready.is_set():
                            logger.info("[WS/wake] Whisper not ready yet — skipping verification, failing open")
                            matched, transcript = True, ""
                        else:
                            matched, transcript = await loop.run_in_executor(
                                None, verify_wake_phrase, clip
                            )
                    except Exception as exc:
                        logger.warning("[WS/wake] Whisper verify error: %s — failing open", exc)
                        matched, transcript = True, ""

                    if not matched:
                        logger.info(
                            "[WS/wake] WAKE_REJECTED_WHISPER model=%s conf=%.3f transcript=%r",
                            model_name, confidence, transcript[:60],
                        )
                        continue

                    if not await _send(websocket, {
                        "type":       "wake",
                        "model":      model_name,
                        "confidence": round(confidence, 4),
                        "ts":         int(time.time() * 1000),
                    }):
                        break
                    logger.info("[WS/wake] WAKE model=%s conf=%.3f transcript=%r",
                                model_name, confidence, transcript[:60])
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
_MIN_SPEECH_FRAMES = 5       # < 400ms → too short, discard
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
    """Synthesize one TTS chunk via Kokoro; retry once on None or exception."""
    for attempt in range(2):
        try:
            from api.routers.voice import _kokoro_to_wav
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

    voice          = "nova"
    speed          = 1.0
    preferred_name = ""  # set via config frame; used in greeting + responses

    # Config frame (first message — wait up to 5s)
    try:
        first = await asyncio.wait_for(websocket.receive(), timeout=5.0)
        if first.get("text"):
            cfg = json.loads(first["text"])
            if cfg.get("type") == "config":
                voice          = cfg.get("voice", voice)
                speed          = float(cfg.get("speed", speed))
                preferred_name = (cfg.get("preferred_name") or "").strip()
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        pass

    logger.info("[SESSION_STARTED] voice=%s speed=%.1f name=%r", voice, speed, preferred_name)

    # Block wake word for the duration of this session
    try:
        from voice.wake_word_service import wake_word_service as _wws
        _wws.set_session_active(True)
    except Exception:
        pass


    # ── Session state ─────────────────────────────────────────────────────────
    pcm_buffer: list[np.ndarray] = []
    silence_count       = 0
    speech_started      = False
    is_speaking         = False      # True while TTS is streaming
    last_activity_t     = time.time()
    interrupt_event     = asyncio.Event()
    last_response_text: str = ""     # for CLARIFY repetition
    current_turn_id     = 0          # monotonic counter; stale tasks self-abort when this advances

    import uuid as _uuid
    _session_id = str(_uuid.uuid4())  # stable ID for context_resolver within this WS session

    from brain.memory_manager import new_session_memory
    memory = new_session_memory()

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
                logger.info("[SESSION_ENDED] inactivity timeout after %.1fs", idle_s)
                try:
                    await websocket.close(1000)
                except Exception:
                    pass
                break

    asyncio.create_task(_timeout_watcher())

    # ── TTS helper: sequential synthesis for short (tool/memory) responses ────

    async def _tts_sequential(text: str) -> bool:
        """Synthesize `text` chunk-by-chunk and stream to client. Interruption disabled."""
        logger.info("[TTS_STARTED] chars=%d", len(text))
        chunks = _split_for_tts(text)
        n = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            wav = await _synthesize_chunk(chunk, voice, speed)
            if wav:
                sent = await _send(websocket, {
                    "type":  "audio",
                    "data":  base64.b64encode(wav).decode(),
                    "chunk": i,
                    "total": n,
                    "final": (i == n),
                    "text":  chunk,
                })
                if not sent:
                    return False
            if websocket.client_state != WebSocketState.CONNECTED:
                return False
        return False

    # ── Tool execution helper ─────────────────────────────────────────────────

    async def _run_tool(tool_name: str, tool_params: dict) -> str:
        from api.tools import registry as _registry
        from api.config import settings as _cfg
        try:
            from api.services.window_context import window_context as _wctx
            _aw = _wctx.get_active_window()
        except Exception:
            _aw = None
        _ctx = {"openai_key": _cfg.openai_api_key, "active_window": _aw}
        result = await asyncio.to_thread(_registry.execute, tool_name, tool_params, _ctx)
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
            _ms.set_last_action(tool_name, tool_params, result.text)
        except Exception:
            pass
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

        if my_turn != current_turn_id:
            logger.info("[STALE_RESPONSE_DROPPED] turn=%d stale on entry (current=%d) — discarding",
                        my_turn, current_turn_id)
            is_speaking = False
            return

        last_activity_t = time.time()
        audio = np.concatenate(frames).astype(np.float32)

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

        # STT — fast mode: beam_size=1, English only, no internal VAD
        _stt_t0 = time.time()
        try:
            from voice.whisper_service import transcribe_audio
            result = await asyncio.to_thread(transcribe_audio, audio, fast=True)
            transcript = result.get("text", "").strip()
            _stt_ms = (time.time() - _stt_t0) * 1000
            logger.info("[VOICE_SESSION_LATENCY] stage=stt ms=%.0f", _stt_ms)
        except Exception as exc:
            logger.warning("[WS/session] STT error: %s", exc)
            await _send(websocket, {"type": "error", "message": "STT failed"})
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

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

        # ── Context resolution — replace vague pronouns before routing ──────
        try:
            from api.services.context_resolver import resolve as _ctx_resolve
            resolved = _ctx_resolve(transcript, _session_id)
            if resolved != transcript:
                logger.info("[CTX_RESOLVED] %r → %r", transcript[:60], resolved[:60])
                transcript = resolved
        except Exception as _exc:
            logger.debug("[CTX_RESOLVE] skipped: %s", _exc)

        await _send(websocket, {"type": "transcript", "text": transcript, "final": True})
        logger.info("[WS/session] transcript: %r", transcript)
        memory.add_user(transcript)
        logger.info("[VOICE_TRACE] stage=stt transcript=%r", transcript[:80])

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
                    logger.info("[TTS_STOPPED] interrupted=%s", _interrupted)
                    memory.add_assistant(_clock_response, tool_name=_clock.tool_name)
                    last_response_text  = _clock_response
                    is_speaking         = False
                    last_activity_t     = time.time()
                    if not _interrupted:
                        await _send(websocket, {"type": "done"})
                        await _send(websocket, {"type": "listening"})
                        logger.info("[SESSION_TRANSITION] → listening (tts_done clock_route)")
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
                logger.info("[TTS_STOPPED] interrupted=%s", _interrupted)
                memory.add_assistant(_sr, tool_name="get_live_system_metrics")
                last_response_text = _sr
                is_speaking        = False
                last_activity_t    = time.time()
                if not _interrupted:
                    await _send(websocket, {"type": "done"})
                    await _send(websocket, {"type": "listening"})
                    logger.info("[SESSION_TRANSITION] → listening (tts_done metrics_route)")
                return
        except Exception as _se:
            logger.debug("[SYSTEM_METRICS_VOICE] skipped: %s", _se)

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

                # Synthesize with emotion speed (bypasses closure `speed`)
                _emo_chunks = _split_for_tts(_tts_r.text)
                for _i, _ec in enumerate(_emo_chunks, 1):
                    _wav = await _synthesize_chunk(_ec, voice, _tts_r.speed_hint)
                    if _wav:
                        await _send(websocket, {
                            "type":  "audio",
                            "data":  base64.b64encode(_wav).decode(),
                            "chunk": _i,
                            "total": len(_emo_chunks),
                            "final": (_i == len(_emo_chunks)),
                            "text":  _ec,
                        })

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
            is_speaking     = False
            last_activity_t = time.time()
            await _send(websocket, {"type": "done"})
            await _send(websocket, {"type": "listening"})
            return

        logger.info("[VOICE_TRACE] stage=intent_router — passing to orchestrator")

        # ── Orchestrator decision ─────────────────────────────────────────────
        from brain.orchestrator import orchestrator as _orch, ActionType
        logger.info("[TURN_START] turn=%d routing transcript=%r", my_turn, transcript[:60])
        decision = await _orch.decide(transcript, memory.history_for_llm())
        logger.info("[ORCHESTRATOR] action=%s reason=%s", decision.action.name, decision.reason)
        logger.info("[VOICE_TRACE] stage=tool_route action=%s tool=%s",
                    decision.action.name, decision.tool_name)

        if my_turn != current_turn_id:
            logger.info("[STALE_RESPONSE_DROPPED] turn=%d dropped after orchestrator (current=%d)",
                        my_turn, current_turn_id)
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        response_text: str = ""
        interrupted:   bool = False

        # ── STOP ──────────────────────────────────────────────────────────────
        if decision.action == ActionType.STOP:
            response_text = "Goodbye! Have a great day."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            await _tts_sequential(response_text)
            await _send(websocket, {"type": "done"})
            try:
                await websocket.close(1000)
            except Exception:
                pass
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
            interrupted = await _tts_sequential(response_text)

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
                    response_text = await _run_tool(tool, decision.tool_params)
                else:
                    response_text = "I couldn't resolve what you're referring to."
            except Exception as exc:
                logger.warning("[WS/session] memory_ref exec error: %s", exc)
                response_text = "I had trouble with that reference."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_sequential(response_text)

        # ── TOOL — matched tool execution ─────────────────────────────────────
        elif decision.action == ActionType.TOOL:
            try:
                response_text = await _run_tool(decision.tool_name, decision.tool_params)
            except Exception as exc:
                logger.warning("[WS/session] tool exec error: %s", exc)
                response_text = "That action ran into an issue."
            # Some tools signal the frontend to trigger a UI sequence.
            # Send the action frame BEFORE TTS so the frontend can start
            # its visual sequence in parallel with the audio playing.
            _FE_ACTIONS: dict[str, str] = {"takeover_mode": "TAKEOVER_START"}
            if decision.tool_name in _FE_ACTIONS:
                await _send(websocket, {
                    "type":   "frontend_action",
                    "action": _FE_ACTIONS[decision.tool_name],
                })
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_sequential(response_text)

        # ── MULTI_STEP — compound command via planner ─────────────────────────
        elif decision.action == ActionType.MULTI_STEP:
            from brain.planner import planner as _planner
            from brain.orchestrator import orchestrator as _o2, ActionType as _AT

            async def _step_fn(step_text: str, hist: list[dict]) -> str:
                step_dec = await _o2.decide(step_text, hist)
                if step_dec.action == _AT.TOOL:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params)
                elif step_dec.action == _AT.MEMORY_REF and step_dec.tool_name:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params)
                else:
                    from api.services.response_pipeline import quick_response
                    return await quick_response(step_text, hist)

            plan = _planner.build(transcript)
            if plan:
                response_text = await _planner.execute(plan, _step_fn, memory.history_for_llm())
            else:
                response_text = "I had trouble parsing those steps."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_sequential(response_text)

        # ── LLM — overlapped streaming generation + TTS ───────────────────────
        else:
            response_text, interrupted = await _run_llm_stream(
                transcript, memory.history_for_llm()
            )

        # ── Post-dispatch bookkeeping ─────────────────────────────────────────
        if response_text:
            last_response_text = response_text
        memory.add_assistant(response_text, tool_name=decision.tool_name)

        # Emit emotion state so frontend orb stays in sync after tool/LLM turns
        await _send(websocket, {
            "type":    "emotion_state",
            "mood":    _current_mood,
            "emotion": getattr(_emo, "emotion", "calmness") if _emo else "calmness",
            "energy":  getattr(_emo, "energy",  0.5)        if _emo else 0.5,
        })
        logger.info("[UI_EMOTION_EVENT] state=%s", _current_mood)
        logger.info("[VOICE_TRACE] stage=audio_stream done response=%r", (response_text or "")[:60])

        logger.info("[TTS_STOPPED] interrupted=%s", interrupted)
        is_speaking     = False
        last_activity_t = time.time()

        if not interrupted:
            await _send(websocket, {"type": "done"})
            await _send(websocket, {"type": "listening"})
        # If interrupted: VAD in the main loop already detected speech; it re-arms naturally

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
                        if speech_started and len(pcm_buffer) >= _MIN_SPEECH_FRAMES:
                            frames = list(pcm_buffer)
                            pcm_buffer.clear()
                            speech_started  = False
                            silence_count   = 0
                            is_speaking     = True
                            interrupt_event.clear()
                            current_turn_id += 1
                            asyncio.create_task(process_utterance(frames, current_turn_id))
                    elif t == "tts_done":
                        is_speaking = False
                        last_activity_t = time.time()  # reset idle timer so session stays alive after response
                        await _send(websocket, {"type": "listening"})
                except Exception:
                    pass
                continue

            # Binary PCM frames
            raw = data.get("bytes")
            if not raw or len(raw) != FRAME_BYTES:
                continue

            pcm = np.frombuffer(raw, dtype=np.float32).copy()
            rms = float(np.sqrt(np.mean(pcm ** 2)))

            if is_speaking:
                continue  # drop all mic frames during TTS — no interrupt detection

            # Normal VAD accumulation
            if rms > _SILENCE_RMS:
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
                    if len(pcm_buffer) >= _MIN_SPEECH_FRAMES:
                        frames = list(pcm_buffer)
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

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[WS/session] unexpected error: %s", exc)
    finally:
        try:
            from voice.wake_word_service import wake_word_service as _wws_cleanup
            _wws_cleanup.set_session_active(False)
            _wws_cleanup.reset_cooldown()
        except Exception:
            pass
        logger.info("[SESSION_ENDED] connection closed")
