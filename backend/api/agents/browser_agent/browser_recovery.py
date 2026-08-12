"""
BrowserRecovery — error recovery strategies for the browser agent.

When a step fails, BrowserRecovery is consulted before the agent gives up.
All methods return bool: True = recovered (caller may retry), False = give up.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import Page, TimeoutError as PWTimeout

from api.agents.agent_types import AgentStep

logger = logging.getLogger("api.agents.browser_agent.recovery")

# Selectors for overlays that block interaction (banners, modals, paywalls)
_OVERLAY_SELECTORS: list[str] = [
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    ".accept-cookies",
    "[data-testid*='cookie'] button",
    "[class*='modal'] [class*='close']",
    "[class*='popup'] [class*='close']",
    "[aria-label='Close']",
    "[aria-label='Dismiss']",
    "button[class*='close']",
    ".modal-close",
    ".popup-close",
    "[data-dismiss='modal']",
    # Paywall / newsletter subscribe overlays
    "[class*='paywall'] [class*='close']",
    "[class*='subscribe'] [class*='close']",
    "[class*='newsletter'] [class*='close']",
]


class BrowserRecovery:
    """Stateless error-recovery helpers."""

    # ── Main entry point ───────────────────────────────────────────────────────

    async def recover_from_error(
        self, page: Page, error: str, step: AgentStep
    ) -> bool:
        """
        Attempt a sequence of recovery strategies after a step failure.

        Order:
          1. Clear blocking overlays
          2. Refresh and re-wait
          3. Navigate back and re-wait
          4. Give up (return False)

        Returns True if the page is now in a usable state.
        """
        logger.info(
            "[BROWSER_RECOVERY_ATTEMPT] step=%d error=%r",
            step.index,
            error[:120],
        )

        # Strategy 1: overlays blocking the action?
        dismissed = await self.clear_overlays(page)
        if dismissed:
            logger.info(
                "[BROWSER_RECOVERY_OVERLAY_CLEARED] step=%d", step.index
            )
            return True

        # Strategy 2: hard refresh
        try:
            await page.reload(wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(1.0)
            body_text: str = await page.evaluate(
                "document.body ? document.body.innerText : ''"
            )
            if len(body_text.strip()) > 50:
                logger.info(
                    "[BROWSER_RECOVERY_REFRESHED] step=%d url=%s",
                    step.index,
                    page.url,
                )
                return True
        except Exception as exc:
            logger.debug("[BROWSER_RECOVERY_REFRESH_FAIL] error=%r", str(exc))

        # Strategy 3: navigate back one step in history
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(0.8)
            logger.info(
                "[BROWSER_RECOVERY_WENT_BACK] step=%d url=%s",
                step.index,
                page.url,
            )
            return True
        except Exception as exc:
            logger.debug("[BROWSER_RECOVERY_BACK_FAIL] error=%r", str(exc))

        logger.warning(
            "[BROWSER_RECOVERY_FAILED] step=%d — all strategies exhausted", step.index
        )
        return False

    # ── Clear overlays ─────────────────────────────────────────────────────────

    async def clear_overlays(self, page: Page) -> bool:
        """
        Dismiss any modal / cookie / popup overlay that blocks interaction.
        Returns True if at least one overlay was dismissed.
        """
        dismissed_any = False
        for sel in _OVERLAY_SELECTORS:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=4_000)
                    await asyncio.sleep(0.3)
                    logger.info(
                        "[BROWSER_OVERLAY_DISMISSED] selector=%r", sel
                    )
                    dismissed_any = True
            except Exception:
                continue

        # Also try pressing Escape to close any focused dialog
        if not dismissed_any:
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception:
                pass

        return dismissed_any

    # ── Retry with fallback selectors ──────────────────────────────────────────

    async def retry_with_different_selector(
        self,
        page: Page,
        action: str,
        fallback_selectors: list[str],
    ) -> bool:
        """
        Try *action* ("click" or "fill") on each selector in *fallback_selectors*
        until one succeeds.

        Returns True if any fallback worked.
        """
        for sel in fallback_selectors:
            try:
                el = await page.query_selector(sel)
                if not el or not await el.is_visible():
                    continue

                if action == "click":
                    await el.click(timeout=8_000)
                    await asyncio.sleep(0.4)
                    logger.info(
                        "[BROWSER_RECOVERY_FALLBACK_CLICK] selector=%r", sel
                    )
                    return True

                if action == "fill":
                    await el.fill("", timeout=5_000)  # clear first
                    logger.info(
                        "[BROWSER_RECOVERY_FALLBACK_FILL] selector=%r", sel
                    )
                    return True

            except Exception as exc:
                logger.debug(
                    "[BROWSER_RECOVERY_FALLBACK_MISS] selector=%r error=%r",
                    sel,
                    str(exc),
                )
                continue

        logger.warning(
            "[BROWSER_RECOVERY_NO_FALLBACK] action=%r all %d selectors failed",
            action,
            len(fallback_selectors),
        )
        return False
