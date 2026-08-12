"""
Tests for Phase 1.5 — Context-Aware Filesystem resolution.

Covers:
  - Priority cascade: an earlier tier must win over a later one even when
    the later tier's raw match/semantic score is numerically higher
  - Confidence bucketing (open / confirm / choices thresholds)
  - Learned resolution ("tier 0") promotion after a confirmed choice
  - Usage affinity (time-of-day / weekday / folder / app / project)
  - Workspace window-title parsing
  - Performance: the common "no workspace/explorer context" path never
    shells out to PowerShell and stays fast
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestWorkspaceTitleParsing(unittest.TestCase):

    def test_vscode_file_and_folder(self):
        from api.services.workspace_context import _extract_candidate_name
        self.assertEqual(
            _extract_candidate_name("main.py — myproject — Visual Studio Code", "vscode"),
            "myproject",
        )

    def test_vscode_folder_only(self):
        from api.services.workspace_context import _extract_candidate_name
        self.assertEqual(
            _extract_candidate_name("myproject — Visual Studio Code", "vscode"),
            "myproject",
        )

    def test_vscode_unsaved_marker_stripped(self):
        from api.services.workspace_context import _extract_candidate_name
        name = _extract_candidate_name("● draft.md — notes — Visual Studio Code", "vscode")
        self.assertEqual(name, "notes")

    def test_no_title_returns_none(self):
        from api.services.workspace_context import _extract_candidate_name
        self.assertIsNone(_extract_candidate_name("", "vscode"))


class TestConfidenceBucketing(unittest.TestCase):
    """Directly exercises _finalize's math without touching real fs_index state."""

    def _finalize_with(self, tier, match_score, learned_hits=None, usage_aff=0.0):
        from api.services import file_resolver as fr
        cand = fr.Candidate(path=Path("/mnt/e/Xyron/dummy.txt"), score=match_score, tier=tier, entry_id=123456789)
        with patch.object(fr, "_entry_id_for", return_value=123456789), \
             patch.object(fr, "_learned_boost", return_value=(0.1 * learned_hits if learned_hits else 0.0)), \
             patch("api.services.fs_index.fs_index.get_usage_affinity", return_value={123456789: usage_aff}):
            return fr._finalize("query", [cand], tier, {"hour": 12, "weekday": 2})

    def test_high_confidence_opens_immediately(self):
        r = self._finalize_with(tier=1, match_score=1.0)  # 0.35 + 0.5 = 0.85
        self.assertEqual(r.decision, "open")

    def test_medium_confidence_asks_for_confirmation(self):
        r = self._finalize_with(tier=8, match_score=0.9)  # 0.05 + 0.45 = 0.50
        self.assertEqual(r.decision, "confirm")

    def test_low_confidence_presents_choices(self):
        r = self._finalize_with(tier=9, match_score=0.5)  # 0.0 + 0.25 = 0.25
        self.assertEqual(r.decision, "choices")

    def test_learned_boost_can_push_medium_to_high(self):
        low = self._finalize_with(tier=8, match_score=0.9, learned_hits=0)
        boosted = self._finalize_with(tier=8, match_score=0.9, learned_hits=3)
        self.assertGreater(boosted.confidence, low.confidence)


class TestPriorityCascade(unittest.TestCase):
    """
    An earlier tier (workspace) must win over a later tier (semantic) even
    when the later tier's raw score is higher — this is the whole point of
    Phase 1.5: priority order over raw similarity.
    """

    def test_workspace_tier_wins_over_stronger_semantic_hit(self):
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

    def test_no_workspace_falls_through_to_semantic(self):
        from api.services import file_resolver as fr

        with patch("api.services.window_context.window_context.get_active_window", return_value=None), \
             patch("api.services.workspace_context.get_active_workspace", return_value=None), \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None), \
             patch("api.services.fs_index.fs_index.get_recent_files", return_value=[]), \
             patch("api.services.fs_index.fs_index.get_frequent_files", return_value=[]), \
             patch("api.services.memory_service.memory_service.get_context",
                   return_value={"last_file": None, "last_folder": None}), \
             patch("api.services.fs_index.fs_index.search_semantic_ranked",
                   return_value=[(0.9, Path("/mnt/e/Xyron/other/found_it.txt"), {})]), \
             patch("api.services.fs_index.fs_index.search_ranked", return_value=[]):

            r = fr.resolve("found it", open_type="file")

        self.assertEqual(r.tier, 8)
        self.assertEqual(r.path, Path("/mnt/e/Xyron/other/found_it.txt"))

    def test_learned_tier_beats_everything_when_path_exists(self):
        from api.services import file_resolver as fr

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            real_path = f.name
        try:
            with patch("api.services.fs_index.fs_index.get_learned_resolution",
                       return_value=(real_path, 5)):
                r = fr.resolve("whatever this was called", open_type="file")
            self.assertEqual(r.decision, "open")
            self.assertEqual(r.tier, 0)
            self.assertEqual(str(r.path), real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_learned_tier_ignored_when_path_no_longer_exists(self):
        from api.services import file_resolver as fr

        with patch("api.services.fs_index.fs_index.get_learned_resolution",
                   return_value=("/mnt/e/Xyron/this_file_was_deleted_xyz.txt", 5)), \
             patch("api.services.window_context.window_context.get_active_window", return_value=None), \
             patch("api.services.workspace_context.get_active_workspace", return_value=None), \
             patch("api.services.explorer_context.explorer_context.get_focused_folder", return_value=None), \
             patch("api.services.fs_index.fs_index.get_recent_files", return_value=[]), \
             patch("api.services.fs_index.fs_index.get_frequent_files", return_value=[]), \
             patch("api.services.memory_service.memory_service.get_context",
                   return_value={"last_file": None, "last_folder": None}), \
             patch("api.services.fs_index.fs_index.search_semantic_ranked", return_value=[]), \
             patch("api.services.fs_index.fs_index.search_ranked", return_value=[]):

            r = fr.resolve("this file was deleted", open_type="file")

        self.assertEqual(r.decision, "none")


class TestLearning(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api.services.fs_index import fs_index
        cls.fs_index = fs_index
        cls.query = "zzz_test_learning_query_unique"
        cls.path = "/mnt/e/Xyron/zzz_test_learning_path.txt"

    def tearDown(self):
        import sqlite3
        conn = sqlite3.connect(str(self.fs_index._db_path))
        conn.execute("DELETE FROM learned_resolutions WHERE query_norm = ?", (self.query,))
        conn.commit()
        conn.close()

    def test_record_and_retrieve(self):
        from api.services.file_resolver import record_confirmed_choice
        record_confirmed_choice(self.query, self.path)
        result = self.fs_index.get_learned_resolution(self.query)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], self.path)
        self.assertEqual(result[1], 1)

    def test_repeated_confirmation_increments_hits(self):
        from api.services.file_resolver import record_confirmed_choice
        record_confirmed_choice(self.query, self.path)
        record_confirmed_choice(self.query, self.path)
        record_confirmed_choice(self.query, self.path)
        result = self.fs_index.get_learned_resolution(self.query)
        self.assertEqual(result[1], 3)


class TestUsageAffinity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api.services.fs_index import fs_index, SEMANTIC_ROOTS
        cls.fs_index = fs_index
        cls.tmp_dir = SEMANTIC_ROOTS[-1] / "_tmp_phase15_usage_test"
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_folder_affinity_promotes_matching_context(self):
        p = self.tmp_dir / "affinity_test.txt"
        p.write_text("content")
        entry_id = self.fs_index.upsert_single(p)

        for _ in range(5):
            self.fs_index.mark_opened(p, context={
                "active_app": "code", "active_folder": "myproj", "active_project": "myproj",
            })

        now_ctx = {"hour": time.localtime().tm_hour, "weekday": time.localtime().tm_wday,
                   "active_app": "code", "active_folder": "myproj", "active_project": "myproj"}
        aff = self.fs_index.get_usage_affinity([entry_id], now_ctx)
        self.assertIn(entry_id, aff)
        self.assertGreater(aff[entry_id], 0.5)

        no_match_ctx = {"hour": time.localtime().tm_hour, "weekday": time.localtime().tm_wday,
                        "active_app": "photoshop", "active_folder": "unrelated", "active_project": "unrelated"}
        aff2 = self.fs_index.get_usage_affinity([entry_id], no_match_ctx)
        self.assertLess(aff2.get(entry_id, 0.0), aff[entry_id])

        self.fs_index.remove_path(p)

    def test_no_events_returns_empty(self):
        aff = self.fs_index.get_usage_affinity([-99999999], {"hour": 5, "weekday": 1})
        self.assertEqual(aff, {})


class TestPerformance(unittest.TestCase):
    """
    The common case — no recognized workspace/Explorer app in the foreground
    — must never shell out to PowerShell, and must resolve fast purely from
    local SQLite/FAISS lookups. This is the "must not slow deterministic
    commands" requirement.
    """

    def test_no_context_path_never_shells_out_and_is_fast(self):
        from api.services import file_resolver as fr
        import subprocess as _subprocess

        with patch("api.services.window_context.window_context.get_active_window", return_value=None), \
             patch("api.services.fs_index.fs_index.get_recent_files", return_value=[]), \
             patch("api.services.fs_index.fs_index.get_frequent_files", return_value=[]), \
             patch("api.services.memory_service.memory_service.get_context",
                   return_value={"last_file": None, "last_folder": None}), \
             patch("api.services.fs_index.fs_index.search_semantic_ranked", return_value=[]), \
             patch("api.services.fs_index.fs_index.search_ranked", return_value=[]), \
             patch.object(_subprocess, "run") as mock_run:

            t0 = time.monotonic()
            r = fr.resolve("nothing matches this at all zzz", open_type="file")
            elapsed_ms = (time.monotonic() - t0) * 1000

            mock_run.assert_not_called()
        self.assertEqual(r.decision, "none")
        self.assertLess(elapsed_ms, 200)


if __name__ == "__main__":
    unittest.main()
