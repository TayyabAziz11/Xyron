"""
Whisper STT service — faster-whisper with GPU auto-detection.

Configuration (environment variables):
  WHISPER_MODEL                 Model size: tiny | base | small | medium | large-v3
                                Default: "small" (better than "base", still fast on GPU)
  WHISPER_LANGUAGE              ISO code ("en", "ur") or "auto" for multilingual.
                                Default: "auto" — detects language per utterance.
  WHISPER_CONFIDENCE_THRESHOLD  avg_logprob floor; segments below this are noise.
                                Default: -1.0  (0=perfect, -1=uncertain, <-2=noise)

GPU:
  Auto-detected. If torch+CUDA is available → float16 GPU.
  Otherwise → CPU int8. No config needed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import re
import numpy as np

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_MODEL_SIZE  = os.getenv("WHISPER_MODEL", "small")
_LANGUAGE    = os.getenv("WHISPER_LANGUAGE", "auto")   # "auto" → None (multilingual)
_CONF_THRESH = float(os.getenv("WHISPER_CONFIDENCE_THRESHOLD", "-1.0"))

# Bias Whisper toward system command vocabulary — no API cost, just text.
_VOICE_INITIAL_PROMPT = (
    "Voice assistant command for Windows. "
    "C drive, D drive, E drive, F drive, folder, settings, volume, brightness, battery, WiFi. "
    "Open, create, delete, close, screenshot, maximize, minimize, lock, shutdown."
)

# Post-processing: fix common phonetic mistakes from Whisper before routing.
_CORRECTIONS: list[tuple[re.Pattern, str | object]] = [
    # Drive letter mishears
    (re.compile(r'\bsee\s+drive\b',         re.I), 'C drive'),
    (re.compile(r'\bsea\s+drive\b',         re.I), 'C drive'),
    (re.compile(r'\bsi\s+drive\b',          re.I), 'C drive'),
    (re.compile(r'\bthe\s+c\s+drive\b',     re.I), 'C drive'),
    (re.compile(r'\bdee\s+drive\b',         re.I), 'D drive'),
    (re.compile(r'\bee\s+drive\b',          re.I), 'E drive'),
    (re.compile(r'\beff\s+drive\b',         re.I), 'F drive'),
    # Inline "C:" or "C/" notation → spoken form
    (re.compile(r'\b([a-fA-F])\s*[:/]',    re.I),
     lambda m: f'{m.group(1).upper()} drive '),
    # Folder/directory typos
    (re.compile(r'\bfould?er\b',            re.I), 'folder'),
    (re.compile(r'\bdirec?t(?:o|a)ry\b',   re.I), 'directory'),
    # Common command normalizations
    (re.compile(r'\bcreate\s+a\s+new\s+folder\b', re.I), 'create folder'),
    (re.compile(r'\bopen\s+the\s+settings?\b',     re.I), 'open settings'),
    (re.compile(r'\bopen\s+file\s+explorer\b',     re.I), 'open file explorer'),
    # Volume numbers (word → digit)
    (re.compile(r'\bvolume\s+to\s+(?:one\s+)?hundred\b',  re.I), 'volume to 100'),
    (re.compile(r'\bvolume\s+to\s+fifty\b',                re.I), 'volume to 50'),
    (re.compile(r'\bvolume\s+to\s+twenty[\s-]?five\b',     re.I), 'volume to 25'),
    (re.compile(r'\bvolume\s+to\s+seventy[\s-]?five\b',    re.I), 'volume to 75'),
    # Brightness numbers
    (re.compile(r'\bbrightness\s+to\s+(?:one\s+)?hundred\b', re.I), 'brightness to 100'),
    (re.compile(r'\bbrightness\s+to\s+fifty\b',               re.I), 'brightness to 50'),
]


def _apply_corrections(text: str) -> str:
    """Apply rule-based corrections to fix Whisper phonetic transcription errors."""
    for pattern, repl in _CORRECTIONS:
        text = pattern.sub(repl, text)  # type: ignore[arg-type]
    return text.strip()

_model = None


# ── Hardware detection ────────────────────────────────────────────────────────

def _detect_device() -> tuple[str, str]:
    """Return (device, compute_type) for faster-whisper."""
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            logger.info("[Whisper] GPU detected: %s — using float16", gpu)
            return "cuda", "float16"
    except ImportError:
        pass
    logger.info("[Whisper] No CUDA — using CPU int8")
    return "cpu", "int8"


# ── Model load ────────────────────────────────────────────────────────────────

def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        device, compute_type = _detect_device()
        logger.info("[Whisper] Loading '%s' on %s (%s)…", _MODEL_SIZE, device, compute_type)
        _model = WhisperModel(_MODEL_SIZE, device=device, compute_type=compute_type)
        logger.info("[Whisper] Model ready.")
    return _model


def preload_model() -> None:
    """Eagerly load the model (call from startup thread to avoid cold-start lag)."""
    try:
        _get_model()
    except Exception as exc:
        logger.warning("[Whisper] Preload failed: %s", exc)


def set_model_size(size: str) -> None:
    """Hot-swap model size — reloads on next transcription call."""
    global _model, _MODEL_SIZE
    _model = None
    _MODEL_SIZE = size
    logger.info("[Whisper] Model size set to '%s'", size)


# ── Confidence filtering ──────────────────────────────────────────────────────

def _filter_segments(segments) -> list:
    """Drop segments below avg_logprob threshold (breathing, noise, hallucinations)."""
    kept = []
    for seg in segments:
        if seg.avg_logprob >= _CONF_THRESH:
            kept.append(seg)
        else:
            logger.debug(
                "[Whisper] Noise segment dropped (logprob=%.2f): %r",
                seg.avg_logprob, seg.text[:40],
            )
    return kept


# ── Public API ────────────────────────────────────────────────────────────────

def transcribe_audio(
    audio: np.ndarray,
    sample_rate: int = 16000,
    language: Optional[str] = None,
    fast: bool = False,
) -> dict:
    """
    Transcribe a float32 numpy array at 16kHz.

    Args:
        audio:    float32 numpy array
        language: ISO code | "auto" | None  (None → WHISPER_LANGUAGE env var)
        fast:     True → beam_size=1, no VAD (40% faster, for real-time session path)

    Returns:
        {text, language, confidence, duration, segments}
    """
    model = _get_model()
    lang = _resolve_lang(language) or "en"  # force English — skip language detection

    segments_raw, info = model.transcribe(
        audio,
        beam_size=1 if fast else 3,
        language=lang,
        vad_filter=not fast,          # skip VAD in fast mode (we do our own)
        vad_parameters={"min_silence_duration_ms": 300},
        temperature=0.0,              # greedy — deterministic + fastest
        condition_on_previous_text=False,
        initial_prompt=_VOICE_INITIAL_PROMPT,  # bias toward command vocabulary
    )

    segments  = _filter_segments(segments_raw)
    raw_text  = " ".join(s.text.strip() for s in segments).strip()
    full_text = _apply_corrections(raw_text)

    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)

    if full_text != raw_text:
        logger.info("[Whisper] correction applied: %r → %r", raw_text, full_text)
    logger.info("[Whisper] transcript=%r lang=%s conf=%.2f",
                full_text[:80], info.language, avg_conf)

    return {
        "text":       full_text,
        "language":   info.language,
        "confidence": round(avg_conf, 3),
        "duration":   round(info.duration, 2) if hasattr(info, "duration") else None,
        "segments":   [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ],
    }


def transcribe_file(wav_path: Path, language: Optional[str] = None) -> dict:
    """Transcribe a file (WAV/MP3/WebM). Confidence filtering applied."""
    model = _get_model()
    lang  = _resolve_lang(language)

    segments_raw, info = model.transcribe(
        str(wav_path),
        beam_size=5,
        language=lang,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    segments  = _filter_segments(segments_raw)
    full_text = " ".join(s.text.strip() for s in segments).strip()
    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)

    return {
        "text":       full_text,
        "language":   info.language,
        "confidence": round(avg_conf, 3),
        "segments":   [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments
        ],
    }


def _resolve_lang(language: Optional[str]) -> Optional[str]:
    """Resolve language param to what faster-whisper expects (None = auto-detect)."""
    lang = language or _LANGUAGE
    return None if lang in (None, "auto") else lang
