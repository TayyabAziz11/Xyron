"""
Tests for Phase 3.5 proven issue 2 — superficial screen descriptions.

Covers:
  - classify_github_page(): pure URL-structure parsing (Part 8), no
    DOM/LLM dependency so it's fully testable offline.
  - ScreenSnapshot.describe(): natural composer built from structured
    perception, never a raw window-title/OCR dump (Part 9), with a
    same-site regression check that ordinary (non-GitHub) browsing still
    falls back to the pre-existing title-based description.
  - Repository follow-ups reusing ContextStack (Part 10) — "review it",
    "open the README", "show issues", "check the latest commit", "what
    does this file do".
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.perception.browser_perception import classify_github_page
from api.services.screen_context_agent import (
    ScreenContextAgent,
    match_repository_followup,
    handle_repository_followup,
)
from api.services.context_stack import context_stack, ContextEntity


def _run(coro):
    return asyncio.run(coro)


class TestGitHubURLClassification(unittest.TestCase):
    def test_repository_home(self):
        r = classify_github_page("https://github.com/tayyab/Xyron")
        self.assertEqual(r["owner"], "tayyab")
        self.assertEqual(r["name"], "Xyron")
        self.assertEqual(r["page_type"], "repository_home")

    def test_file_view_blob(self):
        r = classify_github_page("https://github.com/tayyab/Xyron/blob/main/backend/api/main.py")
        self.assertEqual(r["page_type"], "file_view")
        self.assertEqual(r["branch"], "main")
        self.assertEqual(r["current_path"], "backend/api/main.py")

    def test_file_view_tree(self):
        r = classify_github_page("https://github.com/tayyab/Xyron/tree/main/backend")
        self.assertEqual(r["page_type"], "file_view")
        self.assertEqual(r["current_path"], "backend")

    def test_issues(self):
        r = classify_github_page("https://github.com/tayyab/Xyron/issues")
        self.assertEqual(r["page_type"], "issue")

    def test_pull_request(self):
        r = classify_github_page("https://github.com/tayyab/Xyron/pull/7")
        self.assertEqual(r["page_type"], "pull_request")

    def test_actions(self):
        r = classify_github_page("https://github.com/tayyab/Xyron/actions")
        self.assertEqual(r["page_type"], "actions")

    def test_non_repository_pages_return_none(self):
        for url in ("https://github.com/", "https://github.com/tayyab",
                    "https://github.com/settings/profile"):
            self.assertIsNone(classify_github_page(url), url)

    def test_non_github_url_returns_none(self):
        self.assertIsNone(classify_github_page("https://example.com/tayyab/Xyron"))


class TestScreenDescriptionComposer(unittest.TestCase):
    def setUp(self):
        self.agent = ScreenContextAgent()
        self.fake_window = {
            "title": "tayyab/Xyron: Voice-controlled AI assistant - Google Chrome",
            "proc_name": "chrome", "pid": 1234,
        }

    def test_github_repo_home_description_is_semantic_not_raw_title(self):
        ctx = {
            "current_browser": {"url": "https://github.com/tayyab/Xyron", "title": "Xyron",
                                 "tab_count": 1, "page_type": "github"},
            "current_repository": {
                "owner": "tayyab", "name": "Xyron", "branch": None, "current_path": None,
                "page_type": "repository_home", "description": "Voice-controlled AI desktop assistant",
            },
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=self.fake_window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()

        self.assertIn("Xyron", desc)
        self.assertIn("GitHub", desc)
        self.assertIn("repository", desc.lower())
        # Must not be a bare regurgitation of the raw window title.
        self.assertNotEqual(desc, self.fake_window["title"])
        self.assertNotIn("Voice-controlled AI assistant - Google Chrome", desc)

    def test_github_file_view_mentions_current_path(self):
        ctx = {
            "current_browser": {"url": "...", "title": "main.py", "tab_count": 1, "page_type": "github"},
            "current_repository": {
                "owner": "tayyab", "name": "Xyron", "branch": "main",
                "current_path": "backend/api/main.py", "page_type": "file_view",
            },
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=self.fake_window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("backend/api/main.py", desc)

    def test_non_github_browsing_regression_falls_back_to_title(self):
        window = {"title": "Cats And Dogs Compilation - YouTube - Google Chrome",
                  "proc_name": "chrome", "pid": 5678}
        ctx = {"current_browser": None, "current_repository": None}
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("Cats And Dogs Compilation - YouTube", desc)


class TestRepositoryFollowups(unittest.TestCase):
    def setUp(self):
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="repository", value="tayyab/Xyron", display="Xyron", source="screen_agent",
            metadata={
                "owner": "tayyab", "name": "Xyron", "branch": "main", "current_path": None,
                "page_type": "repository_home", "description": "Voice-controlled AI desktop assistant",
            },
        ))

    def tearDown(self):
        context_stack.clear()

    def test_review_it_uses_only_extracted_data(self):
        action = match_repository_followup("Give me a review of it.")
        self.assertEqual(action, "review")
        resp = _run(handle_repository_followup(action))
        self.assertIn("Xyron", resp)
        self.assertIn("Voice-controlled AI desktop assistant", resp)

    def test_open_readme_targets_correct_url(self):
        action = match_repository_followup("Open the README.")
        self.assertEqual(action, "readme")
        with patch("api.services.screen_context_agent._navigate_existing_tab",
                   new_callable=AsyncMock, return_value=False):
            resp = _run(handle_repository_followup(action))
        self.assertIn("https://github.com/tayyab/Xyron/blob/main/README.md", resp)

    def test_show_issues_targets_correct_url(self):
        action = match_repository_followup("Show me issues.")
        resp = _run(handle_repository_followup(action))
        self.assertIn("https://github.com/tayyab/Xyron/issues", resp)

    def test_check_latest_commit_targets_correct_url(self):
        action = match_repository_followup("Check the latest commit.")
        resp = _run(handle_repository_followup(action))
        self.assertIn("https://github.com/tayyab/Xyron/commits/main", resp)

    def test_explain_file_is_truthful_when_no_file_is_open(self):
        action = match_repository_followup("What does this file do?")
        self.assertEqual(action, "explain_file")
        resp = _run(handle_repository_followup(action))
        # Must NOT fabricate an explanation of content nobody read.
        self.assertIn("don't see a specific file", resp)

    def test_no_repository_in_context_is_handled_honestly(self):
        context_stack.clear()
        resp = _run(handle_repository_followup("review"))
        self.assertIn("don't have a repository", resp)


if __name__ == "__main__":
    unittest.main()
