"""
XTTS v2 multilingual TTS service.

Uses Coqui XTTS-v2 for non-English synthesis (Urdu, Hindi, Arabic, Roman Urdu).
Loaded lazily — NOT imported during English-only startup.

Install:  pip install TTS
Model:    tts_models/multilingual/multi-dataset/xtts_v2  (auto-downloaded on first use)

Voice reference:
  Place a 6–10s clean WAV at assets/voices/xyron_multilingual_reference.wav
  to use voice cloning. If missing, XTTS uses its built-in default speaker.

Logs:
  [XTTS_INIT_START]              — model load begins
  [XTTS_INIT_DONE]               — model ready (ms=...)
  [XTTS_SYNTH_START]             — synthesis requested
  [XTTS_SYNTH_DONE]              — synthesis complete
  [XTTS_SYNTH_FAIL]              — synthesis error
  [XTTS_VOICE_REFERENCE_FOUND]   — reference WAV present, voice cloning active
  [XTTS_VOICE_REFERENCE_MISSING] — no reference, using default speaker
  [XTTS_DEFAULT_VOICE_USED]      — default speaker selected
  [ML_TTS_COLD_START]            — first synthesis (model load + synth)
  [ML_TTS_WARM_HIT]              — model already loaded (cache hit)
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Reference voice WAV — place here for consistent voice identity across languages
_VOICE_REF = (
    Path(__file__).resolve().parent.parent.parent
    / "assets" / "voices" / "xyron_multilingual_reference.wav"
)

# XTTS model ID (override via env var for custom models)
_XTTS_MODEL = os.getenv("XTTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

# XTTS language codes — Coqui's supported identifiers
# See: https://coqui.ai/cpml.txt
_LANG_MAP: dict[str, str] = {
    # NOT native Urdu synthesis — XTTS-v2 has no Urdu language model. This
    # borrows its Arabic phoneme set as the closest available approximation
    # (shared script family) and is a known, documented limitation, not a
    # claim of correctness. See section 14 of the multilingual plan — a
    # genuine Urdu voice (e.g. an online neural TTS with real ur-PK voices)
    # is future work, intentionally out of scope for this local-first phase.
    "ur":       "ar",
    "ur_roman": "en",    # Roman Urdu — English phoneme model sounds natural
    "hi":       "hi",
    "ar":       "ar",
    "mixed":    "en",
    "en":       "en",
}

_model       = None
_model_lock  = threading.Lock()
_model_ready = threading.Event()
_model_tried = False
_bg_load_started = False  # prevents duplicate background load threads


def _load_model() -> object | None:
    global _model, _model_tried

    if _model_ready.is_set():
        return _model

    with _model_lock:
        if _model_ready.is_set():
            return _model
        if _model_tried and _model is None:
            return None     # already failed; don't retry every call

        _model_tried = True
        logger.info("[XTTS_INIT_START] model=%s", _XTTS_MODEL)
        t0 = time.monotonic()
        try:
            # Compatibility shim: transformers 5.x removed BeamSearchScorer
            # Coqui TTS 0.22.0 references it in stream_generator.py at import time.
            # Stub it out so XTTS-v2 synthesis (which uses sampling, not beam search)
            # still works.
            import transformers as _tf
            if not hasattr(_tf, "BeamSearchScorer"):
                class _BeamSearchScorerStub:
                    def __init__(self, *a, **kw):
                        raise RuntimeError("BeamSearchScorer not available in transformers 5.x")
                _tf.BeamSearchScorer = _BeamSearchScorerStub
            if not hasattr(_tf, "ConstrainedBeamSearchScorer"):
                class _ConstrainedStub:
                    def __init__(self, *a, **kw):
                        raise RuntimeError("ConstrainedBeamSearchScorer not in transformers 5.x")
                _tf.ConstrainedBeamSearchScorer = _ConstrainedStub
            # Also patch generation.utils — SampleOutput removed in transformers 5.x
            try:
                from transformers.generation import utils as _gen_utils
                from transformers.generation.utils import GenerateOutput
                if not hasattr(_gen_utils, "SampleOutput"):
                    _gen_utils.SampleOutput = GenerateOutput
                if not hasattr(_gen_utils, "BeamSearchOutput"):
                    _gen_utils.BeamSearchOutput = GenerateOutput
                if not hasattr(_gen_utils, "GreedySearchOutput"):
                    _gen_utils.GreedySearchOutput = GenerateOutput
            except Exception:
                pass
            import os as _os
            _os.environ.setdefault("COQUI_TOS_AGREED", "1")
            from TTS.api import TTS  # type: ignore[import]
            _model = TTS(_XTTS_MODEL)
            ms = (time.monotonic() - t0) * 1000
            logger.info("[XTTS_INIT_DONE] ms=%.0f", ms)
            _model_ready.set()
        except ImportError:
            logger.warning(
                "[XTTS_INIT_START] Coqui TTS not installed. "
                "Run: pip install TTS"
            )
        except Exception as exc:
            logger.error("[XTTS_INIT_START] model load failed: %s", exc)

    return _model


def _auto_generate_voice_reference() -> bool:
    """Auto-generate voice reference WAV from Kokoro nova if the file is missing."""
    ref = Path(_VOICE_REF)
    if ref.exists() and ref.stat().st_size > 10000:
        return True
    ref.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[XTTS_VOICE_REFERENCE_GENERATING] auto-generating from Kokoro nova")
    try:
        from voice.tts_service import synthesize_speech
        _sample_text = (
            "Hello, I'm Xyron, your AI voice assistant. "
            "I can open apps, search the web, control your computer, "
            "and help you with everyday tasks — all by voice."
        )
        wav_bytes = synthesize_speech(_sample_text)
        if wav_bytes and len(wav_bytes) > 10000:
            ref.write_bytes(wav_bytes)
            logger.info("[XTTS_VOICE_REFERENCE_GENERATED] path=%s bytes=%d",
                        _VOICE_REF, len(wav_bytes))
            return True
        logger.warning("[XTTS_VOICE_REFERENCE_GENERATION_FAILED] Kokoro returned empty bytes")
    except Exception as exc:
        logger.warning("[XTTS_VOICE_REFERENCE_GENERATION_FAILED] %s", exc)
    return False


def _speaker_ref() -> Path | None:
    if _VOICE_REF.exists():
        logger.info("[XTTS_VOICE_REFERENCE_FOUND] path=%s", _VOICE_REF)
        return _VOICE_REF
    # Try to auto-generate from Kokoro before falling back to no reference
    if _auto_generate_voice_reference():
        logger.info("[XTTS_VOICE_REFERENCE_FOUND] path=%s (auto-generated)", _VOICE_REF)
        return _VOICE_REF
    logger.warning("[XTTS_VOICE_REFERENCE_MISSING] path=%s", _VOICE_REF)
    logger.info("[XTTS_DEFAULT_VOICE_USED]")
    return None


def _ensure_bg_load() -> None:
    """Start XTTS model load in a background thread (idempotent).

    Routed through gpu_coordinator.defer_background_job so the actual model
    load (torch model construction — CPU/GPU heavy) waits for any in-flight
    voice-session STT/TTS to go idle first, rather than competing with it —
    the same contention pattern that previously caused the 7-90s TTS lag
    (see commit 6c04904). This only defers the LOAD; the thread itself
    starts immediately and is a no-op cost until it actually begins loading.
    """
    global _bg_load_started
    if _model_ready.is_set() or _model_tried:
        return
    with _model_lock:
        if _bg_load_started:
            return
        _bg_load_started = True

    def _deferred_load() -> None:
        try:
            from api.services.gpu_coordinator import defer_background_job
            defer_background_job("xtts_preload", timeout=30.0)
        except Exception:
            pass
        _load_model()

    t = threading.Thread(target=_deferred_load, daemon=True, name="xtts-auto-preload")
    t.start()
    logger.info("[XTTS_BG_LOAD_STARTED] background load triggered (gpu_coordinator-gated)")


def is_ready() -> bool:
    """Whether XTTS has finished loading and can synthesize right now."""
    return _model_ready.is_set()


def synthesize(text: str, lang: str) -> Optional[bytes]:
    """
    Synthesize speech using XTTS-v2.

    If the model is not yet loaded (cold start), kicks off background loading
    (idempotent — safe to call every turn) and returns None immediately so the
    caller falls back to Kokoro English for THIS turn. Once loaded (typically
    within the first few non-English turns of a session, sooner if
    voice_ws.py's early trigger already started the load ahead of the first
    TTS call — see [ML_LANG_DETECT] in voice_ws.py), subsequent calls use
    XTTS. Previously this never called the loader at all outside the
    MULTILINGUAL_TTS_PRELOAD=true startup path, so without that env var XTTS
    never loaded for the life of the process — every non-English reply
    silently used the Kokoro English voice, not just the first one.

    Args:
        text: text to speak
        lang: language code from language_detector ("ur", "hi", "ar", "ur_roman", "mixed")

    Returns:
        WAV bytes at 24000 Hz, or None if model unavailable.
    """
    if not _model_ready.is_set():
        _ensure_bg_load()
        logger.info(
            "[ML_TTS_COLD_START] lang=%s — model not ready, Kokoro fallback in use for this turn "
            "(degraded mode: ur/mixed spoken in the English voice until XTTS finishes loading)",
            lang,
        )
        return None  # Caller falls back to Kokoro for this turn only

    logger.info("[ML_TTS_WARM_HIT] lang=%s", lang)
    logger.info("[XTTS_SYNTH_START] lang=%s text=%r", lang, text[:60])
    t0 = time.monotonic()

    model = _load_model()
    if model is None:
        logger.warning("[XTTS_SYNTH_FAIL] model unavailable (not installed or load failed)")
        return None

    try:
        from voice.pronunciation_preprocessor import preprocess as _pp
        text = _pp(text)
    except Exception:
        pass

    xtts_lang = _LANG_MAP.get(lang, "en")
    ref       = _speaker_ref()

    try:
        if ref:
            samples = model.tts(
                text=text,
                language=xtts_lang,
                speaker_wav=str(ref),
            )
        else:
            speakers = getattr(model, "speakers", None) or []
            speaker  = speakers[0] if speakers else None
            kwargs   = {"speaker": speaker} if speaker else {}
            samples  = model.tts(text=text, language=xtts_lang, **kwargs)

        wav = _to_wav(np.array(samples, dtype=np.float32), sample_rate=24000)
        ms  = (time.monotonic() - t0) * 1000
        logger.info("[XTTS_SYNTH_DONE] lang=%s ms=%.0f bytes=%d", lang, ms, len(wav))
        return wav

    except Exception as exc:
        ms = (time.monotonic() - t0) * 1000
        logger.error("[XTTS_SYNTH_FAIL] lang=%s ms=%.0f error=%s", lang, ms, exc)
        return None


def preload_background() -> None:
    """
    Optionally pre-warm XTTS in a background thread.
    Only triggered when MULTILINGUAL_TTS_PRELOAD=true is set in the environment.
    Does NOT block CORE_READY or FULL_READY.
    Requires ~2GB free VRAM; do NOT enable on machines with <2GB headroom.
    """
    if os.getenv("MULTILINGUAL_TTS_PRELOAD", "").lower() != "true":
        return
    _ensure_bg_load()
    logger.info("[XTTS_INIT_START] background preload started (MULTILINGUAL_TTS_PRELOAD=true)")


def _to_wav(samples: np.ndarray, sample_rate: int = 24000) -> bytes:
    """Convert float32 numpy array to PCM 16-bit WAV bytes."""
    import wave
    buf = io.BytesIO()
    pcm = (samples * 32767.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()
