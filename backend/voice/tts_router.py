"""
TTS Router — routes synthesis to Kokoro (English), OpenAI TTS / Edge-TTS
(Urdu family), or XTTS-v2 (other multilingual, currently unavailable on
this machine).

Routing table:
  lang == "en"                        → Kokoro (tts_service.synthesize_speech)
  lang in (ur, ur_roman, mixed)        → OpenAI TTS (voice.openai_tts_service),
    primary engine as of 2026-09-04 now that OpenAI billing is restored —
    noticeably more natural Urdu prosody than Edge-TTS. Falls back to
    Edge-TTS native Pakistani Urdu voice if OpenAI TTS fails (no key,
    quota, network, timeout) — same failure-handling shape as every other
    engine here, never raises.
    Both cloud engines failing/no network → Kokoro fallback, EXCEPT pure
      Urdu script (lang == "ur") is never fed to Kokoro's English
      phonemizer — that would be unintelligible gibberish, not
      degraded-but-usable speech, so that specific case returns None (no
      audio) and lets the existing "no audio produced" recovery path in
      voice_ws.py's _tts_ml return the session to listening instead of
      speaking garbage.
  lang in (hi, ar) or other            → XTTS-v2 (voice.xtts_service)
    XTTS is currently unable to load on this machine's dependency stack
    (torch/transformers/numpy version conflicts, 2026-08-19) — this branch
    is kept, not deleted, because XTTS is being treated as a legacy/
    unavailable local backend to potentially replace later, not removed.
    It fails fast (see xtts_service.py) and falls through to Kokoro exactly
    as it always has.

Why Edge-TTS for Urdu specifically (not XTTS): XTTS has no real Urdu model
even when it CAN load — it borrows Arabic phonemes as an approximation (see
xtts_service.py). Edge-TTS has genuine native ur-PK voices and no shared
dependency with torch/numpy, so it can't destabilize the rest of the stack.
It does require network access — see edge_tts_service.py for the timeout/
fallback contract.

LRU cache: last 50 (engine, voice, lang, text) tuples cached in memory to
avoid re-synthesis of repeated short responses. The cache key MUST include
the engine — a prior bug used only (lang, voice, text), so switching which
engine handles a language (e.g. this Edge-TTS migration) could serve a
stale cache entry synthesized by the OLD engine under a DIFFERENT voice
than the session is actually using.

Logs:
  [TTS_ROUTE]                        lang=... engine=... voice=...
  [TTS_ROUTER_CACHE_HIT]             — LRU cache served the result
  [TTS_FALLBACK]                     engine=... reason=... → falling back to kokoro
  [XTTS_FALLBACK_TO_KOKORO_ENGLISH]  — XTTS unavailable, Kokoro used instead
  [XTTS_FALLBACK_TO_TEXT]            — both engines failed, text-only response
  (edge_tts_service.py emits its own [EDGE_TTS_*] logs for that engine's stages)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_EDGE_TTS_LANGS = frozenset({"ur", "ur_roman", "mixed"})


def synthesize(text: str, lang: str = "en", voice: str = "nova") -> Optional[bytes]:
    """
    Route TTS synthesis based on language.

    Args:
        text:  text to synthesize
        lang:  output language code ("en", "ur", "ur_roman", "hi", "ar", "mixed")
        voice: the session's actual chosen Kokoro voice (e.g. "onyx"). Used
               for the "en" path and for the Kokoro fallback when the
               primary non-English engine is unavailable — previously
               hardcoded to "nova" regardless of what the user picked,
               which is why a misdetected non-English turn made the voice
               audibly change: the primary engine would fail and silently
               fall back to a DIFFERENT voice than the one actually in use
               for the session.

    Returns:
        Audio bytes (WAV), or None if all applicable engines failed.
    """
    if lang == "en":
        logger.info("[TTS_ROUTE] lang=%s engine=kokoro voice=%s", lang, voice)
        return _kokoro(text, voice)

    if lang in _EDGE_TTS_LANGS:
        return _synthesize_urdu(text, lang, voice)

    return _synthesize_xtts(text, lang, voice)


def _synthesize_urdu(text: str, lang: str, voice: str) -> Optional[bytes]:
    """OpenAI TTS first (better Urdu prosody), Edge-TTS if that fails."""
    _cache_key = f"openai_tts:{voice}:{lang}:{text}"
    _cached = _ml_cache_get(_cache_key)
    if _cached is not None:
        logger.info("[TTS_ROUTER_CACHE_HIT] engine=openai_tts lang=%s voice=%s chars=%d",
                     lang, voice, len(text))
        return _cached

    try:
        from voice.openai_tts_service import synthesize as _openai_tts
        result = _openai_tts(text, lang, voice)
        if result:
            _ml_cache_put(_cache_key, result)
            return result
        logger.warning("[TTS_FALLBACK] engine=openai_tts reason=returned_none lang=%s → edge_tts", lang)
    except Exception as exc:
        logger.warning("[TTS_FALLBACK] engine=openai_tts reason=%s lang=%s → edge_tts", exc, lang)

    return _synthesize_edge(text, lang, voice)


def _synthesize_edge(text: str, lang: str, voice: str) -> Optional[bytes]:
    from voice.edge_tts_service import voice_for_lang

    edge_voice = voice_for_lang(lang)
    _cache_key = f"edge_tts:{edge_voice}:{lang}:{text}"
    _cached = _ml_cache_get(_cache_key)
    if _cached is not None:
        logger.info("[TTS_ROUTER_CACHE_HIT] engine=edge_tts lang=%s voice=%s chars=%d",
                     lang, edge_voice, len(text))
        return _cached

    logger.info("[TTS_ROUTE] lang=%s engine=edge_tts voice=%s", lang, edge_voice)
    result: Optional[bytes] = None
    try:
        from voice.edge_tts_service import synthesize as _edge
        result = _edge(text, lang)
        if result:
            _ml_cache_put(_cache_key, result)
            return result
        logger.warning("[TTS_FALLBACK] engine=edge_tts reason=returned_none lang=%s", lang)
    except Exception as exc:
        logger.warning("[TTS_FALLBACK] engine=edge_tts reason=%s lang=%s", exc, lang)

    if lang == "ur":
        # Pure Urdu script into Kokoro's English phonemizer is unintelligible
        # noise, not degraded-but-usable speech — no audio beats garbage
        # audio. Caller (_tts_ml) already handles "no audio produced" by
        # recovering to listening state.
        logger.warning("[TTS_FALLBACK] lang=ur edge_tts_unavailable — "
                        "not feeding Urdu script to Kokoro, returning no audio")
        return None

    # ur_roman / mixed: Latin-script text is reasonably renderable by
    # Kokoro's English phonemizer (same reasoning xtts_service.py already
    # uses for its own ur_roman -> "en" XTTS language mapping).
    return _kokoro(text, voice)


def _synthesize_xtts(text: str, lang: str, voice: str) -> Optional[bytes]:
    _cache_key = f"xtts:{voice}:{lang}:{text}"
    _cached = _ml_cache_get(_cache_key)
    if _cached is not None:
        logger.info("[TTS_ROUTER_CACHE_HIT] engine=xtts lang=%s chars=%d", lang, len(text))
        return _cached

    logger.info("[TTS_ROUTE] lang=%s engine=xtts", lang)
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
