"""
Phase 12 — Live routing regression tests.

Covers:
 1. "open python folder in E drive" → query=python, type=folder, drive=E
 2. "open e drive and then open python folder" (planner) → two steps
 3. "play song you break my heart" → search_youtube (NOT media_control)
 4. "open youtube and play song you break my heart" → planner youtube context
 5. bare "play" → media_control play_pause
 6. ExplorerSkill sanitizes "python folder" query → query=python, type=folder
 7. get_success_response has no duplicate type word ("folder folder")

Run:
    cd /mnt/e/Xyron/backend
    source .venv/bin/activate
    pytest tests/backend/test_live_routing.py -v
"""
from __future__ import annotations

import sys
import pytest

sys.path.insert(0, "backend")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ir():
    from api.services.intent_router import IntentRouter
    return IntentRouter()


@pytest.fixture(scope="module")
def planner():
    from brain.planner import Planner, _smart_split
    return Planner(), _smart_split


# ── Test 1: "open python folder in E drive" → clean params ───────────────────

class TestFileObjectParse:

    def test_open_python_folder_in_e_drive(self, ir):
        r = ir.route("open python folder in e drive")
        assert r.tool_name == "smart_open", f"Expected smart_open, got {r.tool_name}"
        assert r.params.get("query") == "python", \
            f"Expected query=python, got {r.params.get('query')!r}"
        assert r.params.get("type") == "folder", \
            f"Expected type=folder, got {r.params.get('type')!r}"
        assert r.params.get("drive") == "E", \
            f"Expected drive=E, got {r.params.get('drive')!r}"
        assert r.tier == 2

    def test_open_backend_folder_in_e_drive(self, ir):
        r = ir.route("open backend folder in E drive")
        assert r.tool_name == "smart_open"
        assert r.params.get("query") == "backend"
        assert r.params.get("type") == "folder"
        assert r.params.get("drive") == "E"

    def test_find_resume_file_in_d_drive(self, ir):
        r = ir.route("find resume file in D drive")
        assert r.tool_name == "smart_open"
        assert r.params.get("query") == "resume"
        assert r.params.get("type") == "file"
        assert r.params.get("drive") == "D"

    def test_open_invoice_document_in_e_drive(self, ir):
        r = ir.route("open invoice document in E drive")
        assert r.tool_name == "smart_open"
        assert r.params.get("query") == "invoice"
        assert r.params.get("drive") == "E"

    def test_query_never_contains_folder(self, ir):
        """query param must never include the word 'folder'."""
        for phrase in [
            "open python folder in E drive",
            "open backend folder in C drive",
            "find projects folder in D drive",
        ]:
            r = ir.route(phrase)
            q = (r.params.get("query") or "").lower()
            assert "folder" not in q, \
                f"query {q!r} still contains 'folder' for input {phrase!r}"


# ── Test 2: Planner splits compound drive+folder command ─────────────────────

class TestPlannerDriveFolderSplit:

    def test_split_open_e_drive_then_python(self, planner):
        _, _split = planner
        parts = _split("open e drive and then open python folder")
        assert len(parts) == 2, f"Expected 2 steps, got {parts}"
        assert "e drive" in parts[0].lower()
        assert "python" in parts[1].lower()

    def test_drive_route(self, ir):
        r = ir.route("open e drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "E"

    def test_python_folder_after_drive_inject(self, ir):
        # The planner injects "in E drive" context — simulate it
        r = ir.route("open python folder in e drive")
        assert r.tool_name == "smart_open"
        assert r.params.get("query") == "python"
        assert r.params.get("type") == "folder"
        assert r.params.get("drive") == "E"


# ── Test 3: "play song X" → search_youtube (NOT media_control) ───────────────

class TestYouTubeSongRouting:

    def test_play_song_you_break_my_heart(self, ir):
        r = ir.route("play song you break my heart")
        assert r.tool_name == "search_youtube", \
            f"Expected search_youtube, got {r.tool_name!r} (params={r.params})"
        assert r.params.get("query") == "you break my heart"
        assert r.tier == 2

    def test_play_the_song_you_break_my_heart(self, ir):
        r = ir.route("play the song you break my heart")
        assert r.tool_name == "search_youtube"
        assert r.params.get("query") == "you break my heart"

    def test_play_song_named_x(self, ir):
        r = ir.route("play song named blinding lights")
        assert r.tool_name == "search_youtube"
        assert r.params.get("query") == "blinding lights"

    def test_play_x_on_youtube(self, ir):
        r = ir.route("play blinding lights on youtube")
        assert r.tool_name == "search_youtube"
        assert "blinding lights" in (r.params.get("query") or "").lower()

    def test_play_lofi_music(self, ir):
        r = ir.route("play lofi music")
        assert r.tool_name == "search_youtube", \
            f"Expected search_youtube for 'play lofi music', got {r.tool_name!r}"

    def test_play_chill_songs(self, ir):
        r = ir.route("play chill songs")
        assert r.tool_name == "search_youtube", \
            f"Expected search_youtube for 'play chill songs', got {r.tool_name!r}"


# ── Test 4: planner youtube context — step 2 inherits platform ───────────────

class TestPlannerYouTubeContext:

    def test_split_open_youtube_and_play(self, planner):
        _, _split = planner
        parts = _split("open youtube and play song you break my heart")
        assert len(parts) == 2, f"Expected 2 steps, got {parts}"
        assert "youtube" in parts[0].lower()
        assert "play" in parts[1].lower()

    def test_play_on_youtube_routes_to_search_youtube(self, ir):
        # Planner injects "on youtube" context — simulate
        r = ir.route("play song you break my heart on youtube")
        assert r.tool_name == "search_youtube"

    def test_play_x_on_youtube_direct(self, ir):
        r = ir.route("play you break my heart on youtube")
        assert r.tool_name == "search_youtube"


# ── Test 5: bare "play" → media_control ──────────────────────────────────────

class TestMediaControlGuard:

    def test_bare_play(self, ir):
        r = ir.route("play")
        assert r.tool_name == "media_control"
        assert r.params.get("action") == "play_pause"

    def test_pause(self, ir):
        r = ir.route("pause")
        assert r.tool_name == "media_control"
        assert r.params.get("action") == "play_pause"

    def test_next(self, ir):
        r = ir.route("next song")
        assert r.tool_name == "media_control"
        assert r.params.get("action") == "next"

    def test_stop_music(self, ir):
        r = ir.route("stop music")
        assert r.tool_name == "media_control"
        assert r.params.get("action") == "stop"

    def test_play_it(self, ir):
        r = ir.route("play it")
        assert r.tool_name == "media_control"


# ── Test 6: ExplorerSkill sanitizes "python folder" query ────────────────────

class TestExplorerSkillSanitize:

    def test_sanitize_python_folder(self):
        from operator_mode.skills.explorer_skill import _sanitize_query
        clean, typ = _sanitize_query("python folder", "any")
        assert clean == "python", f"Expected 'python', got {clean!r}"
        assert typ == "folder", f"Expected 'folder', got {typ!r}"

    def test_sanitize_resume_file(self):
        from operator_mode.skills.explorer_skill import _sanitize_query
        clean, typ = _sanitize_query("resume file", "any")
        assert clean == "resume"
        assert typ == "file"

    def test_sanitize_clean_query_unchanged(self):
        from operator_mode.skills.explorer_skill import _sanitize_query
        clean, typ = _sanitize_query("python", "folder")
        assert clean == "python"
        assert typ == "folder"

    def test_sanitize_multi_word(self):
        from operator_mode.skills.explorer_skill import _sanitize_query
        clean, typ = _sanitize_query("my projects folder", "any")
        assert clean == "my projects"
        assert typ == "folder"


# ── Test 7: get_success_response has no duplicate type word ──────────────────

class TestExplorerResponse:

    @pytest.mark.asyncio
    async def test_no_folder_folder(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill = ExplorerSkill()
        # Simulate bad params where query leaked as "python folder"
        resp = await skill.get_success_response({"query": "python folder", "drive": "E", "type": "any"})
        assert "folder folder" not in resp.lower(), \
            f"Duplicate 'folder folder' found in response: {resp!r}"
        assert "python" in resp.lower()
        assert "E" in resp

    @pytest.mark.asyncio
    async def test_clean_query_response(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill = ExplorerSkill()
        resp = await skill.get_success_response({"query": "python", "drive": "E", "type": "folder"})
        assert "folder folder" not in resp.lower()
        assert "Python" in resp  # title-cased
        assert "E" in resp
