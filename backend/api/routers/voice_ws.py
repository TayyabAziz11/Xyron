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

# ── Tier 0d pending-confirmation yes/no matching ──────────────────────────────
# Module-level (not a local inside ws_session) so tests can import the exact
# patterns actually used in production instead of duplicating and risking
# drift. Behavior is unchanged from when these were compiled inline —
# extraction only, no pattern edits beyond adding "send it" for Phase 5
# (voice WhatsApp send confirmations: "Send 'X' to Tayyab?" / "send it").
_CONFIRM_YES_RE = re.compile(
    r'\b(yes|yeah|yep|yup|sure|go ahead|do it|send it|confirm|install it|'
    r'proceed|ok|okay|please|affirmative|absolutely)\b',
    re.IGNORECASE,
)
_CONFIRM_NO_RE = re.compile(
    r'\b(no|nope|cancel|stop|don\'?t|never mind|nevermind|abort|'
    r'reject|skip|forget it|actually no)\b',
    re.IGNORECASE,
)

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

    # Rolling audio buffer for Whisper second-stage verification when OWW fires.
    #
    # UX-refinement finding: verify_wake_phrase()'s own docstring says "pass the
    # last ~2.5s of buffered audio", but this was previously slicing only 15
    # frames (1.2s) — enough for a short trigger like "hey xyron" but not for
    # longer phrases like "wake up xyron", which got truncated to "wake up"
    # (confirmed in logs: WAKE_REJECTED_WHISPER transcript="wake up." — the
    # word "xyron" was never in the clip because the window ended too early).
    # Widened to use the full ~2.5s the function was always documented to want.
    _BUFFER_FRAMES    = 36   # 2.88s — _WHISPER_FRAMES plus post-roll headroom
    _WHISPER_FRAMES   = 32   # last 32 × 80ms = 2.56s sent to Whisper (was 15/1.2s)
    _POST_ROLL_FRAMES = 3    # wait ~240ms after OWW triggers before slicing the
                              # clip, so a trailing syllable still being spoken
                              # at the trigger instant isn't cut off either
    audio_buf: collections.deque[np.ndarray] = collections.deque(maxlen=_BUFFER_FRAMES)
    _pending_wake: Optional[dict] = None   # holds model/confidence during post-roll wait

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

                # ── Post-roll wait: OWW already triggered on a previous frame;
                # keep buffering _POST_ROLL_FRAMES more frames before slicing,
                # so a trailing syllable still being spoken at the trigger
                # instant is captured rather than cut off.
                if _pending_wake is not None:
                    _pending_wake["frames_waited"] += 1
                    if _pending_wake["frames_waited"] < _POST_ROLL_FRAMES:
                        continue

                    model_name = _pending_wake["model"]
                    confidence = _pending_wake["confidence"]
                    _pending_wake = None

                    # ── Second-stage Whisper verification ────────────────────
                    # OWW models produce false positives from background noise.
                    # Whisper confirms a wake keyword was actually spoken before
                    # we send the wake event to the frontend.
                    clip = np.concatenate(list(audio_buf)[-_WHISPER_FRAMES:])
                    _clip_duration_s = len(clip) / 16000.0
                    _clip_rms = float(np.sqrt(np.mean(clip ** 2)))
                    logger.info(
                        "[WAKE_DIAG] model=%s confidence=%.3f threshold=%.3f "
                        "audio_duration_s=%.2f pre_roll_s=%.2f post_roll_frames=%d speech_rms=%.4f",
                        model_name, confidence, _wws._thresholds.get(model_name, 0.5),
                        _clip_duration_s, _clip_duration_s, _POST_ROLL_FRAMES, _clip_rms,
                    )
                    try:
                        from voice.whisper_service import verify_wake_phrase, _model_ready, stt_executor
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
                                # Previously this rejection was invisible to the
                                # user — OWW fired, nothing happened, no signal
                                # the frontend could show. On a slow-loading
                                # machine (large/cold Whisper model, slow disk)
                                # this silently swallows every "Hey Xyron" during
                                # the warmup window. Tell the frontend so it can
                                # show a "still starting up" indicator instead.
                                await _send(websocket, {
                                    "type":   "wake_not_ready",
                                    "model":  model_name,
                                    "ts":     int(time.time() * 1000),
                                })
                            else:
                                matched, transcript = await loop.run_in_executor(
                                    stt_executor, verify_wake_phrase, clip
                                )
                        else:
                            matched, transcript = await loop.run_in_executor(
                                stt_executor, verify_wake_phrase, clip
                            )
                    except Exception as exc:
                        logger.warning("[WS/wake] Whisper verify error: %s — rejecting wake", exc)
                        matched, transcript = False, ""

                    if not matched:
                        logger.info(
                            "[WS/wake] WAKE_REJECTED_WHISPER model=%s conf=%.3f transcript=%r "
                            "reason=whisper_no_match audio_duration_s=%.2f",
                            model_name, confidence, transcript[:60], _clip_duration_s,
                        )
                        # OWW's own _COOLDOWN_S (2s) debounce starts the instant
                        # detect_frame() fires — i.e. BEFORE this Whisper
                        # double-check even runs. So a genuine "Hey Xyron" that
                        # Whisper simply mis-transcribes still burns the full
                        # 2s cooldown, silently swallowing an immediate correct
                        # repeat too (WAKE_REJECTED_DEBOUNCE, no signal to the
                        # user either) — the exact "have to say it 2-3 times"
                        # pattern. Only a real confirmed wake should start the
                        # cooldown; a rejected verification should let the very
                        # next attempt through immediately.
                        _wws.reset_cooldown()
                        # Tell the frontend so it isn't left with dead air —
                        # previously this branch gave zero signal at all.
                        await _send(websocket, {
                            "type":       "wake_rejected",
                            "model":      model_name,
                            "transcript": transcript[:60],
                            "ts":         int(time.time() * 1000),
                        })
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

                triggered, model_name, confidence = _wws.detect_frame(pcm)
                if triggered:
                    _pending_wake = {"model": model_name, "confidence": confidence, "frames_waited": 0}
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
# Problem 6 fix: this used to be 0.008 while the STT pre-check below (search
# "reliably indicates no real speech") independently used 0.010 — any burst
# in the 0.008-0.010 band passed VAD accumulation (logging VAD_SPEECH_START,
# buffering frames, sending a "…" transcript placeholder to the frontend)
# but was then always discarded as STT_SKIPPED_SILENCE a moment later,
# observed repeatedly in production logs as VAD false starts. Unified to the
# STT check's own documented reliable-speech floor, since a burst VAD would
# accept below that was never going to survive to a transcript anyway.
_SILENCE_RMS       = 0.010   # RMS below this = silence
_SILENCE_FRAMES    = 9       # 9 × 80ms = 720ms silence → end of speech
_MIN_SPEECH_FRAMES = 9       # < 720ms → too short, discard (9 × 80ms frames)
_SHORT_MIN_SPEECH_FRAMES = 3 # 240ms — single-word commands ("cancel") during an active flight session
_INTERRUPT_RMS     = 0.020   # RMS above this during TTS = user interrupt

# ── Audio front-end cleanup (noise suppression + gain normalization) ────────
# Runs AFTER the silence/duration/speech-ratio gates below have already
# confirmed the clip contains real speech — never applied to raw audio that
# might still get discarded as silence/noise, so it can't turn a noise floor
# into a false "speech" pass.
_DENOISE_OVER_SUBTRACT = 1.5   # spectral subtraction strength
_DENOISE_FLOOR         = 0.05  # keep at least this fraction of original magnitude (avoids musical-noise artifacts)
_DENOISE_NPERSEG       = 400   # 25ms window at 16kHz
_DENOISE_NOVERLAP      = 200   # 12.5ms hop
_NORMALIZE_TARGET_PEAK = 0.95  # consistent peak level into Whisper regardless of mic gain/distance
_NORMALIZE_MAX_GAIN    = 10.0  # cap amplification so a near-silent residual noise floor isn't blown up


def _denoise_and_normalize(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """
    Lightweight STT front-end cleanup: FFT spectral-gate noise suppression,
    then peak gain normalization. Pure numpy/scipy — no new model/dependency.
    """
    audio_clean = audio
    try:
        from scipy.signal import istft, stft

        _, _, Zxx = stft(audio, fs=sample_rate, nperseg=_DENOISE_NPERSEG, noverlap=_DENOISE_NOVERLAP)
        mag = np.abs(Zxx)
        phase = np.angle(Zxx)

        # Noise profile: median magnitude of the quietest 20% of frames —
        # robust without needing leading "silence" audio (already trimmed
        # away upstream by the time this runs).
        frame_energy = mag.mean(axis=0)
        n_noise_frames = max(1, int(0.2 * len(frame_energy)))
        noise_frame_idx = np.argsort(frame_energy)[:n_noise_frames]
        noise_profile = np.median(mag[:, noise_frame_idx], axis=1, keepdims=True)

        mag_clean = np.maximum(mag - _DENOISE_OVER_SUBTRACT * noise_profile, _DENOISE_FLOOR * mag)
        Zxx_clean = mag_clean * np.exp(1j * phase)
        _, audio_clean = istft(Zxx_clean, fs=sample_rate, nperseg=_DENOISE_NPERSEG, noverlap=_DENOISE_NOVERLAP)
        audio_clean = audio_clean[:len(audio)].astype(np.float32)
    except Exception as exc:
        logger.debug("[AUDIO_DENOISE_SKIPPED] error=%s", exc)
        audio_clean = audio

    peak = float(np.max(np.abs(audio_clean))) if audio_clean.size else 0.0
    if peak > 1e-6:
        gain = min(_NORMALIZE_TARGET_PEAK / peak, _NORMALIZE_MAX_GAIN)
        audio_clean = (audio_clean * gain).astype(np.float32)

    return audio_clean


# ── Semantic transcript correction (GPT-4o-mini) ─────────────────────────────
# Runs after STT, before the transcript reaches intent matching. Only fires
# when STT confidence itself signals uncertainty — hybrid_stt_router's own
# _SIMPLE_VOCAB short-circuit already covers well-known commands, so this
# targets the residual cases: a plausible-sounding but wrong word/entity name
# that confidence-only heuristics can't catch. Fails open (returns the
# original transcript unchanged) on any timeout/error or when OpenAI is
# unavailable/rate-limited — never blocks or breaks a turn.
_CORRECTION_CONF_THRESHOLD = -0.35  # only correct transcripts STT itself is unsure about
_CORRECTION_TIMEOUT_S      = 2.0


def _needs_semantic_correction(transcript: str, confidence: float, stt_language: str = "") -> bool:
    from voice.hybrid_stt_router import _SIMPLE_VOCAB
    norm = transcript.lower().strip().rstrip(".!?,")
    if norm in _SIMPLE_VOCAB:
        return False
    # ── Hard rule: if STT itself says this is NOT English, never correct ──
    # The local LLM (qwen2.5:1.5b) cannot handle Urdu/Hindi/mixed input
    # and TRANSLATES it to English garbage instead of fixing phonetic
    # errors (e.g. "C drive ko kholo" → "C drive open"). Whisper's own
    # language ID is reliable — if it says "hi" or "ur" or "mixed", the
    # audio is not English and correction will only translate, not fix.
    _stt_lang_lower = (stt_language or "").lower().strip()
    if _stt_lang_lower and _stt_lang_lower not in ("en", "english", ""):
        return False
    # Skip correction for Urdu/Roman Urdu — the local LLM (qwen2.5:1.5b)
    # cannot handle Urdu and TRANSLATES it to English garbage instead of
    # correcting phonetic errors (e.g. "Kholo, khe re, kam karo, settings
    # kholo" → "Open, close, do some work, settings open"). Until a model
    # that properly understands Roman Urdu is available, correction does
    # more harm than good for non-English transcripts.
    _urdu_chars = sum(1 for c in transcript if 0x0600 <= ord(c) <= 0x06FF)
    if _urdu_chars > 2:
        return False  # Urdu script — never send to Ollama
    # Quick Roman Urdu check — if 20%+ of words are common Urdu vocabulary,
    # skip correction to prevent translation. We include ALL vocabulary
    # sets (verbs, pronouns, commands, questions, common words like
    # "nahi"/"ko"/"se", and connectors like "aur"/"ya") because even a
    # few Urdu particles in a mixed sentence mean the qwen model will
    # translate instead of correct. Also strip trailing punctuation from
    # each word so "nahi," matches "nahi".
    from cognition.language_detector import (
        _ROMAN_VERBS, _ROMAN_PRONOUNS, _ROMAN_COMMANDS, _ROMAN_QUESTION,
        _ROMAN_COMMON, _ROMAN_CONNECTORS,
    )
    _words = norm.split()
    if _words:
        _urdu_vocab = (
            _ROMAN_VERBS | _ROMAN_PRONOUNS | _ROMAN_COMMANDS | _ROMAN_QUESTION
            | _ROMAN_COMMON | _ROMAN_CONNECTORS
        )
        _hits = sum(1 for w in _words if w.rstrip(",.!?;'") in _urdu_vocab)
        if _hits / len(_words) >= 0.20:
            return False
    return confidence < _CORRECTION_CONF_THRESHOLD


async def _correct_transcript_semantic(transcript: str, confidence: float, stt_language: str = "") -> str:
    """
    Best-effort GPT-4o-mini pass that fixes likely Whisper misrecognitions
    using recent app/folder/file context, before intent matching runs.
    Returns the original transcript unchanged on any failure/timeout or when
    correction isn't warranted.
    """
    if not transcript or not _needs_semantic_correction(transcript, confidence, stt_language):
        return transcript

    try:
        from api.services.openai_client import openai_client
        if not openai_client.available:
            return transcript

        from api.services.context_stack import context_stack as _cstack
        _recent = [e.display for e in _cstack.recent(5) if e.display]
        _context_line = f"Recently used: {', '.join(_recent)}." if _recent else ""

        _prompt = (
            "You are correcting a speech-to-text transcript of a spoken voice-assistant "
            "command for a Windows desktop assistant named Xyron. The transcript may "
            "contain phonetic misrecognitions. Fix ONLY clear transcription errors — "
            "keep the same intent and wording otherwise. If the transcript already looks "
            "correct, return it unchanged. Reply with ONLY the corrected transcript, no "
            "quotes, no explanation.\n"
            "The user may be speaking English, Roman Urdu, Urdu script, Hindi/Devanagari "
            "transliteration of Urdu, or a mix of these — this is normal and NOT an error "
            "to fix. Correct only clear phonetic mis-hearings (e.g. wrong word for a "
            "similar-sounding one) in whichever language/script the transcript is already "
            "in. NEVER translate, paraphrase, or rewrite the transcript into a different "
            "language than what's given — that is not a transcription correction, even for "
            "a single word. Do not swap a correctly-heard Roman Urdu/Urdu word for its "
            "English meaning (e.g. 'kholo' must stay 'kholo', never become 'open'; 'karo' "
            "must stay 'karo', never become 'do') — that is translation, not correction, "
            "and is forbidden even when the rest of the transcript already looks correct. "
            "For example, 'Urdu mein baat karo' or 'Urdu में बात करो' must stay exactly "
            "that meaning and wording, never become an unrelated English sentence, and "
            "'Chrome kholo' is ALREADY correct and must be returned completely unchanged, "
            "not as 'Chrome open'.\n"
            f"{_context_line}\n"
            f"Transcript: {transcript}"
        )
        _t0 = time.time()
        _corrected = await asyncio.wait_for(
            asyncio.to_thread(
                openai_client.generate,
                [{"role": "user", "content": _prompt}],
                "gpt-4o-mini", 60, 0.0,
            ),
            timeout=_CORRECTION_TIMEOUT_S,
        )
        _ms = (time.time() - _t0) * 1000
        _corrected = (_corrected or "").strip().strip('"')
        if _corrected and _corrected.lower() != transcript.lower():
            logger.info("[TRANSCRIPT_SEMANTIC_CORRECTED] ms=%.0f %r → %r",
                        _ms, transcript[:80], _corrected[:80])
            return _corrected
        return transcript
    except asyncio.TimeoutError:
        logger.debug("[TRANSCRIPT_SEMANTIC_CORRECTION] timed out (%ss) — using original",
                     _CORRECTION_TIMEOUT_S)
        return transcript
    except Exception as exc:
        logger.debug("[TRANSCRIPT_SEMANTIC_CORRECTION] failed: %s — using original", exc)
        return transcript


# ── Greeting phrase pool (latency/UX polish pass) ───────────────────────────
# "I'm listening." is a frontend STATE, not a greeting — it read as robotic.
# Module-level (process-lifetime, not per-session) so back-to-back
# wake/manual activations across different sessions don't repeat the same
# phrase, mirroring the existing _pick_ack_variant anti-repetition pattern
# used for tool acks below. The emergency Layer-3 fallback (used only when
# live synthesis times out — see the greeting block) intentionally stays
# "I'm listening." since it's pre-warmed and guaranteed cache-available;
# it is never the happy-path greeting.
_GREETING_POOL = {
    "morning":   ["Good morning.", "Morning.", "Hey.", "Ready."],
    "afternoon": ["Good afternoon.", "Hey.", "Yes?", "Ready."],
    "evening":   ["Good evening.", "Evening.", "Hey.", "Ready."],
}
_last_greeting_text: dict[str, Optional[str]] = {"text": None}


def _pick_greeting_text(tod: str, name: str) -> str:
    if name and name != "boss":
        # A personalized greeting is distinctive enough on its own — still
        # rotate time-of-day phrasing, no separate anti-repeat pool needed.
        return f"Good {tod}, {name}."
    pool = _GREETING_POOL.get(tod, _GREETING_POOL["afternoon"])
    last = _last_greeting_text["text"]
    choices = [p for p in pool if p != last] or pool
    text = random.choice(choices)
    _last_greeting_text["text"] = text
    return text


# Session constants
SESSION_TIMEOUT  = 45.0       # seconds of silence before session auto-ends

# TTS chunking: split response at sentence boundaries, max N chars per chunk.
# Raised 80 → 160 (perf fix): short two-sentence replies (~84 chars) used to
# split into 2 chunks = 2 Kokoro synths + 2 RVC passes (~2.7s measured);
# one synthesis pass for replies this size is strictly faster and the
# streaming benefit of chunking only matters for genuinely long responses.
_TTS_MAX_CHARS   = 160

# Punctuation the Kokoro phonemizer mishandles (em-dash triggers
# "words count mismatch" warnings and awkward pauses; curly quotes leak
# into phonemes). Normalize to speech-friendly equivalents once, here, so
# every TTS path (English Kokoro + multilingual) benefits.
_SPEECH_PUNCTUATION_MAP = str.maketrans({
    "\u2014": ",",   # em dash
    "\u2013": ",",   # en dash
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote / apostrophe
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2026": "...", # ellipsis
})


def _normalize_speech_punctuation(text: str) -> str:
    """Convert smart punctuation to TTS-friendly equivalents."""
    # " — " / " – " should read as a comma join, not " , "
    text = re.sub(r'\s*[\u2014\u2013]\s*', ', ', text)
    return text.translate(_SPEECH_PUNCTUATION_MAP)


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
    text = _normalize_speech_punctuation(text)
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


async def _apply_rvc(wav: bytes) -> bytes:
    """Post-process Kokoro output through RVC for mood-linked pitch/EQ coloring.

    No-op (returns wav unchanged) whenever RVC is disabled, unavailable, or
    anything goes wrong — rvc_engine.convert() already fails open internally,
    this wrapper just adds the async offload (lightweight tier does librosa
    pitch-shift + spectral EQ, which is sync/CPU-bound and must not run on
    the event loop directly — see [EVENT_LOOP_BLOCKER] warnings elsewhere in
    this file) plus a second safety net around the mood lookup itself.
    """
    try:
        from voice.rvc_engine import rvc_engine as _rvc
        if not _rvc.is_available():
            return wav
        from cognition.cognitive_state import cognitive_state as _cs
        mood = getattr(_cs, "mood_state", "CALM") or "CALM"
        preset = _rvc.mood_to_preset(mood)
        return await asyncio.to_thread(_rvc.convert, wav, preset, mood)
    except Exception:
        return wav


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
            # tts_cache is keyed by (voice, text) — a cache built/populated
            # under a different voice simply misses here and falls through
            # to fresh Kokoro synthesis inside synthesize_or_cached(), so
            # this can never return a different voice's audio.
            from api.services.tts_cache_service import tts_cache as _tcc_chunk
            from api.routers.voice import _kokoro_executor as _kko_exec
            wav = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _kko_exec, _tcc_chunk.synthesize_or_cached, text, voice, speed
                ),
                timeout=25.0,
            )
            if wav:
                return await _apply_rvc(wav)
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
    import uuid as _uuid_sess
    session_instance_id = _uuid_sess.uuid4().hex[:12]
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

    logger.info("[SESSION_CREATE] session_instance_id=%s voice=%s speed=%.1f name=%r lang_hint=%s",
                session_instance_id, voice, speed, preferred_name, _cfg_lang_hint)
    logger.info("[TTS_SESSION_VOICE] voice=%s speed=%.1f", voice, speed)
    logger.info("[SESSION_WS_CONNECT] session_instance_id=%s remote=%s",
                session_instance_id, getattr(websocket, 'client', 'unknown'))

    # GPU priority: defer non-critical background GPU work (semantic index /
    # classifier loads) for the lifetime of this session — cleared in the
    # main loop's finally block below, guaranteed on every exit path.
    try:
        from api.services.gpu_coordinator import voice_priority_begin as _gpu_voice_begin
        _gpu_voice_begin(reason=f"session:{session_instance_id}")
    except Exception:
        pass

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
        "pending_video_candidates":   None,   # set after an ambiguous/search-intent search_youtube result
        "pending_open_after_install": None,   # set after install_store_app_exec succeeds
        "ml_detected_lang":           _cfg_lang_hint or "en",  # pre-seed from config; else "en"
        "ml_detected_lang_conf":      1.0 if (_cfg_lang_hint and _cfg_lang_hint not in ("en",)) else 0.0,
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

    # ── Task 6: Stuck-speaking watchdog ───────────────────────────────────────
    # Recovery net for exactly the failure mode behind the keepalive-timeout
    # incident: speaking=true with no audio ever sent and no progress. The
    # greeting path above already guards itself with a strict timeout and a
    # try/finally, but this is defense-in-depth for any other TTS call site
    # in this session that gets stuck. Gated on "no audio sent at all" (not
    # on elapsed time alone) so it never interrupts a legitimately long
    # response that IS actively streaming audio.
    #
    # Urdu fix: only starts tracking when _tts_state["watchdog_armed"] is True
    # — previously tracked from VAD end (is_speaking=True at L5001), which
    # included the entire STT+intelligence pipeline (~14s for first Urdu
    # command) and falsely triggered the stuck threshold. Now armed only
    # when TTS synthesis actually begins.
    _STUCK_THRESHOLD_S = 12.0
    
    async def _stuck_speaking_watchdog() -> None:
        nonlocal is_speaking
        stuck_since: Optional[float] = None
        while websocket.client_state == WebSocketState.CONNECTED:
            await asyncio.sleep(2.0)
            # Only track when TTS is actively synthesizing — the old check
            # (is_speaking and not audio_sent) also fired during the STT +
            # intent pipeline where is_speaking=True but no TTS has started.
            _armed = _tts_state.get("watchdog_armed", False)
            if is_speaking and _armed and not _tts_state.get("audio_sent", False):
                if stuck_since is None:
                    stuck_since = time.time()
                    continue
                stuck_for = time.time() - stuck_since
                if stuck_for > _STUCK_THRESHOLD_S:
                    logger.warning(
                        "[TTS_STUCK_DETECTED] stuck_for_s=%.1f session_instance_id=%s "
                        "audio_queue_depth=%d",
                        stuck_for, session_instance_id, len(pcm_buffer),
                    )
                    # Cannot forcibly kill a running Kokoro thread from here —
                    # Python has no safe preemption for that — so this
                    # "cancels" in the sense Task 6 means: the SESSION stops
                    # waiting on it and recovers; a late-finishing synth
                    # simply warms the cache and is otherwise discarded.
                    logger.info("[TTS_TASK_CANCELLED] reason=stuck_watchdog session_instance_id=%s",
                                session_instance_id)
                    is_speaking = False
                    _tts_state["watchdog_armed"] = False
                    stuck_since = None
                    if websocket.client_state == WebSocketState.CONNECTED:
                        # Previously this recovery was silent — the user hears
                        # nothing at all (no audio was ever sent, that's the
                        # whole trigger condition) and the UI just flips back
                        # to "listening" with no explanation, which reads as
                        # the app having gone completely unresponsive. Speak
                        # a short apology so there's audible confirmation
                        # something happened and what to do next.
                        # Language-aware: match the session's response language.
                        try:
                            _resp_lang = _session_state.get("ml_resp_lang", "en")
                            _stuck_map = {
                                "ur":       "معذرت، تھوڑی دیر ہو گئی — دوبارہ کہیں۔",
                                "ur_roman": "Maafi, thori der ho gayi — dobara kahein.",
                                "hi":       "माफ़ करें, थोड़ी देर हो गई — दोबारा कहें।",
                                "ar":       "عذراً، استغرق وقتاً طويلاً — حاول مرة أخرى.",
                                "mixed":    "Maafi, thori der ho gayi — dobara kahein.",
                            }
                            _stuck_text = _stuck_map.get(_resp_lang, "Sorry, that took too long — please try again.")
                            await _send(websocket, {"type": "response", "text": _stuck_text, "chunk": 1})
                            await _tts_with_fallback(_stuck_text)
                        except Exception as _stuck_exc:
                            logger.debug("[TTS_STUCK_RECOVERY_SPEAK_FAILED] %r", _stuck_exc)
                        await _send(websocket, {"type": "listening"})
                    logger.info("[VOICE_STATE_RECOVERED] from=speaking to=listening")
            else:
                stuck_since = None
    
    _stuck_watchdog_task = asyncio.create_task(_stuck_speaking_watchdog())

    # ── Multilingual TTS helper — XTTS-v2 for non-English responses ─────────

    async def _tts_ml(text: str, lang: str) -> bool:
        """Synthesize text using multilingual TTS (Edge-TTS for Urdu family).

        Streams audio chunk-by-chunk — splits text at sentence boundaries
        (same _split_for_tts the English Kokoro path uses) and synthesizes
        each chunk separately, sending each as its own WebSocket audio
        frame. This means the frontend can start playing the first sentence
        while later sentences are still being synthesized, cutting perceived
        latency dramatically for longer responses. Short acks (1 sentence,
        <= 80 chars) stay as a single chunk with no overhead.

        Falls back to Kokoro English if the ML engine is unavailable.
        """
        _tts_state["audio_sent"] = False
        _tts_state["watchdog_armed"] = True  # arm stuck watchdog only during active TTS
        _tts_playback_done_event.clear()
        _ml_t0 = time.time()
        logger.info("[TTS_ML_ENTER] lang=%s chars=%d", lang, len(text))

        # ── Split text into sentence-sized chunks for streaming ──────────────
        # Same splitting logic as the English Kokoro path — this is what
        # enables streaming: the first chunk is synthesized and sent before
        # later chunks even start synthesizing.
        chunks = _split_for_tts(text)
        n = len(chunks)
        _any_sent = False

        for i, chunk in enumerate(chunks, 1):
            wav = None
            try:
                from voice.tts_router import synthesize as _route_synth
                wav = await asyncio.wait_for(
                    asyncio.to_thread(_route_synth, chunk, lang, voice),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.error("[TTS_ML_CHUNK_FAIL] lang=%s chunk=%d/%d reason=30s_timeout",
                             lang, i, n)
            except Exception as _ml_exc:
                logger.error("[TTS_ML_CHUNK_FAIL] lang=%s chunk=%d/%d error=%s",
                             lang, i, n, _ml_exc)
            finally:
                _tts_state["watchdog_armed"] = False

            if not wav:
                # If this is the first chunk and synthesis failed, there's no
                # audio at all. If it's a later chunk, we already sent earlier
                # audio — just end the stream gracefully.
                logger.warning("[TTS_ML_NO_AUDIO] lang=%s chunk=%d/%d", lang, i, n)
                if not _any_sent:
                    _tts_playback_done_event.set()
                    return False
                # Already sent some audio — send final flag and exit
                _tts_state["audio_sent"] = True
                _tts_playback_done_event.set()
                return True

            sent = await _send(websocket, {
                "type":  "audio",
                "data":  base64.b64encode(wav).decode(),
                "chunk": i,
                "total": n,
                "final": (i == n),
                "text":  chunk,
            })
            if sent:
                _any_sent = True
                _tts_state["audio_sent"] = True
                logger.info("[TTS_ML_CHUNK] lang=%s chunk=%d/%d ms=%.0f bytes=%d",
                            lang, i, n, (time.time() - _ml_t0) * 1000, len(wav))
            else:
                # Send failed — connection probably dropped
                logger.info("[TTS_ML_EXIT] ms=%.0f chunks=%d reason=send_failed",
                            (time.time() - _ml_t0) * 1000, i)
                _tts_playback_done_event.set()
                return False

            if websocket.client_state != WebSocketState.CONNECTED:
                logger.info("[TTS_ML_EXIT] ms=%.0f chunks=%d reason=disconnected",
                            (time.time() - _ml_t0) * 1000, i)
                _tts_playback_done_event.set()
                return False

        logger.info("[TTS_ML_DONE] lang=%s ms=%.0f chunks=%d audio_sent=%s",
                    lang, (time.time() - _ml_t0) * 1000, n, _tts_state["audio_sent"])
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
        _is_immediate_ack: bool = False, _skip_localize: bool = False,
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
            # Localize English response text to the target language before
            # speaking. Try natural qwen generation first (varied, conversational),
            # fall back to template-based localization if qwen is unavailable.
            #
            # _skip_localize bypasses this entirely for callers that already
            # built the exact target-language text themselves (e.g. the
            # hardcoded per-language _lang_switch_ack_map). Bug this fixes
            # (live-caught 2026-08-24): re-running an already-Urdu string
            # through localize_with_fallback tells qwen "translate this
            # English text" with Urdu input — the model doesn't recognize
            # it as already-translated, so it hallucinates an unrelated
            # reply instead of passing it through ("ٹھیک ہے، اب اردو میں
            # بات کرتا ہوں۔" → "آپ کی سوال رہے ہیں؟", a non-sequitur).
            if not _skip_localize:
                try:
                    from api.services.urdu_ack_generator import localize_with_fallback as _loc_fn_async
                    _loc = await _loc_fn_async(text, _resp_lang)
                    if _loc and _loc != text:
                        logger.info("[RESP_LOCALIZED_FOR_TTS] %r → %r", text[:40], _loc[:40])
                        text = _loc
                except Exception:
                    pass
            return await _tts_ml(text, _resp_lang)
        # ── English Kokoro path (unchanged) ──────────────────────────────────────────────
        _tts_state["audio_sent"] = False
        _tts_state["watchdog_armed"] = True  # arm stuck watchdog for English TTS
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
                    _tts_state["watchdog_armed"] = False
                    return False
            if websocket.client_state != WebSocketState.CONNECTED:
                logger.info("[TTS_STATE_EXIT] ms=%.0f chunks=%d reason=disconnected audio_sent=%s",
                            (time.time() - _tts_t0) * 1000, i, _tts_state["audio_sent"])
                if not _tts_state["audio_sent"]:
                    _tts_playback_done_event.set()
                _tts_state["watchdog_armed"] = False
                return False
        logger.info("[TTS_STATE_EXIT] ms=%.0f chunks=%d audio_sent=%s",
                    (time.time() - _tts_t0) * 1000, n, _tts_state["audio_sent"])
        if not _tts_state["audio_sent"]:
            _tts_playback_done_event.set()  # synthesis produced no audio → no tts_done will arrive
        _tts_state["watchdog_armed"] = False
        return False

    async def _tts_with_fallback(text: str, _skip_localize: bool = False) -> bool:
        """_tts_sequential with one retry on a fallback voice if no audio was sent."""
        _interrupted = await _tts_sequential(text, _skip_localize=_skip_localize)
        if not _tts_state["audio_sent"]:
            _fb = "alloy" if voice != "alloy" else "nova"
            logger.warning("[TTS_FALLBACK_ATTEMPT] primary_voice=%s no_audio — retrying with fallback_voice=%s text=%r",
                           voice, _fb, text[:40])
            _interrupted = await _tts_sequential(text, _fb, _skip_localize=_skip_localize)
            if _tts_state["audio_sent"]:
                logger.info("[TTS_FALLBACK_SUCCESS] fallback_voice=%s", _fb)
            else:
                logger.error("[TTS_FALLBACK_FAILED] all voices failed — sending listening text=%r", text[:40])
        return _interrupted

    # Started once per WS connection, now that _tts_with_fallback exists in
    # this closure — drains _narration_queue for the life of the session.
    _narration_task = asyncio.create_task(_narration_speaker_loop())

    # ── WhatsApp incoming-message announcements ────────────────────────────
    # A background thread (wa_incoming_notifier.py, on the Baileys SSE
    # consumer's OWN thread — not this event loop) hands off announcements
    # via voice_announcer.announce(), which does loop.call_soon_threadsafe
    # onto this queue. Drained here, same collapse-if-busy pattern as
    # narration: an incoming WhatsApp message should never interrupt the
    # user mid-command, so if TTS is already speaking, skip it rather than
    # queue up stale announcements to read out later.
    _wa_announce_queue: "asyncio.Queue[dict]" = asyncio.Queue()

    async def _wa_announce_loop() -> None:
        while True:
            payload = await _wa_announce_queue.get()
            try:
                text = payload.get("text") or ""
                if not text:
                    continue
                if is_speaking:
                    logger.info("[WA_ANNOUNCE_SKIPPED] reason=already_speaking text=%r", text[:80])
                    continue
                logger.info("[WA_ANNOUNCE] text=%r", text[:200])
                await _tts_with_fallback(text)
            except Exception as exc:
                logger.debug("[WA_ANNOUNCE] speak error (ignored): %r", exc)
            finally:
                _wa_announce_queue.task_done()

    _wa_announce_task = asyncio.create_task(_wa_announce_loop())
    try:
        from api.services import voice_announcer as _voice_announcer
        _voice_announcer.register(asyncio.get_event_loop(), _wa_announce_queue)
    except Exception:
        logger.debug("[WA_ANNOUNCE] voice_announcer registration failed", exc_info=True)

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

    # Human-sounding, emotion-aware ack/completion replies now live in
    # api.services.conversational_replies (shared pools + anti-repeat).
    # Settings page display names moved there with them.
    from api.services.conversational_replies import (
        SETTINGS_PAGE_NAMES as _SETTINGS_PAGE_NAMES,
        pick_ack as _reply_pick_ack,
        pick_completion as _reply_pick_completion,
    )

    def _build_ack_text(tool_name: str, tool_params: dict,
                        emotion: str | None = None) -> str:
        """Return a command-aware acknowledgement phrase for the given tool —
        natural spoken variants chosen from emotion-tagged pools in
        conversational_replies, toned to how the user sounds this turn
        (upbeat / warm / neutral / reassuring), never repeating the exact
        same wording as the last time this slot fired."""
        text = _reply_pick_ack(tool_name, tool_params, emotion)
        logger.info("[COMMAND_ACK_SELECTED] tool=%s params=%s emotion=%s text=%r",
                    tool_name, tool_params, emotion or "none", text)
        return text

    def _build_completion_text(tool_name: str, tool_params: dict,
                               emotion: str | None = None) -> str:
        """
        UX polish (Part 3): completion speech narrates what already
        happened, in past tense — never "Opening X." after the action is
        already done. Covers every tool (per user feedback: silence on
        success read as broken, so nothing goes fully silent anymore).
        Variants are emotion-toned (conversational_replies); the
        context-aware YouTube mood question is preserved there.
        """
        text = _reply_pick_completion(tool_name, tool_params, emotion)
        logger.info("[COMPLETION_TEXT_SELECTED] tool=%s emotion=%s text=%r",
                    tool_name, emotion or "none", text)
        return text

    # ── Tool execution helper ─────────────────────────────────────────────────
    # Perf-accounting note (Problem 5 fix): tool_ms in V_LATENCY must reflect
    # only _registry.execute()'s own duration, never any ack-synthesis wait
    # that happens to run concurrently/before it in the caller. _run_tool
    # stashes its measured duration here so the TOOL branch below can read
    # the real number instead of a wall-clock delta that also captures the
    # ack-synthesis block.
    #
    # Cross-turn contamination fix (2026-09-05, live-caught): these used to
    # be flat single-slot dicts ({"ms": ...}, {"ok": ...}) shared across the
    # WHOLE WebSocket connection — every turn's process_utterance() call
    # writes through the SAME _run_tool closure. voice_ws.py's own staleness
    # guards (my_turn/current_turn_id) prove overlapping turns ARE a real,
    # anticipated scenario, not a hypothetical: a slow tool call from turn N
    # (e.g. "open YouTube" blocked ~20s in CDP retries — see
    # browser_workspace.py's bounded-retry fix, same investigation) can
    # still be in flight when turn N+1 (e.g. "open Settings", <20ms) starts,
    # runs to completion, and reads this shared state — then turn N's
    # long-delayed write finally lands and clobbers it with turn N's OWN
    # number just before turn N+1's V_LATENCY summary log fires. Live
    # symptom: a Settings turn's own [PERF_TOOL] line correctly showed
    # ms=11, but that SAME turn's later [V_LATENCY] line reported
    # tool_ms=20833 — the youtube turn's number, mislabeled. Now keyed by
    # trace_id (each turn's own, distinct — see process_utterance's
    # _trace_id) instead of a flat key, so two turns's numbers can never
    # collide; .pop() on read keeps the dict from growing unbounded.
    _last_tool_exec_ms: dict[str, float] = {}
    # Part 4 polish: lets the TOOL branch decide silence-on-success without
    # _run_tool needing to change its (str) return-value contract used by
    # every other caller (memory_ref path, MULTI_STEP, confirmation flows).
    _last_tool_success: dict[str, bool] = {}

    async def _run_tool(tool_name: str, tool_params: dict, goal: str = "", trace_id: str = "") -> str:
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
        _ctx = {
            "openai_key":    _cfg.openai_api_key,
            "active_window": _aw,
            "response_lang": _session_state.get("ml_resp_lang", "en"),
        }
        # ── Trace instrumentation ───────────────────────────────────────────
        # Prefer the CALLER's own trace_id (the turn that actually invoked
        # this tool) over the global "current trace" pointer — that global
        # is process-wide/connection-wide and gets reassigned by whichever
        # turn's process_utterance() called tracer().start() MOST RECENTLY,
        # which is not necessarily THIS call's turn under overlapping turns
        # (see this function's _last_tool_exec_ms comment above for the
        # exact live symptom this caused: a tool's own [TOOL_START]/
        # [PERF_TOOL] lines could be stamped with a DIFFERENT turn's trace
        # ID than the one that actually invoked it). Falls back to the
        # global lookup only for the handful of legacy call sites that
        # don't yet pass trace_id — never worse than the old behavior.
        if trace_id:
            _t_id, _ts = trace_id, None
        else:
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
        # Real-mic Urdu test Issue 7: the label used to be derived from the
        # raw tool name ("Opening open application") — it must come from
        # the SAME semantic action/object title activity_events computes,
        # so logs and frontend always read "Opening Settings" / "Opening C
        # Drive" etc.
        try:
            from api.services.activity_events import emit_activity as _emit_act, title_for as _act_title
            _act_stage, _act_title_started = _act_title(tool_name, tool_params, "started")
            logger.info("[PROGRESS_EVENT_CREATED] tool=%s label=%r", tool_name, _act_title_started)
            await _emit_act(websocket, _send, trace_id=_t_id, stage=_act_stage, status="started",
                            title=_act_title_started, tool=tool_name)
        except Exception:
            logger.info("[PROGRESS_EVENT_CREATED] tool=%s label=%r", tool_name, "Working on it")
        _tool_t0 = time.time()
        result = await asyncio.to_thread(_registry.execute, tool_name, tool_params, _ctx)
        _tool_ms = (time.time() - _tool_t0) * 1000
        _last_tool_exec_ms[_t_id] = _tool_ms
        _last_tool_success[_t_id] = result.success
        logger.info("[PERF_TOOL] tool=%s ms=%.0f success=%s", tool_name, _tool_ms, result.success)
        # Generic fine-grained breakdown — any tool may populate
        # data["latency_ms"] with its own sub-stage timings (Phase 4's
        # WhatsApp tools do: contact_resolution_ms/planning_ms/transport_ms
        # etc.). Not WhatsApp-specific: this just surfaces whatever a tool
        # already measured, for any tool that provides it.
        _sub_lat = (result.data or {}).get("latency_ms")
        if isinstance(_sub_lat, dict) and _sub_lat:
            logger.info("[PERF_TOOL_DETAIL] tool=%s %s", tool_name,
                        " ".join(f"{k}={v}" for k, v in _sub_lat.items()))
        try:
            from api.services.activity_events import emit_activity as _emit_act2, title_for as _act_title2
            _act_stage2, _act_title_done = _act_title2(
                tool_name, tool_params, "completed" if result.success else "failed", result.data,
            )
            await _emit_act2(websocket, _send, trace_id=_t_id,
                             stage="completed" if result.success else "failed",
                             status="completed" if result.success else "failed",
                             title=_act_title_done, tool=tool_name)
        except Exception:
            pass
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
                "created_at": time.time(),
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

        # ── YouTube disambiguation gate — ambiguous search or multiple results ─
        if result.error == "youtube_disambiguation":
            _yt_cands = (result.data or {}).get("candidates", [])
            _yt_src_q = (result.data or {}).get("source_query", "")
            _session_state["pending_video_candidates"] = {
                "candidates":   _yt_cands,
                "source_query": _yt_src_q,
                "search_url":   (result.data or {}).get("search_url", ""),
                "created_at":   time.time(),
            }
            logger.info("[YOUTUBE_SELECTION_PENDING] source_query=%r candidates=%d",
                        _yt_src_q, len(_yt_cands))
            return result.data.get("prompt", "Which one would you like to play?")

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
            # ── World State update — activity timeline + goal tracker ────────────
            try:
                from api.services.world_state import world_state as _wstate
                _rdata_ws = result.data or {}
                asyncio.create_task(asyncio.to_thread(
                    _wstate.record_action,
                    (result.text or tool_name)[:120], tool_name,
                    _rdata_ws.get("path") or _rdata_ws.get("action_path"), True, "voice_ws",
                ))
            except Exception:
                pass
            # ── Activity memory — persistent cross-session recall log ─────────
            # Songs played / folders opened / apps launched land in
            # ~/.xyron/activity_memory.jsonl so "what songs did you play
            # yesterday" survives restarts (session-scoped context_stack and
            # activity_timeline can't answer that). File append is off-loop.
            try:
                from api.services.activity_memory import activity_memory as _amem_r
                _rdata_am = result.data or {}
                asyncio.create_task(asyncio.to_thread(
                    _amem_r.record_from_tool, tool_name, tool_params, _rdata_am
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
        _resp_lang_stream = _session_state.get("ml_resp_lang", "en")
        try:
            async for sentence, wav, chunk_idx, is_final in stream_response_with_tts(
                transcript, history, voice=voice, speed=speed, response_lang=_resp_lang_stream
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
            from api.services.failure_messages import offline_fallback as _offline_fallback_llm
            fallback = _offline_fallback_llm()
            await _send(websocket, {"type": "response", "text": fallback, "chunk": 1})
            await _tts_sequential(fallback)
            full_text = fallback
        full_text = full_text.strip()
        if not full_text and not interrupted:
            # stream_response_with_tts completed with no exception but yielded
            # nothing (e.g. both OpenAI and the Ollama rescue failed before
            # queuing anything) — without this, the turn ends in total
            # silence: no exception for the except-branch above to catch,
            # nothing spoken, nothing shown.
            from api.services.failure_messages import offline_fallback as _offline_fallback_llm2
            full_text = _offline_fallback_llm2()
            await _send(websocket, {"type": "response", "text": full_text, "chunk": 1})
            await _tts_sequential(full_text)
        return full_text, interrupted

    # ── Utterance processor ───────────────────────────────────────────────────

    async def process_utterance(frames: list[np.ndarray], my_turn: int) -> None:
        nonlocal is_speaking, last_activity_t, last_response_text

        async def _spawn_tts_watchdog(route: str = "generic") -> None:
            """Send listening if tts_done never arrives once TTS audio should be done playing.

            A flat 2s here used to fire before any response longer than ~2s of
            speech had actually finished playing on the client (a 151-char
            screen-query reply is ~6-8s of audio). That premature clear skipped
            the anti-echo mic flush the real tts_done handler does, so the mic
            re-armed while TTS was still audible — a likely source of
            self-triggered VAD hits / STT hallucinations right after replies.
            Estimate real speech duration from the response text instead, and
            mirror the full post-TTS reset when the watchdog does have to fire.
            """
            nonlocal is_speaking, last_activity_t, speech_started, silence_count, _post_tts_flush_until
            _est_chars = len(last_response_text or "")
            _wait_s = min(20.0, max(2.0, _est_chars / 13.0 + 1.5))
            await asyncio.sleep(_wait_s)
            if websocket.client_state == WebSocketState.CONNECTED:
                if is_speaking:
                    logger.warning("[TTS_DONE_WATCHDOG] is_speaking stuck %.1fs route=%s — force clearing",
                                    _wait_s, route)
                    is_speaking = False
                    last_activity_t = time.time()
                    logger.info("[TTS_DONE_MISSING_FAST_CLEAR] route=%s reason=watchdog_%.1fs", route, _wait_s)
                    logger.info("[SPEAKING_FLAG_CLEARED] reason=tts_done_watchdog route=%s", route)
                    # Mirror the real tts_done handler's anti-echo guard — otherwise a
                    # missed ack leaves the mic armed against still-decaying TTS audio.
                    _post_tts_flush_until = time.time() + 0.7
                    speech_started = False
                    silence_count  = 0
                    pcm_buffer.clear()
                    logger.info("[POST_TTS_MIC_FLUSH_START] flush_window_ms=700 resetting VAD state (watchdog)")
                    logger.info("[VAD_STATE_RESET_AFTER_TTS] speech_started=False silence_count=0 buffer_cleared=True")
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

        # ── Audio front-end cleanup — noise suppression + gain normalization ──
        # Only reached after every silence/duration/ratio gate above has
        # already confirmed real speech, so cleanup never risks amplifying
        # pure noise. Runs before STT so Whisper gets a cleaner, level-
        # normalized signal regardless of mic quality/distance.
        try:
            _pre_clean_rms = _pre_rms
            audio = _denoise_and_normalize(audio, 16000)
            _post_clean_rms = float(np.sqrt(np.mean(audio ** 2)))
            logger.debug("[AUDIO_CLEANUP] rms_before=%.5f rms_after=%.5f",
                         _pre_clean_rms, _post_clean_rms)
        except Exception as _clean_exc:
            logger.debug("[AUDIO_CLEANUP_FAILED] error=%s", _clean_exc)

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
                from api.routers.voice import _kokoro_executor as _kko_exec_ack
                _ack_wav = await asyncio.get_running_loop().run_in_executor(
                    _kko_exec_ack, _tcc_ack.synthesize_or_cached, "Sure.", voice, speed
                )
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
            from voice.whisper_service import stt_executor as _stt_exec
            # hybrid_stt_router.route() is a plain synchronous call into
            # faster-whisper with no internal timeout — on a machine where
            # the Whisper model is still loading (slow disk/cold cache) or
            # the model lock is held by another warmup thread, this could
            # block asyncio.to_thread's worker indefinitely with zero
            # feedback to the user (live-measured: session sits silent past
            # the 12s stuck-watchdog with is_speaking never even set, since
            # nothing downstream of this call has run yet). Bound it so a
            # hang degrades to "please repeat" instead of a dead session.
            try:
                _stt_route_out = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _stt_exec, _stt_route, audio, _audio_dur_ms, _session_state
                    ),
                    timeout=25.0,
                )
            except asyncio.TimeoutError:
                logger.warning("[STT_TIMEOUT] audio_ms=%.0f — Whisper call exceeded 25s, "
                               "model likely still loading/unavailable", _audio_dur_ms)
                is_speaking = True
                _stt_timeout_text = "Sorry, I'm still starting up — please try again in a moment."
                await _send(websocket, {"type": "response", "text": _stt_timeout_text, "chunk": 1})
                await _tts_with_fallback(_stt_timeout_text)
                is_speaking = False
                await _send(websocket, {"type": "listening"})
                return
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
                    _retry_out = await asyncio.get_running_loop().run_in_executor(
                        _stt_exec, _stt_route, audio, _audio_dur_ms, _retry_state
                    )
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

            # Problem 3 fix: hybrid_stt_router rejected this utterance as a
            # decoder hallucination loop (e.g. "open open open ..."). Never
            # let a hallucinated transcript reach the intelligence pipeline,
            # intent router, or tool execution — ask the user to repeat
            # instead of acting on garbage.
            if result.get("hallucinated"):
                logger.warning("[TRACE %s] [STT_HALLUCINATION_REJECTED_AT_DISPATCH]", _trace_id)
                is_speaking = True
                _clarify_text = "I didn't catch that clearly. Please say it again."
                await _send(websocket, {"type": "response", "text": _clarify_text, "chunk": 1})
                await _tts_with_fallback(_clarify_text)
                is_speaking = False
                await _send(websocket, {"type": "listening"})
                return

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
            # Previously silent beyond the raw error frame — timeout and
            # hallucination-rejection above both speak something, this
            # generic-exception branch didn't. Say what happened instead of
            # leaving a voice-only user with dead air.
            is_speaking = True
            from api.services.failure_messages import stt_failure as _stt_failure_msg
            _stt_fail_text = _stt_failure_msg()
            await _send(websocket, {"type": "response", "text": _stt_fail_text, "chunk": 1})
            await _tts_with_fallback(_stt_fail_text)
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

        # ── Duplicate-sentence collapse — defense in depth ───────────────────
        # hybrid_stt_router.route() already applies this right after the
        # fast STT pass (see collapse_duplicate_sentences there for the full
        # story — a duplicate artifact was both forcing unnecessary retries
        # and corrupting routing/context_stack, e.g. "open vs code" silently
        # failing). This is a no-op in the normal case; kept as a second
        # layer in case a duplicate survives the retry/accurate-model path
        # (which doesn't currently re-run the collapse on its own output).
        from voice.hybrid_stt_router import (
            collapse_duplicate_sentences as _collapse_dup,
            strip_trailing_verb_echo as _strip_echo,
        )
        _deduped = _collapse_dup(transcript)
        if _deduped != transcript:
            logger.info("[TRANSCRIPT_DEDUPLICATED] %r → %r", transcript[:80], _deduped)
            transcript = _deduped
            result["text"] = transcript
        _de_echoed = _strip_echo(transcript)
        if _de_echoed != transcript:
            logger.info("[TRANSCRIPT_TRAILING_ECHO_STRIPPED] %r → %r", transcript[:80], _de_echoed)
            transcript = _de_echoed
            result["text"] = transcript

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

        # ── Semantic transcript correction — before any intent matching
        # consumes the transcript, including the fast-path agent dispatch
        # immediately below. Only fires on low-confidence STT results (see
        # _needs_semantic_correction); fails open to the original transcript.
        _corrected_transcript = await _correct_transcript_semantic(
            transcript, result.get("confidence", -999.0),
            result.get("language", ""),
        )
        if _corrected_transcript != transcript:
            transcript = _corrected_transcript
            result["text"] = transcript

        # Captured HERE — after phonetic/semantic correction but BEFORE the
        # intelligence pipeline (a few blocks below) overwrites `transcript`
        # with its mixed_language_engine canonical English rewrite (e.g.
        # "Chrome kholo" -> "open Chrome "). This used to be captured after
        # that rewrite (see the old comment further down, now moved here with
        # it), which meant a Roman-Urdu/Urdu turn's language got detected
        # from its ENGLISH canonical form, not what the user actually said —
        # live-caught bug: after "Chrome kholo" got canonicalized to "open
        # Chrome ", ml_detected_lang was set to "en" for the SESSION, which
        # made hybrid_stt_router.py drop out of multilingual-accurate STT
        # routing on the very next turn and send genuine Roman Urdu audio
        # ("Settings ko kholo") to the English-only tiny.en fast model,
        # producing garbage ("Settings code be called though.") that then
        # skipped both retry-to-accurate and semantic correction because
        # tiny.en was confident about its own wrong answer.
        _original_transcript = transcript

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
                trace_id=_trace_id,
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
        # _original_transcript (used below for language detection + memory)
        # was captured earlier, right after semantic correction — see that
        # capture point for why.
        try:
            import os as _ml_os
            from api.services.language_detector import detect as _lang_detect
            from api.services.ml_normalizer import normalize as _ml_normalize
            from api.services.response_language import (
                check_preference_update as _check_lang_pref,
                select_response_language as _select_resp_lang,
            )
            _raw_stt_lang = (result.get("language") or "en") if isinstance(result, dict) else "en"
            # Detect on _original_transcript (pre-intel-pipeline-canonicalization),
            # NOT `transcript` — by this point `transcript` may already be the
            # mixed_language_engine's English canonical rewrite (e.g. "Chrome
            # kholo" -> "open Chrome "), which would make a genuine Roman-Urdu
            # turn detect as "en" and corrupt ml_detected_lang for the *next*
            # turn's STT routing decision (see _original_transcript's capture
            # comment above for the live-caught failure this fixes).
            _lang_info    = _lang_detect(_original_transcript, _raw_stt_lang)
            _detected_ml  = _lang_info["lang"]
            _session_state["ml_detected_lang"] = _detected_ml
            # Persist the detection CONFIDENCE too — hybrid_stt_router only
            # honors ml_detected_lang for next-turn STT routing when the
            # detection was confident. Live bug this closes: one shaky
            # keyword mis-detection (pure English flagged ur_roman at 0.60-
            # class confidence) forced every following turn onto the slow
            # multilingual "accurate" Whisper model (~3s vs ~0.8s).
            _session_state["ml_detected_lang_conf"] = _lang_info.get("confidence", 0.0)
            # First hi/ar turn this session → kick off XTTS's background load
            # now, in parallel with routing/tool-execution, instead of
            # waiting for the TTS call to discover it's cold. Idempotent and
            # gpu_coordinator-gated (see xtts_service._ensure_bg_load) — cheap
            # to call every turn once already loading/loaded.
            #
            # Gated to (hi, ar) specifically, NOT "any non-English" — voice/
            # tts_router.py routes ur/ur_roman/mixed to Edge-TTS exclusively
            # (_EDGE_TTS_LANGS), never to XTTS. Every Urdu-family session was
            # previously triggering a real background load attempt (a
            # gpu_coordinator wait up to 30s, contending with concurrent
            # Whisper/TTS GPU work — visible as [GPU_JOB_WAIT] timing out
            # "after 30s — proceeding anyway" in production logs) for a model
            # that language would never actually use, on top of XTTS having a
            # separately confirmed permanently corrupted checkpoint on this
            # machine (see xtts_service.py's module docstring) that makes the
            # load fail regardless.
            if _detected_ml in ("hi", "ar") and not _session_state.get("_xtts_warm_triggered"):
                _session_state["_xtts_warm_triggered"] = True
                try:
                    from voice.xtts_service import _ensure_bg_load as _xtts_warm
                    _xtts_warm()
                except Exception:
                    pass
            # Same pattern for the local Qwen semantic-comprehension model
            # (Tier 4 of orchestrator._route_intent, non-English only) — see
            # local_comprehension.py's "Timeout / warm-keep" docstring
            # section for the measured cold-load cost (11.6-15.7s) this
            # closes. Fires once per session on the first non-English turn,
            # in the background, gpu_coordinator-gated exactly like XTTS's
            # own preload — never blocks this turn's response.
            if _detected_ml != "en" and not _session_state.get("_qwen_warm_triggered"):
                _session_state["_qwen_warm_triggered"] = True
                try:
                    from api.services.local_comprehension import ensure_warm as _qwen_warm
                    _qwen_warm(reason=f"first_non_english_turn_lang={_detected_ml}")
                except Exception:
                    pass
            # Check if user is setting a language preference ("always reply in Urdu" etc.)
            _lang_pref_mode = _check_lang_pref(transcript, _session_id)
            # Choose TTS output language
            _resp_lang_mode = _ml_os.getenv("RESPONSE_LANGUAGE_MODE", "auto")
            _ml_resp = _select_resp_lang(
                _detected_ml, _session_id, _resp_lang_mode, _lang_info["confidence"],
                word_count=len(_original_transcript.split()),
                stt_confidence=(result.get("confidence") if isinstance(result, dict) else None),
            )
            _session_state["ml_resp_lang"] = _ml_resp

            # An explicit language-switch command ("Urdu mein baat karo",
            # "reply in English") is fully handled right here by
            # _check_lang_pref above — the turn IS the command, there is
            # nothing left to route. Live-caught bug this closes: this used
            # to fall through into intent_router/orchestrator like any other
            # utterance, which had no tool for "talk in Urdu" and handed it
            # to the local Qwen fallback, which guessed search_files and
            # burned ~14s searching for a file literally named "urdu mein
            # baat karo, یار." before failing — the language switch itself
            # worked, but the user got a bogus "no files found" error
            # instead of any acknowledgement.
            if _lang_pref_mode:
                logger.info("[LANG_SWITCH_ACK] mode=%s resp_lang=%s — short-circuiting routing", _lang_pref_mode, _ml_resp)
                _lang_switch_ack_map = {
                    "en":       "Okay, switching to English.",
                    "ur":       "ٹھیک ہے، اب اردو میں بات کرتا ہوں۔",
                    "ur_roman": "Theek hai, ab Urdu mein baat karta hoon.",
                    "hi":       "ठीक है, अब हिंदी में बात करता हूँ।",
                    "ar":       "حسناً، سأتحدث بالعربية الآن.",
                }
                _lang_ack_text = _lang_switch_ack_map.get(_ml_resp, _lang_switch_ack_map["en"])
                await _send(websocket, {"type": "transcript", "text": transcript, "final": True})
                await _send(websocket, {"type": "response", "text": _lang_ack_text, "chunk": 1})
                memory.add_user(_original_transcript)
                memory.add_assistant(_lang_ack_text)
                is_speaking = True
                # _skip_localize=True: _lang_ack_text is already the exact
                # target-language string from _lang_switch_ack_map above —
                # re-running it through localize_with_fallback (which treats
                # its input as English needing translation) hallucinates an
                # unrelated reply instead of passing it through.
                await _tts_with_fallback(_lang_ack_text, _skip_localize=True)
                is_speaking = False
                await _send(websocket, {"type": "listening"})
                return

            # Normalize non-English command to English for intent routing
            if _detected_ml not in ("en",):
                _ml_en_cmd = _ml_normalize(transcript, _detected_ml)
                if _ml_en_cmd and _ml_en_cmd.strip() and _ml_en_cmd != transcript:
                    logger.info("[TRACE %s] [ML_NORMALIZE_INPUT→OUTPUT] %r → %r",
                                _trace_id, transcript[:60], _ml_en_cmd[:60])
                    transcript = _ml_en_cmd
            logger.info(
                "[TRACE %s] [STT_LANG_ROUTE] mode=%s reason=lang_%s resp_lang=%s",
                _trace_id,
                "multilingual" if _detected_ml != "en" else "english_fast",
                _detected_ml, _ml_resp,
            )
            logger.info(
                "[TRACE %s] [STT_MULTILINGUAL_RESULT] lang=%s transcript=%r ms=%.0f",
                _trace_id, _detected_ml, transcript[:60], _lat.get("stt", 0),
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
        # Conversation memory keeps what the user actually said (original
        # language), not the English canonical command routing uses below —
        # see _original_transcript capture above.
        memory.add_user(_original_transcript)
        _session_state["last_original_transcript"] = _original_transcript
        logger.info("[VOICE_TRACE] stage=stt transcript=%r", transcript[:80])
        logger.info(
            "[BRAIN_PIPELINE] stage=normalized input=%r turn=%d",
            transcript[:80], my_turn,
        )

        # ── Tier 0d: Pending confirmation handler — yes/no before any routing ─
        # Fires when a prior tool returned error="confirm_required" (e.g. install_store_app).
        # Must run before Tier 0 clock so "yes" / "no" answers the pending action.
        _pending = _session_state.get("pending_confirmation")
        if _pending is not None:
            # Bounded expiry — matches the sibling pending-state pattern
            # already used for _pending_open_after_install / _pending_store /
            # _pending_video (all 300s). This one previously had none: a
            # stale confirm_required (e.g. "send 'X' to Tayyab?" from
            # minutes ago) would sit forever and could otherwise be
            # accidentally approved by an unrelated later "yes". Dismiss and
            # fall through to normal routing rather than re-prompting a
            # question the user has moved on from.
            if (time.time() - _pending.get("created_at", 0)) > 300:
                logger.info("[CONFIRMATION_EXPIRED] tool=%s", _pending.get("tool"))
                _session_state["pending_confirmation"] = None
                _pending = None
        if _pending:
            _YES_RE = _CONFIRM_YES_RE
            _NO_RE = _CONFIRM_NO_RE
            if _YES_RE.search(transcript):
                logger.info("[CONFIRMATION_ACCEPTED] tool=%s", _pending["tool"])
                _session_state["pending_confirmation"] = None
                # pre_actions — currently only used by the web-interaction
                # fallback flow to "transfer the current URL" into the
                # automation browser before the actual click/fill runs.
                # Best-effort: a pre_action failing (e.g. navigation
                # timeout) doesn't block trying the main action anyway.
                for _pre_tool, _pre_params in _pending.get("pre_actions", []):
                    try:
                        logger.info("[CONFIRMATION_PRE_ACTION] tool=%s params=%s", _pre_tool, _pre_params)
                        await _run_tool(_pre_tool, _pre_params, goal=transcript, trace_id=_trace_id)
                    except Exception as _pre_exc:
                        logger.warning("[CONFIRMATION_PRE_ACTION_FAILED] tool=%s error=%s", _pre_tool, _pre_exc)
                _conf_resp = await _run_tool(_pending["tool"], _pending["params"], goal=transcript, trace_id=_trace_id)
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
            # Live bug: "sure open it" (affirmation + action chained) failed
            # to match — the old pattern allowed only ONE leading word (an
            # affirmation OR an action verb, never both) followed by at most
            # one trailing target word. Now allows an optional affirmation,
            # then an action verb, then any number of trailing target words
            # ("yeah launch it now" etc.), while still matching bare "yes" or
            # bare "open it" alone.
            _OAI_AFFIRM_WORDS = r'(?:yes|yeah|yep|sure|ok|okay)'
            _OAI_ACTION_WORDS = r'(?:open|launch|start|run)'
            _OAI_TARGET_WORDS = (
                r'(?:it|up|now|please|instagram|whatsapp|spotify|tiktok|telegram'
                r'|snapchat|netflix|youtube|chatgpt|discord|facebook|twitter|zoom'
                r'|reddit|linkedin|pinterest|uber|lyft|amazon|twitch)'
            )
            _OAI_YES_RE = re.compile(
                rf'^\s*(?:'
                rf'{_OAI_AFFIRM_WORDS}(?:[\s,.!]+{_OAI_ACTION_WORDS})?'
                rf'|{_OAI_ACTION_WORDS}'
                rf')'
                rf'(?:\s+{_OAI_TARGET_WORDS})*\s*[.!]?\s*$',
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
                await _run_tool("open_application", {"app_name": _oai_app}, goal=transcript, trace_id=_trace_id)
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

                # ── Web-interaction confirmation gate ──────────────────────────
                # A resolved browser_click/browser_fill/etc follow-up needs a
                # real, controllable browser page. Per the CDP audit: Xyron
                # can only ever CDP-attach to a Chrome instance IT launched
                # (a dedicated Xyron profile — the user's real native Chrome,
                # opened via open_url_native/cmd.exe start, was never started
                # with a debug port and can't be attached to). So the first
                # interaction command in a session always needs to open that
                # controlled browser — ask before doing it, exactly once per
                # session (browser_workspace.is_healthy stays True afterward,
                # so later interaction turns skip straight through here).
                _WEB_INTERACTION_TOOLS = {
                    "browser_click", "browser_fill", "browser_read", "browser_screenshot",
                }
                if _fur.tool_name in _WEB_INTERACTION_TOOLS:
                    from api.agents.browser_agent.browser_workspace import browser_workspace as _bw_check
                    if not _bw_check.is_healthy:
                        _cur_url = (_actx_snap or {}).get("current_url")
                        logger.info("[WEB_CONTROL_CONFIRMATION_NEEDED] tool=%s current_url=%s",
                                    _fur.tool_name, _cur_url)
                        if not _cur_url:
                            _no_url_resp = ("I'm not sure what page you're on — try saying "
                                             "\"open\" the site first, then I can interact with it.")
                            memory.add_assistant(_no_url_resp, tool_name="web_control_no_url")
                            await _send(websocket, {"type": "response", "text": _no_url_resp, "chunk": 1})
                            _interrupted = await _tts_with_fallback(_no_url_resp)
                            if not _interrupted and _tts_state["audio_sent"]:
                                await _send(websocket, {"type": "done"})
                                asyncio.create_task(_spawn_tts_watchdog("web_control_no_url"))
                            else:
                                is_speaking = False
                                await _send(websocket, {"type": "listening"})
                            return
                        _session_state["pending_confirmation"] = {
                            "tool":   _fur.tool_name,
                            "params": _fur.tool_params,
                            "prompt": ("I can't control this Chrome tab directly. I can reopen it in "
                                       "my automation browser so I can interact with it. "
                                       "Would you like me to continue?"),
                            "pre_actions": [("browser_navigate", {"url": _cur_url})],
                            "created_at": time.time(),
                        }
                        logger.info("[CONFIRMATION_PENDING] tool=%s prompt=web_control_fallback", _fur.tool_name)
                        _wc_prompt = _session_state["pending_confirmation"]["prompt"]
                        memory.add_assistant(_wc_prompt, tool_name="web_control_confirmation")
                        await _send(websocket, {"type": "response", "text": _wc_prompt, "chunk": 1})
                        _interrupted = await _tts_with_fallback(_wc_prompt)
                        if not _interrupted and _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("web_control_confirm"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

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
                _fur_resp = await _run_tool(_fur.tool_name, _fur.tool_params, goal=transcript, trace_id=_trace_id)
                _lat["tool"] = (time.time() - _fur_tool_t0) * 1000
                # If the tool set a pending confirmation, _run_tool returns the prompt text.
                # Continue to TTS whether it's a confirmation prompt or a real response.
                # Same personality/humor polish the main TOOL branch applies —
                # follow-up responses ("play X", "download X" after context
                # was set) previously never went through personality_engine
                # at all, so a mode switch ("funny mode") had no effect on
                # exactly the turns that make a conversation feel natural.
                try:
                    from api.agents.personality.personality_engine import personality_engine as _pe_fur
                    _fur_resp = _pe_fur.polish_response(
                        _fur_resp, context={"action": _fur.tool_name, "event": "success"},
                    )
                except Exception:
                    pass
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

        # ── Tier 0m: Activity memory recall — cross-session questions ─────────
        # "what is my most recent folder?", "what was I working on yesterday?",
        # "what songs did you play today?", "play the same songs you played
        # yesterday" — answered from activity_memory's persistent JSONL log,
        # never the LLM. Must run before intent routing ("play the same songs"
        # would otherwise hit the media_control play_pause regex) and answers
        # past-tense variants Tier 0x's present-tense screen query misses.
        try:
            from api.services.activity_memory import activity_memory as _amem_q
            _recall = _amem_q.handle_query(transcript)
        except Exception as _am_exc:
            _recall = None
            logger.debug("[ACTIVITY_RECALL] skipped: %s", _am_exc)
        if _recall and _recall.get("response"):
            _rec_play = _recall.get("play")
            if _rec_play and _rec_play.get("url"):
                # Replay the remembered video right away so the music starts
                # while Xyron speaks the ack — same ordering as Tier 0f4.
                try:
                    await _run_tool("play_youtube_video", {
                        "url":   _rec_play["url"],
                        "title": _rec_play.get("title", ""),
                    }, goal=transcript, trace_id=_trace_id)
                except Exception as _rec_play_exc:
                    logger.warning("[ACTIVITY_RECALL_REPLAY_FAILED] %s", _rec_play_exc)
            _rec_resp = _recall["response"]
            logger.info("[ACTIVITY_RECALL_RESPONSE] action=%s response=%r",
                        _recall.get("action"), _rec_resp[:90])
            memory.add_assistant(_rec_resp, tool_name="activity_memory_recall")
            last_response_text = _rec_resp
            last_activity_t    = time.time()
            await _send(websocket, {"type": "response", "text": _rec_resp, "chunk": 1})
            _interrupted = await _tts_with_fallback(_rec_resp)
            if not _interrupted:
                if _tts_state["audio_sent"]:
                    logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=activity_recall")
                    await _send(websocket, {"type": "done"})
                    asyncio.create_task(_spawn_tts_watchdog("activity_recall"))
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
            else:
                is_speaking = False
                await _send(websocket, {"type": "listening"})
            return

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
            r'|what(?:\'s|\s+is)\s+this\s+(?:page|product|item|site|repo(?:sitory)?)\b'
            r'|what\s+am\s+i\s+(?:on|browsing|shopping\s+for|reading)\b'
            r')\b',
            re.IGNORECASE,
        )
        try:
            if _SCREEN_QUERY_RE.search(transcript):
                from api.services.screen_context_agent import screen_context_agent as _sca
                _screen_t0 = time.time()
                # world_state's browser/repository/product enrichment is kept
                # fresh by perception_engine's own independent observation
                # loop (started once at boot in main.py, ticks every 2.5s,
                # NOT gated behind background_scheduler's BACKGROUND_PAUSE —
                # it keeps running during an active voice session). A manual
                # blocking refresh_now() kick here used to add up to 1.5s to
                # every screen query and, on timeout, left its underlying
                # PowerShell call still running and holding ps_session's lock
                # (see ps_session.py's _LOCK_WAIT_CAP fix) — contending with
                # this same turn's window_context/explorer-path PS calls
                # right after it. world_state.get_context(refresh=False) is
                # at most ~2.5s stale, which is fine for "what's on screen".
                _screen_snap = await asyncio.to_thread(_sca.get_fresh)
                _screen_resp = _screen_snap.describe()

                # Real visual understanding: enrich the fast title/URL-based
                # description with what a vision model actually sees on
                # screen, bounded so a slow/unavailable OpenAI call never
                # blocks the turn past this cap. vision_perception.maybe_
                # capture() self-throttles to one real call per 30s
                # (perception/vision_perception.py), so rapid repeat screen
                # queries reuse the last real read instead of re-paying the
                # API round-trip every time.
                try:
                    from api.config import settings as _vcfg
                    if _vcfg.openai_api_key and _vcfg.openai_api_key.startswith("sk-"):
                        from api.services.perception import vision_perception as _vp
                        from api.services.screen_context_agent import compose_with_vision as _compose_vision
                        _vision_result = await asyncio.wait_for(
                            asyncio.to_thread(_vp.maybe_capture, "voice_screen_query", _vcfg.openai_api_key),
                            timeout=2.5,
                        )
                        if _vision_result and _vision_result.get("description"):
                            _screen_resp = _compose_vision(_screen_resp, _vision_result["description"])
                except asyncio.TimeoutError:
                    logger.info("[SCREEN_QUERY_VISION] timed out — using structured description only")
                except Exception as _vision_exc:
                    logger.debug("[SCREEN_QUERY_VISION] skipped: %s", _vision_exc)

                _lat["screen_context"] = (time.time() - _screen_t0) * 1000
                # Track what was just described so a later turn ("review it",
                # "open the README") can resolve the reference (Part 10) —
                # reuses ContextStack, not a separate GitHub memory system.
                try:
                    from api.services.context_stack import context_stack as _cs_screen
                    _cs_screen.update_from_screen(_screen_snap)
                except Exception:
                    pass
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

        # ── Tier 0x2: Repository follow-ups (Part 10) ─────────────────────────
        # "review it", "open the README", "show issues", "check the latest
        # commit", "what does this file do" — after a screen query already
        # identified a GitHub repository. Uses the ContextStack entity the
        # block above just pushed; no separate GitHub memory system.
        try:
            from api.services.screen_context_agent import match_repository_followup, handle_repository_followup
            _repo_action = match_repository_followup(transcript)
            if _repo_action:
                _repo_resp = await handle_repository_followup(_repo_action)
                logger.info("[REPO_FOLLOWUP_RESPONSE] action=%s response=%r", _repo_action, _repo_resp)
                memory.add_assistant(_repo_resp, tool_name="repository_followup")
                last_response_text = _repo_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _repo_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_repo_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("repository_followup"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
        except Exception as _rf_exc:
            logger.debug("[REPO_FOLLOWUP] skipped: %s", _rf_exc)

        # ── Tier 0x3: Product follow-ups ──────────────────────────────────────
        # "compare it", "find me something cheaper" — after a screen query
        # already described a product (Tier 0x, page_type == shopping). Real
        # search + real extracted prices, same shape as the repository
        # follow-up tier above, not a fast <200ms tier (a live search takes
        # a few seconds).
        try:
            from api.services.screen_context_agent import match_product_followup, handle_product_followup
            _product_action = match_product_followup(transcript)
            if _product_action:
                _product_resp = await handle_product_followup(_product_action)
                logger.info("[PRODUCT_FOLLOWUP_RESPONSE] action=%s response=%r", _product_action, _product_resp)
                memory.add_assistant(_product_resp, tool_name="product_followup")
                last_response_text = _product_resp
                last_activity_t    = time.time()
                await _send(websocket, {"type": "response", "text": _product_resp, "chunk": 1})
                _interrupted = await _tts_with_fallback(_product_resp)
                if not _interrupted:
                    if _tts_state["audio_sent"]:
                        await _send(websocket, {"type": "done"})
                        asyncio.create_task(_spawn_tts_watchdog("product_followup"))
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                else:
                    is_speaking = False
                    await _send(websocket, {"type": "listening"})
                return
        except Exception as _pf_exc:
            logger.debug("[PRODUCT_FOLLOWUP] skipped: %s", _pf_exc)

        # ── Tier 0x4: Bare "yes" confirming a screen-agent offer ──────────────
        # Generalizes the CONTINUE_INSTALL_WORDS bare-confirmation pattern
        # (already used for Microsoft Store installs in follow_up_resolver.py)
        # to whatever the last screen query actually offered — product
        # compare/cheaper, GitHub review — instead of only working for store
        # installs. Falls through silently (returns None) when there's
        # nothing pending, so a bare "yes" with no context still reaches the
        # normal LLM/tier routing below.
        try:
            from api.services.store_agent import CONTINUE_INSTALL_WORDS as _screen_yes_words
            _bare_screen_yes_re = re.compile(rf'^(?:{_screen_yes_words})\s*[.!]?\s*$', re.IGNORECASE)
            if _bare_screen_yes_re.match(transcript.strip()):
                from api.services.screen_context_agent import handle_screen_offer_confirmation
                _offer_resp = await handle_screen_offer_confirmation()
                if _offer_resp:
                    logger.info("[SCREEN_OFFER_CONFIRMED_RESPONSE] response=%r", _offer_resp)
                    memory.add_assistant(_offer_resp, tool_name="screen_offer_followup")
                    last_response_text = _offer_resp
                    last_activity_t    = time.time()
                    await _send(websocket, {"type": "response", "text": _offer_resp, "chunk": 1})
                    _interrupted = await _tts_with_fallback(_offer_resp)
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("screen_offer_followup"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _so_exc:
            logger.debug("[SCREEN_OFFER_CONFIRM] skipped: %s", _so_exc)

        # ── Tier 0x5: Generic screen follow-ups ("what is this / tell me more") ─
        # Catches plain descriptive follow-ups against ANY screen-agent
        # ContextStack entity (window/store_app/app/folder — not just
        # repository/product, which the tiers above already cover with
        # their own action-verb phrasing). Must run before intent-router /
        # DIRECT_AGENT_ROUTE below — without this, "what is this? tell me
        # more about it" had no ContextStack entity to resolve against and
        # got misrouted to a browser research agent that literally searched
        # the follow-up text itself.
        try:
            from api.services.screen_context_agent import match_generic_followup, handle_generic_followup
            if match_generic_followup(transcript):
                _generic_resp = await handle_generic_followup()
                if _generic_resp:
                    logger.info("[GENERIC_FOLLOWUP_RESPONSE] response=%r", _generic_resp)
                    memory.add_assistant(_generic_resp, tool_name="generic_screen_followup")
                    last_response_text = _generic_resp
                    last_activity_t    = time.time()
                    await _send(websocket, {"type": "response", "text": _generic_resp, "chunk": 1})
                    _interrupted = await _tts_with_fallback(_generic_resp)
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("generic_screen_followup"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return
        except Exception as _gf_exc:
            logger.debug("[GENERIC_FOLLOWUP] skipped: %s", _gf_exc)

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
                            }, goal=transcript, trace_id=_trace_id)
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

        # ── Tier 0f4: YouTube video candidate selection ──────────────────────
        # Fires when pending_video_candidates exist (search_youtube left an
        # ambiguous or "search"-intent list) and the user picks one ("play
        # the 2nd one", "this one") or asks to see more ("scroll down",
        # "show more"). Must run before Tier 0g so these short replies don't
        # fall through to a fresh web search or the LLM. Mirrors Tier 0f
        # (store candidate selection) above.
        _pending_video = _session_state.get("pending_video_candidates")
        if _pending_video:
            _video_expired = (time.time() - _pending_video.get("created_at", 0)) > 300
            if _video_expired:
                logger.info("[YOUTUBE_SELECTION_EXPIRED] source_query=%r",
                            _pending_video.get("source_query"))
                _session_state["pending_video_candidates"] = None
            else:
                _VIDEO_SCROLL_RE = re.compile(
                    r'\b(?:scroll\s+down|show\s+(?:me\s+|us\s+)?more|'
                    r'more\s+(?:videos|options|results)|'
                    r'next(?:\s+(?:ones?|page))?|see\s+(?:me\s+)?more)\b',
                    re.IGNORECASE,
                )
                _VIDEO_ORDINAL_RE = re.compile(
                    r'(?:^|\b)'
                    r'(?:(?:play|watch|choose|select|pick|go\s+with|the)\s+)?'
                    r'(?P<ord>first|second|third|fourth|fifth|'
                    r'number\s+(?:one|two|three|four|five)|'
                    r'(?:the\s+)?(?:1(?:st)?|2(?:nd)?|3(?:rd)?|4(?:th)?|5(?:th)?)'
                    r')'
                    r'(?:\s+one|\s+video)?\b',
                    re.IGNORECASE,
                )
                _VIDEO_THIS_ONE_RE = re.compile(
                    r'^(?:play\s+)?(?:this|that)(?:\s+one|\s+video)?\s*[.!]?\s*$',
                    re.IGNORECASE,
                )
                _VIDEO_ORDINAL_IDX = {
                    "first": 0, "1": 0, "1st": 0, "number one": 0,
                    "second": 1, "2": 1, "2nd": 1, "number two": 1,
                    "third": 2, "3": 2, "3rd": 2, "number three": 2,
                    "fourth": 3, "4": 3, "4th": 3, "number four": 3,
                    "fifth": 4, "5": 4, "5th": 4, "number five": 4,
                }

                if _VIDEO_SCROLL_RE.search(transcript):
                    _seen_urls = {c["url"] for c in _pending_video.get("candidates", [])}
                    try:
                        from api.tools.web_tools import youtube_scroll_more
                        _more = await asyncio.to_thread(youtube_scroll_more, _seen_urls)
                    except Exception as _sc_exc:
                        logger.warning("[YOUTUBE_SCROLL_FAILED] error=%s", _sc_exc)
                        _more = []
                    if _more:
                        _pending_video["candidates"] = _more
                        _pending_video["created_at"] = time.time()
                        _session_state["pending_video_candidates"] = _pending_video
                        _names = ", ".join(f"{i+1}. {c['title'][:50]}" for i, c in enumerate(_more[:5]))
                        _sc_resp = f"Here's more: {_names}. Say first, second, third — or scroll down again."
                    else:
                        _session_state["pending_video_candidates"] = None
                        _sc_resp = "That's all I've got for this search."
                    logger.info("[YOUTUBE_SCROLL_MORE] new_candidates=%d", len(_more))
                    memory.add_assistant(_sc_resp, tool_name="youtube_scroll_more")
                    last_response_text = _sc_resp
                    last_activity_t    = time.time()
                    await _send(websocket, {"type": "response", "text": _sc_resp, "chunk": 1})
                    _interrupted = await _tts_with_fallback(_sc_resp)
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=youtube_scroll")
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("youtube_scroll"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return

                _vord_idx = None
                _vord_m = _VIDEO_ORDINAL_RE.search(transcript)
                if _vord_m:
                    _vord_raw = _vord_m.group("ord").lower().strip()
                    for _k, _v in _VIDEO_ORDINAL_IDX.items():
                        if _k in _vord_raw or _vord_raw in _k:
                            _vord_idx = _v
                            break
                elif _VIDEO_THIS_ONE_RE.match(transcript.strip()):
                    _vord_idx = 0  # "this one" / "that one" — the top result just described

                if _vord_idx is not None:
                    _v_cands = _pending_video.get("candidates", [])
                    if _vord_idx < len(_v_cands):
                        _v_sel = _v_cands[_vord_idx]
                        logger.info("[YOUTUBE_SELECTION_ORDINAL_DETECTED] idx=%d title=%r",
                                    _vord_idx, _v_sel["title"])
                        _session_state["pending_video_candidates"] = None
                        _v_resp = await _run_tool("play_youtube_video", {
                            "url":   _v_sel["url"],
                            "title": _v_sel["title"],
                        }, goal=transcript, trace_id=_trace_id)
                        memory.add_assistant(_v_resp, tool_name="play_youtube_video")
                        last_response_text = _v_resp
                        last_activity_t    = time.time()
                        await _send(websocket, {"type": "response", "text": _v_resp, "chunk": 1})
                        _interrupted = await _tts_with_fallback(_v_resp)
                        if not _interrupted:
                            if _tts_state["audio_sent"]:
                                logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=youtube_ordinal")
                                await _send(websocket, {"type": "done"})
                                asyncio.create_task(_spawn_tts_watchdog("youtube_ordinal"))
                            else:
                                is_speaking = False
                                await _send(websocket, {"type": "listening"})
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                        return

                # ── Title-based selection: "play love me like you do" ─────────
                # STT-noise tolerant (Whisper mishears the leading verb, e.g.
                # "play" → "Learn") — pure matching lives in video_selection.py.
                # Live bug: this utterance matched no ordinal/this-one pattern,
                # fell to Tier4 (0.32 < 0.65) and the LLM answered with babble.
                _vt_match_fn = None
                _vt_anap_fn  = None
                try:
                    from api.services.video_selection import (
                        match_candidate as _vt_match_fn,
                        is_anaphoric_play as _vt_anap_fn,
                    )
                except Exception as _vt_imp_exc:
                    logger.debug("[YOUTUBE_TITLE_MATCH] import skipped: %s", _vt_imp_exc)

                _vt_sel = None
                if _vt_match_fn:
                    try:
                        _vt_sel = _vt_match_fn(transcript, _pending_video.get("candidates", []))
                    except Exception as _vt_exc:
                        logger.debug("[YOUTUBE_TITLE_MATCH] skipped: %s", _vt_exc)

                if _vt_sel:
                    _v_sel = _vt_sel["candidate"]
                    logger.info("[YOUTUBE_SELECTION_TITLE_MATCH] idx=%d score=%.2f title=%r",
                                _vt_sel["index"], _vt_sel["score"], _v_sel["title"])
                    _session_state["pending_video_candidates"] = None
                    _v_resp = await _run_tool("play_youtube_video", {
                        "url":   _v_sel["url"],
                        "title": _v_sel["title"],
                    }, goal=transcript, trace_id=_trace_id)
                    memory.add_assistant(_v_resp, tool_name="play_youtube_video")
                    last_response_text = _v_resp
                    last_activity_t    = time.time()
                    await _send(websocket, {"type": "response", "text": _v_resp, "chunk": 1})
                    _interrupted = await _tts_with_fallback(_v_resp)
                    if not _interrupted:
                        if _tts_state["audio_sent"]:
                            logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=youtube_title")
                            await _send(websocket, {"type": "done"})
                            asyncio.create_task(_spawn_tts_watchdog("youtube_title"))
                        else:
                            is_speaking = False
                            await _send(websocket, {"type": "listening"})
                    else:
                        is_speaking = False
                        await _send(websocket, {"type": "listening"})
                    return

                # ── Anaphoric: "play the song" / "play it" — no title given ───
                # The user refers to media by noun, not name. The disambiguation
                # list came from an ambiguous source query, so replay that query
                # with intent=play (autoplay top result) instead of guessing a
                # candidate — and it must fire BEFORE intent_router's
                # media_control regex would turn it into a play/pause toggle
                # (live bug: "No, I say play the song." toggled playback).
                if _vt_anap_fn and _pending_video.get("source_query"):
                    try:
                        _vt_is_anap = _vt_anap_fn(transcript)
                    except Exception:
                        _vt_is_anap = False
                    if _vt_is_anap:
                        logger.info("[YOUTUBE_SELECTION_ANAPHORIC] source_query=%r",
                                    _pending_video.get("source_query"))
                        _anap_q = _pending_video["source_query"]
                        _session_state["pending_video_candidates"] = None
                        _v_resp = await _run_tool("search_youtube", {
                            "query":  _anap_q,
                            "intent": "play",
                        }, goal=transcript, trace_id=_trace_id)
                        memory.add_assistant(_v_resp, tool_name="search_youtube")
                        last_response_text = _v_resp
                        last_activity_t    = time.time()
                        await _send(websocket, {"type": "response", "text": _v_resp, "chunk": 1})
                        _interrupted = await _tts_with_fallback(_v_resp)
                        if not _interrupted:
                            if _tts_state["audio_sent"]:
                                logger.info("[SPEAKING_FLAG_SET] is_speaking=True route=youtube_anaphoric")
                                await _send(websocket, {"type": "done"})
                                asyncio.create_task(_spawn_tts_watchdog("youtube_anaphoric"))
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
                    }, goal=transcript, trace_id=_trace_id)
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
                        "This is casual chit-chat, not a task — talk like a sharp, "
                        "easygoing friend, not a customer-support bot. Contractions, "
                        "light humor, and a bit of personality are all fine. React to "
                        "what the user actually said instead of a generic pleasantry. "
                        "1-2 sentences, spoken out loud — no markdown, no lists, no "
                        "'as an AI' hedging."
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
                            _shaped[:60], _tts_r.speed_hint)

                await _send(websocket, {"type": "response", "text": _shaped, "chunk": 1})

                # Same synth/send/state-tracking path every other route uses —
                # was previously a hand-rolled chunk loop here that duplicated
                # _tts_sequential's synth+send logic without its audio_sent/
                # playback-done bookkeeping, so the SPEAKING_FLAG_SET/watchdog
                # logic right after this branch was reading stale state left
                # over from whatever the last _tts_sequential call had set.
                await _tts_sequential(_shaped, _speed_override=_tts_r.speed_hint)

            except Exception as _exc:
                logger.warning("[VOICE_TRACE] emotional_response error: %s", _exc)
                _OFFLINE = {
                    "self_upgrade_pattern":  "That upgrade just landed. System noted — keep building.",
                    "frustration_pattern":   "I hear you. Send me the error and we'll tear it apart.",
                    "achievement_pattern":   "That's it. Done. Onto the next.",
                    "conversation_pattern":  "I'm doing well, thanks for asking — what's on your mind?",
                }
                _emotional_response_text = _OFFLINE.get(_guard.reason, "I'm here — go ahead.")
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
        decision = await _orch.decide(
            transcript, memory.history_for_llm(),
            detected_language=_session_state.get("ml_detected_lang", "en"),
            trace_id=_trace_id,
        )
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
        _silent_success: bool = False # set by TOOL branch; read in post-dispatch (Part 4)

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
                    # Never delete on a bare pronoun reference without asking —
                    # this used to execute instantly (params carried a hardcoded
                    # "confirmed": True) against whatever context_memory last
                    # remembered, including a bare location name. Route through
                    # the same confirm_required/pending_confirmation gate every
                    # other risky tool call uses instead.
                    paths = decision.tool_params.get("paths", [])
                    logger.info("[MEMORY_USED] delete_ref paths=%s", paths)
                    if not paths:
                        response_text = "I don't have anything remembered to delete."
                    elif len(paths) > 1:
                        response_text = "I'm not sure exactly which of those you mean — please name the file or folder directly."
                    else:
                        _mref_target = paths[0]
                        _mref_prompt = f"Delete {_mref_target}? Say yes to confirm or no to cancel."
                        _session_state["pending_confirmation"] = {
                            "tool":   "delete_file",
                            "params": {"path": _mref_target},
                            "prompt": _mref_prompt,
                            "created_at": time.time(),
                        }
                        logger.info("[CONFIRMATION_PENDING] tool=delete_file prompt=%r reason=memory_delete_ref",
                                    _mref_prompt[:60])
                        response_text = _mref_prompt
                elif tool:
                    logger.info("[MEMORY_USED] %s ref tool=%s", decision.reason, tool)
                    response_text = await _run_tool(tool, decision.tool_params, goal=transcript, trace_id=_trace_id)
                else:
                    response_text = "I couldn't resolve what you're referring to."
            except Exception as exc:
                logger.warning("[WS/session] memory_ref exec error: %s", exc)
                response_text = "I had trouble with that reference."
            await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
            interrupted = await _tts_with_fallback(response_text)

        # ── TOOL — matched tool execution ─────────────────────────────────────
        elif decision.action == ActionType.TOOL:
            # Router-safety net (Problem 3): reject a hallucinated command
            # even if it slipped past the STT-level check — e.g. a repeated
            # phrase that only became the tool's app_name/query param after
            # entity/tool correction. Checked on the resolved param text, not
            # audio duration, so wps/cps signals are disabled here (a large
            # audio_dur_ms neutralises them) and only the repetition/
            # diversity signals apply — "same verb dozens of times".
            from voice.hybrid_stt_router import detect_hallucination as _detect_halluc_router
            _tp_text = " ".join(str(v) for v in decision.tool_params.values() if isinstance(v, str))
            _halluc_router, _halluc_router_reason, _ = _detect_halluc_router(_tp_text or transcript, 60000.0)
            if _halluc_router:
                logger.warning("[ROUTER_SAFETY_REJECTED] tool=%s reason=%s", decision.tool_name, _halluc_router_reason)
                response_text = "I didn't catch that clearly. Please say it again."
                await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
                await _tts_with_fallback(response_text)
                await _send(websocket, {"type": "listening"})
                return

            # Reverted per explicit user feedback: going silent on success
            # ("visually self-evident" tools) meant the assistant gave zero
            # spoken confirmation for "open settings"/"open chrome"/etc,
            # which read as broken rather than polished. Every tool now
            # always speaks a completion phrase (see _build_completion_text
            # below) — kept past-tense/brief rather than the old "Opening
            # X." pre-action phrasing, but never fully silent.
            _SILENT_TOOLS: set = set()
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
            _is_silent_tool = decision.tool_name in _SILENT_TOOLS

            # ── Action-confidence invariant (real-mic Urdu test Issue 1B) ─────
            # By the time an utterance reaches dispatch it has already had
            # two recovery chances: the multilingual STT retry
            # (hybrid_stt_router) and semantic transcript correction. If the
            # routed action is STILL open_application for a target that no
            # layer can identify (object_resolver: unknown) AND the STT
            # confidence was poor, executing is a coin flip — the live
            # failure was tiny.en mishearing "Urdu mein baat karo" as
            # "Xyron, open barhao." (conf -0.67) and blindly launching
            # "barhao". Never execute that class of action: ask a concise
            # clarification instead. Known targets (chrome/settings/youtube/
            # existing folders) and clear transcripts are untouched.
            if decision.tool_name == "open_application":
                _gate_target = str(decision.tool_params.get("app_name", "") or "").strip()
                _gate_stt_conf = result.get("confidence") if isinstance(result, dict) else None
                _gate_obj = None
                if _gate_target:
                    try:
                        from api.services.object_resolver import resolve as _gate_resolve
                        _gate_obj = _gate_resolve(_gate_target)
                    except Exception:
                        _gate_obj = None
                _gate_target_unknown = (
                    _gate_obj is not None
                    and _gate_obj.object_type == "unknown"
                    and _gate_obj.confidence < 0.5
                )
                _gate_garbled = False
                try:
                    from api.tools.system_tools import is_garbled_app_name as _gate_garbled_fn
                    _gate_garbled = bool(_gate_garbled_fn(_gate_target))
                except Exception:
                    pass
                _gate_poor_stt = isinstance(_gate_stt_conf, (int, float)) and _gate_stt_conf < -0.3
                if _gate_garbled or (_gate_target_unknown and _gate_poor_stt):
                    logger.warning(
                        "[LOW_CONF_ACTION_BLOCKED] tool=open_application target=%r stt_conf=%s "
                        "obj_type=%s obj_conf=%s reason=%s",
                        _gate_target, _gate_stt_conf,
                        _gate_obj.object_type if _gate_obj else "?",
                        _gate_obj.confidence if _gate_obj else 0.0,
                        "garbled_target" if _gate_garbled else "unknown_target+poor_stt",
                    )
                    # ── Rescue via local_comprehension before giving up ────────
                    # Live-caught bug (2026-09-04 real backend log): for a
                    # noisy Urdu sentence, mixed_language_engine's deterministic
                    # canonicalization just prepends "open" to the WHOLE raw
                    # transcript without stripping filler words ("چلو پھرکام
                    # کرو YouTube کو کھولو" -> "open چلو پھرکام کرو YouTube
                    # کو"), and intent_router's generic "open X" catch-all then
                    # confidently (conf=1.00) matches that whole garbled string
                    # as an app name. Because that confidence clears
                    # orchestrator._route_intent's >=0.55 early-return
                    # threshold, local_comprehension's OpenAI/Qwen
                    # comprehension tier — the one actually built to parse
                    # noisy/compound non-English sentences — never got a
                    # chance to run at all; this gate caught the garbage
                    # afterward but only apologized instead of trying the
                    # tier that exists for exactly this case. Retry on the
                    # ORIGINAL pre-canonicalization transcript now, before
                    # giving up for real.
                    _gate_rescued = False
                    if _detected_ml not in ("en",):
                        try:
                            from api.services.local_comprehension import (
                                comprehend as _gate_comprehend,
                                validate_and_map as _gate_vmap,
                            )
                            from api.tools import registry as _gate_registry
                            _gate_lc = await asyncio.to_thread(
                                _gate_comprehend, _original_transcript, _detected_ml, _trace_id,
                            )
                            if _gate_lc:
                                _gate_lc = _gate_vmap(_gate_lc, _gate_registry)
                                if _gate_lc.mapped:
                                    logger.info(
                                        "[LOW_CONF_RESCUE_MAPPED] canonical=%r tool=%s conf=%.2f",
                                        _gate_lc.canonical_text, _gate_lc.tool_name,
                                        _gate_lc.route_confidence,
                                    )
                                    decision.tool_name   = _gate_lc.tool_name
                                    decision.tool_params = _gate_lc.tool_params
                                    decision.reason      = "low_conf_rescue_comprehension"
                                    _gate_rescued = True
                        except Exception as _gate_rescue_exc:
                            logger.debug("[LOW_CONF_RESCUE_FAILED] error=%s", _gate_rescue_exc)

                    if not _gate_rescued:
                        if _session_state.get("ml_resp_lang", "en") in ("ur", "ur_roman", "mixed"):
                            _clarify = "Sorry, ye command clear nahi hui — dobara bolenge?"
                        else:
                            _clarify = "Sorry, I didn't catch that clearly — could you say it again?"
                        await _send(websocket, {"type": "response", "text": _clarify, "chunk": 1})
                        await _tts_with_fallback(_clarify)
                        await _send(websocket, {"type": "listening"})
                        return
                    # else: decision now holds the rescued tool/params — fall
                    # through into the normal TOOL execution flow below.

            # Fix 3: Start tool execution IMMEDIATELY as a background task —
            # don't block on ACK synthesis before calling the OS.
            _tool_t0 = time.time()
            _tool_task = asyncio.create_task(
                _run_tool(decision.tool_name, decision.tool_params, goal=transcript, trace_id=_trace_id)
            )

            # Fix 2 / Part 2 polish: build the ack while the tool runs in
            # parallel, but NEVER wait on live synthesis for it — cache hit
            # or nothing. "Never delay execution waiting for acknowledgement
            # speech": if it isn't already cached, skip it outright instead
            # of the previous bounded 0.5s wait. Silent tools (obvious
            # visual result) skip ack-building entirely.
            # Phase 5.3: skip this tool-specific cached ack when the universal
            # immediate ack ("Got it."/"Sure.") already fired this turn —
            # otherwise the user hears two acks back to back ("Got it....
            # Opening Calculator.") where one used to be enough. The tool's
            # own completion message still gets spoken normally below via
            # response_text once _ack_spoken stays False.
            if (
                decision.tool_name in _slow_tools
                and not _is_silent_tool
                and _immediate_ack_state["task"] is None
            ):
                try:
                    from api.services.tts_cache_service import tts_cache as _tcc
                    _ack_text = _build_ack_text(
                        decision.tool_name, decision.tool_params,
                        emotion=_emo.emotion if _emo else None,
                    )
                    # ── Localize ACK for non-English sessions ─────────────────────────
                    # Use natural qwen generation first (varied, conversational),
                    # fall back to template-based localization if qwen is
                    # unavailable or too slow. This is what makes Xyron sound
                    # natural instead of robotic in Urdu.
                    _ack_ml_lang = _session_state.get("ml_resp_lang", "en")
                    if _ack_ml_lang != "en":
                        try:
                            from api.services.urdu_ack_generator import localize_with_fallback as _ack_loc_async
                            _loc_ack = await _ack_loc_async(_ack_text, _ack_ml_lang, decision.tool_name)
                            if _loc_ack and _loc_ack != _ack_text:
                                logger.info("[ACK_LOCALIZED] %r → %r", _ack_text, _loc_ack)
                                _ack_text = _loc_ack
                        except Exception:
                            pass
                        _ack_wav: Optional[bytes] = None
                        # Fast path: ur_roman/mixed deterministic tool acks
                        # go through the local Kokoro cache/synth
                        # (tts_cache_service.synthesize_or_cached_ml) — a
                        # 380-400ms warm/cached-instant path — instead of
                        # tts_router.synthesize()'s OpenAI-TTS-first routing
                        # for this lang class, which measured ~2.3-2.5s per
                        # ack live (2026-09-04). Pure Urdu script (lang ==
                        # "ur") deliberately falls through to the unchanged
                        # OpenAI/Edge-TTS path below — Kokoro can't render
                        # Nastaliq script intelligibly (see tts_cache_service
                        # .py's synthesize_or_cached_ml docstring).
                        if _ack_ml_lang in ("ur_roman", "mixed"):
                            try:
                                from api.services.tts_cache_service import tts_cache as _ml_tcc
                                _ack_fast_t0 = time.time()
                                _ack_wav = await asyncio.wait_for(
                                    asyncio.to_thread(
                                        _ml_tcc.synthesize_or_cached_ml, _ack_text, voice, speed, _ack_ml_lang,
                                    ),
                                    timeout=8.0,  # generous vs measured ~400ms warm / ~4.5s cold Kokoro load
                                )
                                _ack_synth_ms = (time.time() - _ack_fast_t0) * 1000
                                logger.info("[ACK_SYNTH_FAST_MS] ms=%.0f lang=%s text=%r engine=kokoro_cache",
                                            _ack_synth_ms, _ack_ml_lang, _ack_text)
                                _lat["tts"] = _ack_synth_ms
                            except Exception as _fast_exc:
                                logger.warning("[ACK_FAST_TTS_FAIL] lang=%s err=%s — falling back to OpenAI/Edge",
                                               _ack_ml_lang, _fast_exc)
                                _ack_wav = None
                        # Reached when: lang is "ur" (pure script — always
                        # uses this path), OR lang is ur_roman/mixed but the
                        # fast Kokoro path above genuinely failed (exception,
                        # not just a cache miss — a cache miss already
                        # synthesizes inline above and never leaves _ack_wav
                        # None). A real fast-path failure is rare enough
                        # that paying for one OpenAI/Edge-TTS call as a last
                        # resort — rather than silently going ack-less — is
                        # the right tradeoff; this is exactly "OpenAI TTS
                        # only when needed" from the target policy, not a
                        # reversion to OpenAI-first.
                        if _ack_wav is None:
                            try:
                                from voice.tts_router import synthesize as _ack_route
                                _ack_synth_t0 = time.time()
                                _ack_wav = await asyncio.wait_for(
                                    asyncio.to_thread(_ack_route, _ack_text, _ack_ml_lang, voice),
                                    timeout=30.0,
                                )
                                _ack_synth_ms = (time.time() - _ack_synth_t0) * 1000
                                logger.info("[ACK_SYNTH_ML_MS] ms=%.0f lang=%s text=%r",
                                            _ack_synth_ms, _ack_ml_lang, _ack_text)
                                _lat["tts"] = _ack_synth_ms
                            except Exception as _xa:
                                logger.warning("[ACK_ML_TTS_FAIL] lang=%s err=%s — no ACK audio", _ack_ml_lang, _xa)
                    else:
                        # English ACK: cache is keyed by (voice, text) — a
                        # hit is only ever returned for this exact session
                        # voice; any other voice's cached copy is a clean miss
                        # (see [TTS_CACHE_VOICE]/[TTS_VOICE_MATCH] inside get_by_text).
                        # Part 2 polish: cache-hit-only — a live Kokoro
                        # synthesis (even bounded) still delays this turn's
                        # completion behind ack audio. If it isn't already
                        # warm, skip the ack outright; the background
                        # cache-build call still runs so it's warm next time
                        # (fire-and-forget, never awaited).
                        _ack_wav = _tcc.get_by_text(_ack_text, voice)
                        if not _ack_wav:
                            logger.info("[ACK_SYNTH_SKIPPED] reason=not_cached text=%r", _ack_text)
                            asyncio.create_task(asyncio.to_thread(
                                _tcc.synthesize_or_cached, _ack_text, voice, speed
                            ))
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
            # Problem 5 fix: use the tool's own measured duration (set inside
            # _run_tool at [PERF_TOOL]), not a wall-clock delta from _tool_t0 —
            # that delta also spans the ack-synthesis block above and used to
            # misreport ack-wait time as tool_ms (e.g. tool_ms=9840 for a
            # 244ms tool while ack synthesis alone took 9817ms).
            #
            # Keyed by THIS turn's own _trace_id (see _last_tool_exec_ms's
            # declaration comment for the cross-turn contamination this
            # closes) — .pop() with a 0.0/True default so a lookup miss
            # (tool call failed before ever writing, or trace_id somehow
            # wasn't threaded through) degrades to the old "no data" value
            # instead of raising, and so the dict never grows unbounded
            # across a long-lived WS connection.
            _lat["tool"] = _last_tool_exec_ms.pop(_trace_id, 0.0)
            if _perf_rec:
                _perf_rec.set("tool", _lat["tool"])

            # Some tools signal the frontend to trigger a UI sequence.
            _FE_ACTIONS: dict[str, str] = {"takeover_mode": "TAKEOVER_START"}
            if decision.tool_name in _FE_ACTIONS:
                await _send(websocket, {
                    "type":   "frontend_action",
                    "action": _FE_ACTIONS[decision.tool_name],
                })
            _tool_succeeded = _last_tool_success.pop(_trace_id, True)
            if _ack_spoken:
                logger.info("[FINAL_RESPONSE_SKIPPED] reason=ack_already_spoken tool=%s", decision.tool_name)
                interrupted = False
            elif _is_silent_tool and _tool_succeeded:
                # Part 4 polish: visually self-evident success — Calculator
                # opened, Explorer opened — needs no narration. Failure is
                # never silent (falls through to the branch below instead,
                # since _tool_succeeded is False).
                logger.info("[FINAL_RESPONSE_SILENT] reason=silent_tool_success tool=%s", decision.tool_name)
                await _send(websocket, {"type": "listening"})
                is_speaking = False
                interrupted = False
                _silent_success = True
            else:
                # Part 3 polish: completion speech narrates what already
                # happened (past tense), never "Opening X." after the fact —
                # unless the tool failed, where its own message (e.g. "I
                # couldn't find that app") is the useful, informative one.
                # Only override for the specific tools _build_completion_text
                # actually models (the former ack-only set) — every other
                # tool (run_workflow, get_running_apps, etc.) already returns
                # its own specific, often workflow-authored message (e.g.
                # work_mode's "Work mode activated — VS Code and GitHub are
                # open."), which a generic "Done."/"All set." fallback would
                # otherwise silently clobber (live-measured regression).
                if _tool_succeeded and decision.tool_name in _slow_tools:
                    response_text = _build_completion_text(
                        decision.tool_name, decision.tool_params,
                        emotion=_emo.emotion if _emo else None,
                    )
                # User feedback: deterministic tool commands felt "scripted"
                # — this fast path never touched personality_engine at all
                # (only agent-driven flows like browser_agent/coding_agent
                # did). Apply the SAME existing personality/humor engine
                # here so mode switches ("funny mode", "jarvis mode", etc.
                # — already voice-triggerable via agent_intent_detector)
                # actually change how routine commands sound too, not just
                # agent narration. polish_response() is a <2ms pure-text
                # transform (no I/O/LLM), so this adds no latency.
                try:
                    from api.agents.personality.personality_engine import personality_engine as _pe_tool
                    response_text = _pe_tool.polish_response(
                        response_text,
                        context={"action": decision.tool_name, "event": "success" if _tool_succeeded else "error"},
                    )
                except Exception:
                    pass
                logger.info("[FINAL_RESPONSE_SENT] text=%r", response_text[:60])
                await _send(websocket, {"type": "response", "text": response_text, "chunk": 1})
                _tts_tool_t0 = time.time()
                interrupted = await _tts_with_fallback(response_text)
                _lat["tts"] = (time.time() - _tts_tool_t0) * 1000
                if _perf_rec:
                    _perf_rec.set("tts", _lat["tts"])

        # ── MULTI_STEP — compound command via planner ─────────────────────────
        elif decision.action == ActionType.MULTI_STEP:
            from brain.planner import planner as _planner, Plan as _Plan, PlanStep as _PlanStep
            from brain.orchestrator import orchestrator as _o2, ActionType as _AT

            async def _step_fn(step_text: str, hist: list[dict]) -> str:
                step_dec = await _o2.decide(step_text, hist)
                if step_dec.action == _AT.TOOL:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params, goal=step_text, trace_id=_trace_id)
                elif step_dec.action == _AT.MEMORY_REF and step_dec.tool_name:
                    return await _run_tool(step_dec.tool_name, step_dec.tool_params, goal=step_text, trace_id=_trace_id)
                else:
                    from api.services.response_pipeline import quick_response
                    return await quick_response(
                        step_text, hist, response_lang=_session_state.get("ml_resp_lang", "en"),
                    )

            # Urdu/Roman-Urdu compound commands arrive with the canonical
            # English steps ALREADY resolved (orchestrator.decide() —
            # either the deterministic mixed_language_engine.split_compound
            # path or the Tier-4 comprehend_multi() fallback; see
            # decision.reason for which). Build the Plan directly from that
            # list instead of _planner.build(transcript): transcript at
            # this point is still the ORIGINAL (possibly Urdu-script)
            # utterance, and planner.build()'s _SPLIT_RE only recognizes
            # English connectors ("and then"/"also") — re-splitting on it
            # would just fail to find >=2 parts and lose the compound
            # entirely. Each canonical step below still goes back through
            # _step_fn -> orchestrator.decide() -> intent_router/tool
            # registry exactly like every other planner step, English or
            # Urdu — no new execution path, same trust boundary.
            _canonical_steps = decision.context.get("canonical_steps")
            if _canonical_steps:
                plan = _Plan([_PlanStep(index=i, text=s) for i, s in enumerate(_canonical_steps, 1)])
                logger.info("[TRACE %s] [PLANNER_CANONICAL_STEPS] source=%s steps=%s",
                            _trace_id, decision.context.get("compound_source", "?"), _canonical_steps)
            else:
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
        _bp_transcript = _session_state.get("last_original_transcript", transcript)
        _bp_tool       = decision.tool_name or ""
        _bp_response   = response_text or ""
        async def _post_dispatch_brain() -> None:
            # Learning pattern detection
            if _bp_tool:
                try:
                    from api.services.learning_service import learning_service as _ls
                    # sqlite3.connect() + read/write on every turn — was
                    # running synchronously on the event loop thread, a real
                    # contributor to the EVENT_LOOP_BLOCKER lag spikes seen
                    # in [_post_dispatch_brain] task snapshots.
                    _lr = await asyncio.to_thread(_ls.record, _bp_transcript, _bp_tool)
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
        # ── Structured multilingual-turn summary — one line per turn, for
        # pairing real-speech manual test runs against exactly what the
        # pipeline decided (see MULTILINGUAL_MANUAL_VALIDATION.md). Every
        # field is read defensively (session_state/locals may be unset on
        # early-return turns that never reach this point) so this can never
        # itself break a turn.
        try:
            _mlt_resp_lang = _session_state.get("ml_resp_lang", "en")
            logger.info(
                "[ML_TURN] original=%r stt_model=%s stt_conf=%.2f detected_lang=%s "
                "normalized=%r fast_path=%s local_qwen_used=%s intent=%s "
                "response_lang=%s tts_engine=%s total_ms=%.0f",
                (_session_state.get("last_original_transcript", "") or "")[:80],
                locals().get("_stt_model", "?"),
                (result.get("confidence", -999.0) if isinstance(locals().get("result"), dict) else -999.0),
                _session_state.get("ml_detected_lang", "en"),
                (transcript or "")[:80],
                decision.tier <= 2 if "decision" in locals() and decision else "?",
                decision.context.get("local_qwen_used", False) if "decision" in locals() and decision else False,
                (decision.tool_name or decision.action.name) if "decision" in locals() and decision else "?",
                _mlt_resp_lang,
                "kokoro" if _mlt_resp_lang == "en" else "xtts_or_kokoro_fallback(see ML_TTS_WARM_HIT/XTTS_FALLBACK logs)",
                _turn_total_ms,
            )
        except Exception as _mlt_exc:
            logger.debug("[ML_TURN] logging skipped: %s", _mlt_exc)
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
            elif _silent_success:
                # Part 4 polish: no audio was sent on purpose (visually
                # self-evident success) — the TOOL branch already sent
                # "listening" itself. Not a synthesis failure; nothing left
                # to do here.
                pass
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
    # Root cause of the "code=1011 reason=keepalive ping timeout" incident:
    # the old greeting sequence awaited _synthesize_chunk's own 2-attempt,
    # up-to-25s-each retry design (~50s worst case) fully sequentially,
    # before this coroutine ever reached its next websocket.receive() — so
    # nothing on this connection could be sent or read for up to 50s.
    # Combined with concurrent GPU/CPU contention (a background thread
    # loading SentenceTransformer can hold the GIL long enough to starve the
    # event loop — see gpu_coordinator.py) this reliably exceeded uvicorn's
    # own protocol-level WebSocket ping/pong window (that literal message is
    # uvicorn/websockets' own default close reason, not application text).
    #
    # Fix: the greeting is optional UX, never a dependency. One short
    # attempt, a strict small timeout, and a try/finally that unconditionally
    # clears speaking state and starts listening no matter what happens.
    # State sequence: SESSION_ACTIVE -> GREETING_PENDING ->
    # {GREETING_PLAYING | GREETING_SKIPPED | GREETING_FAILED} -> LISTENING.
    import os as _os_greet
    _greeting_enabled = _os_greet.getenv("XYRON_GREETING_ENABLED", "true").strip().lower() not in ("0", "false", "no")
    # Reverted per explicit user feedback: the short "Hey."/"Yes?" pool read
    # as too terse — the full "Good morning, boss. I'm Xyron..." greeting is
    # what they actually want by default. "short" (with its non-repeating
    # pool) is still available via XYRON_GREETING_STYLE=short for anyone who
    # wants it.
    _greeting_style   = _os_greet.getenv("XYRON_GREETING_STYLE", "long").strip().lower()
    _greeting_timeout = float(_os_greet.getenv("XYRON_GREETING_TIMEOUT_S", "4.0"))
    # Layer-3 fallback phrase (Problem 1): short, universal, pre-warmed for
    # common session voices at critical boot (see _critical_boot_supervisor)
    # so it's reliably a cache hit even when the personalized greeting isn't.
    _greeting_fallback_text = "I'm listening."

    import datetime as _dt
    _hour = _dt.datetime.now().hour
    _tod  = "morning" if _hour < 12 else "afternoon" if _hour < 18 else "evening"
    _greet_name = preferred_name or "boss"
    if _greeting_style == "long":
        _greeting_text = f"Good {_tod}, {_greet_name}. I'm Xyron, ready and at your service. Just give the word."
    else:
        # UX polish: the short-style greeting used to default to "I'm
        # listening." — that's a frontend STATE, not a greeting, and it
        # read as robotic on every single activation. Pick from a natural,
        # time-of-day-aware, non-repeating pool instead (see
        # _pick_greeting_text). The fallback phrase below stays
        # "I'm listening." but only as the Layer-3 emergency fallback when
        # live synthesis times out — never the default happy path.
        _greeting_text = _pick_greeting_text(_tod, _greet_name)

    logger.info("[GREETING_REQUESTED] text=%r voice=%s style=%s", _greeting_text, voice, _greeting_style)
    logger.info("[VOICE_STATE_CHANGE] from=SESSION_ACTIVE to=GREETING_PENDING")
    _greeting_state = "GREETING_SKIPPED"
    try:
        if not _greeting_enabled:
            logger.info("[GREETING_SKIPPED] reason=disabled_by_config")
        else:
            is_speaking = True
            _g_wav: Optional[bytes] = None
            _g_text_used = _greeting_text
            from api.services.tts_cache_service import tts_cache as _tcc_greet
            # Register GPU/CPU priority for the duration of the greeting
            # attempt so gpu_coordinator-aware background consumers (model
            # warmups, semantic indexing) defer to it instead of contending
            # with Kokoro for the same GPU path — previously the greeting
            # never registered here at all, so its 4s timeout could start
            # (and expire) entirely underneath unrelated background load.
            from api.services.gpu_coordinator import voice_priority_begin as _vpb, voice_priority_end as _vpe
            logger.info("[GREETING_QUEUE_ENTERED]")
            _vpb("greeting")
            try:
                logger.info("[GREETING_GPU_ACQUIRED]")
                logger.info("[GREETING_SYNTH_START] text=%r timeout_s=%.1f", _greeting_text, _greeting_timeout)
                # A single bounded attempt — asyncio.wait_for abandons (stops
                # waiting on) the to_thread future on timeout; it cannot force
                # the underlying OS thread to stop mid-synthesis, but that
                # thread finishing later only warms the cache — it can no
                # longer block this session. Deliberately no second *live*
                # synth attempt inside the active session (see fallback below,
                # which is cache-only and therefore cannot block).
                try:
                    from api.routers.voice import _kokoro_executor as _kko_exec_greet
                    _g_wav = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            _kko_exec_greet, _tcc_greet.synthesize_or_cached, _greeting_text, voice, speed
                        ),
                        timeout=_greeting_timeout,
                    )
                    if _g_wav:
                        logger.info("[GREETING_SYNTH_DONE] bytes=%d", len(_g_wav))
                except asyncio.TimeoutError:
                    logger.warning("[GREETING_SYNTH_TIMEOUT] timeout_s=%.1f", _greeting_timeout)
                except Exception as _g_exc:
                    logger.warning("[GREETING_SYNTH_FAILED] error=%s", _g_exc)

                # Layer 3: primary attempt failed/timed out — try a cache-ONLY
                # lookup (never live synth, so this cannot block or reintroduce
                # the 1011 risk) of the fallback phrase in the SAME voice.
                # get_by_text is voice-scoped and refuses cross-voice hits, so
                # this can never play a cached nova clip inside an onyx session.
                if not _g_wav and _greeting_text != _greeting_fallback_text:
                    _fb_wav = _tcc_greet.get_by_text(_greeting_fallback_text, voice)
                    if _fb_wav:
                        _g_wav = _fb_wav
                        _g_text_used = _greeting_fallback_text
                        logger.info("[GREETING_FALLBACK_USED] text=%r voice=%s", _greeting_fallback_text, voice)
            finally:
                _vpe("greeting")

            if _g_wav and websocket.client_state == WebSocketState.CONNECTED:
                await _send(websocket, {
                    "type":  "audio",
                    "data":  base64.b64encode(_g_wav).decode(),
                    "chunk": 1,
                    "total": 1,
                    "final": True,
                    "text":  _g_text_used,
                })
                logger.info("[GREETING_AUDIO_PACKET_SENT] bytes=%d text=%r", len(_g_wav), _g_text_used)
                _greeting_state = "GREETING_PLAYING"
                # Short, bounded wait for the frontend's tts_done ack — purely
                # cosmetic (lets playback finish before the UI flips to
                # "listening"); never allowed to approach uvicorn's own
                # keepalive window regardless of what the frontend does.
                _deadline = time.time() + min(6.0, _greeting_timeout + 4.0)
                while websocket.client_state == WebSocketState.CONNECTED and time.time() < _deadline:
                    try:
                        _d = await asyncio.wait_for(websocket.receive(), timeout=1.0)
                        if _d.get("type") == "websocket.disconnect":
                            break
                        _txt = _d.get("text")
                        if _txt:
                            try:
                                if json.loads(_txt).get("type") == "tts_done":
                                    logger.info("[GREETING_FRONTEND_ACK]")
                                    logger.info("[GREETING_PLAYBACK_DONE]")
                                    logger.info("[GREETING_DONE]")
                                    break
                            except Exception:
                                pass
                    except asyncio.TimeoutError:
                        continue
                    except WebSocketDisconnect:
                        break
            else:
                _greeting_state = "GREETING_FAILED"
                logger.info("[GREETING_SKIPPED] reason=synth_unavailable")
    finally:
        is_speaking = False
        logger.info("[GREETING_STATE_CLEARED] state=%s", _greeting_state)
        last_activity_t = time.time()  # reset idle timer after greeting completes
        if websocket.client_state == WebSocketState.CONNECTED:
            await _send(websocket, {"type": "listening"})
        logger.info("[VOICE_STATE_CHANGE] from=GREETING to=LISTENING")
        logger.info("[SESSION_LISTENING_READY] session_instance_id=%s", session_instance_id)

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
    # Task 5 note: the application-level {"type":"ping"} sent below is a
    # best-effort heartbeat the frontend currently no-ops on (see
    # useVoiceWS.ts `case 'ping': break`) — it is NOT the mechanism behind
    # "code=1011 reason=keepalive ping timeout". That message is uvicorn's
    # own protocol-level WebSocket ping/pong (the `websockets` library's
    # default close reason), handled below the ASGI application layer and
    # not directly instrumentable from here. The real fix for that is
    # keeping this process's event loop responsive — see gpu_coordinator.py
    # and the greeting rewrite above — so uvicorn's own keepalive coroutine
    # is never starved of the GIL long enough to miss its window. These
    # markers cover the app-level heartbeat for observability.
    _last_keepalive_recv_t = time.time()
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                data = await asyncio.wait_for(
                    websocket.receive(), timeout=SESSION_TIMEOUT + 5.0
                )
                _now_ka = time.time()
                _lag_ka = _now_ka - _last_keepalive_recv_t
                _last_keepalive_recv_t = _now_ka
                logger.debug("[SESSION_KEEPALIVE_RECEIVED] lag_s=%.1f", _lag_ka)
                if _lag_ka > SESSION_TIMEOUT:
                    logger.warning("[SESSION_KEEPALIVE_LAG] lag_s=%.1f threshold_s=%.1f", _lag_ka, SESSION_TIMEOUT)
            except asyncio.TimeoutError:
                logger.info("[SESSION_KEEPALIVE_TIMEOUT_CAUSE] no_message_received_for_s=%.1f is_speaking=%s",
                           SESSION_TIMEOUT + 5.0, is_speaking)
                if not await _send(websocket, {"type": "ping"}):
                    break
                logger.debug("[SESSION_KEEPALIVE_SENT]")
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
        try:
            _wa_announce_task.cancel()
        except Exception:
            pass
        try:
            from api.services import voice_announcer as _voice_announcer_cleanup
            _voice_announcer_cleanup.unregister(asyncio.get_event_loop())
        except Exception:
            pass
        try:
            _stuck_watchdog_task.cancel()
        except Exception:
            pass
        import time as _t_diag
        _session_age_s = round(_t_diag.time() - (last_activity_t or 0), 1)
        logger.info(
            "[SESSION_DESTROY_DIAGNOSTIC] "
            "session_instance_id=%s "
            "session_active=False "
            "voice_connected=%s "
            "mic_active=%s "
            "chunks_received=%d "
            "speech_started=%s "
            "is_speaking=%s "
            "pcm_buffer_depth=%d "
            "idle_age_s=%.1f "
            "ws_state=%s",
            session_instance_id,
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
        # Release GPU priority — background semantic/classifier loads that
        # deferred during this session (gpu_coordinator.py) may now proceed.
        try:
            from api.services.gpu_coordinator import voice_priority_end as _gpu_voice_end
            _gpu_voice_end(reason=f"session:{session_instance_id}")
        except Exception:
            pass
        try:
            from voice.wake_word_service import wake_word_service as _wws_cleanup
            _wws_cleanup.set_session_active(False)
            _wws_cleanup.reset_cooldown()
            logger.info("[WAKE_LISTENING_RESUMED]")
        except Exception:
            pass
        logger.info("[VOICE_SESSION_CLOSED] session_instance_id=%s", session_instance_id)
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
