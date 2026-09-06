"""
Regression tests for the live voice-session bugs reported in the 2026-08
backend logs:

  1. English → Urdu mis-response: a user speaking ONLY English ("Now can
     you show me the available Wi-Fi network?") got an Urdu-script reply
     spoken by an Urdu TTS voice, because language_detector's Roman-Urdu
     keyword list contained English homographs ("the", "do", "band",
     "sun", "door", ...) and a SINGLE hit flipped the whole turn to
     ur_roman → Ollama Urdu localization + Edge-TTS ur-PK voice.
  2. Session poisoning: one shaky detection set ml_detected_lang for the
     session, forcing every following turn onto the slow multilingual
     "accurate" Whisper model (~3s vs ~0.8s). Now gated on detection
     confidence.
  3. Flight search dead end: "find me a flight from Dubai to Japan" hit
     TravelEntityResolver with no country gazetteer — "Japan"'s best city
     match was Jakarta at 0.47 → bogus clarification question instead of
     searching. Countries now resolve exactly, before fuzzy city scoring.

Run: pytest tests/test_english_misdetect_and_travel_speed.py -v
"""
from __future__ import annotations

import os
import sys

# Ensure backend/ is on sys.path regardless of cwd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ── 1. language_detector: English homographs never flip English → Urdu ──────

@pytest.mark.parametrize("phrase", [
    # The exact live bug: only "the" matched the old Roman-Urdu list.
    "Now can you show me the available Wi-Fi network?",
    # Other homographs that used to be treated as Urdu grammar markers.
    "What do you want me to do?",
    "Open the door please",
    "Play some band music",
    "The sun is really bright today",
    "Put the mat near the door",
    "I do not know the answer",
    "Find me a flight from Dubai to Japan",
])
def test_pure_english_with_homographs_stays_english(phrase):
    from api.services.language_detector import detect
    result = detect(phrase, "en")
    assert result["lang"] == "en", (
        f"{phrase!r} detected as {result['lang']!r} (reason={result['reason']!r}) "
        f"— English homographs must never trigger non-English detection"
    )


@pytest.mark.parametrize("phrase, expected", [
    # Genuine Roman Urdu commands MUST still be detected — the weak-token
    # split must not break the user's Urdu support.
    ("chrome kholo",            "mixed"),      # english app noun + urdu verb
    ("band karo",               "ur_roman"),   # weak 'band' + strong 'karo'
    ("aaj ka mausam kaisa hai", "ur_roman"),
    ("mujhe youtube kholo",     "mixed"),
    ("settings khol do",        "mixed"),      # strong 'khol', weak 'do'
    ("nahi nahi ruk jao",       "ur_roman"),
])
def test_genuine_roman_urdu_still_detected(phrase, expected):
    from api.services.language_detector import detect
    result = detect(phrase, "en")
    assert result["lang"] == expected, (
        f"{phrase!r} → {result['lang']!r} (reason={result['reason']!r}), "
        f"expected {expected!r} — real Roman Urdu must stay non-English"
    )


def test_whisper_acoustic_hint_alone_still_low_confidence():
    """Whisper guessing 'ur' on text with zero Urdu evidence must not
    produce a confident non-English detection (0.60 < the 0.75 auto-switch
    and STT-stickiness gates)."""
    from api.services.language_detector import detect
    result = detect("play believer song", "ur")
    assert result["lang"] == "en", (
        f"en_hits-bearing text must not flip on stt hint: {result!r}"
    )
    # Text with no keywords at all still honors the hint, but weakly.
    result2 = detect("hmm", "ur")
    assert result2["lang"] == "ur" and result2["confidence"] < 0.75


# ── 2. hybrid_stt_router: session-language stickiness needs confidence ──────

def test_stt_stickiness_honors_confident_session_lang():
    from voice.hybrid_stt_router import _decide_mode
    mode, reason = _decide_mode(3000.0, {"ml_detected_lang": "ur_roman",
                                         "ml_detected_lang_conf": 0.88})
    assert mode == "accurate" and reason.startswith("multilingual_session"), (
        f"confident ur_roman session must route to accurate STT, got {mode}/{reason}"
    )


def test_stt_stickiness_ignores_low_confidence_session_lang():
    from voice.hybrid_stt_router import _decide_mode
    # Low-confidence (acoustic-only) detection must NOT force the slow model.
    mode, reason = _decide_mode(3000.0, {"ml_detected_lang": "ur_roman",
                                         "ml_detected_lang_conf": 0.60})
    assert reason != "multilingual_session_lang=ur_roman", (
        f"low-confidence session lang poisoned STT routing: {mode}/{reason}"
    )
    assert mode == "fast_with_retry"  # 3s audio falls through to normal logic


def test_stt_stickiness_legacy_missing_conf_still_multilingual():
    from voice.hybrid_stt_router import _decide_mode
    # Older code paths that never recorded a conf keep legacy behavior.
    mode, reason = _decide_mode(3000.0, {"ml_detected_lang": "ur"})
    assert mode == "accurate" and reason.startswith("multilingual_session")


# ── 3. travel resolver: countries resolve exactly (Japan ≠ Jakarta) ─────────

@pytest.fixture(scope="module")
def resolver():
    pytest.importorskip("jellyfish")
    pytest.importorskip("rapidfuzz")
    from api.agents.browser_agent.travel_entity_resolver import TravelEntityResolver
    return TravelEntityResolver


@pytest.mark.parametrize("raw, expected", [
    ("Japan",     "Japan"),
    ("japan",     "Japan"),
    ("the UK",    "United Kingdom"),
    ("UAE",       "United Arab Emirates"),
    ("America",   "United States"),
    ("turkey",    "Turkey"),
    ("the philippines", "Philippines"),
])
def test_country_names_resolve_exactly(resolver, raw, expected):
    loc = resolver.resolve_location(raw)
    assert loc.evidence == "exact_country_match", (
        f"{raw!r} → evidence={loc.evidence!r} candidates={loc.candidates!r} "
        f"— clean country names must never fall through to fuzzy city scoring"
    )
    assert loc.canonical_city == expected
    assert loc.country == expected
    assert loc.confidence >= 0.95
    assert not loc.candidates


def test_japan_flight_goal_builds_without_clarification(resolver):
    pytest.importorskip("jellyfish")
    from api.agents.browser_agent.travel_goal import build_travel_goal
    goal = build_travel_goal(
        "find me a flight from dubai to japan next month",
        origin_raw="dubai", destination_raw="japan",
    )
    assert goal.destination == "Japan", (
        f"destination={goal.destination!r} clarification={goal.needs_clarification!r}"
    )
    assert goal.needs_clarification is None, (
        f"Japan triggered a bogus clarification: {goal.needs_clarification!r}"
    )
    assert goal.origin == "Dubai"
    assert goal.departure_date == "next month"


def test_cities_still_resolve_via_fuzzy(resolver):
    """The country path must not shadow normal city resolution."""
    loc = resolver.resolve_location("Dubai")
    assert loc.canonical_city == "Dubai" and loc.iata_code == "DXB"
    loc2 = resolver.resolve_location("carachi")
    assert loc2.canonical_city == "Karachi"
