from __future__ import annotations

"""
BrowserCacheCleaner — scan and recycle browser cache directories.

Supports Chrome, Edge, and Firefox on Windows (via /mnt paths in WSL2).
Uses send2trash for safe deletion — never permanently removes files.
Always requests approval before recycling.

Log tags: [CLEANER_APPROVAL_REQUIRED] [CLEANER_DELETE_TO_RECYCLE] [CLEANER_SPACE_FREED]
"""

import asyncio
import glob
import logging
from pathlib import Path

try:
    from send2trash import send2trash as _s2t
    _HAS_SEND2TRASH = True
except ImportError:
    _HAS_SEND2TRASH = False
    _s2t = None  # type: ignore[assignment]

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

from api.agents.agent_types import AgentStatus, AgentTask, StepResult

logger = logging.getLogger("api.agents.automation_agent.browser_cache_cleaner")


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} GiB"


class BrowserCacheCleaner:
    """Scan and recycle browser cache directories."""

    # Glob patterns per browser — all relative to /mnt/c
    BROWSER_CACHE_PATTERNS: dict[str, list[str]] = {
        "chrome": [
            "/mnt/c/Users/*/AppData/Local/Google/Chrome/User Data/Default/Cache",
            "/mnt/c/Users/*/AppData/Local/Google/Chrome/User Data/Default/Cache2",
            "/mnt/c/Users/*/AppData/Local/Google/Chrome/User Data/*/Cache",
        ],
        "edge": [
            "/mnt/c/Users/*/AppData/Local/Microsoft/Edge/User Data/Default/Cache",
            "/mnt/c/Users/*/AppData/Local/Microsoft/Edge/User Data/*/Cache",
        ],
        "firefox": [
            "/mnt/c/Users/*/AppData/Local/Mozilla/Firefox/Profiles/*/cache2",
        ],
    }

    # ── Scan ──────────────────────────────────────────────────────────────────

    async def scan(self) -> dict[str, dict]:
        """
        Returns {browser: {size_bytes, size_human, paths: list[str]}}.
        Only includes browsers with cache directories found.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._scan_sync)

    def _scan_sync(self) -> dict[str, dict]:
        result: dict[str, dict] = {}

        for browser, patterns in self.BROWSER_CACHE_PATTERNS.items():
            total_bytes = 0
            found: list[str] = []

            for pattern in patterns:
                for dir_str in glob.glob(pattern):
                    p = Path(dir_str)
                    if not p.exists():
                        continue
                    sz = self._dir_size(p)
                    total_bytes += sz
                    found.append(dir_str)

            if found:
                result[browser] = {
                    "size_bytes": total_bytes,
                    "size_human": _naturalsize(total_bytes),
                    "paths": found,
                }

        return result

    def _dir_size(self, path: Path) -> int:
        total = 0
        try:
            for entry in path.rglob("*"):
                try:
                    if entry.is_file() and not entry.is_symlink():
                        total += entry.stat().st_size
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass
        return total

    # ── Clean ─────────────────────────────────────────────────────────────────

    async def clean(self, browser: str, task: AgentTask) -> StepResult:
        """
        Recycle cache for the specified browser.
        Requests approval before acting. Returns WAITING_APPROVAL result.
        """
        scan = await self.scan()

        if browser not in scan:
            return StepResult(
                success=False,
                output=f"No {browser.title()} cache found on this system.",
            )

        browser_data = scan[browser]
        size_human = browser_data.get("size_human", "unknown")
        paths = browser_data.get("paths", [])

        logger.info(
            "[CLEANER_APPROVAL_REQUIRED] action=clean_%s_cache size=%s paths=%d",
            browser,
            size_human,
            len(paths),
        )

        if task.ws_send_fn:
            try:
                await task.ws_send_fn({
                    "type": "approval_required",
                    "action": f"clean_{browser}_cache",
                    "summary": (
                        f"I found {size_human} of {browser.title()} cache data "
                        f"across {len(paths)} profile(s). "
                        "Move it to the Recycle Bin?"
                    ),
                    "details": browser_data,
                })
            except Exception as exc:
                logger.warning("[BROWSER_CACHE_CLEANER] ws_send_fn error: %r", exc)

        task.status = AgentStatus.WAITING_APPROVAL
        return StepResult(
            success=True,
            output=(
                f"Waiting for approval to recycle {browser.title()} cache ({size_human})."
            ),
            needs_approval=True,
            approval_prompt=f"Recycle {browser.title()} cache ({size_human})?",
            data={"browser": browser, "size_human": size_human, "paths": paths},
        )

    async def execute_clean(self, browser: str) -> StepResult:
        """
        Actually recycle the cache directories (call after approval received).
        """
        if not _HAS_SEND2TRASH:
            return StepResult(
                success=False,
                output="send2trash not available. Install with: pip install send2trash",
            )

        scan = await self.scan()
        if browser not in scan:
            return StepResult(success=False, output=f"No {browser.title()} cache found.")

        paths = scan[browser].get("paths", [])
        freed = 0
        recycled = 0

        for dir_str in paths:
            p = Path(dir_str)
            if not p.exists():
                continue
            size_before = self._dir_size(p)
            try:
                _s2t(dir_str)
                freed += size_before
                recycled += 1
                logger.info("[CLEANER_DELETE_TO_RECYCLE] path=%s", dir_str)
            except Exception as exc:
                logger.warning(
                    "[BROWSER_CACHE_CLEANER] recycle failed path=%s err=%r",
                    dir_str,
                    exc,
                )

        logger.info("[CLEANER_SPACE_FREED] bytes=%d", freed)

        return StepResult(
            success=True,
            output=(
                f"Recycled {recycled} {browser.title()} cache director(ies), "
                f"freed {_naturalsize(freed)}."
            ),
            data={"freed_bytes": freed, "browser": browser, "recycled_dirs": recycled},
        )
