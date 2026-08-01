"""
Regression tests for the specific, confirmed root causes behind Phase 3.5
proven issue 1 ("open perfume folder" misclassified as an application):

  1. tool_aware_corrector._TOOL_PATTERNS — the generic open_application
     pattern was ordered before the folder-specific pattern, so it won on
     every "open X folder" phrase (confirmed via _predict_tool()).
  2. intent_router's D1 drive-first regex — greedily captured filler words
     ("can you also") between a drive clause and the actual verb as the
     query itself.
  3. intent_router's "open <name> folder" rule only allowed '.'/'!' as
     trailing punctuation, so a genuine question ("...folder?") fell
     through to the wrong rule.
  4. active_context — open_drive was missing from _GOAL_FROM_TOOL entirely,
     so stale app/platform context from an earlier turn survived opening a
     drive.
  5. entity_corrector — compared a folder-typed span against application
     names with no type restriction.
  6. verifier_v2 — a CMD/conhost/powershell window whose title happened to
     contain an unrecognized "app name" was accepted as proof of a
     successful app launch.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestToolAwareCorrectorPatternOrder(unittest.TestCase):
    def test_open_folder_phrases_predict_open_folder_not_open_application(self):
        from api.services.tool_aware_corrector import _predict_tool
        for phrase in ("open perfume folder", "open the perfume folder",
                       "open downloads folder", "open my documents"):
            tool, etype = _predict_tool(phrase)
            self.assertEqual(tool, "open_folder", f"{phrase!r} predicted {tool!r}")

    def test_known_app_names_still_predict_open_application(self):
        from api.services.tool_aware_corrector import _predict_tool
        tool, _ = _predict_tool("open chrome")
        self.assertEqual(tool, "open_application")

    def test_generic_open_is_the_last_resort_fallback(self):
        from api.services.tool_aware_corrector import _predict_tool
        # Nothing more specific matches "open xyzzy" — the generic
        # open_application catch-all should still fire, just last.
        tool, _ = _predict_tool("open xyzzy")
        self.assertEqual(tool, "open_application")


class TestIntentRouterDriveClauseFiller(unittest.TestCase):
    def _route(self, text):
        from api.services.intent_router import IntentRouter
        return IntentRouter().route(text)

    def test_exact_reported_utterance(self):
        r = self._route("Now in E drive, can you also open perfume folder?")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("type"), "folder")
        self.assertEqual(r.params.get("query"), "perfume")

    def test_without_question_mark(self):
        r = self._route("now in e drive can you also open perfume folder")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("query"), "perfume")

    def test_drive_first_pattern_still_works_without_filler(self):
        r = self._route("in E drive open folder named python")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("query"), "python")
        self.assertEqual(r.params.get("drive"), "E")

    def test_trailing_question_mark_on_named_folder_rule(self):
        r = self._route("open the perfume folder?")
        self.assertEqual(r.tool_name, "smart_open")
        self.assertEqual(r.params.get("type"), "folder")


class TestActiveContextOpenDrive(unittest.TestCase):
    def test_open_drive_clears_stale_app_context(self):
        from api.services.active_context import active_context
        active_context.reset()
        active_context.update_from_tool("open_application", {"app_name": "settings"}, {}, True)
        self.assertEqual(active_context.get()["current_app"], "settings")

        active_context.update_from_tool("open_drive", {"drive": "E"}, {"path": "E:\\"}, True)
        ctx = active_context.get()
        self.assertIsNone(ctx["current_app"])
        self.assertEqual(ctx["current_platform"], "explorer")
        self.assertEqual(ctx["current_drive"], "E")
        self.assertEqual(ctx["current_goal"], "filesystem_navigation")
        active_context.reset()

    def test_folder_open_clears_stale_platform(self):
        from api.services.active_context import active_context
        active_context.reset()
        active_context.update_from_tool(
            "install_store_app", {"app_name": "whatsapp"}, {"app_name": "WhatsApp"}, True,
        )
        self.assertEqual(active_context.get()["current_platform"], "microsoft_store")

        active_context.update_from_tool(
            "smart_open", {"query": "perfume", "type": "folder"}, {"path": "E:\\Perfume"}, True,
        )
        ctx = active_context.get()
        self.assertEqual(ctx["current_platform"], "explorer")
        self.assertIsNone(ctx["current_app"])
        active_context.reset()


class TestEntityCorrectorTypeRestriction(unittest.TestCase):
    def test_folder_span_excludes_app_candidates(self):
        from api.services.entity_corrector import _expected_entity_types
        types = _expected_entity_types("perfume folder")
        self.assertEqual(types, frozenset({"folder"}))

    def test_unrestricted_without_type_noun(self):
        from api.services.entity_corrector import _expected_entity_types
        self.assertIsNone(_expected_entity_types("perfume"))

    def test_performance_monitor_never_considered_for_folder_span(self):
        from dataclasses import dataclass
        from api.services.entity_corrector import rescore

        @dataclass
        class FakeCandidate:
            text: str
            confidence: float

        with patch("api.services.entity_corrector._get_entity_db",
                   return_value=[("performance monitor", "Performance Monitor", "app")]):
            result = rescore([FakeCandidate(text="open perfume folder", confidence=0.8)])
        # Must not have been "corrected" into an app name at all.
        self.assertEqual(result[0].text, "open perfume folder")


class TestVerifierV2ShellHostRejection(unittest.TestCase):
    def test_cmd_window_titled_with_unrecognized_app_name_is_rejected(self):
        from api.services import verifier_v2
        with patch.object(
            verifier_v2, "_get_windows_state",
            return_value={"procs": {"cmd"}, "fg_title": "perfume folder", "fg_proc": "cmd"},
        ):
            result = verifier_v2._verify_app_launch(
                "open_application", {"app_name": "perfume folder"}, {}, __import__("time").time(),
            )
        self.assertFalse(result.verified)

    def test_real_app_window_is_still_accepted(self):
        from api.services import verifier_v2
        with patch.object(
            verifier_v2, "_get_windows_state",
            return_value={"procs": {"chrome"}, "fg_title": "Google Chrome", "fg_proc": "chrome"},
        ):
            result = verifier_v2._verify_app_launch(
                "open_application", {"app_name": "chrome"}, {}, __import__("time").time(),
            )
        self.assertTrue(result.verified)

    def test_folder_verification_uses_path_not_window_title(self):
        from api.services import verifier_v2
        with patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None):
            result = verifier_v2.verify(
                "open_directory", {"path": "E:\\Perfume"}, True, {"path": "E:\\Perfume"},
            )
        # Real folder on this machine (see test_object_resolver.py) — path
        # exists, and no window-title heuristic was involved.
        self.assertIn(result.verification_method, ("path_exists", "explorer_path"))


if __name__ == "__main__":
    unittest.main()
