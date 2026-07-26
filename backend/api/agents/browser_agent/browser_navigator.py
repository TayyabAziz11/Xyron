"""
BrowserNavigator — low-level page navigation and DOM interaction.

Prefers DOM / accessibility-tree techniques over screenshots.
All public methods are async, wrapped in try/except, and log using
the required [BROWSER_*] tag format.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, TimeoutError as PWTimeout

logger = logging.getLogger("api.agents.browser_agent.navigator")

# Same WSL interop mechanism system_tools.py uses to launch Windows apps —
# opens each visited URL in the user's real Chrome so automation is visible
# as an actual Chrome tab, not the invisible/headless Playwright driver.
_CMDEXE_CANDIDATES = [
    "/mnt/c/Windows/System32/cmd.exe",
    "/mnt/c/WINDOWS/System32/cmd.exe",
    "/mnt/c/WINDOWS/system32/cmd.exe",
]


def _open_in_real_chrome(url: str) -> None:
    cmdexe = next((p for p in _CMDEXE_CANDIDATES if Path(p).exists()), None)
    if not cmdexe:
        logger.info("[BROWSER_LINUX_DRIVER_INTERNAL] url=%s reason=no_windows_chrome_found", url)
        return
    try:
        subprocess.Popen(
            ["/init", cmdexe, "/c", "start", "", "chrome", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("[BROWSER_REAL_CHROME_OPENED] url=%s", url)
        logger.info("[BROWSER_VISIBLE_NAVIGATION] url=%s driver=real_chrome", url)
    except Exception as exc:
        logger.debug("[BROWSER_REAL_CHROME_OPEN_FAILED] url=%s error=%r", url, str(exc))
        logger.info("[BROWSER_LINUX_DRIVER_INTERNAL] url=%s reason=real_chrome_launch_failed", url)

# Selectors tried in order when looking for cookie / consent banners.
_COOKIE_SELECTORS: list[str] = [
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    ".accept-cookies",
    ".accept-all",
    "[data-testid*='cookie'] button",
    "[aria-label*='cookie' i] button",
    "#cookieConsent button",
    "#gdpr-consent button",
    "button[id*='accept']",
    "button[class*='accept']",
    "[class*='consent'] button",
    "button:has-text('Accept all')",
    "button:has-text('Accept cookies')",
    "button:has-text('I agree')",
    "button:has-text('Got it')",
    "button:has-text('OK')",
    "button:has-text('Allow all')",
]

_POPUP_SELECTORS: list[str] = [
    "[class*='modal'] [class*='close']",
    "[class*='popup'] [class*='close']",
    "[aria-label='Close']",
    "[aria-label='Dismiss']",
    "button[class*='close']",
    ".modal-close",
    ".popup-close",
    "[data-dismiss='modal']",
]

_GOOGLE_RESULT_SELECTOR = "div#search div.g"


class BrowserNavigator:
    """Wraps a Playwright Page with higher-level navigation helpers."""

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── Navigation ─────────────────────────────────────────────────────────────

    async def go_to(self, url: str, mirror: bool = True) -> bool:
        """Navigate to *url*. Returns True on success.

        *mirror* controls whether this also opens in the user's real
        Windows Chrome. Internal housekeeping navigations (Google search
        result pages used only to locate a real destination URL) pass
        mirror=False — the actual destination page still mirrors via a
        separate go_to() call once found, so nothing the user cares about
        is hidden; this just stops every intermediate Google search from
        popping its own visible tab.
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if mirror:
            _open_in_real_chrome(url)
        else:
            logger.info("[BROWSER_LINUX_DRIVER_INTERNAL] url=%s reason=mirror_suppressed_internal_search", url)
        logger.info("[BROWSER_LINUX_DRIVER_INTERNAL] url=%s note=headless_automation_driver", url)
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(0.5)
            logger.info("[BROWSER_PAGE_OPENED] url=%s", url)
            return True
        except Exception as exc:
            logger.warning("[BROWSER_NAV_ERROR] url=%s error=%r", url, str(exc))
            return False

    async def wait_for_load(self) -> None:
        """Wait for network idle + small JS-settle delay."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass  # partial load is fine — page may have streamed enough
        await asyncio.sleep(0.5)

    # ── Google search ──────────────────────────────────────────────────────────

    async def search_google(self, query: str) -> list[dict]:
        """
        Navigate to Google, submit *query*, parse organic results.

        Returns list of dicts: {title, url, snippet}.
        """
        search_url = "https://www.google.com/search?q=" + query.replace(" ", "+")
        ok = await self.go_to(search_url, mirror=False)
        if not ok:
            return []
        await self.handle_cookie_banner()
        await asyncio.sleep(0.5)

        results: list[dict] = []
        try:
            items = await self.page.query_selector_all(_GOOGLE_RESULT_SELECTOR)
            for item in items[:10]:
                try:
                    anchor = await item.query_selector("a")
                    h3 = await item.query_selector("h3")
                    snippet_el = await item.query_selector(
                        "div[data-sncf], div.VwiC3b, span.st"
                    )
                    href = (await anchor.get_attribute("href")) if anchor else ""
                    title = (await h3.inner_text()) if h3 else ""
                    snippet = (await snippet_el.inner_text()) if snippet_el else ""
                    if href and title:
                        results.append(
                            {
                                "title": title.strip(),
                                "url": href.strip(),
                                "snippet": snippet.strip(),
                            }
                        )
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("[BROWSER_SEARCH_PARSE_ERROR] error=%r", str(exc))

        logger.info(
            "[BROWSER_SEARCH_DONE] query=%r results_found=%d", query, len(results)
        )
        return results

    # ── Content extraction ─────────────────────────────────────────────────────

    async def get_page_text(self) -> str:
        """
        Return visible page text stripped of HTML, scripts, and styles.
        Uses JS innerText on the body for a clean DOM read.
        """
        try:
            text: str = await self.page.evaluate(
                """() => {
                    // Remove script/style/nav/footer noise
                    const remove_tags = ['script','style','nav','footer','header','aside','noscript'];
                    remove_tags.forEach(tag => {
                        document.querySelectorAll(tag).forEach(el => el.remove());
                    });
                    return (document.body && document.body.innerText) || '';
                }"""
            )
            # Collapse whitespace runs
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"[ \t]+", " ", text)
            url = self.page.url
            char_count = len(text)
            logger.info(
                "[BROWSER_PAGE_READ] url=%s chars=%d", url, char_count
            )
            return text.strip()
        except Exception as exc:
            logger.warning("[BROWSER_PAGE_READ_ERROR] error=%r", str(exc))
            return ""

    async def get_page_title(self) -> str:
        """Return the page <title> text."""
        try:
            return await self.page.title()
        except Exception:
            return ""

    # ── Scrolling ──────────────────────────────────────────────────────────────

    async def scroll_down(self, px: int = 500) -> None:
        """Scroll the page down by *px* pixels."""
        try:
            await self.page.evaluate(f"window.scrollBy(0, {px})")
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("[BROWSER_SCROLL_ERROR] error=%r", str(exc))

    # ── Overlay handling ───────────────────────────────────────────────────────

    async def handle_cookie_banner(self) -> bool:
        """
        Attempt to dismiss a cookie / GDPR consent overlay.
        Returns True if a banner was found and dismissed.
        """
        _loop_t0 = time.time()
        logger.info("[MICRO_PROFILE] op=cookie_banner_loop_start selector_count=%d", len(_COOKIE_SELECTORS))
        for _idx, sel in enumerate(_COOKIE_SELECTORS, 1):
            _sel_t0 = time.time()
            try:
                btn = await self.page.query_selector(sel)
                _query_ms = (time.time() - _sel_t0) * 1000
                if btn and await btn.is_visible():
                    logger.info(
                        "[MICRO_PROFILE] op=cookie_selector_check index=%d/%d selector=%r "
                        "query_ms=%.1f result=match_found",
                        _idx, len(_COOKIE_SELECTORS), sel, _query_ms,
                    )
                    _click_t0 = time.time()
                    await btn.click(timeout=5_000)
                    _click_ms = (time.time() - _click_t0) * 1000
                    logger.info("[MICRO_PROFILE] op=cookie_click selector=%r click_ms=%.1f", sel, _click_ms)
                    await asyncio.sleep(0.3)
                    logger.info("[MICRO_PROFILE] op=cookie_settle_sleep duration_ms=300.0 kind=fixed_sleep")
                    logger.info("[BROWSER_COOKIE_DISMISSED] selector=%r", sel)
                    logger.info(
                        "[MICRO_PROFILE] op=cookie_banner_loop_end outcome=dismissed "
                        "selectors_checked=%d/%d total_ms=%.1f",
                        _idx, len(_COOKIE_SELECTORS), (time.time() - _loop_t0) * 1000,
                    )
                    return True
                logger.info(
                    "[MICRO_PROFILE] op=cookie_selector_check index=%d/%d selector=%r "
                    "query_ms=%.1f result=no_match",
                    _idx, len(_COOKIE_SELECTORS), sel, _query_ms,
                )
            except Exception as exc:
                logger.info(
                    "[MICRO_PROFILE] op=cookie_selector_check index=%d/%d selector=%r "
                    "query_ms=%.1f result=exception error=%r",
                    _idx, len(_COOKIE_SELECTORS), sel, (time.time() - _sel_t0) * 1000, str(exc),
                )
                continue
        logger.info(
            "[MICRO_PROFILE] op=cookie_banner_loop_end outcome=no_banner_found "
            "selectors_checked=%d/%d total_ms=%.1f",
            len(_COOKIE_SELECTORS), len(_COOKIE_SELECTORS), (time.time() - _loop_t0) * 1000,
        )
        return False

    async def handle_popup(self) -> bool:
        """
        Dismiss a modal popup / overlay if one is visible.
        Returns True if something was dismissed.
        """
        for sel in _POPUP_SELECTORS:
            try:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(timeout=5_000)
                    await asyncio.sleep(0.3)
                    logger.info("[BROWSER_POPUP_DISMISSED] selector=%r", sel)
                    return True
            except Exception:
                continue
        return False

    # ── Element finding ────────────────────────────────────────────────────────

    async def find_element(self, description: str) -> Optional[str]:
        """
        Attempt to find a CSS selector for *description* (a natural-language hint
        such as "submit button" or "email input").

        Returns a CSS selector string if found, else None.
        Strategy: build candidate selectors from the description keywords, then
        test each against the live DOM.
        """
        desc_lower = description.lower()
        candidates: list[str] = []

        # Buttons
        if any(w in desc_lower for w in ("button", "click", "submit", "send", "go")):
            candidates += [
                f"button:has-text('{description}')",
                f"[role='button']:has-text('{description}')",
                f"input[type='submit']",
                f"input[type='button']",
                "button[type='submit']",
            ]

        # Inputs
        if any(w in desc_lower for w in ("input", "field", "email", "password", "search", "name", "phone")):
            for kw in ("email", "password", "search", "name", "phone", "username", "text"):
                if kw in desc_lower:
                    candidates += [
                        f"input[type='{kw}']",
                        f"input[name*='{kw}']",
                        f"input[id*='{kw}']",
                        f"input[placeholder*='{kw}' i]",
                    ]

        # Links
        if any(w in desc_lower for w in ("link", "href", "anchor")):
            candidates.append(f"a:has-text('{description}')")

        # Generic text-based selector
        candidates.append(f"text={description}")

        for sel in candidates:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    return sel
            except Exception:
                continue

        logger.debug("[BROWSER_FIND_ELEMENT_MISS] description=%r", description)
        return None

    # ── Safe interaction ───────────────────────────────────────────────────────

    async def safe_click(self, selector: str) -> bool:
        """
        Click *selector* if it is present and visible.
        Returns True on success.
        """
        try:
            el = await self.page.query_selector(selector)
            if el and await el.is_visible():
                text = await el.inner_text()
                logger.info(
                    "[BROWSER_ACTION_CLICK] selector=%r text=%r",
                    selector,
                    text[:80],
                )
                await el.click(timeout=10_000)
                await asyncio.sleep(0.5)
                return True
        except Exception as exc:
            logger.warning(
                "[BROWSER_CLICK_ERROR] selector=%r error=%r", selector, str(exc)
            )
        return False
