from __future__ import annotations

"""
DuplicateFinder — locate duplicate files by MD5 content hash.

Algorithm:
  1. First pass: group files by size (cheap — stat only). Single-member
     size groups are skipped immediately.
  2. Second pass: MD5 hash only files sharing a size.

Files > 1 GB are skipped to cap hashing time.
Streams in 8 KB chunks to keep memory constant regardless of file size.
All I/O runs in a thread-pool executor.
"""

import asyncio
import hashlib
import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

logger = logging.getLogger("api.agents.automation_agent.duplicate_finder")

_MAX_FILE_BYTES = 1024 ** 3  # 1 GiB — skip larger files
_CHUNK_BYTES = 8 * 1024       # 8 KiB


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} GiB"


class DuplicateFinder:
    """Find duplicate files using two-phase size + content hashing."""

    # ── Hash-based (accurate) ─────────────────────────────────────────────────

    async def find_duplicates(self, directory: Path) -> list[list[Path]]:
        """
        Find duplicates by MD5 content hash.
        Returns list of groups; each group contains ≥ 2 paths with identical content.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._find_by_hash_sync, directory)

    def _find_by_hash_sync(self, directory: Path) -> list[list[Path]]:
        # Phase 1: group by size
        size_map: dict[int, list[Path]] = defaultdict(list)
        try:
            for entry in directory.rglob("*"):
                try:
                    if not (entry.is_file() and not entry.is_symlink()):
                        continue
                    st = entry.stat()
                    if st.st_size == 0 or st.st_size > _MAX_FILE_BYTES:
                        continue
                    size_map[st.st_size].append(entry)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

        # Phase 2: hash only files that share a size
        hash_map: dict[str, list[Path]] = defaultdict(list)
        for _size, paths in size_map.items():
            if len(paths) < 2:
                continue  # unique size → cannot be a duplicate
            for p in paths:
                digest = self._md5(p)
                if digest is not None:
                    hash_map[digest].append(p)

        return [group for group in hash_map.values() if len(group) > 1]

    def _md5(self, path: Path) -> Optional[str]:
        """Compute MD5 of file, streaming in 8 KiB chunks."""
        h = hashlib.md5()
        try:
            with path.open("rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except (OSError, PermissionError) as exc:
            logger.debug("[DUPLICATE_FINDER] hash failed path=%s err=%r", path, exc)
            return None

    # ── Name-based (fast, less accurate) ─────────────────────────────────────

    async def find_by_name(self, directory: Path) -> list[list[Path]]:
        """
        Find files with the same filename (case-insensitive).
        Faster than content hashing but may produce false positives.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._find_by_name_sync, directory)

    def _find_by_name_sync(self, directory: Path) -> list[list[Path]]:
        name_map: dict[str, list[Path]] = defaultdict(list)
        try:
            for entry in directory.rglob("*"):
                try:
                    if entry.is_file() and not entry.is_symlink():
                        name_map[entry.name.lower()].append(entry)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass
        return [g for g in name_map.values() if len(g) > 1]

    # ── Reporting ─────────────────────────────────────────────────────────────

    def format_report(self, duplicates: list[list[Path]]) -> str:
        """Human-readable duplicate report with wasted-space summary."""
        if not duplicates:
            return "No duplicate files found."

        total_wasted = 0
        lines = [f"Found {len(duplicates)} duplicate group(s):\n"]

        for i, group in enumerate(duplicates, 1):
            try:
                size = group[0].stat().st_size
            except OSError:
                size = 0
            wasted = size * (len(group) - 1)
            total_wasted += wasted
            lines.append(
                f"  Group {i}  ({_naturalsize(size)} each,  {len(group)} copies,"
                f"  {_naturalsize(wasted)} wasted):"
            )
            for p in group:
                lines.append(f"    {p}")
            lines.append("")

        lines.append(f"Total wasted space: {_naturalsize(total_wasted)}")
        lines.append(
            "To free space, keep one copy from each group and recycle the rest."
        )
        return "\n".join(lines)
