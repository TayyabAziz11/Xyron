from __future__ import annotations

"""
DiskAnalyzer — scans disk usage across common Windows user directories.

Operates via /mnt paths (WSL2 mount of NTFS). All I/O runs in a thread
pool executor so the asyncio loop is never blocked.

Scan targets:
  - Windows Temp + per-user AppData/Local/Temp
  - per-user Downloads
  - per-user Desktop
  - Chrome, Edge, Firefox browser caches
"""

import asyncio
import glob
import logging
import os
from pathlib import Path

try:
    import humanize as _humanize
    _HAS_HUMANIZE = True
except ImportError:
    _HAS_HUMANIZE = False

logger = logging.getLogger("api.agents.automation_agent.disk_analyzer")


def _naturalsize(n: int) -> str:
    if _HAS_HUMANIZE:
        return _humanize.naturalsize(n, binary=True)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} TiB"


class DiskAnalyzer:
    """Non-destructive disk usage scanner."""

    # ── Path discovery ─────────────────────────────────────────────────────────

    def _user_homes(self) -> list[Path]:
        """Enumerate Windows user home directories under /mnt/c/Users."""
        base = Path("/mnt/c/Users")
        if not base.exists():
            return []
        skip = {"Public", "Default", "All Users", "Default User", "defaultuser0"}
        return [p for p in base.iterdir() if p.is_dir() and p.name not in skip]

    def _temp_paths(self) -> list[Path]:
        candidates: list[Path] = []
        # TEMP env var (WSL may export Windows path)
        for var in ("TEMP", "TMP"):
            val = os.environ.get(var)
            if val:
                p = Path(val)
                if p.exists():
                    candidates.append(p)
        # Windows system temp
        win_temp = Path("/mnt/c/Windows/Temp")
        if win_temp.exists():
            candidates.append(win_temp)
        # Per-user temps
        for home in self._user_homes():
            ut = home / "AppData" / "Local" / "Temp"
            if ut.exists():
                candidates.append(ut)
        return candidates

    def _download_paths(self) -> list[Path]:
        return [h / "Downloads" for h in self._user_homes() if (h / "Downloads").exists()]

    def _desktop_paths(self) -> list[Path]:
        return [h / "Desktop" for h in self._user_homes() if (h / "Desktop").exists()]

    def _browser_cache_paths(self) -> dict[str, list[Path]]:
        result: dict[str, list[Path]] = {"chrome": [], "edge": [], "firefox": []}
        for home in self._user_homes():
            chrome = home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
            if chrome.exists():
                result["chrome"].append(chrome)

            edge = home / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"
            if edge.exists():
                result["edge"].append(edge)

            ff_base = home / "AppData" / "Local" / "Mozilla" / "Firefox" / "Profiles"
            if ff_base.exists():
                try:
                    for profile in ff_base.iterdir():
                        cache = profile / "cache2"
                        if cache.exists():
                            result["firefox"].append(cache)
                except (PermissionError, OSError):
                    pass
        return result

    # ── Size helpers ───────────────────────────────────────────────────────────

    def _get_dir_size(self, path: Path) -> tuple[int, int]:
        """Return (total_bytes, file_count). Skips permission errors silently."""
        total_bytes = 0
        file_count = 0
        try:
            for entry in path.rglob("*"):
                try:
                    if entry.is_file() and not entry.is_symlink():
                        total_bytes += entry.stat().st_size
                        file_count += 1
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass
        return total_bytes, file_count

    # ── Public API ─────────────────────────────────────────────────────────────

    async def analyze(self, paths: list[Path] | None = None) -> dict:
        """
        Scan disk usage. Default paths: temp, downloads, desktop, browser caches.

        Returns:
        {
          "total_size_bytes": int,
          "categories": {
            "temp_files":    {"size": int, "count": int, "paths": list[str]},
            "browser_cache": {"size": int, "count": int, "paths": list[str]},
            "downloads":     {"size": int, "count": int, "paths": list[str]},
            "desktop":       {"size": int, "count": int, "paths": list[str]},
          },
          "estimated_recoverable_bytes": int,
          "report": str,
        }
        """
        logger.info("[CLEANER_SCAN_START] target=disk_analysis")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._analyze_sync, paths)

    def _analyze_sync(self, _paths: list[Path] | None) -> dict:
        cats: dict[str, dict] = {
            "temp_files":    {"size": 0, "count": 0, "paths": []},
            "browser_cache": {"size": 0, "count": 0, "paths": []},
            "downloads":     {"size": 0, "count": 0, "paths": []},
            "desktop":       {"size": 0, "count": 0, "paths": []},
        }

        # Temp
        for p in self._temp_paths():
            sz, ct = self._get_dir_size(p)
            cats["temp_files"]["size"] += sz
            cats["temp_files"]["count"] += ct
            cats["temp_files"]["paths"].append(str(p))

        # Browser cache
        for _browser, paths in self._browser_cache_paths().items():
            for cp in paths:
                sz, ct = self._get_dir_size(cp)
                cats["browser_cache"]["size"] += sz
                cats["browser_cache"]["count"] += ct
                cats["browser_cache"]["paths"].append(str(cp))

        # Downloads
        for p in self._download_paths():
            sz, ct = self._get_dir_size(p)
            cats["downloads"]["size"] += sz
            cats["downloads"]["count"] += ct
            cats["downloads"]["paths"].append(str(p))

        # Desktop
        for p in self._desktop_paths():
            sz, ct = self._get_dir_size(p)
            cats["desktop"]["size"] += sz
            cats["desktop"]["count"] += ct
            cats["desktop"]["paths"].append(str(p))

        total = sum(v["size"] for v in cats.values())
        recoverable = cats["temp_files"]["size"] + cats["browser_cache"]["size"]

        lines = [
            "=== Disk Usage Report ===",
            f"Total scanned:        {_naturalsize(total)}",
            f"Estimated recoverable: {_naturalsize(recoverable)}",
            "",
            "Categories:",
        ]
        for cat, data in cats.items():
            label = cat.replace("_", " ").title()
            lines.append(
                f"  {label:<20} {_naturalsize(data['size']):>10}  ({data['count']:,} files)"
            )
        if recoverable > 0:
            lines.append(
                f"\nI can free approximately {_naturalsize(recoverable)} by cleaning "
                "temp files and browser caches."
            )

        return {
            "total_size_bytes": total,
            "categories": cats,
            "estimated_recoverable_bytes": recoverable,
            "report": "\n".join(lines),
        }
