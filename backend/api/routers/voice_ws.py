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
SESSION_TIMEOUT  = 8.0        # seconds of silence before session auto-ends

# TTS chunking: split response at sentence boundaries, max N chars per chunk
_TTS_MAX_CHARS   = 80


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
            wav = await asyncio.to_thread(_kokoro_to_wav, text, voice, speed)
            if wav:
                return wav
            if attempt == 0:
                logger.warning("[WS/session] Kokoro returned None, retrying...")
                await asyncio.sleep(0.15)
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

    voice = "nova"
    speed = 1.0

    # Config frame (first message — wait up to 5s)
    try:
        first = await asyncio.wait_for(websocket.receive(), timeout=5.0)
        if first.get("text"):
            cfg = json.loads(first["text"])
            if cfg.get("type") == "config":
                voice = cfg.get("voice", voice)
                speed = float(cfg.get("speed", speed))
    except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
        pass

    logger.info("[SESSION_STARTED] voice=%s speed=%.1f", voice, speed)

    # Block wake word for the duration of this session
    try:
        from voice.wake_word_service import wake_word_service as _wws
        _wws.set_session_active(True)
    except Exception:
        pass

    # ── Instant "Yes?" ACK ────────────────────────────────────────────────────
    ack_path = Path("/tmp/xyron-ack/on_it.wav")
    ack_has_audio = False
    if ack_path.exists():
        try:
            ack_b64 = base64.b64encode(ack_path.read_bytes()).decode()
            await _send(websocket, {"type": "ack", "text": "Yes?", "audio": ack_b64})
            ack_has_audio = True
        except Exception:
            pass
    if not ack_has_audio:
        await _send(websocket, {"type": "ack", "text": "Yes?"})

    # Post-TTS deaf window: give the client time to finish playing the ack audio
    # before we start recording. Without this, machines with no hardware echo
    # cancellation (WSL2, USB mics) pick up the ack TTS in the mic and send it
    # to Whisper, causing hallucinated transcriptions.
    if ack_has_audio:
        await asyncio.sleep(1.2)

    await _send(websocket, {"type": "listening"})

    # ── Session state ─────────────────────────────────────────────────────────
    pcm_buffer: list[np.ndarray] = []
    silence_count       = 0
    speech_started      = False
    is_speaking         = False      # True while TTS is streaming
    last_activity_t     = time.time()
    interrupt_event     = asyncio.Event()
    last_response_text: str = ""     # for CLARIFY repetition

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
        """Synthesize `text` chunk-by-chunk with interrupt check. Returns True if interrupted."""
        interrupt_event.clear()
        logger.info("[TTS_STARTED] chars=%d", len(text))
        chunks = _split_for_tts(text)
        n = len(chunks)
        for i, chunk in enumerate(chunks, 1):
            if interrupt_event.is_set():
                logger.info("[TTS_INTERRUPTED] at chunk %d/%d", i, n)
                return True
            wav = await _synthesize_chunk(chunk, voice, speed)
            if interrupt_event.is_set():
                logger.info("[TTS_INTERRUPTED] post-synth chunk %d/%d", i, n)
                return True
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
        interrupt_event.clear()
        logger.info("[TTS_STARTED] streaming LLM+TTS pipeline")
        full_text  = ""
        interrupted = False
        try:
            async for sentence, wav, chunk_idx, is_final in stream_response_with_tts(
                transcript, history, voice=voice, speed=speed
            ):
                if interrupt_event.is_set():
                    interrupted = True
                    logger.info("[TTS_INTERRUPTED] LLM stream at chunk %d", chunk_idx)
                    break
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

    async def process_utterance(frames: list[np.ndarray]) -> None:
        nonlocal is_speaking, last_activity_t, last_response_text

        last_activity_t = time.time()
        audio = np.concatenate(frames).astype(np.float32)

        # STT — fast mode: beam_size=1, English only, no internal VAD
        try:
            from voice.whisper_service import transcribe_audio
            result = await asyncio.to_thread(transcribe_audio, audio, fast=True)
            transcript = result.get("text", "").strip()
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

        await _send(websocket, {"type": "transcript", "text": transcript, "final": True})
        logger.info("[WS/session] transcript: %r", transcript)
        memory.add_user(transcript)

        # ── Orchestrator decision ─────────────────────────────────────────────
        from brain.orchestrator import orchestrator as _orch, ActionType
        decision = await _orch.decide(transcript, memory.history_for_llm())
        logger.info("[ORCHESTRATOR] action=%s reason=%s", decision.action.name, decision.reason)

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

        logger.info("[TTS_STOPPED] interrupted=%s", interrupted)
        is_speaking     = False
        last_activity_t = time.time()

        if not interrupted:
            await _send(websocket, {"type": "done"})
            await _send(websocket, {"type": "listening"})
        # If interrupted: VAD in the main loop already detected speech; it re-arms naturally

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
                            asyncio.create_task(process_utterance(frames))
                    elif t == "tts_done":
                        is_speaking = False
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
                # Interrupt detection: significant user speech during TTS
                if rms > _INTERRUPT_RMS:
                    interrupt_event.set()
                    logger.info("[TTS_INTERRUPTED] user speech rms=%.4f", rms)
                    await _send(websocket, {"type": "listening"})
                continue  # always skip VAD accumulation while TTS plays

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
                        asyncio.create_task(process_utterance(frames))
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
