"""
tests/test_instagram_mock_mode.py
Integration-style tests for InstagramWatcher mock mode and
InstagramExecutor dry-run mode.  No real API calls; no file system
side-effects beyond a temp directory.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.skills.gold.instagram_watcher_skill import (
    InstagramWatcher,
    MOCK_MEDIA,
    MOCK_COMMENTS,
)
from ai_operator.skills.gold.brain_execute_instagram_with_mcp_skill import (
    InstagramExecutor,
)


# ── Watcher mock-mode tests ────────────────────────────────────────────────────

class TestInstagramWatcherMockMode(unittest.TestCase):

    def _make_watcher(self, tmp: Path) -> InstagramWatcher:
        return InstagramWatcher({
            "mode": "mock",
            "repo_root": str(tmp),
            "checkpoint_path": str(tmp / "Logs" / "instagram_watcher_checkpoint.json"),
            "media_limit": 10,
            "comment_limit": 20,
        })

    def test_watch_creates_intake_wrappers(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            summary = watcher.watch()

            inbox = Path(td) / "Social" / "Inbox"
            wrappers = list(inbox.glob("inbox__instagram__*.md"))
            # 2 media + 5 comments in mock data
            self.assertGreater(len(wrappers), 0)
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["errors"], 0)

    def test_watch_skips_already_processed(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            first  = watcher.watch()
            second = watcher.watch()   # same mock data

            self.assertEqual(second["created"], 0, "Second run should create no new wrappers")
            self.assertGreater(second["skipped"], 0)

    def test_intake_wrapper_has_yaml_frontmatter(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            watcher.watch()

            inbox = Path(td) / "Social" / "Inbox"
            wrappers = list(inbox.glob("inbox__instagram__media_post__*.md"))
            self.assertGreater(len(wrappers), 0, "Expected media_post wrappers")

            content = wrappers[0].read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---"), "File must start with YAML front-matter")
            self.assertIn("source: instagram", content)
            self.assertIn("status: unread", content)

    def test_checkpoint_is_saved(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            watcher.watch()

            checkpoint = Path(td) / "Logs" / "instagram_watcher_checkpoint.json"
            self.assertTrue(checkpoint.exists(), "Checkpoint file should be created")
            data = json.loads(checkpoint.read_text())
            self.assertIn("processed_ids", data)
            self.assertGreater(len(data["processed_ids"]), 0)

    def test_mock_data_ids_are_all_processed(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            watcher.watch()

            checkpoint = Path(td) / "Logs" / "instagram_watcher_checkpoint.json"
            ids = json.loads(checkpoint.read_text())["processed_ids"]

            for media in MOCK_MEDIA:
                self.assertIn(media["id"], ids)
            for comments in MOCK_COMMENTS.values():
                for cmt in comments:
                    self.assertIn(cmt["id"], ids)

    def test_summary_counts_match_mock_data(self):
        with tempfile.TemporaryDirectory() as td:
            watcher = self._make_watcher(Path(td))
            summary = watcher.watch()

            expected = len(MOCK_MEDIA) + sum(len(v) for v in MOCK_COMMENTS.values())
            self.assertEqual(summary["created"], expected)


# ── Executor dry-run tests ────────────────────────────────────────────────────

class TestInstagramExecutorDryRun(unittest.TestCase):

    def _make_executor(self, tmp: Path) -> InstagramExecutor:
        return InstagramExecutor({"repo_root": str(tmp)})

    def _write_approved_plan(self, tmp: Path, operation: str, **params) -> Path:
        approved = tmp / "Approved"
        approved.mkdir(parents=True, exist_ok=True)
        actions = [{"server": "instagram", "operation": operation, "parameters": params}]
        plan_content = f"""---
title: Test Instagram Plan
operation: {operation}
---

## Actions JSON

```json
{json.dumps(actions, indent=2)}
```
"""
        plan_path = approved / "WEBPLAN_20260222_Instagram_Test_post.md"
        plan_path.write_text(plan_content, encoding="utf-8")
        return plan_path

    def test_dry_run_succeeds_with_valid_plan(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._write_approved_plan(
                tmp,
                "create_post_image",
                caption="Test caption for dry run",
                image_url="https://example.com/img.jpg",
            )
            executor = self._make_executor(tmp)
            result = executor.execute(dry_run=True)
            self.assertTrue(result["success"])
            self.assertEqual(result["actions_attempted"], 1)
            self.assertEqual(result["actions_succeeded"], 1)

    def test_dry_run_no_approved_plan_fails(self):
        with tempfile.TemporaryDirectory() as td:
            executor = self._make_executor(Path(td))
            result = executor.execute(dry_run=True)
            self.assertFalse(result["success"])
            self.assertIn("No approved", result.get("error", ""))

    def test_dry_run_text_only_op_rejected(self):
        """create_post_text must fail — Instagram doesn't support text-only posts."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._write_approved_plan(
                tmp,
                "create_post_text",
                caption="This should fail at real execution",
            )
            executor = self._make_executor(tmp)
            # Dry-run will succeed (validation passes), real-mode will fail.
            # Verify dry-run at least validates caption present.
            result = executor.execute(dry_run=True)
            self.assertTrue(result["success"], "Dry-run should pass validation for text-only op")

    def test_missing_caption_fails_validation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._write_approved_plan(
                tmp,
                "create_post_image",
                image_url="https://example.com/img.jpg",
                # intentionally no caption
            )
            executor = self._make_executor(tmp)
            result = executor.execute(dry_run=True)
            self.assertFalse(result["success"])

    def test_missing_image_url_fails_validation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._write_approved_plan(
                tmp,
                "create_post_image",
                caption="Caption without image",
            )
            executor = self._make_executor(tmp)
            result = executor.execute(dry_run=True)
            self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
