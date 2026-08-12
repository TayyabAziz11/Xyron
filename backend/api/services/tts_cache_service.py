"""
TTS cache service — pre-synthesized WAV for common short responses.

On startup (after Kokoro is ready) this service synthesizes a fixed set of
acknowledgement phrases and saves them to /tmp/xyron-tts-cache/.  Every
subsequent request for a cached phrase returns the bytes immediately without
hitting Kokoro.

Usage:
    from api.services.tts_cache_service import tts_cache
    wav = tts_cache.get("on_it")                # None if not cached yet
    wav = tts_cache.synthesize_or_cached(text, voice, speed)
"""
from __future__ import annotations

import logging
import pathlib
import time
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_DIR = pathlib.Path("/tmp/xyron-tts-cache")

# Keys → text to pre-synthesize.  Keep short — one TTS chunk each.
_ACK_PHRASES: dict[str, str] = {
    "on_it":       "On it.",
    "opening":     "Opening.",
    "opening_it":  "Opening it.",
    "done":        "Done.",
    "got_it":      "Got it.",
    "working_on_it": "Working on it.",
    "listening":   "Listening.",
    "ready":       "Ready.",
    "cant_find":   "I couldn't find it.",
    "cant_confirm": "I couldn't confirm it opened.",
    "playing_now": "Playing it now.",
    "sure":        "Sure.",
}


class TTSCacheService:
    """
    UX-refinement fix: every cache entry is keyed by (voice, text) — never by
    text alone. Previously `_text_map` mapped bare lowercased text -> key, so
    a phrase synthesized once in one voice (e.g. the "nova" startup warmup)
    would be returned as a "cache hit" for a later session using a different
    voice (e.g. "onyx"), silently playing the wrong voice's audio. Two call
    sites in voice_ws.py had grown ad-hoc `_build_voice == voice` guards to
    work around this — those are no longer needed now that a mismatched
    voice simply can't produce a cache hit at all; it's a clean miss that
    falls through to fresh synthesis (and gets cached correctly for next
    time, under its own voice's key).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, bytes] = {}     # "voice:key" -> WAV bytes (in-memory)
        self._text_map: dict[str, str] = {}    # "voice::lower_text" -> "voice:key"
        self._voice_of: dict[str, str] = {}    # "voice:key" -> voice it was synthesized in
        self._ready = False
        self._build_voice: str = "nova"        # voice used when build() was last called
        self._build_speed: float = 1.0
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _text_lookup_key(voice: str, text: str) -> str:
        return f"{voice}::{text.lower().strip()}"

    # ── Build ────────────────────────────────────────────────────────────────

    def build(self, voice: str = "nova", speed: float = 1.0) -> None:
        """Pre-synthesize all ACK phrases. Runs in warmup thread after Kokoro is ready."""
        try:
            from api.routers.voice import _kokoro_to_wav
        except Exception as exc:
            logger.warning("[TTS_CACHE] kokoro import failed — cache unavailable: %s", exc)
            return

        loaded = 0
        for key, text in _ACK_PHRASES.items():
            # Voice-qualify the on-disk filename too — otherwise a later
            # build() with a different voice would load the previous
            # voice's file off disk and think it was a match for the new one.
            _vkey = f"{voice}:{key}"
            file_path = _CACHE_DIR / f"{voice}_{key}.wav"
            wav: Optional[bytes] = None

            # Load from disk if already synthesized
            if file_path.exists():
                try:
                    wav = file_path.read_bytes()
                    if len(wav) < 100:
                        wav = None   # corrupt/empty file
                except Exception:
                    wav = None

            if wav is None:
                try:
                    t0 = time.monotonic()
                    wav = _kokoro_to_wav(text, voice, speed)
                    if wav:
                        file_path.write_bytes(wav)
                        logger.debug("[TTS_CACHE] synthesized key=%s voice=%s ms=%.0f",
                                     key, voice, (time.monotonic() - t0) * 1000)
                except Exception as exc:
                    logger.warning("[TTS_CACHE] synthesis failed key=%s voice=%s: %s", key, voice, exc)
                    continue

            if wav:
                with self._lock:
                    self._cache[_vkey] = wav
                    self._text_map[self._text_lookup_key(voice, text)] = _vkey
                    self._voice_of[_vkey] = voice
                loaded += 1

        with self._lock:
            self._ready = loaded > 0
            self._build_voice = voice
            self._build_speed = speed

        logger.info("[TTS_CACHE_REBUILT] entries=%d/%d voice=%s speed=%.1f dir=%s",
                    loaded, len(_ACK_PHRASES), voice, speed, _CACHE_DIR)

    # ── Lookup ───────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[bytes]:
        """Return cached WAV by its raw internal key ("voice:name"), or None."""
        with self._lock:
            result = self._cache.get(key)
        if result:
            logger.info("[TTS_CACHE_HIT] key=%s", key)
        return result

    def get_by_text(self, text: str, voice: str) -> Optional[bytes]:
        """Return cached WAV matching exact text AND voice (case-insensitive
        text), or None. Voice is part of the lookup key — a phrase cached
        for one voice is never returned as a hit for another; a mismatch is
        a clean cache miss, not a wrong-voice hit.

        Also asserts the invariant with `_voice_of` as a safety net: even
        though the (voice, text) key makes a cross-voice hit structurally
        very unlikely, if some future change ever managed to store the wrong
        voice under a key, this catches and refuses it loudly rather than
        silently playing the wrong voice."""
        key = self._text_map.get(self._text_lookup_key(voice, text))
        if not key:
            return None
        wav = self.get(key)
        if not wav:
            return None
        actual_voice = self._voice_of.get(key)
        match = actual_voice == voice
        logger.info("[TTS_CACHE_VOICE] key=%s cache_voice=%s", key, actual_voice)
        logger.info("[TTS_VOICE_MATCH] requested_voice=%s cache_voice=%s match=%s",
                    voice, actual_voice, match)
        if not match:
            logger.error("[TTS_VOICE_MISMATCH] key=%s requested_voice=%s cache_voice=%s — "
                         "refusing to play wrong-voice clip", key, voice, actual_voice)
            return None
        return wav

    def synthesize_or_cached(self, text: str, voice: str, speed: float) -> Optional[bytes]:
        """Return cached WAV for (text, voice), or synthesize fresh via Kokoro.

        Phase 4.12 fix: this used to return the freshly-synthesized bytes
        without ever writing them back into `self._cache`/`_text_map` — so
        a phrase not already populated by `build()` would re-synthesize
        via Kokoro on *every single call*, forever, silently defeating the
        entire point of a cache. Now a successful on-the-fly synthesis is
        stored under a key derived from the (voice, text) pair, so the
        second and every later call for that exact phrase *in that voice*
        becomes a true cache hit — and never a hit for a different voice."""
        cached = self.get_by_text(text, voice)
        if cached:
            return cached
        logger.info("[TTS_CACHE_MISS] text=%r voice=%s", text[:50], voice)
        try:
            from api.routers.voice import _kokoro_to_wav
            wav = _kokoro_to_wav(text, voice, speed)
            if wav:
                key = f"adhoc:{voice}:{text.lower().strip()}"
                with self._lock:
                    self._cache[key] = wav
                    self._text_map[self._text_lookup_key(voice, text)] = key
                    self._voice_of[key] = voice
                logger.info("[TTS_CACHE_STORED] text=%r key=%s voice=%s", text[:50], key, voice)
            return wav
        except Exception as exc:
            logger.warning("[TTS_CACHE] synthesis error: %s", exc)
            return None

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._ready


tts_cache = TTSCacheService()
