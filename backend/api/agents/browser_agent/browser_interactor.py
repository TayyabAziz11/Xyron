"""
BrowserInteractor — high-level interaction helpers built on top of Playwright.

Wraps every action in try/except and emits structured log tags.
Does NOT handle approval gating — that belongs to BrowserFormAgent /
BrowserPurchaseGuard.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, TimeoutError as PWTimeout

logger = logging.getLogger("api.agents.browser_agent.interactor")


class BrowserInteractor:
    """Stateless helper — every method receives *page* explicitly."""

    # ── Click ──────────────────────────────────────────────────────────────────

    async def click(self, page: Page, selector: str) -> bool:
        """
        Click the first visible element matching *selector*.
        Returns True on success.
        """
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                text = ""
                try:
                    text = (await el.inner_text())[:60]
                except Exception:
                    pass
                logger.info(
                    "[BROWSER_ACTION_CLICK] selector=%r text=%r", selector, text
                )
                await el.click(timeout=10_000)
                await asyncio.sleep(0.4)
                return True
            logger.debug(
                "[BROWSER_CLICK_NOT_FOUND] selector=%r", selector
            )
            return False
        except Exception as exc:
            logger.warning(
                "[BROWSER_CLICK_ERROR] selector=%r error=%r", selector, str(exc)
            )
            return False

    # ── Fill ───────────────────────────────────────────────────────────────────

    async def fill_field(self, page: Page, selector: str, value: str) -> bool:
        """
        Clear and fill *selector* with *value*.
        Returns True on success.
        """
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                logger.info(
                    "[BROWSER_ACTION_FILL] field=%r value=%r",
                    selector,
                    value[:40] + ("…" if len(value) > 40 else ""),
                )
                await el.triple_click(timeout=5_000)
                await el.fill(value, timeout=5_000)
                await asyncio.sleep(0.2)
                return True
            logger.debug(
                "[BROWSER_FILL_NOT_FOUND] selector=%r", selector
            )
            return False
        except Exception as exc:
            logger.warning(
                "[BROWSER_FILL_ERROR] selector=%r error=%r", selector, str(exc)
            )
            return False

    # ── Select ─────────────────────────────────────────────────────────────────

    async def select_option(self, page: Page, selector: str, value: str) -> bool:
        """
        Select an option from a <select> element by value or visible label.
        Returns True on success.
        """
        try:
            el = await page.query_selector(selector)
            if el:
                await el.select_option(value=value, timeout=5_000)
                logger.info(
                    "[BROWSER_ACTION_SELECT] selector=%r value=%r", selector, value
                )
                await asyncio.sleep(0.2)
                return True
            return False
        except Exception:
            try:
                # Fallback: try matching by label text
                el2 = await page.query_selector(selector)
                if el2:
                    await el2.select_option(label=value, timeout=5_000)
                    return True
            except Exception as exc2:
                logger.warning(
                    "[BROWSER_SELECT_ERROR] selector=%r error=%r", selector, str(exc2)
                )
            return False

    # ── Keyboard ───────────────────────────────────────────────────────────────

    async def press_key(self, page: Page, key: str) -> None:
        """Press a keyboard *key* (e.g. 'Enter', 'Tab', 'Escape')."""
        try:
            await page.keyboard.press(key)
            await asyncio.sleep(0.2)
        except Exception as exc:
            logger.debug("[BROWSER_KEY_ERROR] key=%r error=%r", key, str(exc))

    # ── Hover ──────────────────────────────────────────────────────────────────

    async def hover(self, page: Page, selector: str) -> None:
        """Hover over *selector* to reveal dropdown / tooltip content."""
        try:
            el = await page.query_selector(selector)
            if el:
                await el.hover(timeout=5_000)
                await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug(
                "[BROWSER_HOVER_ERROR] selector=%r error=%r", selector, str(exc)
            )

    # ── Scroll to element ──────────────────────────────────────────────────────

    async def scroll_to_element(self, page: Page, selector: str) -> None:
        """Scroll *selector* into the viewport."""
        try:
            await page.evaluate(
                f"""
                (() => {{
                    const el = document.querySelector({repr(selector)});
                    if (el) el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                }})()
                """
            )
            await asyncio.sleep(0.4)
        except Exception as exc:
            logger.debug(
                "[BROWSER_SCROLL_TO_ERROR] selector=%r error=%r", selector, str(exc)
            )

    # ── Screenshot ─────────────────────────────────────────────────────────────

    async def take_screenshot(self, page: Page, path: Optional[str] = None) -> bytes:
        """
        Capture a screenshot of the current page.
        If *path* is given, also saves to disk.
        Returns raw PNG bytes.
        """
        try:
            kwargs: dict = {"full_page": False}
            if path:
                kwargs["path"] = path
            data: bytes = await page.screenshot(**kwargs)
            logger.info(
                "[BROWSER_SCREENSHOT] url=%s path=%s bytes=%d",
                page.url,
                path or "memory-only",
                len(data),
            )
            return data
        except Exception as exc:
            logger.warning("[BROWSER_SCREENSHOT_ERROR] error=%r", str(exc))
            return b""

    # ── Infinite scroll ────────────────────────────────────────────────────────

    async def handle_infinite_scroll(
        self, page: Page, max_scrolls: int = 5
    ) -> None:
        """
        Scroll down incrementally to trigger lazy-loaded content.
        Stops if page height stops growing.
        """
        try:
            prev_height: int = await page.evaluate("document.body.scrollHeight")
            for _ in range(max_scrolls):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)
                new_height: int = await page.evaluate(
                    "document.body.scrollHeight"
                )
                if new_height <= prev_height:
                    break
                prev_height = new_height
        except Exception as exc:
            logger.debug("[BROWSER_INF_SCROLL_ERROR] error=%r", str(exc))

    # ── Navigation wait ────────────────────────────────────────────────────────

    async def wait_for_navigation(self, page: Page) -> None:
        """
        Wait for the page to settle after a click that triggers navigation.
        Uses a generous timeout with graceful fallback.
        """
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20_000)
            await asyncio.sleep(0.5)
        except PWTimeout:
            # Page may be already loaded or is a SPA that never fires load.
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.debug("[BROWSER_NAV_WAIT_ERROR] error=%r", str(exc))
