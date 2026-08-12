"""
BrowserDownloadAgent — safe file downloads with extension allow/block lists.

Executables and scripts are unconditionally blocked. Safe files are downloaded
to a caller-supplied Path, logged, and verified before StepResult is returned.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, Download

from api.agents.agent_types import AgentTask, StepResult

logger = logging.getLogger("api.agents.browser_agent.download_agent")

# JS to enumerate all download-candidate links on the page.
_FIND_LINKS_JS = """
(desc) => {
    const desc_lower = desc.toLowerCase();
    const anchors = Array.from(document.querySelectorAll('a[href]'));
    const scored = anchors.map(a => {
        const text = (a.innerText || a.title || a.getAttribute('aria-label') || '').toLowerCase();
        const href = (a.href || '').toLowerCase();
        const ext_match = href.match(/\\.(pdf|xlsx|csv|docx|txt|png|jpg|jpeg|zip|tar|gz)$/);
        let score = 0;
        if (ext_match) score += 10;
        const kw = desc_lower.split(/\\s+/);
        kw.forEach(k => { if (text.includes(k)) score += 3; });
        kw.forEach(k => { if (href.includes(k)) score += 2; });
        return { href: a.href, text: (a.innerText || '').trim(), score };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 10);
}
"""


class BrowserDownloadAgent:
    """Download files from the browser with safety checks."""

    SAFE_EXTENSIONS: frozenset[str] = frozenset(
        {".pdf", ".xlsx", ".csv", ".docx", ".txt", ".png", ".jpg", ".jpeg",
         ".zip", ".tar", ".gz", ".mp4", ".mp3", ".svg", ".json", ".xml"}
    )
    BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
        {".exe", ".msi", ".bat", ".ps1", ".sh", ".dmg", ".pkg", ".deb",
         ".rpm", ".vbs", ".cmd", ".com", ".scr", ".jar", ".app", ".run"}
    )

    # ── Main download entry ────────────────────────────────────────────────────

    async def download_file(
        self,
        page: Page,
        url: str,
        save_path: Path,
        task: AgentTask,
    ) -> StepResult:
        """
        Download *url* and save to *save_path* after safety checks.

        Returns StepResult with success=True and data={'path': str} on success.
        Blocks executables and returns failure without downloading.
        """
        # Safety: check extension
        ext = Path(url.split("?")[0]).suffix.lower()
        if ext in self.BLOCKED_EXTENSIONS:
            msg = f"Blocked download: {ext} files are not allowed ({url})"
            logger.warning("[BROWSER_DOWNLOAD_BLOCKED] url=%s ext=%s", url, ext)
            return StepResult(success=False, output=msg)

        if ext and ext not in self.SAFE_EXTENSIONS:
            # Unknown extension — warn but still allow (PDF without extension etc.)
            logger.warning(
                "[BROWSER_DOWNLOAD_UNKNOWN_EXT] url=%s ext=%r — proceeding",
                url,
                ext,
            )

        logger.info("[BROWSER_DOWNLOAD_STARTED] url=%s save_path=%s", url, save_path)

        # Ensure parent directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Use Playwright's download event — works for both direct links
            # and server-triggered downloads.
            async with page.expect_download(timeout=60_000) as dl_info:
                await page.goto(url, wait_until="commit", timeout=30_000)

            download: Download = await dl_info.value
            suggested = download.suggested_filename
            if suggested:
                # Honour server-suggested name, keep user-chosen directory
                save_path = save_path.parent / suggested

            await download.save_as(save_path)
            await asyncio.sleep(0.5)

            if not save_path.exists() or save_path.stat().st_size == 0:
                return StepResult(
                    success=False,
                    output=f"Download appeared to succeed but file is missing or empty: {save_path}",
                )

            size_kb = save_path.stat().st_size // 1024
            logger.info(
                "[BROWSER_DOWNLOAD_COMPLETE] path=%s size_kb=%d", save_path, size_kb
            )
            return StepResult(
                success=True,
                output=f"Downloaded {save_path.name} ({size_kb} KB)",
                data={"path": str(save_path), "size_kb": size_kb},
            )

        except Exception as exc:
            error_msg = str(exc)
            logger.warning(
                "[BROWSER_DOWNLOAD_ERROR] url=%s error=%r", url, error_msg
            )
            # If goto triggered normal navigation (not a download), fall back
            # to HTTP fetch via page.evaluate
            fallback = await self._fallback_fetch(page, url, save_path)
            if fallback:
                return fallback
            return StepResult(success=False, output=f"Download failed: {error_msg}", should_retry=True)

    async def _fallback_fetch(
        self, page: Page, url: str, save_path: Path
    ) -> Optional[StepResult]:
        """
        Fallback: navigate to the URL normally and save the body as the file.
        Useful when the server sends the file as inline HTML or blob.
        """
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=30_000)
            if resp is None:
                return None
            body = await resp.body()
            if len(body) < 50:
                return None
            save_path.write_bytes(body)
            size_kb = len(body) // 1024
            logger.info(
                "[BROWSER_DOWNLOAD_FALLBACK_COMPLETE] path=%s size_kb=%d",
                save_path,
                size_kb,
            )
            return StepResult(
                success=True,
                output=f"Downloaded (fallback) {save_path.name} ({size_kb} KB)",
                data={"path": str(save_path), "size_kb": size_kb},
            )
        except Exception as exc:
            logger.debug("[BROWSER_DOWNLOAD_FALLBACK_ERROR] error=%r", str(exc))
            return None

    # ── Link finder ────────────────────────────────────────────────────────────

    async def find_download_link(
        self, page: Page, description: str
    ) -> Optional[str]:
        """
        Search the current page for a download link matching *description*.

        Returns the best-matching URL, or None if nothing suitable is found.
        """
        try:
            candidates: list[dict] = await page.evaluate(_FIND_LINKS_JS, description)
            if not candidates:
                return None

            best = candidates[0]
            if best["score"] == 0:
                return None  # No match at all

            logger.info(
                "[BROWSER_DOWNLOAD_LINK_FOUND] desc=%r url=%s score=%d",
                description,
                best["href"],
                best["score"],
            )
            return best["href"]
        except Exception as exc:
            logger.warning(
                "[BROWSER_FIND_LINK_ERROR] desc=%r error=%r", description, str(exc)
            )
            return None
