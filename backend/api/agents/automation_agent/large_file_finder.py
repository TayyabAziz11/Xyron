from __future__ import annotations

"""
LargeFileFinder — locate large and old-large files on the filesystem.

find_large       — files >= min_size_mb, sorted by size descending.
find_old_large   — files >= min_size_mb not modified in N days.

Both methods run in a thread-pool executor.
mtime is used as a last-touch proxy (atime is typically disabled on NTFS
when mounted via WSL2).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

logger = logging.getLogger("api.agents.automation_agent.large_file_finder")

_SECS_PER_DAY = 86_400


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} TiB"


class LargeFileFinder:
    """Find large / old-large files without modifying anything."""

    async def find_large(
        self,
        directory: Path,
        min_size_mb: int = 100,
        top_n: int = 20,
    ) -> list[dict]:
        """
        Return [{path, size_bytes, size_human, modified}] sorted by size desc.
        Only files >= min_size_mb are included; top_n results returned.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._find_large_sync, directory, min_size_mb, top_n
        )

    def _find_large_sync(
        self,
        directory: Path,
        min_size_mb: int,
        top_n: int,
    ) -> list[dict]:
        min_bytes = min_size_mb * 1024 * 1024
        results: list[dict] = []

        try:
            for entry in directory.rglob("*"):
                try:
                    if not (entry.is_file() and not entry.is_symlink()):
                        continue
                    st = entry.stat()
                    if st.st_size < min_bytes:
                        continue
                    modified = datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc
                    ).isoformat()
                    results.append({
                        "path": str(entry),
                        "size_bytes": st.st_size,
                        "size_human": _naturalsize(st.st_size),
                        "modified": modified,
                    })
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        results.sort(key=lambda x: x["size_bytes"], reverse=True)
        return results[:top_n]

    async def find_old_large(
        self,
        directory: Path,
        min_size_mb: int = 50,
        older_than_days: int = 90,
    ) -> list[dict]:
        """
        Find files >= min_size_mb whose mtime is older than older_than_days.
        Returns [{path, size_bytes, size_human, modified, days_old}] sorted by size.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._find_old_large_sync, directory, min_size_mb, older_than_days
        )

    def _find_old_large_sync(
        self,
        directory: Path,
        min_size_mb: int,
        older_than_days: int,
    ) -> list[dict]:
        min_bytes = min_size_mb * 1024 * 1024
        cutoff = time.time() - older_than_days * _SECS_PER_DAY
        results: list[dict] = []

        try:
            for entry in directory.rglob("*"):
                try:
                    if not (entry.is_file() and not entry.is_symlink()):
                        continue
                    st = entry.stat()
                    if st.st_size < min_bytes:
                        continue
                    if st.st_mtime > cutoff:
                        continue  # recently modified — not "old"
                    modified = datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc
                    ).isoformat()
                    days_old = int((time.time() - st.st_mtime) / _SECS_PER_DAY)
                    results.append({
                        "path": str(entry),
                        "size_bytes": st.st_size,
                        "size_human": _naturalsize(st.st_size),
                        "modified": modified,
                        "days_old": days_old,
                    })
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        results.sort(key=lambda x: x["size_bytes"], reverse=True)
        return results
