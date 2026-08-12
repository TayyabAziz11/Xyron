"""
Unit tests for active_context and follow_up_resolver.
Tests cover all Part 1–6 scenarios from the spec.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure backend/ is on sys.path
BACKEND = str(Path(__file__).parent.parent)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api.services.active_context import ActiveContextService, CONTEXT_TTL
from api.services.follow_up_resolver import resolve, FollowUpResult


# ── ActiveContext tests ────────────────────────────────────────────────────────

class TestActiveContext:

    def setup_method(self):
        self.ctx = ActiveContextService()

    def test_initially_empty(self):
        snap = self.ctx.get()
        assert snap["current_platform"] is None
        assert snap["current_folder"] is None

    def test_is_active_false_initially(self):
        assert self.ctx.is_active() is False

    def test_open_app_microsoft_store(self):
        self.ctx.update_from_tool(
            "open_application",
            {"app_name": "Microsoft Store"},
            {},
            success=True,
        )
        snap = self.ctx.get()
        assert snap["current_platform"] == "microsoft_store"
        assert snap["current_goal"] == "app_installation"
        assert snap["current_app"] == "Microsoft Store"
        assert self.ctx.is_active()

    def test_open_youtube_url(self):
        self.ctx.update_from_tool(
            "search_youtube",
            {"query": "believer"},
            {},
            success=True,
        )
        snap = self.ctx.get()
        assert snap["current_platform"] == "youtube"
        assert snap["current_goal"] == "media_search"
        assert snap["current_entity"] == "believer"

    def test_open_directory_updates_folder(self):
        self.ctx.update_from_tool(
            "open_directory",
            {"path": "C:\\Users\\Downloads"},
            {"path": "C:\\Users\\Downloads"},
            success=True,
        )
        snap = self.ctx.get()
        assert snap["current_folder"] == "Downloads"
        assert snap["current_goal"] == "file_management"

    def test_smart_open_updates_folder(self):
        self.ctx.update_from_tool(
            "smart_open",
            {"query": "hackathon"},
            {"path": "E:\\Projects\\Hackathon", "action_path": "E:\\Projects\\Hackathon"},
            success=True,
        )
        snap = self.ctx.get()
        assert snap["current_folder"] == "Hackathon"

    def test_failed_tool_does_not_update_platform(self):
        # Failure should not establish platform context
        self.ctx.update_from_tool("open_application", {"app_name": "Store"}, {}, success=False)
        snap = self.ctx.get()
        assert snap["current_platform"] is None
        assert self.ctx.is_active() is False

    def test_context_expiry(self):
        self.ctx.update_from_tool("search_youtube", {"query": "test"}, {}, success=True)
        # Artificially age the context
        self.ctx._last_update -= CONTEXT_TTL + 1
        snap = self.ctx.get()
        assert snap["current_platform"] is None  # expired

    def test_reset_clears_context(self):
        self.ctx.update_from_tool("search_youtube", {"query": "test"}, {}, success=True)
        self.ctx.reset()
        assert self.ctx.is_active() is False

    def test_current_platform_shortcut(self):
        self.ctx.update_from_tool("search_youtube", {"query": "q"}, {}, success=True)
        assert self.ctx.current_platform() == "youtube"

    def test_current_folder_shortcut(self):
        self.ctx.update_from_tool(
            "open_directory", {"path": "/downloads"}, {"path": "/downloads"}, success=True
        )
        assert self.ctx.current_folder() is not None


# ── FollowUpResolver tests ─────────────────────────────────────────────────────

def _ctx(platform=None, folder=None, entity=None, goal=None):
    return {
        "current_platform": platform,
        "current_folder":   folder,
        "current_entity":   entity,
        "current_goal":     goal,
    }


class TestFollowUpResolver:

    # ── Microsoft Store context ───────────────────────────────────────────────

    def test_download_app_in_store_context(self):
        r = resolve("download whatsapp", _ctx(platform="microsoft_store"))
        assert r.tool_name == "install_store_app"
        assert r.tool_params["app_name"] == "whatsapp"
        assert r.was_resolved

    def test_install_app_in_store_context(self):
        r = resolve("install spotify", _ctx(platform="microsoft_store"))
        assert r.tool_name == "install_store_app"
        assert r.tool_params["app_name"] == "spotify"

    def test_get_app_in_store_context(self):
        r = resolve("get telegram", _ctx(platform="microsoft_store"))
        assert r.tool_name == "install_store_app"
        assert r.tool_params["app_name"] == "telegram"

    def test_download_this_app_asks_clarification(self):
        r = resolve("download this app", _ctx(platform="microsoft_store"))
        assert r.needs_clarification is True
        assert "install" in r.clarification_prompt.lower() or "app" in r.clarification_prompt.lower()

    def test_install_this_app_asks_clarification(self):
        r = resolve("install the app", _ctx(platform="microsoft_store"))
        assert r.needs_clarification is True

    # ── YouTube context ───────────────────────────────────────────────────────

    def test_play_in_youtube_context(self):
        r = resolve("play believer", _ctx(platform="youtube"))
        assert r.tool_name == "search_youtube"
        assert r.tool_params["query"] == "believer"

    def test_watch_in_youtube_context(self):
        r = resolve("watch imagine dragons", _ctx(platform="youtube"))
        assert r.tool_name == "search_youtube"
        assert "imagine dragons" in r.tool_params["query"]

    def test_search_in_youtube_context(self):
        r = resolve("search for lofi music", _ctx(platform="youtube"))
        assert r.tool_name == "search_youtube"
        assert "lofi music" in r.tool_params["query"]

    def test_media_control_in_youtube_passes_through(self):
        r = resolve("pause it", _ctx(platform="youtube"))
        assert r.tool_name == ""  # not intercepted — let normal routing handle

    # ── Folder / explorer context ─────────────────────────────────────────────

    def test_open_latest_file_in_folder(self):
        r = resolve("open latest file", _ctx(platform="explorer", folder="Downloads"))
        assert r.was_resolved
        assert "Downloads" in r.resolved
        assert "latest" in r.resolved.lower()

    def test_open_latest_document_in_folder(self):
        r = resolve("open last document", _ctx(folder="Documents"))
        assert r.was_resolved

    def test_open_it_in_vscode(self):
        r = resolve("open it in vs code", _ctx(folder="hackathon"))
        assert r.was_resolved
        assert "hackathon" in r.resolved.lower()
        assert "vs code" in r.resolved.lower()

    def test_open_it_in_notepad(self):
        r = resolve("open it in notepad", _ctx(folder="Projects"))
        assert r.was_resolved
        assert "Projects" in r.resolved
        assert "notepad" in r.resolved.lower()

    def test_open_it_uses_current_folder(self):
        r = resolve("open it", _ctx(folder="Hackathon"))
        assert r.was_resolved
        assert "Hackathon" in r.resolved

    def test_open_it_no_folder_does_nothing(self):
        r = resolve("open it", _ctx())
        assert r.was_resolved is False

    # ── Bare install without context ──────────────────────────────────────────

    def test_bare_download_routes_to_store(self):
        r = resolve("download whatsapp", _ctx())
        assert r.tool_name == "install_store_app"
        assert r.tool_params["app_name"] == "whatsapp"

    def test_bare_install_routes_to_store(self):
        r = resolve("install capcut", _ctx())
        assert r.tool_name == "install_store_app"

    def test_pip_install_not_intercepted(self):
        r = resolve("install pip package requests", _ctx())
        # Should NOT route to store — has "pip" or "package"
        assert r.tool_name == ""

    def test_npm_install_not_intercepted(self):
        r = resolve("install npm package lodash", _ctx())
        assert r.tool_name == ""

    # ── No context — pass-through ─────────────────────────────────────────────

    def test_regular_command_passes_through(self):
        r = resolve("what time is it", _ctx())
        assert r.was_resolved is False
        assert r.tool_name == ""
        assert r.resolved == "what time is it"

    def test_open_youtube_passes_through(self):
        r = resolve("open youtube", _ctx())
        assert r.was_resolved is False

    # ── Performance ───────────────────────────────────────────────────────────

    def test_resolver_under_50ms(self):
        ctx = _ctx(platform="microsoft_store")
        t0 = time.monotonic()
        for _ in range(100):
            resolve("download whatsapp", ctx)
        elapsed_ms = (time.monotonic() - t0) * 1000 / 100
        assert elapsed_ms < 50, f"Resolver averaged {elapsed_ms:.1f}ms (limit 50ms)"

    def test_context_update_under_20ms(self):
        svc = ActiveContextService()
        t0 = time.monotonic()
        for _ in range(100):
            svc.update_from_tool("search_youtube", {"query": "test"}, {}, success=True)
        elapsed_ms = (time.monotonic() - t0) * 1000 / 100
        assert elapsed_ms < 20, f"Context update averaged {elapsed_ms:.1f}ms (limit 20ms)"
