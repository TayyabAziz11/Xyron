"""
Text-to-Speech service — 3-tier fallback chain.

Tier 1  Kokoro (kokoro-onnx)   — local ONNX, ~100 ms first-word, offline
Tier 2  edge-tts               — Microsoft cloud, high quality, async
Tier 3  pyttsx3 / espeak-ng    — local final fallback, always available

Public API (unchanged):
    synthesize_speech(text)  → Optional[bytes]  (WAV, blocking)
    synthesize_kokoro(text)  → Optional[bytes]  (WAV, blocking)
    synthesize_edge(text)    → Optional[bytes]  (MP3, async)
    is_tts_available()       → bool
    get_tts_info()           → dict
"""
from __future__ import annotations

import asyncio
import io
import logging
import tempfile
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Tier 1: Kokoro ────────────────────────────────────────────────────────────

_kokoro_lock    = threading.Lock()
_kokoro_engine  = None
_kokoro_ready   = False
_kokoro_tried   = False

_KOKORO_VOICE   = "af_heart"   # default voice — warm, natural English
_KOKORO_SPEED   = 1.0
_KOKORO_REPO    = "hexgrad/Kokoro-82M-ONNX"
_KOKORO_MODEL   = "kokoro-v0_19.onnx"
_KOKORO_VOICES  = "voices.bin"


def _load_kokoro() -> bool:
    global _kokoro_engine, _kokoro_ready, _kokoro_tried
    if _kokoro_tried:
        return _kokoro_ready
    _kokoro_tried = True

    try:
        from kokoro_onnx import Kokoro
        from huggingface_hub import hf_hub_download

        logger.info("[TTS/Kokoro] Downloading model (first-run only)…")
        model_path  = hf_hub_download(_KOKORO_REPO, _KOKORO_MODEL)
        voices_path = hf_hub_download(_KOKORO_REPO, _KOKORO_VOICES)

        _kokoro_engine = Kokoro(model_path, voices_path)
        _kokoro_ready  = True
        logger.info("[TTS/Kokoro] Ready (voice=%s)", _KOKORO_VOICE)
        return True

    except Exception as exc:
        logger.info("[TTS/Kokoro] Not available: %s", exc)
        return False


def synthesize_kokoro(text: str, voice: str = _KOKORO_VOICE, speed: float = _KOKORO_SPEED) -> Optional[bytes]:
    """
    Synthesize speech with Kokoro ONNX (local, ~100 ms on CPU).
    Returns WAV bytes or None on failure.
    """
    if not text or not text.strip():
        return None
    text = text.strip()[:500]

    with _kokoro_lock:
        if not _load_kokoro():
            return None
        try:
            import soundfile as sf
            samples, sr = _kokoro_engine.create(text, voice=voice, speed=speed, lang="en-us")
            buf = io.BytesIO()
            sf.write(buf, samples, sr, format="WAV")
            return buf.getvalue()
        except Exception as exc:
            logger.warning("[TTS/Kokoro] synthesis failed: %s", exc)
            return None


# ── Tier 2: edge-tts ─────────────────────────────────────────────────────────

_EDGE_VOICE = "en-US-AriaNeural"


async def synthesize_edge(text: str, voice: str = _EDGE_VOICE) -> Optional[bytes]:
    """
    Synthesize speech with edge-tts (Microsoft cloud, async).
    Returns MP3 bytes or None on failure.
    """
    if not text or not text.strip():
        return None
    text = text.strip()[:500]

    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks) if chunks else None
    except Exception as exc:
        logger.warning("[TTS/edge-tts] failed: %s", exc)
        return None


def synthesize_edge_sync(text: str, voice: str = _EDGE_VOICE) -> Optional[bytes]:
    """Synchronous wrapper for synthesize_edge."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, synthesize_edge(text, voice))
                return fut.result(timeout=15)
        return loop.run_until_complete(synthesize_edge(text, voice))
    except Exception as exc:
        logger.warning("[TTS/edge-tts] sync wrapper failed: %s", exc)
        return None


# ── Tier 3: pyttsx3 ──────────────────────────────────────────────────────────

_pyttsx3_lock    = threading.Lock()
_pyttsx3_engine  = None
_pyttsx3_ready   = False
_pyttsx3_tried   = False


def _init_pyttsx3() -> bool:
    global _pyttsx3_engine, _pyttsx3_ready, _pyttsx3_tried
    if _pyttsx3_tried:
        return _pyttsx3_ready
    _pyttsx3_tried = True
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 165)
        engine.setProperty("volume", 0.9)
        voices = engine.getProperty("voices")
        if voices:
            en = [v for v in voices if "en" in (v.id or "").lower()]
            if en:
                engine.setProperty("voice", en[0].id)
        _pyttsx3_engine = engine
        _pyttsx3_ready  = True
        logger.info("[TTS/pyttsx3] Ready")
        return True
    except Exception as exc:
        logger.info("[TTS/pyttsx3] Not available: %s", exc)
        return False


def _synthesize_pyttsx3(text: str) -> Optional[bytes]:
    text = text.strip()[:500]
    with _pyttsx3_lock:
        if not _init_pyttsx3():
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            _pyttsx3_engine.save_to_file(text, tmp_path)
            _pyttsx3_engine.runAndWait()
            data = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)
            return data
        except Exception as exc:
            logger.warning("[TTS/pyttsx3] failed: %s", exc)
            return None


# ── Public API ────────────────────────────────────────────────────────────────

def synthesize_speech(
    text: str,
    rate: Optional[int] = None,
    volume: Optional[float] = None,
) -> Optional[bytes]:
    """
    Convert text to audio bytes (3-tier fallback: Kokoro → edge-tts → pyttsx3).
    Returns WAV bytes (Kokoro/pyttsx3) or MP3 bytes (edge-tts), or None.
    Blocking. Suitable for use in worker threads.
    """
    if not text or not text.strip():
        return None

    # Tier 1: Kokoro (local)
    result = synthesize_kokoro(text)
    if result:
        return result

    # Tier 2: edge-tts (cloud)
    result = synthesize_edge_sync(text)
    if result:
        return result

    # Tier 3: pyttsx3
    return _synthesize_pyttsx3(text)


def is_tts_available() -> bool:
    """True if any TTS tier can produce audio."""
    return _init_pyttsx3() or True   # edge-tts always callable (network permitting)


def get_tts_info() -> dict:
    """Return active TTS engine metadata."""
    kokoro_ok  = _kokoro_ready or _load_kokoro()
    pyttsx3_ok = _pyttsx3_ready or _init_pyttsx3()

    tier = "kokoro" if kokoro_ok else "edge-tts" if True else "pyttsx3" if pyttsx3_ok else None
    return {
        "available": True,
        "tiers": {
            "kokoro":   kokoro_ok,
            "edge_tts": True,
            "pyttsx3":  pyttsx3_ok,
        },
        "active_tier": tier,
        "voice": _KOKORO_VOICE if kokoro_ok else _EDGE_VOICE,
    }
