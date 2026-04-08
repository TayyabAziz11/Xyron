"""Whisper speech-to-text service using faster-whisper (local, free)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_model = None
_model_size = "base"  # tiny | base | small | medium | large-v3


def _get_model():
    """Lazy-load the Whisper model (downloads on first use, cached after)."""
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper '%s' model (first run may download)...", _model_size)
            _model = WhisperModel(_model_size, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded.")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
    return _model


def transcribe_audio(audio: np.ndarray, sample_rate: int = 16000, language: str = "en") -> dict:
    """Transcribe a numpy float32 audio array. Returns dict with text and metadata.

    Args:
        audio: float32 numpy array at 16kHz
        sample_rate: audio sample rate (should be 16000 for Whisper)
        language: ISO language code (e.g. 'en', 'ar')

    Returns:
        {"text": "...", "language": "en", "duration": 5.2, "segments": [...]}
    """
    model = _get_model()

    # faster-whisper accepts numpy arrays directly
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        language=language if language != "auto" else None,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    segment_list = []
    full_text_parts = []
    for seg in segments:
        segment_list.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    full_text = " ".join(full_text_parts).strip()

    return {
        "text": full_text,
        "language": info.language,
        "duration": round(info.duration, 2) if hasattr(info, "duration") else None,
        "segments": segment_list,
    }


def transcribe_file(wav_path: Path, language: str = "en") -> dict:
    """Transcribe a WAV file. Convenience wrapper around transcribe_audio."""
    model = _get_model()
    segments, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        language=language if language != "auto" else None,
        vad_filter=True,
    )
    full_text_parts = []
    segment_list = []
    for seg in segments:
        full_text_parts.append(seg.text.strip())
        segment_list.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    return {
        "text": " ".join(full_text_parts).strip(),
        "language": info.language,
        "segments": segment_list,
    }


def set_model_size(size: str) -> None:
    """Change model size. Options: tiny, base, small, medium, large-v3.
    Call before first transcription. Bigger = more accurate but slower.
    """
    global _model, _model_size
    _model = None  # reset so it reloads
    _model_size = size
    logger.info("Whisper model size set to '%s'", size)
