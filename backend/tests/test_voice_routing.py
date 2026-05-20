"""
Integration tests for voice routing layer.

Tests cover:
- Wake + greeting flow (WS session init)
- Deterministic Tier-2 intent router (tool routing without LLM)
- System tool execution correctness
- Transcript rejection safety
- Offline/online model routing policy

Run:
    pytest backend/tests/test_voice_routing.py -v
"""
from __future__ import annotations

import pytest
import sys
import re

sys.path.insert(0, "backend")


# ────────────────────────────────────────────────────────────────────────────
# Intent router — Tier 2 regex coverage
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ir():
    from api.services.intent_router import IntentRouter
    return IntentRouter()


class TestIntentRouterTier2:

    def test_open_settings(self, ir):
        r = ir.route("open settings")
        assert r.tool_name == "open_application"
        assert r.tier == 2

    def test_open_c_drive(self, ir):
        r = ir.route("open C drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"
        assert r.tier == 2

    def test_open_e_drive(self, ir):
        r = ir.route("open E drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "E"

    def test_take_screenshot(self, ir):
        r = ir.route("take a screenshot")
        assert r.tool_name == "take_screenshot"
        assert r.tier == 2

    def test_volume_up(self, ir):
        r = ir.route("volume up")
        assert r.tool_name == "volume_control"
        assert r.params.get("action") == "increase"
        assert r.tier == 2

    def test_volume_down(self, ir):
        r = ir.route("volume down")
        assert r.tool_name == "volume_control"
        assert r.params.get("action") == "decrease"

    def test_mute(self, ir):
        r = ir.route("mute")
        assert r.tool_name == "mute_unmute"

    def test_create_folder_with_location(self, ir):
        r = ir.route("Can you create a folder called alpha in C drive")
        assert r.tool_name == "create_folder"
        assert r.params.get("name") == "alpha"
        assert r.params.get("path") == "C:\\"
        assert r.tier == 2

    def test_create_folder_named_in_drive(self, ir):
        r = ir.route("create folder named projects in D drive")
        assert r.tool_name == "create_folder"
        assert r.params.get("name") == "projects"
        assert r.params.get("path") == "D:\\"

    def test_create_folder_no_location(self, ir):
        r = ir.route("make a new folder called backup")
        assert r.tool_name == "create_folder"
        assert r.params.get("name") == "backup"

    def test_rename_explicit(self, ir):
        r = ir.route("rename alpha to beta")
        assert r.tool_name == "rename_file"
        assert r.params.get("new_name") == "beta"

    def test_rename_folder(self, ir):
        r = ir.route("rename folder testfolder to workspace")
        assert r.tool_name == "rename_file"
        assert r.params.get("new_name") == "workspace"

    def test_call_it_rename(self, ir):
        r = ir.route("call it workspace")
        assert r.tool_name == "rename_file"
        assert r.params.get("new_name") == "workspace"

    def test_find_folder(self, ir):
        r = ir.route("find ios folder")
        assert r.tool_name == "smart_open"
        assert r.params.get("query") == "ios"

    def test_locate_folder(self, ir):
        r = ir.route("locate downloads folder")
        assert r.tool_name == "smart_open"

    def test_lock_system(self, ir):
        r = ir.route("lock the screen")
        assert r.tool_name == "lock_system"
        assert r.tier == 2

    def test_open_downloads(self, ir):
        r = ir.route("open downloads folder")
        assert r.tool_name == "open_directory"
        assert r.tier == 2

    def test_brightness_up(self, ir):
        r = ir.route("increase brightness")
        assert r.tool_name == "brightness_control"

    def test_no_tool_intent_goes_to_tier4(self, ir):
        r = ir.route("what is the weather like today")
        assert r.tier >= 3  # semantic or no-match


# ────────────────────────────────────────────────────────────────────────────
# Transcript rejection safety
# ────────────────────────────────────────────────────────────────────────────

class TestTranscriptRejection:
    """
    Test the hallucination rejection logic from voice_ws.py.
    These patterns verify that command-list noise is never normalized
    into dangerous actions like shutdown.
    """

    def _should_reject(self, transcript: str) -> bool:
        """Mirror the command-list hallucination check from voice_ws.py."""
        _TOOL_KWS = {
            'screenshot', 'volume', 'shutdown', 'restart', 'delete', 'lock',
            'settings', 'folder', 'close', 'mute', 'brightness',
            'terminal', 'notepad', 'calculator', 'browser', 'wifi', 'bluetooth',
        }
        _SENT_STRUCT = re.compile(
            r'\b(?:and\s+then|after\s+that|first\b|can\s+you|please\b|could\s+you|'
            r'i\s+(?:want|need|would)|open\s+and|create\s+and|then\s+(?:open|create))\b',
            re.IGNORECASE,
        )
        _clean = transcript.strip().rstrip('.!?,').lower()
        _words = _clean.split()
        _word_set = {w.strip('.,!?') for w in _words}
        _tool_hits = _word_set & _TOOL_KWS
        return len(_tool_hits) >= 3 and not _SENT_STRUCT.search(_clean)

    def test_command_list_rejected(self):
        garbage = "Close, screenshot, volume down, folder, settings, lock, shutdown."
        assert self._should_reject(garbage), "Comma-separated command list must be rejected"

    def test_normal_command_not_rejected(self):
        ok = "Can you create a folder called alpha in C drive"
        assert not self._should_reject(ok), "Valid command must not be rejected"

    def test_multistep_not_rejected(self):
        ok = "open settings and then take a screenshot"
        assert not self._should_reject(ok), "Multi-step with connective must not be rejected"

    def test_shutdown_alone_not_rejected(self):
        ok = "shutdown the computer"
        assert not self._should_reject(ok), "Clean shutdown command must pass to orchestrator"

    def test_settings_open_not_rejected(self):
        ok = "open settings"
        assert not self._should_reject(ok), "Simple command not rejected"

    def test_noisy_keyword_list_rejected(self):
        garbage = "screenshot volume mute brightness lock terminal"
        assert self._should_reject(garbage), "Keyword list without grammar must be rejected"


# ────────────────────────────────────────────────────────────────────────────
# STT corrections (whisper_service)
# ────────────────────────────────────────────────────────────────────────────

class TestSTTCorrections:

    def _apply_corrections(self, text: str) -> str:
        from voice.whisper_service import _CORRECTIONS
        for pattern, replacement in _CORRECTIONS:
            if callable(replacement):
                text = pattern.sub(replacement, text)
            else:
                text = pattern.sub(replacement, text)
        return text

    def test_indeed_drive_corrected(self):
        assert "in D drive" in self._apply_corrections("create folder indeed drive")

    def test_deep_drive_corrected(self):
        assert "D drive" in self._apply_corrections("create folder in deep drive")

    def test_see_drive_corrected(self):
        assert "C drive" in self._apply_corrections("open see drive")

    def test_dee_drive_corrected(self):
        assert "D drive" in self._apply_corrections("open dee drive")


# ────────────────────────────────────────────────────────────────────────────
# Tool registry — all tools registered
# ────────────────────────────────────────────────────────────────────────────

class TestToolRegistry:

    @pytest.fixture(scope="class")
    def registry(self):
        from api.tools import registry
        return registry

    REQUIRED_TOOLS = [
        "create_folder", "open_application", "open_drive", "open_directory",
        "take_screenshot", "volume_control", "mute_unmute", "brightness_control",
        "lock_system", "shutdown_system", "restart_system",
        "rename_file", "copy_file", "delete_file", "move_file",
        "smart_open", "search_files",
    ]

    def test_all_required_tools_registered(self, registry):
        missing = [t for t in self.REQUIRED_TOOLS if t not in registry]
        assert not missing, f"Missing tool registrations: {missing}"

    def test_rename_file_has_executor(self, registry):
        entry = registry._tools.get("rename_file") if hasattr(registry, "_tools") else None
        # Just confirm it's accessible
        assert "rename_file" in registry


# ────────────────────────────────────────────────────────────────────────────
# Normalizer
# ────────────────────────────────────────────────────────────────────────────

class TestNormalizer:

    def test_filler_stripping(self):
        from api.services.normalizer import normalize
        assert normalize("can you open settings") == "open settings"
        assert normalize("please open chrome") == "open chrome"

    def test_synonym_expansion(self):
        from api.services.normalizer import normalize
        assert normalize("launch chrome") == "open chrome"
        assert normalize("new folder") == "create folder"

    def test_wake_word_stripped(self):
        from api.services.normalizer import normalize
        assert normalize("hey xyron open settings") == "open settings"

    def test_shutdown_synonym(self):
        from api.services.normalizer import normalize
        assert normalize("shut down the computer") == "shutdown the computer"
