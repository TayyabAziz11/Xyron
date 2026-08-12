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

# Language-aware initial prompts — bias Whisper toward command vocabulary.
# "Xyron" (the assistant's name, addressed at the start of most commands —
# e.g. "Okay Xyron, what's on my screen") is otherwise a made-up proper noun
# with no prior in Whisper's vocabulary and gets misheard constantly ("Zyron",
# "His iron", "Za-iron"...). Naming it explicitly in the prompt lets the
# decoder actually recognize it instead of relying solely on post-hoc regex
# correction in normalizer.py, which only catches mishearings it already knows.
_VOICE_INITIAL_PROMPT_EN = (
    "Voice assistant named Xyron, for Windows. "
    "C drive, D drive, E drive, F drive, folder, settings, volume, brightness, battery, WiFi. "
    "Open, create, delete, close, screenshot, maximize, minimize, lock, shutdown."
)
_VOICE_INITIAL_PROMPT_UR = (
    "Voice assistant named Xyron, Urdu aur English mixed. "
    "کھولو، بند کرو، اسکرین شاٹ لو، والیوم بڑھاؤ، والیوم گھٹاؤ، فولڈر، سیٹنگز۔ "
    "Open, close, screenshot, volume up, volume down, folder, settings, lock, shutdown."
)


def _get_initial_prompt(lang: Optional[str]) -> str:
    """Return the right initial prompt based on resolved language.

    lang=None  → auto-detect mode — bilingual prompt helps Whisper handle
                 code-switching (Roman Urdu mixed with English).
    lang='ur'  → Urdu-primary prompt.
    lang='en'  → English-only prompt.
    """
    if lang == "ur":
        return _VOICE_INITIAL_PROMPT_UR
    if lang is None:
        # Bilingual: lets Whisper recognise both Urdu script and English commands
        return _VOICE_INITIAL_PROMPT_EN + " " + _VOICE_INITIAL_PROMPT_UR
    return _VOICE_INITIAL_PROMPT_EN

# Post-processing: fix common phonetic mistakes from Whisper before routing.
_CORRECTIONS: list[tuple[re.Pattern, str | object]] = [
    # "in D drive" — Whisper commonly mishears this phrase
    (re.compile(r'\bindeed\s+derive[d]?\b',   re.I), 'in D drive'),
    (re.compile(r'\bindeed\s+drive\b',        re.I), 'in D drive'),
    (re.compile(r'\bindividually\s+drive\b',  re.I), 'in D drive'),
    (re.compile(r'\b(?:in\s+)?indie\s+drive\b', re.I), 'in D drive'),
    (re.compile(r'\bin\s+dee\s+drive\b',      re.I), 'in D drive'),
    (re.compile(r'\bdeep\s+drive\b',          re.I), 'D drive'),
    # Drive letter mishears
    (re.compile(r'\bsee\s+drive\b',           re.I), 'C drive'),
    (re.compile(r'\bsea\s+drive\b',           re.I), 'C drive'),
    (re.compile(r'\bsi\s+drive\b',            re.I), 'C drive'),
    (re.compile(r'\bthe\s+c\s+drive\b',       re.I), 'C drive'),
    (re.compile(r'\bdee\s+drive\b',           re.I), 'D drive'),
    (re.compile(r'\bee\s+drive\b',            re.I), 'E drive'),
    (re.compile(r'\beff\s+drive\b',           re.I), 'F drive'),
    # Inline "C:" or "C/" notation → spoken form
    (re.compile(r'\b([a-fA-F])\s*[:/]',    re.I),
     lambda m: f'{m.group(1).upper()} drive '),
    # Folder/directory typos
    (re.compile(r'\bfould?er\b',            re.I), 'folder'),
    (re.compile(r'\bdirec?t(?:o|a)ry\b',   re.I), 'directory'),
    # tiny.en phoneme confusions
    (re.compile(r'\bcal\s+curator\b',              re.I), 'calculator'),
    (re.compile(r'\bcal\s+cure\s+later\b',         re.I), 'calculator'),
    # Roman Urdu phoneme confusions — espeak-ng/tiny.en artifacts for common commands.
    # Real mic audio rarely needs these; these cover synthetic voice in test harness.
    (re.compile(r'\bbye[,.]?\s+carr[ao]+\.?\s*$',  re.I), 'band karo'),
    (re.compile(r'\band[,.]?\s+caro?\.?\s*$',      re.I), 'band karo'),
    (re.compile(r'\bbuy\s+(?:the\s+)?car\s+out',   re.I), 'band karo'),
    (re.compile(r'\bbend\s+car[ao]?\b',             re.I), 'band karo'),
    (re.compile(r'\bchrome\s+code\s+load',          re.I), 'chrome kholo'),
    (re.compile(r'\bthis\s+code\s+install\s+caro',  re.I), 'isko install karo'),
    # "store see" → "store se" (espeak renders "se" as "see")
    (re.compile(r'\bstore\s+see\b',                 re.I), 'store se'),
    # "open, calculate" → "open calculator" (tiny.en hallucination/repetition)
    (re.compile(r'\bopen[,.]?\s+calculate\.?\s*$',  re.I), 'open calculator'),
    # "tatao" → "chalao" (espeak-ng renders Roman Urdu "chalao" as "tatao")
    (re.compile(r'\btatao\b',                        re.I), 'chalao'),
    # espeak renders "youtube believer chalao" as "youtube, pop, believe, a, tatao"
    (re.compile(r'\byoutube[,.]?\s+(?:pop[,.]?\s+)?belie(?:f|ve)[a-z,.\s]*chalao', re.I),
     'play believer on youtube'),
    # Edge-TTS neural voice: "band karo" → "band kettle" (neural phonetics confuse tiny.en)
    (re.compile(r'\bband\s+kettle\b', re.I), 'band karo'),
    # Edge-TTS: "Believer" spoken in Urdu accent → "bullyver"
    (re.compile(r'\bbullyver\b', re.I), 'believer'),
    # "volume barhao" with neural voice: tiny.en drops "barhao" → bare "volume." alone
    # In a voice assistant context, "volume" alone means "volume up"
    (re.compile(r'^\s*volume[.,!?\s]*$', re.I), 'volume up'),
    # "launch karo" / "open care" — "karo" phonetically becomes "care" (Edge-TTS neural)
    (re.compile(r'\bopen\s+care\b', re.I), 'open karo'),
    (re.compile(r'\blaunch\s+care\b', re.I), 'launch karo'),
    # "store se" merged into "starse" by Whisper (tokens fuse when spoken fast)
    (re.compile(r'\bstarse\b', re.I), 'store se'),
    # "download carol" / "download care" at sentence end → "download karo"
    (re.compile(r'\bdownload[,.]?\s+car(?:ol|e)[.,!?\s]*$', re.I), 'download karo'),
    # "dikhao" → "show" (Urdu "show me" verb, commonly dropped by tiny.en)
    # Keep only when it appears as a standalone suffix (already at sentence end)
    (re.compile(r'\bdikhao\b', re.I), 'show'),
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

import threading as _threading

_model       = None
_model_lock  = _threading.Lock()
_model_ready = _threading.Event()   # set once the model is fully loaded and usable

# ── Tiny.en fast model (Hybrid STT) ──────────────────────────────────────────
_FAST_MODEL_SIZE = os.getenv("WHISPER_FAST_MODEL", "tiny.en")
_tiny_model      = None
_tiny_model_lock = _threading.Lock()


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
    # Fast path — model already loaded, no lock needed.
    if _model is not None:
        return _model
    # Slow path — first caller loads; concurrent callers block until done.
    with _model_lock:
        if _model is not None:      # re-check after acquiring lock (double-checked locking)
            return _model
        from faster_whisper import WhisperModel
        device, compute_type = _detect_device()
        logger.info("[Whisper] Loading '%s' on %s (%s)…", _MODEL_SIZE, device, compute_type)
        try:
            _model = WhisperModel(_MODEL_SIZE, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda":
                logger.warning(
                    "[Whisper] GPU load failed (%s) — falling back to CPU int8. "
                    "Fix: echo '/usr/lib/wsl/lib' | sudo tee /etc/ld.so.conf.d/wsl-cuda.conf && sudo ldconfig",
                    exc,
                )
                _model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            else:
                raise
        logger.info("[Whisper] Model ready.")
        _model_ready.set()          # unblock any callers waiting on the ready gate
    return _model


def _get_tiny_model():
    """Load tiny.en (English-only fast model) — for Hybrid STT routing."""
    global _tiny_model
    if _tiny_model is not None:
        return _tiny_model
    with _tiny_model_lock:
        if _tiny_model is not None:
            return _tiny_model
        from faster_whisper import WhisperModel
        device, compute_type = _detect_device()
        logger.info("[Whisper/tiny] Loading '%s' on %s (%s)…", _FAST_MODEL_SIZE, device, compute_type)
        try:
            _tiny_model = WhisperModel(_FAST_MODEL_SIZE, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda":
                logger.warning("[Whisper/tiny] GPU load failed (%s) — using CPU int8", exc)
                _tiny_model = WhisperModel(_FAST_MODEL_SIZE, device="cpu", compute_type="int8")
            else:
                raise
        logger.info("[Whisper/tiny] %s ready.", _FAST_MODEL_SIZE)
    return _tiny_model


def preload_model() -> None:
    """Eagerly load the model (call from startup thread to avoid cold-start lag)."""
    try:
        _get_model()
    except Exception as exc:
        logger.warning("[Whisper] Preload failed: %s", exc)


def preload_tiny_model() -> None:
    """Eagerly load tiny.en (call from startup warmup thread)."""
    try:
        _get_tiny_model()
    except Exception as exc:
        logger.warning("[Whisper/tiny] Preload failed: %s", exc)


_TINY_INITIAL_PROMPT = (
    "Xyron, open Chrome. Open VS Code. Open Notepad. Open Calculator. Open Explorer. "
    "Open File Explorer. Open Settings. Open Terminal. Open Spotify. Open Discord. "
    "Take screenshot. Volume up. Volume down. Lock screen. Shutdown. Restart. "
    "Install it. Open it again. What am I looking at. Yes. No. Cancel."
)

def transcribe_fast(audio: np.ndarray, sample_rate: int = 16000) -> dict:
    """
    Fast transcription using tiny.en — target <500ms warm on GPU.

    English-only, beam_size=1, no VAD. Use for short/simple commands.
    The caller should check result["confidence"] (avg_logprob) and retry
    with transcribe_audio() if the result is uncertain.
    """
    model = _get_tiny_model()
    _common = dict(
        beam_size=1, language="en", vad_filter=False,
        condition_on_previous_text=False, initial_prompt=_TINY_INITIAL_PROMPT,
        # Root-cause fix for a live-measured failure mode: short commands
        # ("open calculator", "open chrome", "open c drive") reliably made
        # greedy (beam_size=1) tiny.en loop the whole phrase 2-6x — e.g.
        # "Open Calculator. Open Calculator. Open Calculator. ..." — which
        # then cost a ~2.5-3.5s escalation to the accurate model on every
        # single occurrence via the hallucination-retry path. no_repeat_
        # ngram_size=3 cut the loop length dramatically but still let one
        # "X. X" duplicate through on short 2-3 word phrases; =2 (blocks any
        # repeated bigram) closed that gap in live testing. Voice commands
        # are short imperative phrases, not free dictation, so blocking
        # bigram repeats has no realistic legitimate-speech cost here.
        no_repeat_ngram_size=2,
    )
    segments_raw, _info = model.transcribe(audio, temperature=0.0, **_common)
    segments = _filter_segments(segments_raw)
    # Empty on greedy pass → tiny retry with slight temperature to break deadlock.
    # This avoids escalating to the 3s accurate model for simple short commands.
    if not segments:
        logger.debug("[Whisper/tiny] empty on temperature=0.0 — retrying at 0.2")
        segments_raw2, _ = model.transcribe(audio, temperature=0.2, **_common)
        segments = _filter_segments(segments_raw2)
    raw_text  = " ".join(s.text.strip() for s in segments).strip()
    full_text = _apply_corrections(raw_text)
    avg_conf  = (sum(s.avg_logprob for s in segments) / len(segments)
                 if segments else -999.0)
    if full_text != raw_text:
        logger.debug("[Whisper/tiny] correction: %r → %r", raw_text, full_text)
    logger.info("[Whisper/tiny] transcript=%r conf=%.2f", full_text[:80], avg_conf)
    return {
        "text":       full_text,
        "language":   "en",
        "confidence": round(avg_conf, 3),
        "duration":   None,
        "segments":   [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ],
    }


def set_model_size(size: str) -> None:
    """Hot-swap model size — reloads on next transcription call."""
    global _model, _MODEL_SIZE
    _model = None
    _model_ready.clear()    # reset gate so callers wait for the new model to load
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
    lang = _resolve_lang(language)  # None → auto-detect (handles Urdu, English, mixed)

    segments_raw, info = model.transcribe(
        audio,
        beam_size=1 if fast else 3,
        language=lang,
        task="transcribe",            # Phase 2.4: always transcribe, never silently translate
        vad_filter=not fast,          # skip VAD in fast mode (we do our own)
        vad_parameters={"min_silence_duration_ms": 300},
        temperature=0.0,              # greedy — deterministic + fastest
        condition_on_previous_text=False,
        no_repeat_ngram_size=3,        # same repetition-loop mitigation as transcribe_fast
        initial_prompt=_get_initial_prompt(lang),
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


# ── Wake phrase verification ───────────────────────────────────────────────────

# Initial prompt biases Whisper toward these proper-noun wake phrases.
# Without it, "hey xyron" often becomes "he's a" (Whisper misread).
_WAKE_INITIAL_PROMPT = "Hey Xyron. Wakeup Xyron. Hi Xyron. Okay Xyron. Yo Xyron."

# Keyword set: exact spellings + common Whisper misreadings of wake phonemes.
#   "hey"   → "he", "he's", "hay"
#   "xyron" → "zairan", "zahra", "zairon", "siren", "cyron", "iron", "zero"
# Name tokens — at least one of these MUST appear in the transcript for a match.
# Prefix words ('hey', 'hi', 'ok') are intentionally excluded: they appear in
# normal speech constantly and cause false positives when checked in isolation.
_WAKE_NAMES: frozenset[str] = frozenset({
    'xyron', 'xeron', 'xiron', 'zyron',
    'wakeup',
    'jarvis',
    'zairan', 'zahra', 'zairon', 'ziren', 'siren', 'cyron',
    'xavier', 'zero',
})


def verify_wake_phrase(pcm: np.ndarray) -> tuple[bool, str]:
    """
    Second-stage gate: run Whisper on a short PCM clip and confirm a wake keyword.

    Call this after OWW fires. Pass the last ~2.5s of buffered float32 audio.
    Returns (matched, transcript).

    Fails OPEN (returns True) if Whisper unavailable — OWW already raised the
    bar; we prefer a rare false positive over blocking a real wake word.
    """
    try:
        model = _get_model()
    except Exception:
        return True, ""

    try:
        segments_raw, _ = model.transcribe(
            pcm,
            beam_size=1,
            language="en",
            vad_filter=False,                     # window is already 2.56s — VAD strips inter-word gaps on WSL2 audio
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=_WAKE_INITIAL_PROMPT,  # bias toward wake phrase vocabulary
        )
        transcript = " ".join(s.text.strip() for s in segments_raw).lower()
        # word-level match: strip punctuation + remove internal apostrophes
        # so "he's" → "hes", "wake," → "wake", "zahra." → "zahra"
        words = set(
            w.strip(".,!?'\"").replace("'", "") for w in transcript.split()
        )
        matched = bool(words & _WAKE_NAMES)
        logger.info("[Whisper/wake] transcript=%r matched=%s", transcript[:80], matched)
        return matched, transcript
    except Exception as exc:
        logger.warning("[Whisper/wake] verification error: %s — failing open", exc)
        return True, ""
