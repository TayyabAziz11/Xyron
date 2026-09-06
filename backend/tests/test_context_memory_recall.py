"""
Regression tests for the context-understanding bugs in the 2026-08 backend
logs ("open youtube and play any famous english song" → "play love me like
you do" not understood):

  1. YouTube candidate selection by TITLE — STT produced "Learn love me
     like you do." (Whisper misheard "play"); Tier 0f4 only understood
     ordinals/this-one, so it fell to Tier4 (0.32 < 0.65) and the LLM
     babbled. video_selection.match_candidate must pick the right
     candidate despite the noise word.
  2. Anaphoric "play the song" — got routed to media_control play_pause
     ("playback toggled"). is_anaphoric_play must flag these so Tier 0f4
     replays the source search instead.
  3. Persistent activity memory — songs played / folders opened / apps
     launched must survive restarts (JSONL) and answer voice recall:
     "what is my most recent folder", "what songs did you play today",
     "what was I working on", "play the same songs you played yesterday".

Run: pytest tests/test_context_memory_recall.py -v
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from api.services.video_selection import match_candidate, is_anaphoric_play
from api.services.activity_memory import ActivityMemory, parse_recall_query, period_bounds


# ── The exact live disambiguation list from the backend log ───────────────────

_CANDIDATES = [
    {"title": "Imagine Dragons - Believer", "url": "https://youtu.be/believer1"},
    {"title": "Ellie Goulding - Love Me Like You Do (Lyrics)", "url": "https://youtu.be/loveme1"},
    {"title": "Top 50 English Songs 2026 - Best Popular Music Playlist", "url": "https://youtu.be/playlist1"},
    {"title": "Alan Walker - Faded", "url": "https://youtu.be/faded1"},
    {"title": "Shape of You - Ed Sheeran [Official Video]", "url": "https://youtu.be/shape1"},
]


# ── 1. Title-based candidate selection (STT-noise tolerant) ──────────────────

@pytest.mark.parametrize("utterance", [
    "Learn love me like you do.",          # the EXACT live STT transcript
    "play love me like you do",
    "Love me like you do",
    "no, play love me like you do please",
])
def test_stt_noisy_title_selects_right_candidate(utterance):
    m = match_candidate(utterance, _CANDIDATES)
    assert m is not None, f"{utterance!r} matched nothing — live bug"
    assert m["index"] == 1, f"picked {m['candidate']['title']!r} instead of Love Me Like You Do"
    assert m["score"] >= 0.6


@pytest.mark.parametrize("utterance", ["play believer", "believer", "Believer please"])
def test_single_word_title_selects(utterance):
    m = match_candidate(utterance, _CANDIDATES)
    assert m is not None and m["index"] == 0, f"{utterance!r} → {m!r}"


@pytest.mark.parametrize("utterance", [
    "play faded",
    "shape of you",
    "ed sheeran shape of you",
])
def test_other_titles_select_their_own_candidate(utterance):
    m = match_candidate(utterance, _CANDIDATES)
    assert m is not None, f"{utterance!r} matched nothing"
    assert m["candidate"]["title"].lower().find(
        "faded" if "faded" in utterance else "shape") != -1


@pytest.mark.parametrize("utterance", [
    "open chrome",
    "what time is it",
    "scroll down",
    "hello there",
])
def test_unrelated_utterances_dont_match(utterance):
    assert match_candidate(utterance, _CANDIDATES) is None, (
        f"{utterance!r} false-positived — would hijack unrelated turns"
    )


def test_decorative_title_tokens_ignored():
    cands = [{"title": "Song Title (Official Lyric Video) [4K]", "url": "u"}]
    # user never says the decorations; title must still be selectable
    m = match_candidate("play song title", cands)
    assert m is not None and m["score"] >= 0.99


# ── 2. Anaphoric "play the song" detection ───────────────────────────────────

@pytest.mark.parametrize("utterance", [
    "play the song",
    "play the song.",
    "No, I say play the song.",        # the EXACT live turn-10 transcript
    "play it",
    "now play the video",
    "play the music",
])
def test_anaphoric_play_detected(utterance):
    assert is_anaphoric_play(utterance), f"{utterance!r} must resolve anaphorically"


@pytest.mark.parametrize("utterance", [
    "play love me like you do",         # names a title — NOT anaphoric
    "play believer",
    "play the song love me like you do",
    "pause the music",                  # different verb handled elsewhere
    "open chrome",
])
def test_title_utterances_not_anaphoric(utterance):
    assert not is_anaphoric_play(utterance), (
        f"{utterance!r} wrongly flagged anaphoric — would replay the wrong thing"
    )


# ── 3. Activity memory: recording + persistence ──────────────────────────────

@pytest.fixture()
def mem(tmp_path):
    return ActivityMemory(store_path=tmp_path / "activity_memory.jsonl")


def test_record_played_song(mem):
    mem.record_from_tool("play_youtube_video",
                         {"url": "https://youtu.be/x", "title": "Love Me Like You Do"},
                         {})
    songs = mem.songs("today")
    assert len(songs) == 1
    assert songs[0]["name"] == "Love Me Like You Do"
    assert songs[0]["url"] == "https://youtu.be/x"


def test_record_autoplayed_search(mem):
    mem.record_from_tool("search_youtube",
                         {"query": "believer"},
                         {"autoplayed": True, "title": "Imagine Dragons - Believer",
                          "url": "https://youtu.be/b"})
    assert mem.songs("today")[0]["name"] == "Imagine Dragons - Believer"


def test_record_folder_and_app(mem):
    mem.record_from_tool("open_directory", {"path": r"E:\Projects\Xyron"},
                         {"path": r"E:\Projects\Xyron"})
    mem.record_from_tool("open_application", {"app_name": "Chrome"}, {})
    assert mem.folders("today")[0]["name"] == "Xyron"
    assert mem.apps("today")[0]["name"] == "Chrome"


def test_persistence_across_instances(tmp_path):
    p = tmp_path / "activity_memory.jsonl"
    m1 = ActivityMemory(store_path=p)
    m1.record_from_tool("play_youtube_video",
                        {"url": "https://youtu.be/x", "title": "Faded"}, {})
    # Fresh instance = simulated backend restart — memory must survive
    m2 = ActivityMemory(store_path=p)
    assert m2.songs("today")[0]["name"] == "Faded"


def test_yesterday_window_excludes_today(mem):
    mem.record_from_tool("play_youtube_video",
                         {"url": "u", "title": "Today Song"}, {})
    assert mem.songs("today")
    assert not mem.songs("yesterday"), "today's song leaked into yesterday's window"


def test_period_bounds_sane():
    from datetime import datetime
    now = datetime(2026, 8, 23, 15, 0, 0)
    s, e = period_bounds("yesterday", now=now)
    assert s < e <= now.timestamp()
    assert e - s == 86400
    s2, e2 = period_bounds("today", now=now)
    assert s2 <= now.timestamp() == e2


@pytest.mark.parametrize("phrase, max_window_minutes", [
    ("just now", 10),
    ("a minute ago", 10),
    ("a few minutes ago", 25),
    ("few minutes ago", 25),
    ("5 minutes ago", 15),
    ("an hour ago", 100),
    ("2 hours ago", 140),
    ("a while ago", 100),
])
def test_minute_and_hour_period_bounds(phrase, max_window_minutes):
    """'A few minutes ago' / 'N minutes ago' / 'an hour ago' must resolve to
    a tight recent window, not the 7-day 'recently' catch-all."""
    s, e = period_bounds(phrase)
    assert 0 < (e - s) <= max_window_minutes * 60, f"{phrase!r} → window {(e - s) / 60:.1f}min"


def test_recent_folder_respects_explicit_minutes_ago_period(mem):
    """A folder opened 2 hours ago must NOT be reported as opened 'a few
    minutes ago' — the whole point of asking with a time qualifier."""
    old_ts = time.time() - 2 * 3600
    mem._entries.append({"ts": int(old_ts), "kind": "folder", "name": "OldStuff"})
    mem.record_from_tool("open_directory", {}, {"path": r"C:\Users\Dell\Desktop"})
    r = mem.handle_query("what folder did you open a few minutes ago")
    assert r and "Desktop" in r["response"] and "OldStuff" not in r["response"]


# ── 4. Voice recall query handling ───────────────────────────────────────────

def test_recent_folder_question(mem):
    mem.record_from_tool("open_directory", {}, {"path": r"C:\Users\Dell\Documents\Invoices"})
    r = mem.handle_query("what is my most recent folder")
    assert r and "Invoices" in r["response"]
    assert r["play"] is None


def test_recent_folder_question_garbled_stt_live_log(mem):
    """Exact live-log regression (2026-08-28): STT heard 'last' as 'slot' and
    phrased it in 2nd person. Before the fix this fell through to smart_open,
    which searched the filesystem for a folder literally named that sentence
    and failed after 11s. It must answer from memory instead."""
    mem.record_from_tool("open_directory", {}, {"path": r"C:\Users\Dell\Desktop"})
    r = mem.handle_query("do you remember what slot folder you open")
    assert r is not None, "garbled 2nd-person folder recall not recognized"
    assert "Desktop" in r["response"]


def test_song_recall_question(mem):
    mem.record_from_tool("play_youtube_video",
                         {"url": "u1", "title": "Believer"}, {})
    mem.record_from_tool("play_youtube_video",
                         {"url": "u2", "title": "Faded"}, {})
    r = mem.handle_query("what songs did you play today")
    assert r and "Faded" in r["response"] and "Believer" in r["response"]


def test_replay_returns_playable_entry(mem):
    mem.record_from_tool("play_youtube_video",
                         {"url": "https://youtu.be/x", "title": "Love Me Like You Do"}, {})
    r = mem.handle_query("play the same songs you played today")
    assert r is not None
    assert r["play"] == {"url": "https://youtu.be/x", "title": "Love Me Like You Do"}
    assert "Love Me Like You Do" in r["response"]


def test_replay_empty_memory_is_graceful(mem):
    r = mem.handle_query("play the same songs you played yesterday")
    assert r is not None and r["play"] is None and "don't remember" in r["response"]


def test_worked_on_question(mem):
    mem.record_from_tool("open_directory", {}, {"path": r"E:\Xyron"})
    mem.record_from_tool("open_application", {"app_name": "VS Code"}, {})
    r = mem.handle_query("what was i working on today")
    assert r and "Xyron" in r["response"]


@pytest.mark.parametrize("utterance, action", [
    ("play the same songs you played yesterday", "replay_songs"),
    ("play those songs again",                   "replay_songs"),
    ("what songs did you play today",            "recall_songs"),
    ("what is my most recent folder",            "recent_folder"),
    ("what was the last folder i opened",        "recent_folder"),
    ("what was i working on yesterday",          "worked_on"),
    ("what did i work on today",                 "worked_on"),
    ("what apps did i open today",               "recent_apps"),
    # Live-log regression: STT garbled "last" -> "slot" and the user asked
    # in 2nd person ("you", since Xyron did the opening) — must still land
    # on recent_folder instead of falling through to smart_open/object search.
    ("do you remember what slot folder you open", "recent_folder"),
    ("what folder did you open",                 "recent_folder"),
    ("what folder did you open last",            "recent_folder"),
    ("folder did you open last",                 "recent_folder"),
    ("do you remember what app you opened",      "recent_apps"),
    ("what were you working on today",           "worked_on"),
])
def test_parse_recall_queries(utterance, action):
    d = parse_recall_query(utterance)
    assert d is not None and d["action"] == action, f"{utterance!r} → {d!r}"


@pytest.mark.parametrize("utterance", [
    "play any famous english song",      # fresh search, not a recall
    "open youtube",
    "what am i working on",              # present tense → Tier 0x screen query
    "what is on my screen",
    "pause the music",
    "find me a flight from dubai to japan",
])
def test_non_recall_queries_fall_through(utterance):
    assert parse_recall_query(utterance) is None, (
        f"{utterance!r} wrongly classified as recall — would shadow real routing"
    )


# ── 5. Resolver guards — recall/selection must reach the right tier ─────────

def test_v1_play_regex_ignores_recall_phrasing():
    """follow_up_resolver's YouTube 'play X' fast path must not swallow
    'play the same songs you played yesterday' — that's Tier 0m's job."""
    from api.services.follow_up_resolver import _PLAY_RE
    assert _PLAY_RE.match("play believer"), "normal play commands must still match"
    assert not _PLAY_RE.match("play the same songs you played yesterday")
    assert not _PLAY_RE.match("play the same songs from today")


def test_v2_skips_v1_fastpath_while_video_selection_pending():
    """While a YouTube disambiguation list is pending, the user's reply is a
    SELECTION — v1's generic 'play X → fresh search' must stand down so
    Tier 0f4 can match the exact candidate."""
    from api.services.follow_up_resolver_v2 import resolve_v2
    active_ctx = {"current_platform": "youtube"}
    session_state = {"pending_video_candidates": {"candidates": [], "created_at": 0}}
    r = resolve_v2("play love me like you do", active_ctx, None, session_state)
    assert r.tool_name != "search_youtube" or not r.was_resolved, (
        f"v1 fast path hijacked a pending-list selection: {r.tool_name}/{r.tool_params}"
    )
    # Sanity: with no pending list the same utterance still gets v1 routing
    r2 = resolve_v2("play love me like you do", active_ctx, None, {})
    assert r2.was_resolved and r2.tool_name == "search_youtube"
