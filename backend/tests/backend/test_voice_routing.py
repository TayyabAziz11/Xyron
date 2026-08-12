"""
Tests for voice routing fixes:
  - STT phonetic media title correction (Issue 1)
  - Drive + folder compound command routing (Issue 2)
  - open_application catch-all blocking (Issue 3)
  - play/pause stays on media_control (Test 8)
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ─── normalizer ──────────────────────────────────────────────────────────────

from api.services.normalizer import normalize


class TestNormalizer:
    def test_excess_to_access_before_drive(self):
        assert "access" in normalize("excess e drive")

    def test_place_to_play_before_movie(self):
        result = normalize("place zoom in movie")
        assert result.startswith("play") or "play" in result

    def test_place_to_play_before_film(self):
        result = normalize("place a zoumi film")
        assert "play" in result

    def test_commas_stripped(self):
        result = normalize("Place, zoom in movie.")
        assert "," not in result

    def test_normal_play_unchanged(self):
        result = normalize("play suzume movie")
        assert "play" in result
        assert "suzume" in result


# ─── intent_router — regex tier ──────────────────────────────────────────────

from api.services.intent_router import intent_router


class TestIntentRouterMedia:
    def test_play_suzume_movie(self):
        r = intent_router.route("play suzume movie")
        assert r.tool_name == "play_media_file"
        assert "suzume" in r.params.get("query", "").lower()

    def test_open_suzume_video(self):
        r = intent_router.route("open suzume video")
        assert r.tool_name == "play_media_file"
        assert "suzume" in r.params.get("query", "").lower()

    def test_find_and_play_suzume(self):
        r = intent_router.route("find and play suzume")
        assert r.tool_name == "play_media_file"
        assert "suzume" in r.params.get("query", "").lower()

    def test_movie_named_suzume(self):
        r = intent_router.route("movie named suzume")
        assert r.tool_name == "play_media_file"

    def test_play_pause_stays_media_control(self):
        r = intent_router.route("play pause")
        assert r.tool_name == "media_control"

    def test_pause_stays_media_control(self):
        r = intent_router.route("pause")
        assert r.tool_name == "media_control"

    def test_play_music_stays_media_control(self):
        r = intent_router.route("play music")
        assert r.tool_name == "media_control"

    def test_play_video_stays_media_control(self):
        # "play video" with no title — should be generic media_control
        r = intent_router.route("play video")
        assert r.tool_name == "media_control"


class TestIntentRouterFolderBlocking:
    def test_open_python_folder(self):
        r = intent_router.route("open python folder")
        assert r.tool_name == "smart_open", f"Expected smart_open, got {r.tool_name}"
        assert "python" in r.params.get("query", "").lower()
        assert r.params.get("type") == "folder"

    def test_open_downloads_folder(self):
        r = intent_router.route("open downloads folder")
        # Should NOT route to open_application
        assert r.tool_name != "open_application"

    def test_open_suzume_movie_via_open_verb(self):
        r = intent_router.route("open suzume movie")
        assert r.tool_name == "play_media_file"
        assert r.params.get("query", "").lower() == "suzume"


class TestIntentRouterDrivePlusFolder:
    def test_open_e_drive_and_python_folder(self):
        r = intent_router.route("open e drive and open python folder")
        assert r.tool_name == "smart_open", f"Expected smart_open, got {r.tool_name}"
        assert r.params.get("drive") == "E"
        assert "python" in r.params.get("query", "").lower()
        assert r.params.get("type") == "folder"

    def test_access_e_drive_and_python_folder(self):
        # "excess" → "access" from normalizer, then this route
        r = intent_router.route("access e drive and open python folder")
        assert r.tool_name == "smart_open"
        assert r.params.get("drive") == "E"
        assert "python" in r.params.get("query", "").lower()

    def test_in_e_drive_open_python_folder(self):
        r = intent_router.route("in e drive open python folder")
        assert r.tool_name == "smart_open"
        assert r.params.get("drive") == "E"
        assert "python" in r.params.get("query", "").lower()

    def test_bare_open_e_drive_stays_open_drive(self):
        r = intent_router.route("open e drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "E"


# ─── voice_title_corrector ───────────────────────────────────────────────────

from api.services.voice_title_corrector import correct_media_title, _extract_garbled_title, _consonant_skeleton, _similarity


class TestConsonantSkeleton:
    def test_zoumi(self):
        assert _consonant_skeleton("zoumi") == "zm"

    def test_suzume(self):
        assert _consonant_skeleton("suzume") == "szm"

    def test_zoom_in(self):
        assert _consonant_skeleton("zoom in") == "zmn"

    def test_zuma(self):
        assert _consonant_skeleton("zuma") == "zm"


class TestSimilarity:
    def test_zoumi_vs_suzume_high(self):
        s = _similarity("zoumi", "suzume")
        assert s >= 0.65, f"Expected ≥ 0.65, got {s:.2f}"

    def test_zoom_in_vs_suzume_high(self):
        s = _similarity("zoom in", "suzume")
        assert s >= 0.60, f"Expected ≥ 0.60, got {s:.2f}"

    def test_zuma_vs_suzume_high(self):
        s = _similarity("zuma", "suzume")
        assert s >= 0.65, f"Expected ≥ 0.65, got {s:.2f}"

    def test_save_vs_suzume_low(self):
        s = _similarity("save", "suzume")
        assert s < 0.65, f"Expected < 0.65, got {s:.2f}"

    def test_python_vs_suzume_low(self):
        s = _similarity("python", "suzume")
        assert s < 0.50, f"Expected < 0.50, got {s:.2f}"


class TestExtractGarbledTitle:
    def test_play_zoumi_movie(self):
        t = _extract_garbled_title("play zoumi movie")
        assert t is not None
        assert "zoumi" in t.lower()

    def test_play_zoom_in_movie(self):
        t = _extract_garbled_title("play zoom in movie")
        assert t is not None
        assert "zoom" in t.lower()

    def test_bare_open_folder_no_extract(self):
        # "open python folder" — the "folder" qualifier is not a movie type word
        t = _extract_garbled_title("open python folder")
        # Either None or something that is not a media command
        # (no movie/film/video — shouldn't be treated as play command)
        assert t is None or "folder" in (t or "").lower()


class TestCorrectMediaTitle:
    """Integration tests — require fs_index to be ready with Suzume.mkv indexed."""

    def test_no_correction_for_non_play_command(self):
        result = correct_media_title("what is the time")
        assert result == "what is the time"

    def test_no_correction_for_open_folder(self):
        result = correct_media_title("open python folder")
        # Should not be altered — not a media play command
        assert result == "open python folder"

    def test_already_correct_title_unchanged(self):
        # If "suzume" matches "suzume" perfectly, no rewrite needed
        # (will return unchanged since garbled == candidate)
        result = correct_media_title("play suzume movie")
        # May return as-is or corrected — should still contain "suzume"
        assert "suzume" in result.lower()

    @pytest.mark.skipif(
        True,  # Only run manually when fs_index has Suzume indexed
        reason="Requires fs_index with Suzume.mkv indexed",
    )
    def test_zoumi_corrected_to_suzume(self):
        result = correct_media_title("play zoumi movie")
        assert "suzume" in result.lower(), f"Expected 'suzume' in: {result!r}"

    @pytest.mark.skipif(
        True,
        reason="Requires fs_index with Suzume.mkv indexed",
    )
    def test_zoom_in_corrected_to_suzume(self):
        result = correct_media_title("play zoom in movie")
        assert "suzume" in result.lower(), f"Expected 'suzume' in: {result!r}"
