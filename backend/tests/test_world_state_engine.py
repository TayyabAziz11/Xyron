"""
Tests for the World State Engine (Phase 1.6 — architectural foundation).

Covers:
  - Pub/sub core: diff-only notification, subscriber dispatch, "*" wildcard
  - ActivityTimeline: bounded, newest-first
  - GoalTracker: categorization from active_context/tool/workspace signals + history
  - refresh_sensors(): populates owned fields + FocusGraph from the sensors
  - get_context(): Reasoning Context API shape, backward-compat keys for
    file_resolver/fs_index (window/active_app/active_folder/active_project/hour/weekday)
  - file_resolver still resolves correctly through the World State indirection
    (no regression from the Phase 1.5 architecture)
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestPubSub(unittest.TestCase):

    def _fresh(self):
        from api.services.world_state import WorldStateService
        return WorldStateService()

    def test_publish_notifies_subscriber(self):
        ws = self._fresh()
        events = []
        ws.subscribe("current_task", lambda old, new: events.append((old, new)))
        ws.publish("current_task", "writing report", source="test")
        time.sleep(0.2)
        self.assertEqual(events, [(None, "writing report")])

    def test_diff_only_no_notification_on_same_value(self):
        ws = self._fresh()
        events = []
        ws.subscribe("current_task", lambda old, new: events.append((old, new)))
        ws.publish("current_task", "writing report", source="test")
        ws.publish("current_task", "writing report", source="test")
        time.sleep(0.2)
        self.assertEqual(len(events), 1)

    def test_wildcard_subscriber_fires_for_any_field(self):
        ws = self._fresh()
        events = []
        ws.subscribe("*", lambda old, new: events.append(new))
        ws.publish("current_task", "a", source="test")
        ws.publish("current_intent", "smart_open", source="test")
        time.sleep(0.2)
        self.assertEqual(set(events), {"a", "smart_open"})

    def test_unsubscribe_stops_notifications(self):
        ws = self._fresh()
        events = []
        cb = lambda old, new: events.append(new)
        ws.subscribe("current_task", cb)
        ws.unsubscribe("current_task", cb)
        ws.publish("current_task", "x", source="test")
        time.sleep(0.2)
        self.assertEqual(events, [])

    def test_slow_subscriber_does_not_block_publisher(self):
        ws = self._fresh()
        def slow_cb(old, new):
            time.sleep(1.0)
        ws.subscribe("current_task", slow_cb)
        t0 = time.monotonic()
        ws.publish("current_task", "x", source="test")
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.2)

    def test_get_returns_current_value(self):
        ws = self._fresh()
        ws.publish("current_intent", "smart_open", source="test")
        self.assertEqual(ws.get("current_intent"), "smart_open")


class TestActivityTimeline(unittest.TestCase):

    def test_bounded_and_newest_first(self):
        from api.services.activity_timeline import ActivityTimeline
        t = ActivityTimeline(maxlen=3)
        for i in range(5):
            t.record(f"action {i}")
        recent = t.recent(10)
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0].description, "action 4")
        self.assertEqual(recent[-1].description, "action 2")

    def test_to_list_shape(self):
        from api.services.activity_timeline import ActivityTimeline
        t = ActivityTimeline()
        t.record("opened file.txt", tool="smart_open", entity="/mnt/e/file.txt", success=True, source="test")
        d = t.to_list(1)[0]
        self.assertEqual(d["description"], "opened file.txt")
        self.assertEqual(d["tool"], "smart_open")
        self.assertTrue(d["success"])


class TestGoalTracker(unittest.TestCase):

    def test_workspace_signal_wins_categorization(self):
        from api.services.goal_tracker import GoalTracker
        g = GoalTracker()
        g.update_from_workspace("vscode")
        self.assertEqual(g.get_goal(), "coding")

    def test_unknown_signals_are_ignored(self):
        from api.services.goal_tracker import GoalTracker
        g = GoalTracker()
        g.update_from_tool("some_unmapped_tool")
        self.assertIsNone(g.get_goal())

    def test_repeated_same_goal_does_not_grow_history(self):
        from api.services.goal_tracker import GoalTracker
        g = GoalTracker()
        g.update_from_workspace("vscode")
        g.update_from_workspace("visual_studio")  # also "coding" — no-op
        self.assertEqual(len(g.history()), 1)

    def test_history_ordered_newest_first(self):
        from api.services.goal_tracker import GoalTracker
        g = GoalTracker()
        g.update_from_tool("write_file")   # writing
        g.update_from_workspace("blender")  # design
        hist = g.history()
        self.assertEqual(hist[0]["goal"], "design")
        self.assertEqual(hist[1]["goal"], "writing")


class TestReasoningContextAPI(unittest.TestCase):

    def test_get_context_shape_has_all_spec_fields(self):
        from api.services.world_state import world_state
        with patch("api.services.window_context.window_context.get_active_window", return_value=None):
            ctx = world_state.get_context(refresh=True)
        required = {
            "current_application", "current_foreground_window", "current_workspace",
            "current_project", "current_explorer_folder", "current_browser", "current_url",
            "current_tab", "current_document", "current_file", "current_product",
            "current_conversation_entities", "current_task", "current_goal", "current_intent",
            "recent_actions", "active_entities", "recent_files", "recent_folders",
            "current_focus_object", "focus_graph",
        }
        self.assertTrue(required.issubset(ctx.keys()), required - ctx.keys())

    def test_backward_compat_keys_for_file_resolver(self):
        from api.services.world_state import world_state
        with patch("api.services.window_context.window_context.get_active_window", return_value=None):
            ctx = world_state.get_context(refresh=True)
        for key in ("window", "active_app", "active_folder", "active_project", "hour", "weekday"):
            self.assertIn(key, ctx)

    def test_no_window_leaves_stub_fields_none(self):
        from api.services.world_state import world_state
        with patch("api.services.window_context.window_context.get_active_window", return_value=None), \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None):
            ctx = world_state.get_context(refresh=True)
        self.assertIsNone(ctx["current_browser"])
        self.assertIsNone(ctx["current_url"])
        self.assertIsNone(ctx["current_tab"])
        self.assertIsNone(ctx["current_product"])

    def test_refresh_sensors_populates_focus_graph(self):
        from api.services.world_state import world_state
        fake_window = {"title": "x — myproj — Visual Studio Code", "proc_name": "code", "pid": 1}
        with patch("api.services.window_context.window_context.get_active_window", return_value=fake_window), \
             patch("api.services.workspace_context.get_active_workspace",
                   return_value={"app": "vscode", "root": Path("/mnt/e/Xyron/myproj"), "raw_title": "x"}), \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None):
            ctx = world_state.get_context(refresh=True)
        self.assertEqual(ctx["current_application"], "code")
        self.assertEqual(ctx["current_project"], "myproj")
        self.assertEqual(ctx["focus_graph"]["focused_object"], {"type": "workspace", "value": "/mnt/e/Xyron/myproj"})


class TestFileResolverIntegration(unittest.TestCase):
    """Confirms file_resolver still resolves correctly now that its context
    snapshot comes from World State instead of querying sensors directly —
    a regression check for the Phase 1.5 -> 1.6 migration."""

    def test_workspace_tier_still_wins_through_world_state(self):
        from api.services import file_resolver as fr

        fake_window = {"title": "x — myproj — Visual Studio Code", "proc_name": "code", "pid": 1}
        with patch("api.services.window_context.window_context.get_active_window", return_value=fake_window), \
             patch("api.services.workspace_context.get_active_workspace",
                   return_value={"app": "vscode", "root": Path("/mnt/e/Xyron/myproj"), "raw_title": "x"}), \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None), \
             patch("api.services.fs_index.fs_index.get_candidates_under_root",
                   return_value=[(1, "/mnt/e/Xyron/myproj/report.txt")]), \
             patch("api.services.fs_index.fs_index.search_semantic_ranked",
                   return_value=[(0.95, Path("/mnt/e/Xyron/other/unrelated_high_sim.txt"), {})]):

            r = fr.resolve("report", open_type="file")

        self.assertEqual(r.tier, 1)
        self.assertEqual(r.path, Path("/mnt/e/Xyron/myproj/report.txt"))

    def test_single_sensor_call_shared_across_tiers(self):
        """get_active_workspace should be called once (by refresh_sensors), not
        once per tier — the whole point of routing through World State."""
        from api.services import file_resolver as fr

        fake_window = {"title": "x — myproj — Visual Studio Code", "proc_name": "code", "pid": 1}
        with patch("api.services.window_context.window_context.get_active_window", return_value=fake_window), \
             patch("api.services.workspace_context.get_active_workspace",
                   return_value={"app": "vscode", "root": Path("/mnt/e/Xyron/myproj"), "raw_title": "x"}) as mock_ws, \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None), \
             patch("api.services.fs_index.fs_index.get_candidates_under_root", return_value=[]), \
             patch("api.services.fs_index.fs_index.get_recent_files", return_value=[]), \
             patch("api.services.fs_index.fs_index.get_frequent_files", return_value=[]), \
             patch("api.services.memory_service.memory_service.get_context",
                   return_value={"last_file": None, "last_folder": None}), \
             patch("api.services.fs_index.fs_index.search_semantic_ranked", return_value=[]), \
             patch("api.services.fs_index.fs_index.search_ranked", return_value=[]):

            fr.resolve("nothing matches", open_type="file")

        mock_ws.assert_called_once()


if __name__ == "__main__":
    unittest.main()
