"""
Edge-TTS service — native Urdu voices via Microsoft Edge's online TTS.

Why this exists: XTTS (Coqui, local) cannot load on this machine's current
dependency stack (torch/torchaudio/transformers/numpy version conflicts —
see the 2026-08-19 investigation notes in xtts_service.py), and even where
it DOES load, it has no real Urdu model at all — it borrows Arabic phonemes
as an approximation (see xtts_service.py's _LANG_MAP comment). Edge-TTS has
genuine native Pakistani Urdu voices and zero dependency on torch/numpy, so
using it can never destabilize the rest of the ML stack (Whisper, Kokoro,
sentence-transformers, Qwen/Ollama).

This is NOT local-only — it requires network access to Microsoft's TTS
endpoint. tts_router.py handles the fallback when it's unreachable or times
out; this module itself never raises, only returns None on failure.

Voice selection: EDGE_TTS_URDU_VOICE env var, default "ur-PK-AsadNeural".
Both "ur-PK-AsadNeural" (male) and "ur-PK-UzmaNeural" (female) are genuine
Pakistani Urdu neural voices — the Asad default is a guess matching Xyron's
existing male "onyx" Kokoro default, not a verified listening comparison;
override the env var after listening to both.

Prosody (EDGE_TTS_RATE / EDGE_TTS_PITCH / EDGE_TTS_VOLUME env vars, default
"+0%"/"+0Hz"/"+0%" — i.e. the voice's own natural pace, unchanged): edge-tts
accepts SSML-style relative adjustments per Microsoft's speech-synthesis
markup (e.g. "-8%" slower, "+5Hz" higher). Left neutral by default rather
than guessed non-neutral — same rule this codebase already applies in
pronunciation_preprocessor.py: a prosody tweak is only worth shipping after
a human has actually A/B-listened to it, not assumed to sound better.
Override the env vars once you've listened and picked values you like.

Pronunciation: text is passed through pronunciation_preprocessor.preprocess()
before synthesis — currently a no-op (its TERM_OVERRIDES table is empty
pending listening tests) but this is the module that actually needs it,
since XTTS (the preprocessor's original target) cannot load on this
machine's dependency stack and Edge-TTS is the real production Urdu path.

Logs:
  [EDGE_TTS_START]       lang=... voice=... text=...
  [EDGE_TTS_FIRST_AUDIO] ms=... lang=... voice=...
  [EDGE_TTS_COMPLETE]    ms=... bytes=... lang=... voice=...
  [EDGE_TTS_FAILED]      reason=... lang=... voice=...
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_URDU_VOICE = os.getenv("EDGE_TTS_URDU_VOICE", "ur-PK-AsadNeural")
_TIMEOUT_S = float(os.getenv("EDGE_TTS_TIMEOUT_S", "6.0"))
_RATE   = os.getenv("EDGE_TTS_RATE", "+0%")
_PITCH  = os.getenv("EDGE_TTS_PITCH", "+0Hz")
_VOLUME = os.getenv("EDGE_TTS_VOLUME", "+0%")

# lang -> Edge voice. Only Urdu-family languages route here for now (see
# tts_router.py's routing table) — kept as a dict, not a single constant,
# so a future language can be added without touching any caller.
_LANG_VOICE_MAP: dict[str, str] = {
    "ur":       DEFAULT_URDU_VOICE,
    "ur_roman": DEFAULT_URDU_VOICE,
    "mixed":    DEFAULT_URDU_VOICE,
}


def voice_for_lang(lang: str) -> str:
    return _LANG_VOICE_MAP.get(lang, DEFAULT_URDU_VOICE)


async def _synthesize_mp3_async(text: str, voice: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=_RATE, pitch=_PITCH, volume=_VOLUME)
    audio_bytes = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_bytes.extend(chunk["data"])
    return bytes(audio_bytes)


def _mp3_to_wav(mp3_bytes: bytes) -> bytes:
    """Edge-TTS streams MP3; every other engine in tts_router.py (Kokoro,
    XTTS) returns WAV, and the frontend's audio Blob is built as 'audio/wav'
    (web/src/hooks/useVoiceSession.ts) — convert so callers never need to
    know which engine actually produced the audio.

    Uses soundfile (libsndfile's native MP3 decoder, in-process) rather than
    pydub, which shells out to an ffmpeg subprocess per call — live-measured
    on this machine: ~130-180ms/call for pydub/ffmpeg vs ~3ms/call for
    soundfile, for byte-for-byte the same MP3 input (only difference is
    normal decoder rounding noise, max sample delta 518/32768 — inaudible,
    same class of variance as any two MP3 decoders disagreeing on the last
    bit or two of the inverse DCT). Requires libsndfile >=1.1 (MP3 support);
    this environment has 1.2.2."""
    import soundfile as sf
    data, samplerate = sf.read(io.BytesIO(mp3_bytes), dtype="int16")
    out = io.BytesIO()
    sf.write(out, data, samplerate, format="WAV", subtype="PCM_16")
    return out.getvalue()


def synthesize(text: str, lang: str, voice: Optional[str] = None) -> Optional[bytes]:
    """
    Synthesize speech via Edge-TTS. Returns WAV bytes, or None on any
    failure (network, timeout, empty text, conversion error) — never
    raises, so tts_router.py can fall back cleanly.

    Bounded by EDGE_TTS_TIMEOUT_S (default 6s) so a dead network can never
    hang a voice turn — this is intentionally tighter than the 30s outer
    timeout voice_ws.py's _tts_ml already wraps every tts_router.synthesize
    call in, so a network failure here fails fast instead of eating most of
    that budget.

    Must be called from a worker thread (e.g. via asyncio.to_thread), never
    directly on the asyncio event loop — it runs its own event loop via
    asyncio.run() internally for the streaming Edge-TTS client.
    """
    if not text or not text.strip():
        return None

    from voice.pronunciation_preprocessor import preprocess as _preprocess
    text = _preprocess(text)

    edge_voice = voice or voice_for_lang(lang)
    logger.info("[EDGE_TTS_START] lang=%s voice=%s text=%r", lang, edge_voice, text[:60])
    t0 = time.monotonic()

    try:
        mp3_bytes = asyncio.run(
            asyncio.wait_for(_synthesize_mp3_async(text, edge_voice), timeout=_TIMEOUT_S)
        )
    except asyncio.TimeoutError:
        logger.warning("[EDGE_TTS_FAILED] reason=timeout_%.0fs lang=%s voice=%s",
                        _TIMEOUT_S, lang, edge_voice)
        return None
    except Exception as exc:
        logger.warning("[EDGE_TTS_FAILED] reason=%s lang=%s voice=%s", exc, lang, edge_voice)
        return None

    ms_synth = (time.monotonic() - t0) * 1000
    logger.info("[EDGE_TTS_FIRST_AUDIO] ms=%.0f lang=%s voice=%s", ms_synth, lang, edge_voice)

    if not mp3_bytes:
        logger.warning("[EDGE_TTS_FAILED] reason=empty_audio lang=%s voice=%s", lang, edge_voice)
        return None

    try:
        wav_bytes = _mp3_to_wav(mp3_bytes)
    except Exception as exc:
        logger.warning("[EDGE_TTS_FAILED] reason=mp3_to_wav_%s lang=%s voice=%s", exc, lang, edge_voice)
        return None

    ms_total = (time.monotonic() - t0) * 1000
    logger.info("[EDGE_TTS_COMPLETE] ms=%.0f bytes=%d lang=%s voice=%s",
                ms_total, len(wav_bytes), lang, edge_voice)
    return wav_bytes
