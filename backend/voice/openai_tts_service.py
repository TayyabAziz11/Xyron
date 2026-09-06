"""
OpenAI TTS service — cloud speech synthesis for Urdu-family responses.

Why this exists: Edge-TTS (edge_tts_service.py) was the Urdu TTS engine
while OpenAI billing was exhausted (2026-09-03, credit_balance_exhausted).
Credits are back — OpenAI's TTS API produces noticeably more natural Urdu
prosody than Edge-TTS's ur-PK voices, so this becomes the primary engine
for ur/ur_roman/mixed, with Edge-TTS demoted to the fallback if this fails
(network error, quota, timeout). Scope is deliberately Urdu-only per user
instruction — English still goes straight to Kokoro (tts_router.py never
routes "en" here), keeping OpenAI API spend confined to the one language
that actually needs it right now.

Voice names: this module is handed the session's existing OpenAI-style
voice id (nova/alloy/echo/onyx/fable/shimmer — see
api/routers/voice.py's _KOKORO_VOICE_MAP) unchanged, since OpenAI's TTS
API already expects exactly those names — no mapping table needed, unlike
edge_tts_service.py's Kokoro/Edge voice translation.

Response format requested as WAV directly (response_format="wav") so no
MP3->WAV conversion step is needed, unlike edge_tts_service.py.

Never raises — returns None on any failure so tts_router.py can fall back
to Edge-TTS exactly as it already falls back to Kokoro.

Logs:
  [OPENAI_TTS_START]    voice=... chars=...
  [OPENAI_TTS_COMPLETE] ms=... bytes=... voice=...
  [OPENAI_TTS_FAILED]   reason=... voice=...
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 12s, not 6s: live-observed real completions taking 4.7-5.7s even when
# they succeed (network + synthesis time for a short phrase) — a 6s budget
# was clipping genuinely-in-flight requests and falling back to Edge-TTS
# needlessly (live log: "[OPENAI_TTS_FAILED] reason=Request timed out."
# immediately followed by a successful Edge-TTS synth of the same text).
# tts_router.py's caller (voice_ws.py's _tts_ml) already wraps the whole
# per-chunk synth call (this + its Edge-TTS fallback) in its own 30s
# timeout, so widening this alone cannot cause an unbounded stall.
_TIMEOUT_S = float(os.getenv("OPENAI_TTS_TIMEOUT_S", "12.0"))
_MODEL     = os.getenv("OPENAI_TTS_MODEL", "tts-1")  # tts-1 = low-latency; tts-1-hd = higher quality/slower

# OpenAI's valid TTS voice ids — used to validate/fallback an unrecognized
# session voice (e.g. a raw Kokoro id like "af_heart" that never went
# through _KOKORO_VOICE_MAP) instead of sending a value the API will reject.
_VALID_VOICES = frozenset({
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "onyx", "nova", "sage", "shimmer",
})
_DEFAULT_VOICE = "onyx"

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    from api.config import settings
    key = getattr(settings, "openai_api_key", "") or ""
    if not (key and key.startswith("sk-")):
        return None
    from openai import OpenAI
    _client = OpenAI(api_key=key, timeout=_TIMEOUT_S, max_retries=0)
    return _client


def synthesize(text: str, lang: str, voice: Optional[str] = None) -> Optional[bytes]:
    """
    Synthesize speech via OpenAI's TTS API. Returns WAV bytes, or None on
    any failure (no key, quota, network, timeout) — never raises, so
    tts_router.py can fall back to Edge-TTS cleanly.

    `lang` is accepted (not used in the request — OpenAI's TTS models
    auto-detect language from the input script) to match edge_tts_service
    .synthesize()'s call signature, since tts_router.py calls both
    interchangeably.
    """
    if not text or not text.strip():
        return None

    client = _get_client()
    if client is None:
        logger.debug("[OPENAI_TTS_FAILED] reason=no_api_key lang=%s", lang)
        return None

    from voice.pronunciation_preprocessor import preprocess as _preprocess
    text = _preprocess(text)

    tts_voice = voice if voice in _VALID_VOICES else _DEFAULT_VOICE
    logger.info("[OPENAI_TTS_START] voice=%s chars=%d lang=%s", tts_voice, len(text), lang)
    t0 = time.monotonic()

    try:
        resp = client.audio.speech.create(
            model=_MODEL,
            voice=tts_voice,
            input=text,
            response_format="wav",
        )
        wav_bytes = resp.read() if hasattr(resp, "read") else resp.content
    except Exception as exc:
        logger.warning("[OPENAI_TTS_FAILED] reason=%s voice=%s lang=%s", exc, tts_voice, lang)
        return None

    if not wav_bytes:
        logger.warning("[OPENAI_TTS_FAILED] reason=empty_audio voice=%s lang=%s", tts_voice, lang)
        return None

    ms_total = (time.monotonic() - t0) * 1000
    logger.info("[OPENAI_TTS_COMPLETE] ms=%.0f bytes=%d voice=%s lang=%s",
                ms_total, len(wav_bytes), tts_voice, lang)
    return wav_bytes
