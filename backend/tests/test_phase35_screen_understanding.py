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
    match_product_followup,
    handle_product_followup,
    handle_screen_offer_confirmation,
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

    def test_shopping_page_describes_product_with_offer(self):
        window = {"title": "Wireless Headphones - Google Chrome", "proc_name": "chrome", "pid": 111}
        ctx = {
            "current_browser": {"url": "https://amazon.com/dp/B000", "title": "Wireless Headphones",
                                 "tab_count": 1, "page_type": "shopping"},
            "current_repository": None,
            "current_product": {
                "name": "Wireless Headphones", "brand": "Acme", "price": "49.99", "currency": "USD",
                "rating": "4.5", "review_count": "1200",
            },
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("Wireless Headphones", desc)
        self.assertIn("49.99", desc)
        self.assertIn("compare", desc.lower())
        self.assertIn("cheaper", desc.lower())

    def test_shopping_page_without_product_schema_falls_back_gracefully(self):
        window = {"title": "Deals - Google Chrome", "proc_name": "chrome", "pid": 112}
        ctx = {
            "current_browser": {"url": "https://www.ebay.com/deals", "title": "Deals",
                                 "tab_count": 1, "page_type": "shopping"},
            "current_repository": None,
            "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("ebay.com", desc)
        self.assertIn("shopping", desc.lower())

    def test_youtube_page_type_names_the_video(self):
        window = {"title": "Believer - Imagine Dragons - YouTube - Google Chrome",
                  "proc_name": "chrome", "pid": 113}
        ctx = {
            "current_browser": {"url": "https://youtube.com/watch?v=x",
                                 "title": "Believer - Imagine Dragons - YouTube",
                                 "tab_count": 1, "page_type": "youtube"},
            "current_repository": None, "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("Believer - Imagine Dragons", desc)
        self.assertIn("watching", desc.lower())

    def test_google_search_page_type_names_the_query(self):
        window = {"title": "best wireless headphones - Google Search - Google Chrome",
                  "proc_name": "chrome", "pid": 114}
        ctx = {
            "current_browser": {"url": "https://www.google.com/search?q=best+wireless+headphones",
                                 "title": "best wireless headphones - Google Search",
                                 "tab_count": 1, "page_type": "google_search"},
            "current_repository": None, "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("best wireless headphones", desc)
        self.assertIn("searching", desc.lower())

    def test_documentation_page_type(self):
        window = {"title": "API Reference - Google Chrome", "proc_name": "chrome", "pid": 115}
        ctx = {
            "current_browser": {"url": "https://docs.python.org/3/", "title": "API Reference",
                                 "tab_count": 1, "page_type": "documentation"},
            "current_repository": None, "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("documentation", desc.lower())
        self.assertIn("API Reference", desc)

    def test_chatgpt_page_type(self):
        window = {"title": "ChatGPT - Google Chrome", "proc_name": "chrome", "pid": 116}
        ctx = {
            "current_browser": {"url": "https://chatgpt.com/", "title": "ChatGPT",
                                 "tab_count": 1, "page_type": "chatgpt"},
            "current_repository": None, "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertIn("ChatGPT", desc)

    def test_banking_page_type_does_not_leak_title(self):
        window = {"title": "Account Summary - MyBank - Google Chrome", "proc_name": "chrome", "pid": 117}
        ctx = {
            "current_browser": {"url": "https://mybank.com/accounts",
                                 "title": "Account Summary - MyBank",
                                 "tab_count": 1, "page_type": "banking"},
            "current_repository": None, "current_product": None,
        }
        with patch("api.services.window_context.window_context.get_active_window", return_value=window), \
             patch("api.services.world_state.world_state.get_context", return_value=ctx):
            desc = self.agent.get_fresh().describe()
        self.assertNotIn("Account Summary", desc)
        self.assertIn("banking", desc.lower())


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


class TestProductFollowups(unittest.TestCase):
    def setUp(self):
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="product", value="Wireless Headphones", display="Wireless Headphones",
            source="screen_agent",
            metadata={
                "name": "Wireless Headphones", "brand": "Acme", "price": "49.99", "currency": "USD",
                "offer": ["cheaper", "compare"],
            },
        ))

    def tearDown(self):
        context_stack.clear()

    def test_find_cheaper_matches_and_reports_real_prices(self):
        action = match_product_followup("Can you find me something cheaper?")
        self.assertEqual(action, "cheaper")
        with patch("api.services.screen_context_agent._search_prices", new_callable=AsyncMock,
                   return_value=[{"title": "Acme Headphones - BestBuy", "price": "$39.99"}]):
            resp = _run(handle_product_followup(action))
        # Real extracted price, not a bare acknowledgment.
        self.assertIn("39.99", resp)
        self.assertIn("49.99", resp)  # still names the current page's price for comparison

    def test_compare_it_matches(self):
        self.assertEqual(match_product_followup("Compare it for me."), "compare")

    def test_no_prices_found_is_handled_honestly(self):
        with patch("api.services.screen_context_agent._search_prices", new_callable=AsyncMock,
                   return_value=[]):
            resp = _run(handle_product_followup("cheaper"))
        self.assertIn("couldn't find", resp.lower())

    def test_no_product_in_context_is_handled_honestly(self):
        context_stack.clear()
        resp = _run(handle_product_followup("cheaper"))
        self.assertIn("don't have a product", resp)


class TestScreenOfferConfirmation(unittest.TestCase):
    """Bare 'yes' after a screen-agent offer (Part 10 generalized beyond
    GitHub) — dispatches to the offer's default action using whatever
    ContextStack entity the last screen query pushed."""

    def tearDown(self):
        context_stack.clear()

    def test_bare_yes_confirms_pending_product_offer(self):
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="product", value="Wireless Headphones", display="Wireless Headphones",
            source="screen_agent",
            metadata={"name": "Wireless Headphones", "price": "49.99", "currency": "USD",
                      "offer": ["cheaper", "compare"]},
        ))
        with patch("api.services.screen_context_agent._search_prices", new_callable=AsyncMock,
                   return_value=[{"title": "Acme Headphones - BestBuy", "price": "$39.99"}]):
            resp = _run(handle_screen_offer_confirmation())
        self.assertIsNotNone(resp)
        self.assertIn("39.99", resp)

    def test_bare_yes_confirms_pending_repository_offer(self):
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="repository", value="tayyab/Xyron", display="Xyron", source="screen_agent",
            metadata={"owner": "tayyab", "name": "Xyron", "branch": "main", "current_path": None,
                      "page_type": "repository_home", "description": "Voice assistant",
                      "offer": ["review"]},
        ))
        resp = _run(handle_screen_offer_confirmation())
        self.assertIsNotNone(resp)
        self.assertIn("Xyron", resp)

    def test_no_pending_offer_returns_none(self):
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="folder", value="C:\\Downloads", display="Downloads", source="some_tool",
            metadata={},
        ))
        resp = _run(handle_screen_offer_confirmation())
        self.assertIsNone(resp)

    def test_stale_offer_is_not_confirmed(self):
        context_stack.clear()
        entity = ContextEntity(
            type="product", value="Wireless Headphones", display="Wireless Headphones",
            source="screen_agent",
            metadata={"name": "Wireless Headphones", "offer": ["cheaper"]},
        )
        entity.pushed_at -= 120  # simulate an offer made two minutes ago
        context_stack.push(entity)
        resp = _run(handle_screen_offer_confirmation())
        self.assertIsNone(resp)


if __name__ == "__main__":
    unittest.main()
