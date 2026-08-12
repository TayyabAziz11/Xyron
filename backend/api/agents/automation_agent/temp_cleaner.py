from __future__ import annotations

"""
TempCleaner — scan and recycle Windows temp directories.

SAFETY GUARANTEE: Uses send2trash to move files to the Recycle Bin.
Never calls os.remove / shutil.rmtree / os.unlink on user files.
Always requests approval before moving any files.

Log tags: [CLEANER_JUNK_FOUND] [CLEANER_APPROVAL_REQUIRED]
          [CLEANER_DELETE_TO_RECYCLE] [CLEANER_SPACE_FREED]
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

logger = logging.getLogger("api.agents.automation_agent.temp_cleaner")


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} GiB"


class TempCleaner:
    """Scan and recycle Windows temp directories via send2trash."""

    TEMP_GLOB_PATTERNS: list[str] = [
        "/mnt/c/Windows/Temp",
        "/mnt/c/Users/*/AppData/Local/Temp",
    ]

    # ── Scan ──────────────────────────────────────────────────────────────────

    async def scan_temp(self) -> dict:
        """
        Scan all temp directories.
        Returns {files: list[str], total_bytes: int, count: int, size_human: str}.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._scan_sync)

    def _scan_sync(self) -> dict:
        all_files: list[Path] = []
        total_bytes = 0

        for pattern in self.TEMP_GLOB_PATTERNS:
            for dir_str in glob.glob(pattern):
                dir_path = Path(dir_str)
                if not dir_path.exists():
                    continue
                try:
                    for entry in dir_path.rglob("*"):
                        try:
                            if entry.is_file() and not entry.is_symlink():
                                size = entry.stat().st_size
                                all_files.append(entry)
                                total_bytes += size
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    continue

        logger.info(
            "[CLEANER_JUNK_FOUND] size_bytes=%d count=%d",
            total_bytes,
            len(all_files),
        )

        return {
            "files": [str(f) for f in all_files],
            "total_bytes": total_bytes,
            "count": len(all_files),
            "size_human": _naturalsize(total_bytes),
        }

    # ── Clean ─────────────────────────────────────────────────────────────────

    async def clean_temp(
        self,
        file_list: list[Path],
        task: AgentTask,
    ) -> StepResult:
        """
        Move files to Recycle Bin using send2trash.
        NEVER permanently deletes. Caller must have already obtained approval.
        """
        if not _HAS_SEND2TRASH:
            return StepResult(
                success=False,
                output=(
                    "send2trash is not installed. "
                    "Install it with: pip install send2trash"
                ),
            )

        freed_bytes = 0
        recycled = 0
        failed: list[str] = []

        for f in file_list:
            try:
                path = f if isinstance(f, Path) else Path(f)
                if not path.exists():
                    continue
                size = path.stat().st_size
                _s2t(str(path))
                freed_bytes += size
                recycled += 1
                logger.info("[CLEANER_DELETE_TO_RECYCLE] path=%s", path)
            except Exception as exc:
                failed.append(str(f))
                logger.warning(
                    "[CLEANER_DELETE_TO_RECYCLE] FAILED path=%s err=%r", f, exc
                )

        logger.info("[CLEANER_SPACE_FREED] bytes=%d", freed_bytes)

        msg = f"Recycled {recycled:,} temp files, freed {_naturalsize(freed_bytes)}."
        if failed:
            msg += f" {len(failed):,} file(s) could not be moved (locked or already gone)."

        return StepResult(
            success=True,
            output=msg,
            data={
                "freed_bytes": freed_bytes,
                "recycled": recycled,
                "failed": failed,
            },
        )

    # ── Approval ──────────────────────────────────────────────────────────────

    async def request_approval(self, scan_result: dict, task: AgentTask) -> bool:
        """
        Send approval request via task.ws_send_fn.

        Returns False immediately — the runtime awaits user response
        asynchronously via the WAITING_APPROVAL mechanism.
        """
        size_human = scan_result.get("size_human", "unknown")
        count = scan_result.get("count", 0)

        logger.info(
            "[CLEANER_APPROVAL_REQUIRED] action=clean_temp size=%s count=%d",
            size_human,
            count,
        )

        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn({
                    "type": "approval_required",
                    "action": "clean_temp",
                    "summary": (
                        f"I found {size_human} of temp files ({count:,} files). "
                        "Move them to the Recycle Bin?"
                    ),
                    "details": scan_result,
                })
            except Exception as exc:
                logger.warning("[TEMP_CLEANER] ws_send_fn error: %r", exc)

        # Return False — approval is async; the caller should not block here.
        return False
