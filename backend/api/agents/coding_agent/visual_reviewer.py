from __future__ import annotations

"""
VisualReviewer — Phase 4.6 visual verification role for CodingAgent.

Uses Playwright to:
  1. Navigate to the dev-server URL
  2. Capture a full-page screenshot (saved to project folder)
  3. Inspect the screenshot for blankness using PIL
  4. Check DOM for required sections (nav, hero, content, footer)
  5. Capture browser console errors
  6. Capture failed network requests
  7. Repeat on mobile viewport (375 × 812)

Returns a VisualReport dict with issues list and pass/fail result.

Log tags:
  [VISUAL_VERIFY_START] [SCREENSHOT_CAPTURED] [CONSOLE_ERRORS_CHECKED]
  [NETWORK_ERRORS_CHECKED] [VISUAL_ISSUE_FOUND] [VISUAL_VERIFY_PASS]
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Selectors that should be present in a healthy page
_REQUIRED_SELECTORS = [
    ("nav, header, [class*='nav'], [class*='header']",    "Navigation"),
    ("h1, h2, [class*='hero'] h1, [class*='hero'] h2",   "Hero heading"),
    ("main, [class*='hero'], [class*='banner']",          "Hero section"),
    ("footer, [class*='footer']",                         "Footer"),
]

# DOM patterns that indicate an error page
_ERROR_PATTERNS = [
    "Cannot GET",
    "Application error",
    "Error: Hydration failed",
    "Unhandled Runtime Error",
    "Module not found",
    "SyntaxError",
    "ReferenceError",
    "TypeError:",
]


class VisualReviewer:
    """Playwright-based visual inspector for localhost dev previews."""

    async def review(
        self,
        url: str,
        project_path: Path,
        timeout_ms: int = 12_000,
    ) -> dict[str, Any]:
        """Run full visual review and return a VisualReport dict.

        Parameters
        ----------
        url:          Dev server URL (e.g. ``"http://localhost:5173"``).
        project_path: Project root — screenshots saved here.
        timeout_ms:   Navigation timeout in milliseconds.
        """
        logger.info("[VISUAL_VERIFY_START] url=%s", url)

        report: dict[str, Any] = {
            "url":            url,
            "passed":         False,
            "issues":         [],
            "screenshots":    [],
            "console_errors": [],
            "network_errors": [],
            "dom_checks":     {},
        }

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                try:
                    # ── Desktop review ─────────────────────────────────────────
                    ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
                    await self._review_viewport(
                        ctx, url, project_path, "desktop", timeout_ms, report
                    )
                    await ctx.close()

                    # ── Mobile review (non-blocking — skip on error) ────────────
                    try:
                        mobile_ctx = await browser.new_context(
                            viewport={"width": 375, "height": 812},
                        )
                        await self._review_viewport(
                            mobile_ctx, url, project_path, "mobile", timeout_ms, report
                        )
                        await mobile_ctx.close()
                    except Exception as mob_exc:
                        logger.debug("[VISUAL_REVIEWER] mobile review skipped: %s", mob_exc)

                finally:
                    await browser.close()

        except ImportError:
            report["issues"].append("Playwright not available — skipping visual review")
            logger.warning("[VISUAL_REVIEWER] playwright not available")
        except Exception as exc:
            report["issues"].append(f"Visual review error: {exc}")
            logger.error("[VISUAL_REVIEWER] review error: %s", exc)

        # Pass if no critical issues were found
        critical = [i for i in report["issues"] if "critical" in i.lower() or "blank" in i.lower() or "error overlay" in i.lower()]
        report["passed"] = not critical and bool(report.get("screenshots"))

        if report["passed"]:
            logger.info("[VISUAL_VERIFY_PASS] url=%s", url)
        else:
            logger.warning("[VISUAL_VERIFY_FAIL] url=%s issues=%s", url, report["issues"])

        return report

    # ── Viewport review ────────────────────────────────────────────────────────

    async def _review_viewport(
        self,
        ctx,
        url: str,
        project_path: Path,
        label: str,
        timeout_ms: int,
        report: dict,
    ) -> None:
        page = await ctx.new_page()
        console_errors: list[str] = []
        network_errors: list[str] = []

        # Collect console errors
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Collect network failures
        page.on(
            "requestfailed",
            lambda req: network_errors.append(f"{req.method} {req.url} — {req.failure}"),
        )

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await asyncio.sleep(1.5)  # wait for JS hydration

            # ── Screenshot ─────────────────────────────────────────────────────
            screenshot_path = project_path / f"xyron_review_{label}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            report["screenshots"].append(str(screenshot_path))
            logger.info("[SCREENSHOT_CAPTURED] %s", screenshot_path)

            # ── Blankness check via PIL ────────────────────────────────────────
            blank = await asyncio.to_thread(self._is_blank_screenshot, screenshot_path)
            if blank:
                issue = f"[CRITICAL] Blank screen detected on {label} viewport"
                report["issues"].append(issue)
                logger.warning("[VISUAL_ISSUE_FOUND] %s", issue)

            # ── Error overlay check ────────────────────────────────────────────
            body_text = await page.inner_text("body")
            for pattern in _ERROR_PATTERNS:
                if pattern.lower() in body_text.lower():
                    issue = f"[CRITICAL] Error overlay on {label}: '{pattern}'"
                    report["issues"].append(issue)
                    logger.warning("[VISUAL_ISSUE_FOUND] %s", issue)
                    break

            # ── DOM section checks (desktop only) ─────────────────────────────
            if label == "desktop":
                for selector, name in _REQUIRED_SELECTORS:
                    found = await page.query_selector(selector)
                    report["dom_checks"][name] = bool(found)
                    if not found:
                        report["issues"].append(f"Missing: {name} ({selector})")
                        logger.info("[VISUAL_ISSUE_FOUND] missing selector: %s", name)

            # ── Console errors ─────────────────────────────────────────────────
            if console_errors:
                report["console_errors"].extend(console_errors[:10])
                logger.info("[CONSOLE_ERRORS_CHECKED] count=%d", len(console_errors))
                # Only flag critical JS errors (not React warning noise)
                critical_console = [
                    e for e in console_errors
                    if any(kw in e for kw in ("TypeError", "ReferenceError", "SyntaxError", "Cannot read"))
                ]
                if critical_console:
                    report["issues"].append(
                        f"Console errors on {label}: {critical_console[0][:100]}"
                    )
            else:
                logger.info("[CONSOLE_ERRORS_CHECKED] no errors on %s", label)

            # ── Network errors ─────────────────────────────────────────────────
            if network_errors:
                report["network_errors"].extend(network_errors[:5])
                # Only flag non-favicon failures
                real_failures = [e for e in network_errors if "favicon" not in e.lower()]
                if real_failures:
                    report["issues"].append(
                        f"Network failures on {label}: {real_failures[0][:120]}"
                    )
                logger.info("[NETWORK_ERRORS_CHECKED] failures=%d", len(real_failures))
            else:
                logger.info("[NETWORK_ERRORS_CHECKED] no failures on %s", label)

        finally:
            await page.close()

    # ── Screenshot analysis ────────────────────────────────────────────────────

    @staticmethod
    def _is_blank_screenshot(path: Path) -> bool:
        """Return True if the screenshot is >90% white/near-white pixels."""
        if not path.exists():
            return True
        try:
            from PIL import Image
            img = Image.open(path).convert("RGB")
            # Sample a grid of pixels rather than every pixel (faster)
            w, h = img.size
            step_x = max(1, w // 50)
            step_y = max(1, h // 50)
            total = 0
            white = 0
            for y in range(0, h, step_y):
                for x in range(0, w, step_x):
                    r, g, b = img.getpixel((x, y))
                    total += 1
                    if r > 245 and g > 245 and b > 245:
                        white += 1
            ratio = white / total if total else 1.0
            return ratio > 0.90
        except Exception as exc:
            logger.debug("[VISUAL_REVIEWER] PIL check failed: %s", exc)
            return False

    # ── Issue formatting ───────────────────────────────────────────────────────

    @staticmethod
    def format_critique(report: dict) -> str:
        """Return a human-readable critique from the report."""
        if not report.get("issues"):
            return "Visual review passed — no issues detected."

        lines = [f"Visual review found {len(report['issues'])} issue(s):"]
        for i, issue in enumerate(report["issues"], 1):
            lines.append(f"  {i}. {issue}")
        return "\n".join(lines)
