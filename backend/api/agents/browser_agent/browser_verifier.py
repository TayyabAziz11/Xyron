"""
BrowserVerifier — post-action verification checks.

Each verify_* method logs [BROWSER_VERIFY_SUCCESS] or [BROWSER_VERIFY_FAIL]
and returns a bool so callers can decide to retry or surface an error.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import Page

logger = logging.getLogger("api.agents.browser_agent.verifier")


def _log_ok(check: str) -> None:
    logger.info("[BROWSER_VERIFY_SUCCESS] check=%s", check)


def _log_fail(check: str, detail: str = "") -> None:
    logger.warning("[BROWSER_VERIFY_FAIL] check=%s detail=%r", check, detail)


class BrowserVerifier:
    """Stateless post-step assertion helpers."""

    # ── Page loaded ────────────────────────────────────────────────────────────

    async def verify_page_loaded(self, page: Page) -> bool:
        """
        Confirm the page has a non-empty body with at least some text.
        Guards against blank pages, error pages, or redirect loops.
        """
        try:
            state = await page.evaluate("document.readyState")
            body_text: str = await page.evaluate(
                "document.body ? document.body.innerText : ''"
            )
            if state in ("complete", "interactive") and len(body_text.strip()) > 50:
                _log_ok("page_loaded")
                return True
            _log_fail("page_loaded", f"readyState={state} body_chars={len(body_text)}")
            return False
        except Exception as exc:
            _log_fail("page_loaded", str(exc))
            return False

    # ── Search results present ─────────────────────────────────────────────────

    async def verify_search_results(self, page: Page) -> bool:
        """
        Check that search result elements are present on the page.
        Covers Google, Bing, and DuckDuckGo layouts.
        """
        result_selectors = [
            "div#search div.g",      # Google organic
            "li.b_algo",             # Bing
            ".result",               # DDG / generic
            "article",               # News / generic
        ]
        try:
            for sel in result_selectors:
                items = await page.query_selector_all(sel)
                if items:
                    _log_ok(f"search_results[{sel}]")
                    return True
            _log_fail("search_results", "no result elements found")
            return False
        except Exception as exc:
            _log_fail("search_results", str(exc))
            return False

    # ── Form filled ────────────────────────────────────────────────────────────

    async def verify_form_filled(self, page: Page, expected_fields: dict) -> bool:
        """
        Confirm that form inputs contain the expected values.

        *expected_fields*: {selector: expected_value}
        Returns True only if ALL fields match.
        """
        all_ok = True
        for selector, expected_value in expected_fields.items():
            try:
                el = await page.query_selector(selector)
                if el is None:
                    _log_fail(
                        "form_filled",
                        f"selector not found: {selector!r}",
                    )
                    all_ok = False
                    continue
                actual: str = await el.input_value() or ""
                if actual.strip() == str(expected_value).strip():
                    _log_ok(f"form_field[{selector}]")
                else:
                    _log_fail(
                        "form_filled",
                        f"selector={selector!r} expected={expected_value!r} actual={actual!r}",
                    )
                    all_ok = False
            except Exception as exc:
                _log_fail("form_filled", str(exc))
                all_ok = False

        return all_ok

    # ── Download complete ──────────────────────────────────────────────────────

    async def verify_download_complete(self, path: Path) -> bool:
        """
        Check that *path* exists and is a non-empty file.
        Also ensures the file is not a partial download (.crdownload / .part).
        """
        try:
            if not path.exists():
                _log_fail("download_complete", f"path not found: {path}")
                return False
            if path.stat().st_size == 0:
                _log_fail("download_complete", f"file is empty: {path}")
                return False
            partial_suffixes = {".crdownload", ".part", ".tmp", ".download"}
            if path.suffix.lower() in partial_suffixes:
                _log_fail("download_complete", f"partial file suffix: {path.suffix}")
                return False
            _log_ok(f"download_complete[{path.name}]")
            return True
        except Exception as exc:
            _log_fail("download_complete", str(exc))
            return False

    # ── URL navigation ─────────────────────────────────────────────────────────

    async def verify_navigation(self, page: Page, expected_url_part: str) -> bool:
        """
        Confirm the current URL contains *expected_url_part*.
        Case-insensitive substring match.
        """
        try:
            current_url = page.url.lower()
            target = expected_url_part.lower()
            if target in current_url:
                _log_ok(f"navigation[{expected_url_part}]")
                return True
            _log_fail(
                "navigation",
                f"expected={expected_url_part!r} got={page.url!r}",
            )
            return False
        except Exception as exc:
            _log_fail("navigation", str(exc))
            return False
