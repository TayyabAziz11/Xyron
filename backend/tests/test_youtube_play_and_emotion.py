"""
Regression tests for the live voice-session bugs fixed in the human-voice
upgrade:

  1. Routing: "open YouTube and play any famous song" was swallowed by the
     generic `play` regex (registered before search_youtube) and became
     media_control play_pause → "Playing / paused." with nothing opened.
  2. Emotion: calm praise ("Perfect. Now can you open YouTube...") and
     "It's work time, buddy." were mis-flagged as stress because generic
     words ("now", "time") lived in _STRESS_WORDS; genuine spoken praise
     without exclamation marks never reached excitement.

Run: pytest tests/test_youtube_play_and_emotion.py -v
"""
from __future__ import annotations

import os
import sys

# Ensure backend/ is on sys.path regardless of cwd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(scope="module")
def router():
    from api.services.intent_router import IntentRouter
    return IntentRouter()


@pytest.fixture(scope="module")
def emotion():
    from cognition.emotion_engine import EmotionEngine
    return EmotionEngine()


# ── routing: youtube open+play compound ─────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_query", [
    ("open youtube and play any famous song",      "any famous song"),
    ("open youtube and play any famous songlish song", "any famous songlish song"),
    ("open YouTube and play shape of you",         "shape of you"),
    ("youtube play lofi beats",                    "lofi beats"),
    ("open yt and play some jazz",                 "some jazz"),
])
def test_youtube_open_and_play_routes_to_search_youtube(router, phrase, expected_query):
    result = router.route(phrase)
    assert result.tool_name == "search_youtube", (
        f"{phrase!r} → {result.tool_name!r} {result.params!r} "
        f"(expected search_youtube, not media_control)"
    )
    assert result.params.get("query") == expected_query, (
        f"{phrase!r} → query={result.params.get('query')!r} "
        f"(expected {expected_query!r})"
    )


@pytest.mark.parametrize("phrase", [
    "play music",
    "play",
    "pause",
    "pause the music",
    "resume music",
    "next song",
])
def test_plain_media_commands_still_hit_media_control(router, phrase):
    """The compound pattern requires 'youtube' — plain media verbs unchanged."""
    result = router.route(phrase)
    assert result.tool_name == "media_control", (
        f"{phrase!r} → {result.tool_name!r} (expected media_control)"
    )


def test_play_x_on_youtube_unchanged(router):
    """play BEFORE youtube is handled by the original search_youtube pattern."""
    result = router.route("play shape of you on youtube")
    assert result.tool_name == "search_youtube", (
        f"got {result.tool_name!r} {result.params!r}"
    )
    assert result.params.get("query") == "shape of you"


def test_open_youtube_alone_still_opens_app(router):
    result = router.route("open youtube")
    assert result.tool_name == "open_application", (
        f"{result.tool_name!r} — bare 'open youtube' must stay an app open"
    )


# ── emotion: stress misreads fixed ───────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "Perfect. Now can you open YouTube and play something?",
    "It's work time, buddy.",
    "Now open the Microsoft Store.",
    "Great, open chrome now.",
])
def test_calm_praise_not_stress(emotion, phrase):
    result = emotion.detect_text(phrase)
    assert result.emotion != "stress", (
        f"{phrase!r} misread as stress (generic words in _STRESS_WORDS regression)"
    )


def test_genuine_praise_is_excitement(emotion):
    result = emotion.detect_text("Perfect. Now can you open YouTube and play something?")
    assert result.emotion == "excitement", (
        f"spoken praise without '!' should be excitement, got {result.emotion!r}"
    )


@pytest.mark.parametrize("phrase", [
    "this is urgent, do it immediately",
    "hurry up, I have a deadline",
    "jaldi karo",
])
def test_real_stress_still_detected(emotion, phrase):
    result = emotion.detect_text(phrase)
    assert result.emotion == "stress", (
        f"{phrase!r} → {result.emotion!r} (genuine urgency must stay stress)"
    )


@pytest.mark.parametrize("phrase", [
    "this keeps failing again!",
    "ugh it's broken again",
])
def test_frustration_beats_excitement(emotion, phrase):
    result = emotion.detect_text(phrase)
    assert result.emotion == "frustration", (
        f"{phrase!r} → {result.emotion!r} (frustration vocab must win)"
    )


@pytest.mark.parametrize("phrase", [
    "wow amazing!",
    "wow, that's amazing!",
])
def test_excitement_with_punctuation(emotion, phrase):
    result = emotion.detect_text(phrase)
    assert result.emotion == "excitement", (
        f"{phrase!r} → {result.emotion!r}"
    )
