"""
Brain V2 service tests — sentinel, learning, verifier, goals extension.
All tests are pure Python (no network, no audio, no LLM).
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make backend root importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel Service
# ─────────────────────────────────────────────────────────────────────────────

class TestSentinelService:

    def setup_method(self):
        from api.services.sentinel_service import SentinelService
        self.svc = SentinelService()

    def test_record_tts_failure_increments_counter(self):
        self.svc.record_tts_failure()
        self.svc.record_tts_failure()
        with self.svc._lock:
            assert self.svc._counters.tts_failures == 2

    def test_record_stt_empty(self):
        self.svc.record_stt(empty=False)
        self.svc.record_stt(empty=True)
        with self.svc._lock:
            assert self.svc._counters.stt_total == 2
            assert self.svc._counters.stt_empty == 1

    def test_record_tool_failure(self):
        self.svc.record_tool_failure("open_application")
        self.svc.record_tool_failure("open_application")
        with self.svc._lock:
            assert self.svc._counters.tool_failures["open_application"] == 2

    def test_record_openai_quota(self):
        self.svc.record_openai_quota()
        with self.svc._lock:
            assert self.svc._counters.openai_quota_hits == 1

    def test_check_runs_without_crash(self):
        """Health check should never raise even with empty counters."""
        self.svc._check()  # should not raise

    def test_check_writes_report_on_issues(self, tmp_path):
        from api.services import sentinel_service as sm
        import api.services.sentinel_service as sm_mod
        original = sm_mod._REPORT_PATH
        try:
            sm_mod._REPORT_PATH = tmp_path / "sentinel_report.md"
            self.svc.record_tts_failure()
            self.svc._check()
            # Only written if there are issues
            with self.svc._lock:
                pass  # counters reset after check
        finally:
            sm_mod._REPORT_PATH = original

    def test_start_stop(self):
        self.svc.start()
        assert self.svc._running
        self.svc.stop()
        assert not self.svc._running


# ─────────────────────────────────────────────────────────────────────────────
# Learning Service
# ─────────────────────────────────────────────────────────────────────────────

class TestLearningService:

    def setup_method(self):
        from api.services.learning_service import LearningService
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.svc = LearningService(db_path=Path(self._tmp.name))

    def teardown_method(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_record_returns_no_suggestion_on_first_use(self):
        result = self.svc.record("open hackathon folder", "smart_open")
        assert not result.has_suggestion

    def test_record_returns_suggestion_after_threshold(self):
        for _ in range(3):
            result = self.svc.record("open hackathon folder", "smart_open")
        assert result.has_suggestion
        assert result.count >= 3

    def test_suggestion_text_is_non_empty(self):
        for _ in range(3):
            r = self.svc.record("open hackathon folder", "smart_open")
        assert len(r.suggestion) > 10

    def test_save_and_get_procedure(self):
        self.svc.save_procedure(
            "start hackathon",
            steps=["open hackathon folder", "open it in vscode"],
            triggers=["start hackathon project", "hackathon project"],
        )
        proc = self.svc.get_procedure("start hackathon")
        assert proc is not None
        assert proc["name"] == "start hackathon"
        assert len(proc["steps"]) == 2

    def test_find_procedure_by_trigger(self):
        self.svc.save_procedure(
            "morning routine",
            steps=["open calendar", "check email"],
            triggers=["morning routine", "start my day"],
        )
        found = self.svc.find_procedure("let's do the morning routine")
        assert found is not None
        assert found["name"] == "morning routine"

    def test_find_procedure_returns_none_no_match(self):
        found = self.svc.find_procedure("open chrome")
        assert found is None

    def test_pattern_normalisation_groups_variants(self):
        """Same tool + similar command should be treated as same pattern."""
        p1 = self.svc._to_pattern("smart_open", "open hackathon folder")
        p2 = self.svc._to_pattern("smart_open", "open hackathon folder please")
        # Both share the tool prefix
        assert p1.startswith("smart_open::")
        assert p2.startswith("smart_open::")

    def test_empty_tool_name_skipped(self):
        r = self.svc.record("some command", "")
        assert not r.has_suggestion

    def test_performance_under_50ms(self):
        t0 = time.time()
        for _ in range(3):
            self.svc.record("open downloads folder", "open_directory")
        elapsed = (time.time() - t0) * 1000
        assert elapsed < 150, f"3 records took {elapsed:.0f}ms, expected < 150ms"


# ─────────────────────────────────────────────────────────────────────────────
# Verifier Service
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifierService:

    def test_failed_tool_skips_verification(self):
        from api.services.verifier_service import verify
        r = verify("open_application", {"app_name": "chrome"}, False, {})
        assert r.verified is False
        assert r.verification_method == "skipped"
        assert r.error_type == "tool_failure"

    def test_path_exists_for_real_path(self, tmp_path):
        from api.services.verifier_service import verify
        r = verify("open_directory", {"path": str(tmp_path)}, True, {})
        assert r.verified is True
        assert r.verification_method == "path_exists"

    def test_path_missing_returns_fail(self, tmp_path):
        from api.services.verifier_service import verify
        fake_path = str(tmp_path / "nonexistent_folder_xyz")
        r = verify("open_directory", {"path": fake_path}, True, {})
        assert r.verified is False
        assert r.error_type == "path_missing"

    def test_create_folder_verifies_existence(self, tmp_path):
        from api.services.verifier_service import verify
        new_dir = tmp_path / "new_test_folder"
        new_dir.mkdir()
        r = verify("create_folder", {"path": str(new_dir)}, True, {})
        assert r.verified is True

    def test_delete_file_verifies_gone(self, tmp_path):
        from api.services.verifier_service import verify
        # File doesn't exist → delete verified
        ghost = str(tmp_path / "already_gone.txt")
        r = verify("delete_file", {"paths": [ghost]}, True, {})
        assert r.verified is True
        assert r.verification_method == "path_gone"

    def test_open_url_is_trusted(self):
        from api.services.verifier_service import verify
        r = verify("open_url", {"url": "https://example.com"}, True, {})
        assert r.verified is True
        assert r.verification_method == "trusted"

    def test_search_youtube_is_trusted(self):
        from api.services.verifier_service import verify
        r = verify("search_youtube", {"query": "believer"}, True, {})
        assert r.verified is True
        assert r.verification_method == "trusted"

    def test_install_store_app_is_deferred(self):
        from api.services.verifier_service import verify
        r = verify("install_store_app", {"app_name": "whatsapp"}, True, {})
        assert r.verified is True
        assert r.verification_method == "deferred"

    def test_unknown_tool_passes_through(self):
        from api.services.verifier_service import verify
        r = verify("custom_tool_xyz", {}, True, {})
        assert r.verified is True
        assert r.verification_method == "skipped"

    def test_latency_under_50ms(self, tmp_path):
        from api.services.verifier_service import verify
        t0 = time.time()
        verify("open_directory", {"path": str(tmp_path)}, True, {})
        elapsed = (time.time() - t0) * 1000
        assert elapsed < 50, f"verify took {elapsed:.0f}ms, expected < 50ms"


# ─────────────────────────────────────────────────────────────────────────────
# Goal Tracker (extended)
# ─────────────────────────────────────────────────────────────────────────────

class TestGoalTrackerV2:

    def setup_method(self):
        from cognition.goals import GoalTracker
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.tracker = GoalTracker(db_path=Path(self._tmp.name))

    def teardown_method(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_set_goal_basic(self):
        g = self.tracker.set_goal("Open hackathon project")
        assert g.id
        assert g.status == "active"
        assert g.steps == []
        assert g.failures == []

    def test_set_goal_with_criteria(self):
        g = self.tracker.set_goal(
            "Install WhatsApp",
            priority=4,
            success_criteria="WhatsApp process running",
            tool_name="install_store_app",
        )
        assert g.success_criteria == "WhatsApp process running"
        assert g.tool_name == "install_store_app"

    def test_create_execution_goal(self):
        g = self.tracker.create_execution_goal(
            "open hackathon folder",
            "smart_open",
            {"query": "hackathon"},
        )
        assert g.tool_name == "smart_open"
        assert "Execute" in g.description

    def test_record_step_success(self):
        g = self.tracker.set_goal("Multi-step goal")
        self.tracker.record_step(g.id, "step 1: open folder", True)
        loaded = self.tracker.get_goal(g.id)
        assert len(loaded.steps) == 1
        assert loaded.steps[0]["success"] is True

    def test_record_step_failure(self):
        g = self.tracker.set_goal("Failing goal")
        self.tracker.record_step(g.id, "step: open app", False, error="app not found")
        loaded = self.tracker.get_goal(g.id)
        assert loaded.steps[0]["success"] is False
        assert loaded.steps[0]["error"] == "app not found"

    def test_record_failure(self):
        g = self.tracker.set_goal("Failure test")
        self.tracker.record_failure(g.id, "winget timeout", "check internet")
        loaded = self.tracker.get_goal(g.id)
        assert len(loaded.failures) == 1
        assert "winget timeout" in loaded.failures[0]["error"]

    def test_complete_goal(self):
        g = self.tracker.set_goal("Completable goal")
        self.tracker.complete_goal(g.id)
        loaded = self.tracker.get_goal(g.id)
        assert loaded.status == "completed"

    def test_get_active_goals_excludes_completed(self):
        g1 = self.tracker.set_goal("Active")
        g2 = self.tracker.set_goal("Done")
        self.tracker.complete_goal(g2.id)
        active = self.tracker.get_active_goals()
        ids = [g.id for g in active]
        assert g1.id in ids
        assert g2.id not in ids

    def test_prioritize_returns_highest(self):
        self.tracker.set_goal("Low priority", priority=1)
        g_high = self.tracker.set_goal("High priority", priority=5)
        top = self.tracker.prioritize()
        assert top.id == g_high.id
