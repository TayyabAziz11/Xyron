"""
Response language policy for Xyron multilingual support.

Determines which language Xyron should use for its TTS response, based on:
  1. Per-session explicit preference ("always reply in Urdu")
  2. Global RESPONSE_LANGUAGE_MODE env var
  3. Auto-detection from input language

Logs:
  [RESPONSE_LANGUAGE_SELECTED] — output language chosen for this turn
  [LANGUAGE_PREF_UPDATED]      — user updated their language preference
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Per-session language preference (cleared when session closes).
# Key: session_id (str), Value: mode (str)
_SESSION_PREFS: dict[str, str] = {}

# Global preference — persists across WebSocket sessions within the same process.
# Set when the user says "always reply in Urdu" (or any global-pref phrase).
# Reset when the user says "reply in English from now on".
_GLOBAL_LANG_PREF: str | None = None

# ── Preference-update trigger phrases ─────────────────────────────────────────
_TRIGGER_ENGLISH: frozenset[str] = frozenset({
    "reply in english", "respond in english", "answer in english",
    "speak english", "speak in english", "english mein bolo",
    "reply in english from now on", "always reply in english",
    "always respond in english",
})
_TRIGGER_URDU: frozenset[str] = frozenset({
    "reply in urdu", "respond in urdu", "urdu mein jawab do",
    "urdu mein bolo", "urdu mein batao", "always reply in urdu",
    "always respond in urdu", "ab urdu mein jawab do",
})
_TRIGGER_ROMAN_URDU: frozenset[str] = frozenset({
    "reply in roman urdu", "roman urdu mein jawab do",
    "roman urdu mein bolo",
})
_TRIGGER_HINDI: frozenset[str] = frozenset({
    "reply in hindi", "hindi mein jawab do", "hindi mein bolo",
    "always reply in hindi",
})
_TRIGGER_ARABIC: frozenset[str] = frozenset({
    "reply in arabic", "arabic mein jawab do",
})


def check_preference_update(transcript: str, session_id: str) -> str | None:
    """
    Scan transcript for a language preference command.

    If found, update the session preference and return the new mode string.
    Returns None if no preference command was detected.
    Logs [LANGUAGE_PREF_UPDATED] on change.
    """
    t = transcript.lower().strip().rstrip(".!?،۔")
    mode: str | None = None

    if any(kw in t for kw in _TRIGGER_ENGLISH):
        mode = "english"
    elif any(kw in t for kw in _TRIGGER_URDU):
        mode = "urdu"
    elif any(kw in t for kw in _TRIGGER_ROMAN_URDU):
        mode = "roman_urdu"
    elif any(kw in t for kw in _TRIGGER_HINDI):
        mode = "hindi"
    elif any(kw in t for kw in _TRIGGER_ARABIC):
        mode = "arabic"

    if mode:
        global _GLOBAL_LANG_PREF
        _SESSION_PREFS[session_id] = mode
        _GLOBAL_LANG_PREF = mode   # survives new WebSocket sessions in this process
        logger.info("[LANGUAGE_PREF_UPDATED] session=%s mode=%s global=True", session_id, mode)

    return mode


# Auto-detect mode only switches the response language away from English
# when the detector is genuinely confident. Script-based detection (Arabic/
# Devanagari chars, 0.88-0.95) clears this easily; the STT-acoustic-hint
# tie-breaker (0.60 — Whisper's language-ID guessing on short/ambiguous
# audio with zero textual corroboration) does not. Live bug: a user
# speaking plain English ("Play a song called Believer.") had Whisper's
# language-ID guess "hi" at 0.60, which silently swapped the reply language
# (and, via the XTTS-unavailable fallback, the TTS voice) mid-session —
# never something the user asked for.
_MIN_AUTO_SWITCH_CONFIDENCE = 0.75


def select_response_language(
    detected_input_lang: str,
    session_id: str,
    global_mode: str = "auto",
    confidence: float = 1.0,
) -> str:
    """
    Choose the TTS output language for this turn.

    Priority (highest → lowest):
      1. Per-session explicit preference (set via "always reply in Urdu" etc.)
      2. Global env var RESPONSE_LANGUAGE_MODE
      3. Auto-detection: mirror the input language (only if confidence is high
         enough — see _MIN_AUTO_SWITCH_CONFIDENCE)

    Args:
        detected_input_lang: "en" | "ur" | "ur_roman" | "hi" | "ar" | "mixed"
        session_id:          WebSocket session ID
        global_mode:         env var value ("auto", "english", "same_as_user", ...)
        confidence:          language_detector's confidence for detected_input_lang

    Returns:
        Output language code: "en" | "ur" | "ur_roman" | "hi" | "ar"
    """
    # Per-session pref takes priority; fall back to process-wide global pref
    session_pref = _SESSION_PREFS.get(session_id) or _GLOBAL_LANG_PREF

    if session_pref == "english":
        lang = "en"
    elif session_pref == "urdu":
        lang = "ur"
    elif session_pref == "roman_urdu":
        lang = "ur_roman"
    elif session_pref == "hindi":
        lang = "hi"
    elif session_pref == "arabic":
        lang = "ar"
    elif global_mode == "english":
        lang = "en"
    elif global_mode == "urdu":
        lang = "ur"
    elif global_mode == "roman_urdu":
        lang = "ur_roman"
    elif global_mode == "hindi":
        lang = "hi"
    elif global_mode in ("same_as_user", "auto"):
        if detected_input_lang in ("ur", "ur_roman", "hi", "ar") and confidence >= _MIN_AUTO_SWITCH_CONFIDENCE:
            lang = detected_input_lang
        elif detected_input_lang == "mixed" and confidence >= _MIN_AUTO_SWITCH_CONFIDENCE:
            lang = "ur_roman"   # mixed input → reply in Roman Urdu
        else:
            lang = "en"
    else:
        lang = "en"

    logger.info(
        "[RESPONSE_LANGUAGE_SELECTED] input=%s confidence=%.2f session_pref=%s global=%s → output=%s",
        detected_input_lang, confidence, session_pref or "none", global_mode, lang,
    )
    return lang


def clear_session(session_id: str) -> None:
    """Remove all preferences for a closed session."""
    _SESSION_PREFS.pop(session_id, None)
