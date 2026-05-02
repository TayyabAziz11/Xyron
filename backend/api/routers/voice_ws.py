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

    await _send(websocket, {
        "type":       "ready",
        "models":     _wws.model_names,
        "thresholds": {n: _wws._thresholds.get(n, 0.5) for n in _wws.model_names},
        "cooldown_s": 3.0,
    })
    logger.info("[WS/wake] connected — models: %s", _wws.model_names)

    PING_EVERY = 20.0

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
                triggered, model_name, confidence = _wws.detect_frame(pcm)
                if triggered:
                    if not await _send(websocket, {
                        "type":       "wake",
                        "model":      model_name,
                        "confidence": round(confidence, 4),
                        "ts":         int(time.time() * 1000),
                    }):
                        break
                    logger.info("[WS/wake] WAKE model=%s conf=%.3f", model_name, confidence)
                continue

            text = data.get("text")
            if text:
                try:
                    msg = json.loads(text)
                    if msg.get("type") == "reset_cooldown":
                        _wws.reset_cooldown()
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
_SILENCE_RMS     = 0.008  # RMS below this = silence
_SILENCE_FRAMES  = 9      # 9 × 80ms = 720ms silence → end of speech
_MIN_SPEECH_FRAMES = 5    # < 400ms → too short, discard

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
    """Synthesize one TTS chunk asynchronously."""
    try:
        from api.routers.voice import _kokoro_to_wav
        wav = await asyncio.to_thread(_kokoro_to_wav, text, voice, speed)
        return wav
    except Exception as exc:
        logger.warning("[WS/session] TTS error: %s", exc)
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

    logger.info("[WS/session] started voice=%s speed=%.1f", voice, speed)

    # ── Instant "Yes?" ACK ────────────────────────────────────────────────────
    ack_path = Path("/tmp/xyron-ack/on_it.wav")
    if ack_path.exists():
        try:
            ack_b64 = base64.b64encode(ack_path.read_bytes()).decode()
            await _send(websocket, {"type": "ack", "text": "Yes?", "audio": ack_b64})
        except Exception:
            pass
    else:
        await _send(websocket, {"type": "ack", "text": "Yes?"})

    await _send(websocket, {"type": "listening"})

    # ── State ─────────────────────────────────────────────────────────────────
    pcm_buffer: list[np.ndarray] = []
    silence_count  = 0
    speech_started = False
    is_speaking    = False  # True while TTS chunks are playing

    # ── Utterance processor ───────────────────────────────────────────────────

    async def process_utterance(frames: list[np.ndarray]) -> None:
        nonlocal is_speaking

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
            return

        if not transcript:
            is_speaking = False
            await _send(websocket, {"type": "listening"})
            return

        await _send(websocket, {"type": "transcript", "text": transcript, "final": True})
        logger.info("[WS/session] transcript: %r", transcript)

        # LLM dispatch
        try:
            from api.services.command_service import classify_intent, _dispatch_to_skill
            import uuid as _uuid
            intent     = classify_intent(transcript)
            cid        = str(_uuid.uuid4())[:8]
            raw_result = await asyncio.to_thread(_dispatch_to_skill, transcript, intent, cid)
            if isinstance(raw_result, dict):
                response_text = str(
                    raw_result.get("spoken") or raw_result.get("message") or raw_result
                ).strip()
            else:
                response_text = str(raw_result).strip() if raw_result else "Done."
        except Exception as exc:
            logger.warning("[WS/session] dispatch error: %s", exc)
            response_text = "I ran into an issue with that."

        await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})

        # TTS — stream sentence-by-sentence
        chunks = _split_for_tts(response_text)
        n      = len(chunks)
        for i, chunk_text in enumerate(chunks, 1):
            wav = await _synthesize_chunk(chunk_text, voice, speed)
            if wav:
                sent = await _send(websocket, {
                    "type":  "audio",
                    "data":  base64.b64encode(wav).decode(),
                    "chunk": i,
                    "total": n,
                    "final": (i == n),
                    "text":  chunk_text,
                })
                if not sent:
                    break
            if websocket.client_state != WebSocketState.CONNECTED:
                break

        await _send(websocket, {"type": "done"})
        is_speaking = False

    # ── Main receive loop ─────────────────────────────────────────────────────
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                data = await asyncio.wait_for(websocket.receive(), timeout=30.0)
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
                            speech_started = False
                            silence_count  = 0
                            is_speaking    = True
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
            if is_speaking:
                continue  # ignore mic while TTS is playing

            pcm = np.frombuffer(raw, dtype=np.float32).copy()
            rms = float(np.sqrt(np.mean(pcm ** 2)))

            if rms > _SILENCE_RMS:
                speech_started = True
                silence_count  = 0
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
                        speech_started = False
                        silence_count  = 0
                        is_speaking    = True
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
        logger.info("[WS/session] connection closed")
