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
import re

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
    "urdu mein baat karo", "urdu me baat karo",
    "urdu mein baat karo", "urdu me baat kar",
    "roman urdu mein baat karo", "roman urdu me baat karo",
    "can you speak urdu", "speak urdu", "talk in urdu",
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

# Broader construction patterns ("<lang> mein <any verb>", "in <lang>") —
# the exact-phrase sets above only caught a handful of fixed verbs (bolo/
# batao/jawab do), so a perfectly ordinary switch request like "English mein
# explain karo" matched NONE of them and fell through to auto-detection,
# where language_detector (correctly, for its own purpose) flagged "mein"/
# "karo" as Roman Urdu grammar markers — inverting the user's actual
# request. These patterns are checked in addition to the exact sets, most
# specific first (roman_urdu before urdu, since "roman urdu mein" contains
# "urdu mein" as a substring).
#   NOTE: no trailing \b after "में"/"میں" — Devanagari "में" ends with ं
#   (U+0902, combining ANUSVARA, Unicode category Mn) and Urdu "میں" ends
#   with ں (U+06BA, ARABIC LETTER NOON GHUNNA) — neither is treated as a
#   word character by Python's re engine, so \b silently never matches
#   right after either (live-confirmed: r'में\b' fails to match "urdu में
#   बाद..." even with a plain space following). \s+ before it is already
#   enough of a boundary.
#   Live-caught bug this closes: "میں" (actual Urdu/Nastaliq script "mein")
#   was missing entirely — only Latin "mein" and Devanagari "में" (Hindi
#   script) were recognized. A real Whisper transcript of spoken Urdu asking
#   "Urdu میں بات کر سکتے ہو؟" (can you speak in Urdu?) — Whisper commonly
#   keeps the embedded loanword "Urdu" in Latin script mid-Urdu-sentence —
#   matched NONE of these patterns, so the turn fell through to full
#   intent-routing instead of the dedicated language-switch handler, and the
#   local Qwen fallback hallucinated a search_files command out of the
#   user's own question (~14-30s wasted, then a bogus "no files found").
#   Also added: real Urdu-script language names (اردو/انگریزی/انگلش/ہندی/
#   عربی) for phrases that are ENTIRELY in Urdu script, not just "mein".
_TRIGGER_ROMAN_URDU_RE = re.compile(
    r'\broman\s+urdu\s+(?:mein\b|में|میں)|\bin\s+roman\s+urdu\b|رومن\s*اردو\s*میں|'
    r'\broman\s+urdu\s+(?:mein|me)\s+(?:baat|jawab|bolo|batao)\b',
    re.IGNORECASE)
_TRIGGER_URDU_RE       = re.compile(
    r'\burdu\s+(?:mein\b|में|میں)|\bin\s+urdu\b|\bspeak\s+(?:in\s+)?urdu\b|اردو\s*میں|'
    # Capability questions and "baat/bolo" constructions that carry no
    # "mein": "urdu bol sakte ho?", "urdu baat kar sakte ho", "urdu bolna",
    r'\burdu\s+(?:bol|baat)\s+(?:sakt[ei]|karo|kar|do|na|lo)\b|'
    r'\b(?:can\s+you|tum|aap)\s+(?:urdu\s+)?(?:speak|talk|bol(?:te|na)?).*\burdu\b',
    re.IGNORECASE)
_TRIGGER_ENGLISH_RE    = re.compile(
    r'\benglish\s+(?:mein\b|में|میں)|\bin\s+english\b|\bspeak\s+(?:in\s+)?english\b|(?:انگلش|انگریزی)\s*میں',
    re.IGNORECASE)
_TRIGGER_HINDI_RE      = re.compile(
    r'\bhindi\s+(?:mein\b|में|میں)|\bin\s+hindi\b|ہندی\s*میں',
    re.IGNORECASE)
_TRIGGER_ARABIC_RE     = re.compile(
    r'\barabic\s+(?:mein\b|में|میں)|\bin\s+arabic\b|عربی\s*میں',
    re.IGNORECASE)


def check_preference_update(transcript: str, session_id: str) -> str | None:
    """
    Scan transcript for a language preference command.

    If found, update the session preference and return the new mode string.
    Returns None if no preference command was detected.
    Logs [LANGUAGE_PREF_UPDATED] on change.
    """
    t = transcript.lower().strip().rstrip(".!?،۔")
    mode: str | None = None

    # Most specific first — "roman urdu mein" must not match the plain urdu
    # check below (it contains "urdu mein" as a substring).
    if any(kw in t for kw in _TRIGGER_ROMAN_URDU) or _TRIGGER_ROMAN_URDU_RE.search(t):
        mode = "roman_urdu"
    elif any(kw in t for kw in _TRIGGER_ENGLISH) or _TRIGGER_ENGLISH_RE.search(t):
        mode = "english"
    elif any(kw in t for kw in _TRIGGER_URDU) or _TRIGGER_URDU_RE.search(t):
        mode = "urdu"
    elif any(kw in t for kw in _TRIGGER_HINDI) or _TRIGGER_HINDI_RE.search(t):
        mode = "hindi"
    elif any(kw in t for kw in _TRIGGER_ARABIC) or _TRIGGER_ARABIC_RE.search(t):
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

# Auto-mode session stickiness — NOT a permanent lock. language_detector's
# fallback branch returns ("en", 0.95) whenever a short utterance has no
# keyword match at all (e.g. "Pending wale dikhao", "Sirf unpaid") — a false
# "confident English" reading, not a real language switch. Without this, a
# session speaking Roman Urdu would snap back to English on every short
# follow-up that happened to miss the detector's keyword lists. Only applies
# when the low-signal turn is short (<= _STICKY_MAX_WORDS words); a longer,
# unambiguous all-English sentence still switches back immediately via the
# confidence>=threshold branch below, and an explicit "English mein explain
# karo" always wins via check_preference_update before this function is even
# reached. Confidence DECAYS (_STICKY_DECAY_TURNS) rather than locking
# forever, so an abandoned Urdu session eventually reverts to English on its
# own once the user has moved on.
_STICKY_DECAY_TURNS = 3
_STICKY_MAX_WORDS   = 4
_SESSION_STICKY_LANG: dict[str, dict] = {}  # session_id -> {"lang": str, "decay": int}


_STICKY_MIN_STT_CONFIDENCE = -0.6  # matches hybrid_stt_router's own "very_low_conf" bar


def select_response_language(
    detected_input_lang: str,
    session_id: str,
    global_mode: str = "auto",
    confidence: float = 1.0,
    word_count: int = 999,
    stt_confidence: float | None = None,
) -> str:
    """
    Choose the TTS output language for this turn.

    Priority (highest → lowest):
      1. Per-session explicit preference (set via "always reply in Urdu" etc.)
      2. Global env var RESPONSE_LANGUAGE_MODE
      3. Auto-detection: mirror the input language (only if confidence is high
         enough — see _MIN_AUTO_SWITCH_CONFIDENCE), with short-turn decaying
         stickiness to the session's last non-English language (see above)

    Args:
        detected_input_lang: "en" | "ur" | "ur_roman" | "hi" | "ar" | "mixed"
        session_id:          WebSocket session ID
        global_mode:         env var value ("auto", "english", "same_as_user", ...)
        confidence:          language_detector's confidence for detected_input_lang
        word_count:          words in the transcript this turn — gates stickiness
                              to short/ambiguous utterances only (default 999 =
                              "long", so callers that don't pass it get the old,
                              non-sticky behavior rather than an unintended one)
        stt_confidence:      Whisper's own avg_logprob confidence for this turn's
                              transcript (negative log-scale; e.g. -0.2 = confident,
                              -0.8 = garbled). Live-observed failure this closes: a
                              badly mis-heard 2-word fragment ("Xyron, open." for
                              what was probably "Chrome kholo") got forced into a
                              sticky non-English reply language purely because it
                              was short, and the LLM was then asked to answer
                              nonsense text in a language it didn't ask for —
                              producing multi-script garbage. A low-confidence STT
                              pass is noise, not a real short follow-up like
                              "pending wale dikhao" (which Whisper transcribes
                              confidently) — don't trust stickiness for it.

    Returns:
        Output language code: "en" | "ur" | "ur_roman" | "hi" | "ar"
    """
    # Per-session pref takes priority; fall back to process-wide global pref
    session_pref = _SESSION_PREFS.get(session_id) or _GLOBAL_LANG_PREF

    _HARD_PREF_LANG = {
        "urdu": "ur", "roman_urdu": "ur_roman", "hindi": "hi", "arabic": "ar",
    }
    _hard_pref_lang = _HARD_PREF_LANG.get(session_pref) or _HARD_PREF_LANG.get(global_mode)

    # Live-caught bug (2026-09-04 real backend log): once a hard non-English
    # preference is set (e.g. the user asked "kya tum Urdu mein baat kar
    # sakte ho?" — a CAPABILITY QUESTION that still matches _TRIGGER_URDU_RE
    # and sets a persistent "always urdu" preference), every SUBSEQUENT turn
    # got forced into Urdu regardless of what language the user actually
    # spoke — including a clean, unambiguous "Open settings." / "Open
    # Display Settings now." (lang=en confidence=0.95). The "auto" branch
    # below already has the right instinct (a confident, non-garbled input
    # always wins over a short-turn sticky guess — see its own comment), but
    # that nuance never applied here because a hard session_pref/global_mode
    # short-circuited before reaching it at all. Apply the same "a clearly
    # confident, non-garbled reading of the CURRENT turn wins" rule here:
    # override the hard preference for THIS turn only when input is
    # genuinely confident English — the preference itself is left untouched
    # (not cleared), so the next ambiguous/Urdu turn reverts to it exactly
    # as the user asked. No word-count gate here (unlike the auto-mode
    # sticky-decay check below) — a hard "always Urdu" preference isn't a
    # decaying window the way auto-detected stickiness is, so ANY clearly
    # confident, non-garbled English turn should get an English reply, not
    # just long ones.
    if (
        _hard_pref_lang is not None
        and detected_input_lang == "en"
        and confidence >= _MIN_AUTO_SWITCH_CONFIDENCE
        and (stt_confidence is None or stt_confidence >= _STICKY_MIN_STT_CONFIDENCE)
    ):
        lang = "en"
    elif session_pref == "english":
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
        _sticky = _SESSION_STICKY_LANG.get(session_id)
        if detected_input_lang in ("ur", "ur_roman", "hi", "ar") and confidence >= _MIN_AUTO_SWITCH_CONFIDENCE:
            lang = detected_input_lang
            _SESSION_STICKY_LANG[session_id] = {"lang": lang, "decay": _STICKY_DECAY_TURNS}
        elif detected_input_lang == "mixed" and confidence >= _MIN_AUTO_SWITCH_CONFIDENCE:
            lang = "ur_roman"   # mixed input → reply in Roman Urdu
            _SESSION_STICKY_LANG[session_id] = {"lang": lang, "decay": _STICKY_DECAY_TURNS}
        elif (
            _sticky and _sticky["decay"] > 0 and word_count <= _STICKY_MAX_WORDS
            and (stt_confidence is None or stt_confidence >= _STICKY_MIN_STT_CONFIDENCE)
        ):
            # Short, low-signal turn with an active sticky session — stay in
            # that language and decay the counter one step. A longer,
            # unambiguous sentence (word_count > _STICKY_MAX_WORDS) skips
            # this branch entirely and falls through to "en" below, so a
            # genuine full-sentence English switch still takes effect
            # immediately rather than waiting out the decay.
            lang = _sticky["lang"]
            _sticky["decay"] -= 1
        else:
            lang = "en"
            _SESSION_STICKY_LANG.pop(session_id, None)
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
    _SESSION_STICKY_LANG.pop(session_id, None)
