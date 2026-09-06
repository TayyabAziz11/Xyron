"""
Tests for the human, emotion-aware reply layer + TTS chunking speed fixes.

Covers:
  - api.services.conversational_replies: tone mapping, anti-repeat,
    non-empty replies for every covered tool, media_control replies.
  - api.routers.voice_ws._split_for_tts: 84-char two-sentence replies stay
    ONE chunk (Kokoro + RVC single pass) and smart punctuation is
    normalized before synthesis.

Run: pytest tests/test_conversational_replies.py -v
"""
from __future__ import annotations

import os
import random
import sys

# Ensure backend/ is on sys.path regardless of cwd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from api.services import conversational_replies as cr


# ── tone mapping ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("emotion, expected_tone", [
    ("excitement",  "upbeat"),
    ("hype",        "upbeat"),
    ("pride",       "upbeat"),
    ("humor",       "upbeat"),
    ("stress",      "reassuring"),
    ("frustration", "reassuring"),
    ("curiosity",   "warm"),
    ("seriousness", "neutral"),
    ("calmness",    "neutral"),
    ("sarcasm",     "neutral"),
])
def test_emotion_to_tone(emotion, expected_tone):
    assert cr.emotion_to_tone(emotion) == expected_tone


@pytest.mark.parametrize("emotion", [None, "", "unknown_emotion", "  EXCITEMENT  "])
def test_emotion_to_tone_edge_cases(emotion):
    tone = cr.emotion_to_tone(emotion)
    assert tone in cr.TONES
    # unknown/None must degrade to neutral; whitespace/case must normalize
    if emotion in (None, "", "unknown_emotion"):
        assert tone == "neutral"
    elif "excitement" in str(emotion).lower():
        assert tone == "upbeat"


# ── no empty replies for any covered tool × tone ─────────────────────────────

_SAMPLE_PARAMS = {
    "open_application":      {"app_name": "microsoft store"},
    "open_system_settings":  {"page": "display"},
    "open_drive":            {"drive": "D"},
    "open_directory":        {"query": "Downloads"},
    "smart_open":            {"path": r"C:\Users\me\Projects"},
    "search_youtube":        {"query": "any famous song"},
    "search_web":            {"query": "weather today"},
    "open_url":              {"site": "example.com"},
    "play_media_file":       {"query": "song.mp3"},
    "install_store_app":     {"app_name": "instagram"},
    "some_future_tool":      {},   # exercises the _generic fallback
}


@pytest.mark.parametrize("tool_name, params", list(_SAMPLE_PARAMS.items()))
@pytest.mark.parametrize("emotion", [None, "excitement", "stress", "curiosity", "seriousness"])
def test_ack_never_empty(tool_name, params, emotion):
    text = cr.pick_ack(tool_name, params, emotion)
    assert isinstance(text, str) and text.strip(), (
        f"empty ack for {tool_name!r} emotion={emotion!r}"
    )
    assert "{" not in text, f"unformatted placeholder leaked: {text!r}"


@pytest.mark.parametrize("tool_name, params", list(_SAMPLE_PARAMS.items()))
@pytest.mark.parametrize("emotion", [None, "excitement", "stress", "curiosity", "seriousness"])
def test_completion_never_empty(tool_name, params, emotion):
    text = cr.pick_completion(tool_name, params, emotion)
    assert isinstance(text, str) and text.strip(), (
        f"empty completion for {tool_name!r} emotion={emotion!r}"
    )
    assert "{" not in text, f"unformatted placeholder leaked: {text!r}"


def test_pick_reply_dispatch():
    # Anti-repeat makes exact equality flaky — dispatch must just return
    # a non-empty human reply for both kinds.
    assert cr.pick_reply("ack", "open_application", {"app_name": "chrome"}).strip()
    assert cr.pick_reply("completion", "open_application", {"app_name": "chrome"}).strip()


def test_entity_names_render():
    done = cr.pick_completion("open_application", {"app_name": "chrome"}, None)
    assert "Chrome" in done, f"expected titled app name, got {done!r}"
    settings = cr.pick_ack("open_system_settings", {"page": "wifi"}, None)
    assert "Wi-Fi" in settings, f"expected human page label, got {settings!r}"
    store = cr.pick_ack("install_store_app", {"app_name": "instagram"}, None)
    assert "Instagram" in store, f"expected titled app name, got {store!r}"


def test_youtube_open_keeps_mood_question_whitelist():
    # The only whitelisted follow-up: bare "open youtube" completion.
    text = cr.pick_completion("open_application", {"app_name": "youtube"}, None)
    assert "YouTube" in text and ("mood" in text or "recommendation" in text or "watch" in text)


# ── anti-repeat ──────────────────────────────────────────────────────────────

def test_ack_anti_repeat():
    cr._last_reply.clear()
    random.seed(0)
    prev = None
    for _ in range(30):
        text = cr.pick_ack("open_application", {"app_name": "notepad"}, None)
        if prev is not None:
            assert text != prev, "same ack variant spoken twice in a row"
        prev = text


def test_completion_anti_repeat():
    cr._last_reply.clear()
    random.seed(0)
    prev = None
    for _ in range(30):
        text = cr.pick_completion("search_web", {}, None)
        if prev is not None:
            assert text != prev, "same completion variant spoken twice in a row"
        prev = text


def test_media_reply_actions():
    cr._last_reply.clear()
    for action in ("play_pause", "next", "prev", "stop"):
        text = cr.media_reply(action)
        assert isinstance(text, str) and text.strip()
    # "Playing / paused." must never come back
    assert cr.media_reply("play_pause") != "Playing / paused."
    # unknown action falls back safely
    assert cr.media_reply("volume_up").strip()


# ── TTS chunking + punctuation normalization (voice_ws) ─────────────────────

@pytest.fixture(scope="module")
def voice_ws():
    from api.routers import voice_ws as vws
    return vws


def test_tts_chunk_limit_raised(voice_ws):
    assert voice_ws._TTS_MAX_CHARS >= 160, (
        "chunk limit must stay >= 160 — 80 caused double Kokoro+RVC passes"
    )


def test_84_char_two_sentence_reply_single_chunk(voice_ws):
    # The exact turn-8 shape: two short sentences, ~84 chars total.
    text = ("I found the Microsoft Store page for Instagram. "
            "Say install when you're ready.")
    assert len(text) <= 160
    chunks = voice_ws._split_for_tts(text)
    assert len(chunks) == 1, f"expected ONE chunk, got {len(chunks)}: {chunks}"
    assert "".join(chunks).strip() == text.strip()


def test_long_text_still_splits_at_sentence_boundaries(voice_ws):
    s1 = "Here is the first fairly long sentence that goes on for a while."
    s2 = "The second sentence also rambles quite a bit to pass the limit."
    s3 = "And a third one so we definitely exceed the chunk budget now."
    chunks = voice_ws._split_for_tts(f"{s1} {s2} {s3}")
    assert len(chunks) > 1
    assert all(len(c) <= voice_ws._TTS_MAX_CHARS for c in chunks)
    assert " ".join(chunks) == f"{s1} {s2} {s3}"


def test_smart_punctuation_normalized_before_tts(voice_ws):
    text = "Got it \u2014 opening Chrome\u2019s settings now."
    chunks = voice_ws._split_for_tts(text)
    joined = " ".join(chunks)
    assert "\u2014" not in joined and "\u2019" not in joined, (
        f"smart punctuation leaked into TTS input: {joined!r}"
    )
    assert "Got it, opening Chrome's settings now." == joined.strip()
