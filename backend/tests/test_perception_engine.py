"""
Tests for Phase 2 — Perception Engine.

Covers:
  - Browser Perception: page-type classification, product schema extraction,
    and the safety-critical guard (never triggers a Chrome connect/launch)
  - Desktop Perception: document-from-title parsing, UI Automation snapshot shape
  - Selection Tracker: priority order (browser > desktop > explorer > clipboard)
  - Vision Perception: throttling, no-op without an API key, never auto-triggered
  - Multi Monitor Manager: graceful failure, primary/foreground helpers
  - Event Dispatcher: orchestration, diff-only World State publishing
  - Regression: World State Reasoning API still has Phase 1.6 shape
  - Live integration smoke tests (skip gracefully if the PowerShell bridge
    isn't available — CI/non-WSL2 environments)
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _run(coro):
    return asyncio.run(coro)


class TestBrowserPerceptionClassification(unittest.TestCase):

    def test_shopping_via_schema(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://example.com/anything", "", has_product_schema=True), "shopping")

    def test_github(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://github.com/anthropics/claude-code", "repo"), "github")

    def test_youtube(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://www.youtube.com/watch?v=x", "some video"), "youtube")

    def test_google_search(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://www.google.com/search?q=test", "test - Google Search"), "google_search")

    def test_amazon_shopping_by_host(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://www.amazon.com/dp/B0XXXXX", "Widget"), "shopping")

    def test_unknown_for_unrecognized(self):
        from api.services.perception.browser_perception import classify_page_type
        self.assertEqual(classify_page_type("https://example.org/random-page", "Random"), "unknown")


class TestProductSchemaExtraction(unittest.TestCase):

    def test_extracts_core_fields(self):
        from api.services.perception.browser_perception import _extract_product_from_schema
        schema = {
            "name": "Wireless Mouse",
            "brand": {"name": "Logitech"},
            "offers": {"price": "29.99", "priceCurrency": "USD", "availability": "https://schema.org/InStock",
                       "seller": {"name": "Acme Store"}},
            "aggregateRating": {"ratingValue": "4.5", "reviewCount": "1203"},
            "image": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
            "category": "Electronics",
            "sku": "WM-100",
        }
        product = _extract_product_from_schema(schema)
        self.assertEqual(product["name"], "Wireless Mouse")
        self.assertEqual(product["brand"], "Logitech")
        self.assertEqual(product["price"], "29.99")
        self.assertEqual(product["availability"], "InStock")
        self.assertEqual(product["rating"], "4.5")
        self.assertEqual(product["review_count"], "1203")
        self.assertEqual(product["seller"], "Acme Store")
        self.assertEqual(len(product["images"]), 2)

    def test_handles_missing_offers_gracefully(self):
        from api.services.perception.browser_perception import _extract_product_from_schema
        product = _extract_product_from_schema({"name": "Bare Product"})
        self.assertEqual(product["name"], "Bare Product")
        self.assertIsNone(product["price"])


class TestBrowserPerceptionSafety(unittest.TestCase):
    """The safety-critical requirement: never trigger a Chrome connect/launch."""

    def test_refresh_returns_none_when_not_healthy(self):
        from api.services.perception import browser_perception
        with patch("api.agents.browser_agent.browser_workspace.browser_workspace") as mock_ws:
            mock_ws.is_healthy = False
            result = _run(browser_perception.refresh())
        self.assertIsNone(result)

    def test_refresh_never_calls_get_or_create_page_when_unhealthy(self):
        from api.services.perception import browser_perception
        with patch("api.agents.browser_agent.browser_workspace.browser_workspace") as mock_ws:
            mock_ws.is_healthy = False
            mock_ws.get_or_create_page = AsyncMock()
            _run(browser_perception.refresh())
            mock_ws.get_or_create_page.assert_not_called()

    def test_refresh_extracts_data_when_healthy(self):
        from api.services.perception import browser_perception

        mock_page = MagicMock()
        mock_page.url = "https://github.com/foo/bar"
        mock_page.title = AsyncMock(return_value="foo/bar: A repo")
        mock_page.evaluate = AsyncMock(side_effect=[None, "", None])  # schema, selection, error

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        with patch("api.agents.browser_agent.browser_workspace.browser_workspace") as mock_ws:
            mock_ws.is_healthy = True
            mock_ws.get_or_create_page = AsyncMock(return_value=mock_page)
            mock_ws._context = mock_context
            result = _run(browser_perception.refresh())

        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "https://github.com/foo/bar")
        self.assertEqual(result["page_type"], "github")


class TestDesktopPerception(unittest.TestCase):

    def test_parse_document_from_title_word(self):
        from api.services.perception.desktop_perception import _parse_document_from_title
        doc = _parse_document_from_title("Report.docx - Word", "winword")
        self.assertEqual(doc["name"], "Report.docx")
        self.assertEqual(doc["app"], "Word")

    def test_parse_document_returns_none_for_unmapped_app(self):
        from api.services.perception.desktop_perception import _parse_document_from_title
        self.assertIsNone(_parse_document_from_title("Anything - SomeApp", "someapp"))

    def test_refresh_returns_empty_without_window(self):
        from api.services.perception import desktop_perception
        with patch("api.services.window_context.window_context.get_active_window", return_value=None):
            result = desktop_perception.refresh(window=None)
        self.assertEqual(result, {})

    def test_refresh_builds_task_description_from_document(self):
        from api.services.perception import desktop_perception
        with patch("api.services.perception.desktop_perception.get_ui_automation_snapshot", return_value={}):
            result = desktop_perception.refresh(window={"proc_name": "winword", "title": "Report.docx - Word"})
        self.assertEqual(result["document"]["name"], "Report.docx")
        self.assertIn("Report.docx", result["task"])


class TestSelectionTrackerPriority(unittest.TestCase):

    def test_browser_selection_wins_over_everything(self):
        from api.services.perception import selection_tracker
        result = selection_tracker.refresh(
            browser_snapshot={"selected_text": "hello from browser"},
            desktop_snapshot={"selected_text": "hello from desktop"},
        )
        self.assertEqual(result["source"], "browser")

    def test_desktop_wins_when_no_browser_selection(self):
        from api.services.perception import selection_tracker
        result = selection_tracker.refresh(
            browser_snapshot=None,
            desktop_snapshot={"selected_text": "desktop text"},
        )
        self.assertEqual(result["source"], "desktop")

    def test_clipboard_is_last_resort(self):
        from api.services.perception import selection_tracker
        with patch("api.services.perception.selection_tracker._clipboard_text", return_value="clip content"):
            result = selection_tracker.refresh(browser_snapshot=None, desktop_snapshot=None, window=None)
        self.assertEqual(result["source"], "clipboard")

    def test_none_when_nothing_available(self):
        from api.services.perception import selection_tracker
        with patch("api.services.perception.selection_tracker._clipboard_text", return_value=None):
            result = selection_tracker.refresh(browser_snapshot=None, desktop_snapshot=None, window=None)
        self.assertIsNone(result)


class TestVisionPerception(unittest.TestCase):

    def test_no_op_without_api_key(self):
        from api.services.perception import vision_perception
        result = vision_perception.maybe_capture("test", openai_key="")
        self.assertIsNone(result)

    def test_throttled_on_rapid_repeat_calls(self):
        from api.services.perception import vision_perception
        vision_perception._last_capture_at = 0.0
        with patch("api.services.screen_context_service.capture_screen_b64", return_value="fakeb64"), \
             patch("api.services.perception.vision_perception._describe", return_value="a description"):
            first = vision_perception.maybe_capture("reason1", openai_key="sk-fake")
            second = vision_perception.maybe_capture("reason2", openai_key="sk-fake")
        self.assertIsNotNone(first)
        self.assertIsNone(second)  # throttled


class TestMultiMonitorManager(unittest.TestCase):

    def test_graceful_empty_on_failure(self):
        from api.services.perception import multi_monitor_manager
        with patch("api.services.ps_session.run_ps", return_value=(False, "")):
            self.assertEqual(multi_monitor_manager.get_monitors(), [])

    def test_primary_index_defaults_to_zero_when_none_marked_primary(self):
        from api.services.perception.multi_monitor_manager import MonitorInfo, get_primary_index
        mons = [MonitorInfo(0, False, "A", 0, 0, 100, 100, False, False)]
        self.assertEqual(get_primary_index(mons), 0)

    def test_foreground_monitor_falls_back_to_primary(self):
        from api.services.perception.multi_monitor_manager import MonitorInfo, get_foreground_monitor_index
        mons = [
            MonitorInfo(0, True, "A", 0, 0, 100, 100, False, False),
            MonitorInfo(1, False, "B", 100, 0, 100, 100, False, False),
        ]
        self.assertEqual(get_foreground_monitor_index(mons), 0)


class TestEventDispatcher(unittest.TestCase):

    def test_tick_publishes_diff_only(self):
        from api.services.perception.event_dispatcher import PerceptionEventDispatcher
        from api.services.world_state import world_state

        d = PerceptionEventDispatcher()
        with patch("api.services.world_state.world_state.refresh_sensors"), \
             patch("api.services.perception.browser_perception.refresh", new=AsyncMock(return_value=None)), \
             patch("api.services.perception.desktop_perception.refresh", return_value={}), \
             patch("api.services.perception.selection_tracker.refresh", return_value=None):
            events = []
            world_state.subscribe("current_browser", lambda old, new: events.append(new))
            _run(d.tick())
            _run(d.tick())  # second identical tick — should not re-notify
            import time as _t; _t.sleep(0.2)
        self.assertLessEqual(len(events), 1)

    def test_tick_does_not_clobber_current_document_when_desktop_empty(self):
        from api.services.perception.event_dispatcher import PerceptionEventDispatcher
        from api.services.world_state import world_state

        world_state.publish("current_document", "some_file.txt", source="test_setup")
        d = PerceptionEventDispatcher()
        with patch("api.services.world_state.world_state.refresh_sensors"), \
             patch("api.services.perception.browser_perception.refresh", new=AsyncMock(return_value=None)), \
             patch("api.services.perception.desktop_perception.refresh", return_value={}), \
             patch("api.services.perception.selection_tracker.refresh", return_value=None):
            _run(d.tick())
        self.assertEqual(world_state.get("current_document"), "some_file.txt")


class TestPerceptionEngineIntegration(unittest.TestCase):

    def test_request_vision_respects_throttle_and_publishes_focus(self):
        from api.services.perception.perception_engine import PerceptionEngine
        from api.services.world_state import world_state

        engine = PerceptionEngine()
        with patch("api.services.perception.vision_perception.maybe_capture",
                   return_value={"description": "a chart on screen", "reason": "test", "monitor": 0, "ts": 0}):
            result = _run(engine.request_vision("test", "sk-fake"))
        self.assertIsNotNone(result)
        ctx = world_state.get_context()
        self.assertEqual(ctx["current_focus_object"], {"type": "vision", "value": "a chart on screen"})

    def test_world_state_reasoning_api_unchanged_shape(self):
        """Regression: Phase 1.6 fields must still all be present after Phase 2."""
        from api.services.world_state import world_state
        with patch("api.services.window_context.window_context.get_active_window", return_value=None):
            ctx = world_state.get_context(refresh=True)
        for key in ("window", "active_app", "active_folder", "active_project", "hour", "weekday",
                    "current_goal", "goal_history", "recent_actions", "active_entities"):
            self.assertIn(key, ctx)


@unittest.skipUnless(Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists(),
                      "PowerShell bridge unavailable — not a WSL2-with-Windows environment")
class TestLiveIntegration(unittest.TestCase):
    """Real integration tests against the actual PowerShell bridge (no mocks).
    Skipped automatically outside WSL2 (CI, native Linux, etc.)."""

    def test_multi_monitor_manager_real_query(self):
        from api.services.perception.multi_monitor_manager import get_monitors
        monitors = get_monitors()
        self.assertGreaterEqual(len(monitors), 1)
        self.assertTrue(any(m.primary for m in monitors))

    def test_multi_monitor_manager_repeated_calls_dont_hang(self):
        """Regression for the Add-Type redefinition / multi-line hang bug."""
        from api.services.perception.multi_monitor_manager import get_monitors
        import time
        for _ in range(3):
            t0 = time.monotonic()
            monitors = get_monitors()
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 5.0)
            self.assertGreaterEqual(len(monitors), 1)

    def test_desktop_perception_real_query_returns_shape(self):
        from api.services.perception.desktop_perception import get_ui_automation_snapshot
        snap = get_ui_automation_snapshot()
        self.assertIsInstance(snap, dict)

    def test_browser_perception_safe_when_no_chrome_session(self):
        from api.services.perception import browser_perception
        from api.agents.browser_agent.browser_workspace import browser_workspace
        was_healthy_before = browser_workspace.is_healthy
        result = _run(browser_perception.refresh())
        if not was_healthy_before:
            self.assertIsNone(result)
        self.assertEqual(browser_workspace.is_healthy, was_healthy_before,
                          "browser_perception must never change connection state")


if __name__ == "__main__":
    unittest.main()
