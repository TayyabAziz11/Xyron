"""
Tests for activity_events.py — Phase 3.6 Task 9/11: structured activity
events. Titles must be deterministic (no LLM/TTS wait — Task 11) and must
never leak internal jargon (intent router, tool call, tier, object
resolver, etc. — Task 9).
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.activity_events import title_for, emit_activity, STAGES

_FORBIDDEN_JARGON = (
    "intent router", "tool call", "orchestrator", "tier", "object resolver",
    "context stack", "registry.execute",
)


class TestTitleFor(unittest.TestCase):
    def test_open_application_matches_task9_example(self):
        stage, started = title_for("open_application", {"app_name": "settings"}, "started")
        _, done = title_for("open_application", {"app_name": "settings"}, "completed")
        self.assertEqual(stage, "opening_app")
        self.assertEqual(started, "Opening Settings")
        self.assertEqual(done, "Settings is open")

    def test_folder_open(self):
        stage, started = title_for("smart_open", {"query": "perfume", "type": "folder"}, "started")
        _, done = title_for("smart_open", {"query": "perfume"}, "completed", {"path": "E:\\Perfume"})
        self.assertEqual(stage, "opening_folder")
        self.assertIn("Perfume", started)
        self.assertIn("Perfume", done)

    def test_web_search(self):
        stage, started = title_for("search_web", {"query": "latest AI news"}, "started")
        self.assertEqual(stage, "searching_web")
        self.assertEqual(started, "Searching the web")

    def test_unknown_tool_gets_generic_fallback(self):
        stage, started = title_for("some_future_tool_nobody_wrote_yet", {}, "started")
        self.assertEqual(stage, "running_tool")
        self.assertTrue(started)

    def test_failed_status_has_distinct_title(self):
        _, started = title_for("open_application", {"app_name": "chrome"}, "started")
        _, failed = title_for("open_application", {"app_name": "chrome"}, "failed")
        self.assertNotEqual(started, failed)

    def test_no_internal_jargon_in_any_title(self):
        cases = [
            ("open_application", {"app_name": "chrome"}),
            ("smart_open", {"query": "perfume", "type": "folder"}),
            ("search_web", {"query": "news"}),
            ("install_store_app", {"app_name": "whatsapp"}),
            ("read_screen", {}),
        ]
        for tool, params in cases:
            for status in ("started", "completed", "failed"):
                _, title = title_for(tool, params, status)
                low = title.lower()
                for jargon in _FORBIDDEN_JARGON:
                    self.assertNotIn(jargon, low, f"{tool}/{status} leaked jargon: {title!r}")

    def test_all_mapped_stages_are_in_canonical_enum(self):
        from api.services.activity_events import _TOOL_ACTIVITY, _DEFAULT_ACTIVITY
        for stage, *_ in list(_TOOL_ACTIVITY.values()) + [_DEFAULT_ACTIVITY]:
            self.assertIn(stage, STAGES)


class TestEmitActivity(unittest.TestCase):
    def test_emit_calls_send_fn_with_canonical_schema(self):
        sent = {}

        async def fake_send(ws, payload):
            sent.update(payload)
            return True

        asyncio.run(emit_activity(
            None, fake_send, trace_id="T-1", stage="opening_app", status="started",
            title="Opening Chrome", tool="open_application",
        ))

        self.assertEqual(sent["type"], "activity")
        self.assertEqual(sent["trace_id"], "T-1")
        self.assertEqual(sent["stage"], "opening_app")
        self.assertEqual(sent["status"], "started")
        self.assertEqual(sent["title"], "Opening Chrome")
        self.assertEqual(sent["tool"], "open_application")
        self.assertIn("timestamp", sent)

    def test_emit_is_fast(self):
        import time

        async def fake_send(ws, payload):
            return True

        t0 = time.perf_counter()
        asyncio.run(emit_activity(
            None, fake_send, trace_id="T-2", stage="running_tool", status="started", title="Working on it",
        ))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(elapsed_ms, 5.0, "activity emission must add <5ms overhead (Task 11)")

    def test_invalid_stage_falls_back_to_running_tool(self):
        sent = {}

        async def fake_send(ws, payload):
            sent.update(payload)

        asyncio.run(emit_activity(
            None, fake_send, trace_id="T-3", stage="not_a_real_stage", status="started", title="x",
        ))
        self.assertEqual(sent["stage"], "running_tool")


if __name__ == "__main__":
    unittest.main()
