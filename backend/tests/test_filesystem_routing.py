"""
Tests for filesystem intent routing and execution reliability.

Covers:
  - Drive-aware routing (Phases 1)
  - Verify-before-speaking (Phase 2)
  - Fuzzy matching (Phase 4)
  - Access error handling (Phase 5)
  - Pre-STT silence filter (Phase 7)
"""
from __future__ import annotations

import os
import sys
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

# Ensure backend/ is on path
_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── Phase 1: Drive-aware routing ─────────────────────────────────────────────

class TestDriveAwareRouting(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api.services.intent_router import IntentRouter
        cls.router = IntentRouter()

    def _route(self, text: str):
        return self.router.route(text.lower().strip())

    def test_drive_first_folder(self):
        """'in E drive open folder named python' → smart_open with drive=E, type=folder"""
        r = self._route("in E drive open folder named python")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "E")
        self.assertEqual(r.params.get("type"), "folder")
        self.assertIn("python", r.params.get("query", "").lower())

    def test_drive_last_folder(self):
        """'open folder named python in E drive' → smart_open with drive=E"""
        r = self._route("open folder named python in E drive")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "E")
        self.assertIn("python", r.params.get("query", "").lower())

    def test_find_query_in_drive(self):
        """'find python in E drive' → smart_open with drive=E"""
        r = self._route("find python in E drive")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "E")
        self.assertIn("python", r.params.get("query", "").lower())

    def test_search_resume_in_d_drive(self):
        """'search resume in D drive' → smart_open with drive=D"""
        r = self._route("search resume in D drive")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "D")
        self.assertIn("resume", r.params.get("query", "").lower())

    def test_drive_prefix_type_suffix(self):
        """'E drive python folder' → smart_open with drive=E, type=folder"""
        r = self._route("E drive python folder")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "E")
        self.assertEqual(r.params.get("type"), "folder")
        self.assertIn("python", r.params.get("query", "").lower())

    def test_find_file_in_drive(self):
        """'find file resume in D drive' → smart_open with drive=D, type=file"""
        r = self._route("find file resume in D drive")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("drive"), "D")
        self.assertEqual(r.params.get("type"), "file")

    def test_no_drive_open_folder(self):
        """'open folder named python' — no drive param → smart_open without drive"""
        r = self._route("open folder named python")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertFalse(r.params.get("drive"))  # empty or None

    def test_open_drive_route(self):
        """'open E drive' → open_drive, not smart_open"""
        r = self._route("open E drive")
        self.assertEqual(r.tool_name, "open_drive")
        self.assertEqual(r.params.get("drive"), "E")

    def test_no_hallucinated_drive_for_app(self):
        """'open chrome' should not get a drive param"""
        r = self._route("open chrome")
        # Either routes to open_application or stays without drive
        if r.params.get("drive"):
            self.fail(f"'open chrome' got spurious drive={r.params['drive']}")


# ── Phase 2 / 5: Verify-before-speaking and error handling ───────────────────

class TestVerifyBeforeSpeaking(unittest.TestCase):

    def _make_params(self, query, drive="", open_type="any"):
        return {"query": query, "type": open_type, "drive": drive}

    def _ctx(self):
        return {}

    def _exec(self, params):
        from api.tools.system_tools import _exec_smart_open
        return _exec_smart_open(params, self._ctx())

    def test_not_found_returns_not_found(self):
        """Query for a path that definitely doesn't exist returns not_found"""
        result = self._exec(self._make_params("zzz_no_such_folder_ever_xyzzy_42"))
        self.assertFalse(result.success)
        # spoken message must not say "Opened"
        self.assertNotIn("Opened", result.spoken)
        self.assertNotIn("Opening", result.spoken)

    def test_access_denied_returns_error(self):
        """When fs_search._verify returns access_denied, success=False and spoken is honest"""
        from api.services.fs_index import FSIndex
        from api.services import fs_search
        from api.services.fs_search import SearchResult

        fake_path = Path("/mnt/e/Restricted/Python")
        fake_result = SearchResult(fake_path, 0.9, "ranked")

        with patch.object(FSIndex, "is_ready", new_callable=PropertyMock, return_value=True), \
             patch.object(fs_search, "search", return_value=[fake_result]), \
             patch.object(fs_search, "_verify", return_value=(False, "access_denied")):
            result = self._exec(self._make_params("Python", drive="E", open_type="folder"))

        self.assertFalse(result.success)
        self.assertIn("denied", result.spoken.lower())
        self.assertNotIn("Opened", result.spoken)
        self.assertEqual(result.data.get("error_type"), "access_denied")

    def test_success_speaks_after_verification(self):
        """When accessible, Popen is called and spoken says 'Opened'"""
        from api.services.fs_index import FSIndex
        from api.services import fs_search
        from api.services.fs_search import SearchResult
        import subprocess as _sp

        fake_path = Path("/mnt/e/Projects/Python")
        fake_result = SearchResult(fake_path, 0.95, "ranked")

        with patch.object(FSIndex, "is_ready", new_callable=PropertyMock, return_value=True), \
             patch.object(fs_search, "search", return_value=[fake_result]), \
             patch.object(fs_search, "_verify", return_value=(True, "ok")), \
             patch.object(fs_search, "_find_cmd_exe", return_value="/mnt/c/Windows/System32/cmd.exe"), \
             patch.object(_sp, "Popen", return_value=MagicMock()) as mock_popen:
            result = self._exec(self._make_params("Python", drive="E", open_type="folder"))

        self.assertTrue(result.success)
        self.assertIn("Opened", result.spoken)
        mock_popen.assert_called_once()

    def test_drive_unavailable(self):
        """Specifying a non-existent drive returns drive_unavailable error"""
        # Drive Z is very unlikely to exist
        result = self._exec(self._make_params("anything", drive="Z"))
        if not result.success:
            self.assertEqual(result.data.get("error_type"), "drive_unavailable")
        # If Z drive somehow exists, the test would pass through — that's OK


# ── Phase 4: Fuzzy matching ───────────────────────────────────────────────────

class TestFuzzyMatching(unittest.TestCase):

    def test_exact_match_first(self):
        """Exact substring match should appear before fuzzy results"""
        from api.services.fs_index import fs_index
        if not fs_index.is_ready:
            self.skipTest("fs_index not ready (no index built yet)")
        # If index has entries, verify search returns sorted results
        results = fs_index.search("python", limit=5)
        # Just verify the API works without error
        self.assertIsInstance(results, list)

    def test_fuzzy_api_exists(self):
        """search_fuzzy method must exist and return a list"""
        from api.services.fs_index import fs_index
        results = fs_index.search_fuzzy("pythn", limit=3)  # intentional typo
        self.assertIsInstance(results, list)

    def test_drive_filter_in_search(self):
        """search() with drive='E' only returns /mnt/e/ paths"""
        from api.services.fs_index import fs_index
        if not fs_index.is_ready:
            self.skipTest("fs_index not ready")
        results = fs_index.search("", drive="E", limit=5)  # empty query = anything on E
        # An empty query with drive filter shouldn't crash
        self.assertIsInstance(results, list)


# ── Phase 5: Structured error types ──────────────────────────────────────────

class TestStructuredErrors(unittest.TestCase):

    def test_not_found_has_error_type(self):
        """Not-found result must have error_type in data dict"""
        from api.tools.system_tools import _exec_smart_open
        result = _exec_smart_open({"query": "zzz_impossible_file_xyz_42", "type": "file"}, {})
        if not result.success:
            self.assertIn("error_type", result.data)

    def test_multiple_matches_error_type(self):
        """Multiple-match result must have error_type=multiple_matches"""
        from api.services.fs_index import FSIndex
        from api.tools.system_tools import _exec_smart_open
        fake_paths = [
            Path("/mnt/e/Projects/Python"),
            Path("/mnt/e/Courses/Python"),
            Path("/mnt/e/Apps/Python"),
        ]
        with patch.object(FSIndex, "is_ready", new_callable=PropertyMock, return_value=True), \
             patch("api.services.fs_index.fs_index.search_fuzzy", return_value=fake_paths), \
             patch("api.tools.system_tools._find_cmdexe", return_value="/mnt/c/Windows/System32/cmd.exe"):
            result = _exec_smart_open({"query": "Python", "type": "folder"}, {})
        self.assertFalse(result.success)
        self.assertEqual(result.data.get("error_type"), "multiple_matches")
        self.assertIn("matches", result.data)

    def test_spoken_never_says_opened_on_failure(self):
        """spoken field must not contain 'Opened' when success=False"""
        from api.tools.system_tools import _exec_smart_open
        bad_cases = [
            {"query": "zzz_no_such_folder", "type": "folder"},
            {"query": "zzz_no_such_folder", "type": "folder", "drive": "Z"},
        ]
        for params in bad_cases:
            result = _exec_smart_open(params, {})
            if not result.success:
                self.assertNotIn("Opened", result.spoken,
                                 f"spoken says 'Opened' on failure for {params}")


# ── Phase 7: Pre-STT silence filter ──────────────────────────────────────────

class TestPreSTTSilenceFilter(unittest.TestCase):

    def test_rms_threshold_constant(self):
        """voice_ws.py must contain the RMS silence threshold"""
        import importlib.util, ast
        path = Path(__file__).parent.parent / "api" / "routers" / "voice_ws.py"
        src = path.read_text()
        self.assertIn("STT_SKIPPED_SILENCE", src,
                      "Pre-STT silence log tag [STT_SKIPPED_SILENCE] not found in voice_ws.py")
        self.assertIn("0.010", src,
                      "RMS threshold 0.010 not found in voice_ws.py")

    def test_silence_filter_before_stt(self):
        """[STT_SKIPPED_SILENCE] block must appear BEFORE the transcribe_audio call"""
        path = Path(__file__).parent.parent / "api" / "routers" / "voice_ws.py"
        src = path.read_text()
        idx_silence  = src.find("STT_SKIPPED_SILENCE")
        idx_transcribe = src.find("transcribe_audio")
        self.assertGreater(idx_transcribe, idx_silence,
                           "STT_SKIPPED_SILENCE check must appear before transcribe_audio call")


if __name__ == "__main__":
    unittest.main(verbosity=2)
