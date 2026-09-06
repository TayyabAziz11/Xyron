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

# Cookie / consent banner detection — split into plain-CSS selectors
# (native document.querySelector, safe to batch into one page.evaluate())
# and text-phrase matches (the old list used Playwright-only :has-text(),
# which isn't valid in-page JS — re-implemented below as a plain text
# comparison over every <button>). Order matches the original list, so
# behavior/priority is unchanged — only the round-trip count changes.
_COOKIE_CSS_SELECTORS: list[str] = [
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
]
_COOKIE_TEXT_PHRASES: list[str] = [
    "Accept all", "Accept cookies", "I agree", "Got it", "OK", "Allow all",
]
# Kept for logging/back-compat with anything that still refers to the flat list.
_COOKIE_SELECTORS: list[str] = _COOKIE_CSS_SELECTORS + [
    f"button:has-text('{p}')" for p in _COOKIE_TEXT_PHRASES
]

# Runs entirely in-page — zero additional CDP round trips regardless of how
# many selectors/phrases are checked. Clicks in-JS (bypasses Playwright's
# actionability checks) since this is a best-effort dismiss, not a
# user-critical interaction; a real click failure just leaves the banner
# up, which the caller already tolerates today.
_COOKIE_BATCH_JS = """
([cssSelectors, textPhrases]) => {
    function isVisible(el) {
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden'
            && r.width > 0 && r.height > 0;
    }
    for (const sel of cssSelectors) {
        try {
            const el = document.querySelector(sel);
            if (el && isVisible(el)) { el.click(); return sel; }
        } catch (e) { /* skip */ }
    }
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const phrase of textPhrases) {
        const lower = phrase.toLowerCase();
        for (const btn of buttons) {
            const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
            if ((text === lower || text.includes(lower)) && isVisible(btn)) {
                btn.click();
                return "button:has-text('" + phrase + "')";
            }
        }
    }
    return null;
}
"""

# Per-domain cache: once we've confirmed a domain has no banner (or found
# the selector that works), never pay for a rescan on that domain again
# within the TTL. Google Flights doesn't start showing a consent banner
# mid-session, so an hour is conservative, not aggressive.
_COOKIE_CACHE_TTL_S = 3600.0
_cookie_banner_cache: dict[str, dict] = {}


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc or url

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
        """Wait for network idle + small JS-settle delay.

        networkidle is best-effort: heavy SPAs (Google Flights, etc.)
        NEVER go fully idle because of analytics/websocket polling, so
        the wait always ran out at the old 15s cap before proceeding —
        pure dead time. 4s keeps genuine idle detection for fast pages
        while capping the penalty for never-idle pages.
        """
        try:
            await self.page.wait_for_load_state("networkidle", timeout=4_000)
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

        Phase 5.2: was a 17-selector sequential scan (17 CDP round trips,
        ~3.2s, every single search — measured live, see Phase 5.1 report).
        Now: per-domain cache first (0 round trips once confirmed), then a
        single page.evaluate() covering all 17 checks in-page (1 round
        trip total instead of 17) when a scan is actually needed.
        """
        domain = _domain_of(self.page.url)
        now = time.time()
        cached = _cookie_banner_cache.get(domain)

        if cached is not None and (now - cached["ts"]) < _COOKIE_CACHE_TTL_S:
            if cached["no_banner"]:
                logger.info(
                    "[MICRO_PROFILE] op=cookie_banner_cache_hit domain=%s outcome=no_banner_confirmed "
                    "age_s=%.0f scan_skipped=true",
                    domain, now - cached["ts"],
                )
                return False
            if cached["selector"]:
                _t0 = time.time()
                try:
                    btn = await self.page.query_selector(cached["selector"])
                    if btn and await btn.is_visible():
                        await btn.click(timeout=5_000)
                        await asyncio.sleep(0.3)
                        _ms = (time.time() - _t0) * 1000
                        logger.info(
                            "[MICRO_PROFILE] op=cookie_banner_cache_hit domain=%s "
                            "outcome=cached_selector_worked selector=%r ms=%.1f",
                            domain, cached["selector"], _ms,
                        )
                        logger.info("[BROWSER_COOKIE_DISMISSED] selector=%r", cached["selector"])
                        _cookie_banner_cache[domain] = {
                            "no_banner": False, "selector": cached["selector"], "ts": now,
                        }
                        return True
                except Exception:
                    pass
                logger.info(
                    "[MICRO_PROFILE] op=cookie_banner_cache_miss domain=%s "
                    "reason=cached_selector_no_longer_matches",
                    domain,
                )
                # fall through to a full rescan below

        _scan_t0 = time.time()
        try:
            matched = await self.page.evaluate(
                _COOKIE_BATCH_JS, [_COOKIE_CSS_SELECTORS, _COOKIE_TEXT_PHRASES],
            )
        except Exception as exc:
            logger.info("[MICRO_PROFILE] op=cookie_banner_batched_scan domain=%s outcome=error error=%r",
                        domain, str(exc))
            matched = None
        _scan_ms = (time.time() - _scan_t0) * 1000

        if matched:
            await asyncio.sleep(0.3)
            logger.info(
                "[MICRO_PROFILE] op=cookie_banner_batched_scan domain=%s outcome=matched "
                "selector=%r scan_ms=%.1f",
                domain, matched, _scan_ms,
            )
            logger.info("[BROWSER_COOKIE_DISMISSED] selector=%r", matched)
            _cookie_banner_cache[domain] = {"no_banner": False, "selector": matched, "ts": now}
            return True

        logger.info(
            "[MICRO_PROFILE] op=cookie_banner_batched_scan domain=%s outcome=no_banner_found scan_ms=%.1f",
            domain, _scan_ms,
        )
        _cookie_banner_cache[domain] = {"no_banner": True, "selector": None, "ts": now}
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
