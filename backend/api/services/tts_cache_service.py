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
    def _text_lookup_key(voice: str, text: str, lang: str = "en") -> str:
        # lang defaults to "en" so every pre-existing English call site
        # (get_by_text/synthesize_or_cached with no lang arg) produces the
        # EXACT SAME key it always has — this extension is additive, not a
        # behavior change for English callers.
        return f"{lang}:{voice}::{text.lower().strip()}"

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

    def get_by_text(self, text: str, voice: str, lang: str = "en") -> Optional[bytes]:
        """Return cached WAV matching exact text AND voice (case-insensitive
        text), or None. Voice is part of the lookup key — a phrase cached
        for one voice is never returned as a hit for another; a mismatch is
        a clean cache miss, not a wrong-voice hit.

        lang defaults to "en" — every pre-existing English call site is
        unaffected. Non-English callers (get_by_text_ml below) pass the
        actual ml_resp_lang so a Roman-Urdu ack phrase and its literal-
        English homograph (rare, but possible) never collide in cache.

        Also asserts the invariant with `_voice_of` as a safety net: even
        though the (voice, text) key makes a cross-voice hit structurally
        very unlikely, if some future change ever managed to store the wrong
        voice under a key, this catches and refuses it loudly rather than
        silently playing the wrong voice."""
        key = self._text_map.get(self._text_lookup_key(voice, text, lang))
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

    # ── Roman-Urdu/mixed deterministic-ack fast path ───────────────────────
    # Live-caught latency bug (2026-09-04): a deterministic tool ack like
    # "YouTube khol raha hoon." was unconditionally routed through
    # tts_router.synthesize(), which tries OpenAI TTS FIRST for ALL of
    # ur/ur_roman/mixed (voice/tts_router.py's _EDGE_TTS_LANGS) — a ~2.3-
    # 2.5s network round-trip measured live for a 4-word acknowledgement
    # that never needed cloud-quality prosody in the first place. Measured
    # directly (2026-09-04): Kokoro synthesizes the SAME phrase in ~380-
    # 400ms warm (voice/api/routers/voice.py's _kokoro_to_wav — same engine
    # tts_router.py's own module docstring already documents as an accepted
    # quality tier for ur_roman/mixed: "Latin-script text is reasonably
    # renderable by Kokoro's English phonemizer" — this promotes an
    # already-vetted FALLBACK engine to the PRIMARY engine for exactly this
    # one case, it does not introduce a new, unvalidated quality tier.
    #
    # Deliberately scoped to ur_roman/mixed ONLY — pure Urdu script (lang
    # == "ur") stays on the existing OpenAI-first/Edge-TTS path unchanged;
    # Kokoro's English phonemizer cannot render Nastaliq script
    # intelligibly (see tts_router.py's _synthesize_edge lang=="ur" guard),
    # and this module has no way to validate that quality tradeoff.
    #
    # Persisted to the SAME on-disk _CACHE_DIR the English build() cache
    # uses, so a phrase synthesized once survives process restarts —
    # "subsequent commands should be near-instant" applies across sessions,
    # not just within one.
    _LATIN_RENDERABLE_ML_LANGS = frozenset({"ur_roman", "mixed"})

    @staticmethod
    def _ml_disk_path(voice: str, lang: str, text: str) -> pathlib.Path:
        import hashlib
        h = hashlib.sha1(f"{lang}:{text.lower().strip()}".encode("utf-8")).hexdigest()[:20]
        return _CACHE_DIR / f"ml_{voice}_{lang}_{h}.wav"

    def get_by_text_ml(self, text: str, voice: str, lang: str) -> Optional[bytes]:
        """ur_roman/mixed variant of get_by_text — checks the in-memory
        cache first, then falls back to an on-disk hit (a phrase cached in
        a PRIOR process run) before reporting a miss. Returns None
        immediately for any lang outside _LATIN_RENDERABLE_ML_LANGS —
        callers must not use this for pure Urdu script."""
        if lang not in self._LATIN_RENDERABLE_ML_LANGS:
            return None
        hit = self.get_by_text(text, voice, lang)
        if hit is not None:
            return hit
        # Cold in-memory cache (fresh process) but a prior run already
        # synthesized this exact phrase to disk — load and re-populate the
        # in-memory index so this is a true one-time-per-process disk read.
        path = self._ml_disk_path(voice, lang, text)
        if path.exists():
            try:
                wav = path.read_bytes()
                if len(wav) >= 100:
                    vkey = f"ml:{lang}:{voice}:{text.lower().strip()}"
                    with self._lock:
                        self._cache[vkey] = wav
                        self._text_map[self._text_lookup_key(voice, text, lang)] = vkey
                        self._voice_of[vkey] = voice
                    logger.info("[TTS_CACHE_ML_DISK_HIT] lang=%s voice=%s text=%r", lang, voice, text[:50])
                    return wav
            except Exception:
                pass
        return None

    def synthesize_or_cached_ml(self, text: str, voice: str, speed: float, lang: str) -> Optional[bytes]:
        """ur_roman/mixed fast path: cache hit (memory or disk) returns
        immediately; a miss synthesizes via Kokoro directly — NOT via
        voice.tts_router (which would try OpenAI TTS first for this lang,
        exactly the latency this method exists to avoid) — and persists
        the result both in memory and to disk for next time.

        Returns None for any lang outside _LATIN_RENDERABLE_ML_LANGS — the
        caller must fall through to the existing OpenAI/Edge-TTS path for
        pure Urdu script."""
        if lang not in self._LATIN_RENDERABLE_ML_LANGS:
            return None
        cached = self.get_by_text_ml(text, voice, lang)
        if cached:
            return cached
        logger.info("[TTS_CACHE_ML_MISS] lang=%s text=%r voice=%s", lang, text[:50], voice)
        try:
            from api.routers.voice import _kokoro_to_wav
            wav = _kokoro_to_wav(text, voice, speed)
            if wav:
                vkey = f"ml:{lang}:{voice}:{text.lower().strip()}"
                with self._lock:
                    self._cache[vkey] = wav
                    self._text_map[self._text_lookup_key(voice, text, lang)] = vkey
                    self._voice_of[vkey] = voice
                try:
                    self._ml_disk_path(voice, lang, text).write_bytes(wav)
                except Exception:
                    pass
                logger.info("[TTS_CACHE_ML_STORED] lang=%s text=%r voice=%s", lang, text[:50], voice)
            return wav
        except Exception as exc:
            logger.warning("[TTS_CACHE_ML] kokoro synthesis error lang=%s: %s", lang, exc)
            return None


tts_cache = TTSCacheService()
