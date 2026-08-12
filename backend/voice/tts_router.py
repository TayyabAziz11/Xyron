"""
TTS Router — routes synthesis to Kokoro (English) or XTTS-v2 (multilingual).

Rules:
  lang == "en"  → Kokoro via tts_service.synthesize_speech()
  lang != "en"  → XTTS-v2 via xtts_service.synthesize()
  XTTS fails    → fallback to Kokoro English + logs [XTTS_FALLBACK_TO_KOKORO_ENGLISH]

LRU cache: last 50 (text, lang) pairs cached in memory to avoid re-synthesis
of repeated short responses ("Chrome khol raha hoon.", "Band kar raha hoon.", etc.)

Logs:
  [TTS_ROUTER_SELECTED]             — engine + language chosen
  [TTS_ROUTER_CACHE_HIT]            — LRU cache served the result
  [XTTS_FALLBACK_TO_KOKORO_ENGLISH] — XTTS unavailable, Kokoro used instead
  [XTTS_FALLBACK_TO_TEXT]           — both engines failed, text-only response
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def synthesize(text: str, lang: str = "en", voice: str = "nova") -> Optional[bytes]:
    """
    Route TTS synthesis based on language.

    Args:
        text:  text to synthesize
        lang:  output language code ("en", "ur", "ur_roman", "hi", "ar", "mixed")
        voice: the session's actual chosen Kokoro voice (e.g. "onyx"). Used
               for the "en" path and for the Kokoro fallback when XTTS is
               unavailable — previously hardcoded to "nova" regardless of
               what the user picked, which is why a misdetected non-English
               turn made the voice audibly change: XTTS would fail (not
               ready) and silently fall back to a DIFFERENT voice than the
               one actually in use for the session.

    Returns:
        Audio bytes (WAV), or None if all engines failed.
    """
    if lang == "en":
        logger.info("[TTS_ROUTER_SELECTED] engine=kokoro language=%s voice=%s", lang, voice)
        return _kokoro(text, voice)

    # Non-English → try LRU cache first
    _cache_key = f"{lang}:{voice}:{text}"
    _cached = _ml_cache_get(_cache_key)
    if _cached is not None:
        logger.info("[TTS_ROUTER_CACHE_HIT] lang=%s chars=%d", lang, len(text))
        return _cached

    # Cache miss → attempt XTTS-v2
    logger.info("[TTS_ROUTER_SELECTED] engine=xtts language=%s", lang)
    result: Optional[bytes] = None
    try:
        from voice.xtts_service import synthesize as _xtts
        result = _xtts(text, lang)
        if result:
            _ml_cache_put(_cache_key, result)
            return result
        logger.warning("[XTTS_FALLBACK_TO_KOKORO_ENGLISH] lang=%s reason=xtts_returned_none", lang)
    except Exception as exc:
        logger.error("[XTTS_FALLBACK_TO_KOKORO_ENGLISH] lang=%s error=%s", lang, exc)

    # XTTS failed → Kokoro fallback, same voice the session was already using
    return _kokoro(text, voice)


def _kokoro(text: str, voice: str = "nova") -> Optional[bytes]:
    """Synthesize via Kokoro ONNX using the session's actual chosen voice."""
    try:
        from api.routers.voice import _kokoro_to_wav
        return _kokoro_to_wav(text, voice, 1.0)
    except Exception as exc:
        logger.error("[XTTS_FALLBACK_TO_TEXT] Kokoro also failed: %s", exc)
        return None


# ── Simple LRU cache for multilingual audio bytes ────────────────────────────
# functools.lru_cache cannot cache mutable/large values directly; using a
# manual dict-based cache with maxsize eviction (LRU order via dict insertion order).
_ML_CACHE_MAX = 50
_ml_cache: dict[str, bytes] = {}


def _ml_cache_get(key: str) -> Optional[bytes]:
    val = _ml_cache.get(key)
    if val is not None:
        # Move to end (most-recently-used)
        _ml_cache.pop(key)
        _ml_cache[key] = val
    return val


def _ml_cache_put(key: str, val: bytes) -> None:
    if key in _ml_cache:
        _ml_cache.pop(key)
    elif len(_ml_cache) >= _ML_CACHE_MAX:
        # Evict least-recently-used (first inserted)
        oldest = next(iter(_ml_cache))
        _ml_cache.pop(oldest)
        logger.debug("[TTS_ROUTER_CACHE_EVICT] evicted key=%s", oldest[:40])
    _ml_cache[key] = val
