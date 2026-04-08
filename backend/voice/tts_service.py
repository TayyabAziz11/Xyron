"""
Text-to-Speech service for AI Operator.

Primary: pyttsx3 (wraps espeak on Linux — fast, no model download)
Fallback: plain text response with a note that TTS is unavailable

Usage:
    from backend.voice.tts_service import synthesize_speech
    audio_bytes = synthesize_speech("Your email draft is ready")
"""
from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Thread lock — pyttsx3 engine is not thread-safe
_lock = threading.Lock()
_engine = None
_tts_available = False
_init_attempted = False


def _init_engine():
    """Lazy-initialize pyttsx3 engine. Returns True if successful."""
    global _engine, _tts_available, _init_attempted
    if _tts_available:
        return True
    if _init_attempted:
        return False  # already tried and failed
    _init_attempted = True

    try:
        import pyttsx3
        engine = pyttsx3.init()

        # Configure voice properties
        engine.setProperty('rate', 165)    # words per minute (default ~200)
        engine.setProperty('volume', 0.9)  # 0.0 to 1.0

        # Try to pick a decent voice
        voices = engine.getProperty('voices')
        if voices:
            # Prefer english voices
            en_voices = [
                v for v in voices
                if 'en' in (v.id or '').lower() or 'english' in (v.name or '').lower()
            ]
            if en_voices:
                engine.setProperty('voice', en_voices[0].id)

        _engine = engine
        _tts_available = True
        logger.info("TTS engine initialized (pyttsx3 / %s)", engine.getProperty('voice'))
        return True

    except Exception as exc:
        logger.warning(
            "TTS not available: %s\n"
            "Install espeak-ng for Linux: sudo apt-get install espeak-ng\n"
            "Then: pip install pyttsx3",
            exc,
        )
        _tts_available = False
        return False


def synthesize_speech(
    text: str,
    rate: Optional[int] = None,
    volume: Optional[float] = None,
) -> Optional[bytes]:
    """
    Convert text to WAV audio bytes.

    Args:
        text: Text to speak (will be truncated to 500 chars for speed)
        rate: Speech rate in words-per-minute (default 165)
        volume: Volume 0.0–1.0 (default 0.9)

    Returns:
        WAV audio as bytes, or None if TTS is unavailable.
    """
    if not text or not text.strip():
        return None

    # Limit length to keep latency low
    text = text.strip()[:500]

    with _lock:
        if not _init_engine():
            return None

        try:
            if rate is not None:
                _engine.setProperty('rate', max(80, min(300, rate)))
            if volume is not None:
                _engine.setProperty('volume', max(0.0, min(1.0, volume)))

            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name

            _engine.save_to_file(text, tmp_path)
            _engine.runAndWait()

            audio_bytes = Path(tmp_path).read_bytes()
            Path(tmp_path).unlink(missing_ok=True)

            logger.debug("TTS synthesized %d chars → %d bytes", len(text), len(audio_bytes))
            return audio_bytes

        except Exception as exc:
            logger.error("TTS synthesis failed: %s", exc)
            return None


def is_tts_available() -> bool:
    """Check whether TTS is functional."""
    return _init_engine()


def get_tts_info() -> dict:
    """Return TTS engine metadata."""
    if not _init_engine():
        return {"available": False, "engine": None, "voice": None}

    try:
        return {
            "available": True,
            "engine": "pyttsx3",
            "voice": str(_engine.getProperty('voice')),
            "rate": _engine.getProperty('rate'),
            "volume": _engine.getProperty('volume'),
        }
    except Exception:
        return {"available": True, "engine": "pyttsx3"}
