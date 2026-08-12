from __future__ import annotations

"""
JunkDetector — pattern-based junk file classifier.

Identifies files that are safe to recycle based on extension/name patterns
and known safe-to-clean directory lists. Never deletes anything — only
classifies.
"""

import fnmatch
import logging
from pathlib import Path

logger = logging.getLogger("api.agents.automation_agent.junk_detector")


class JunkDetector:
    """Pattern-based junk file classifier."""

    JUNK_PATTERNS: list[str] = [
        "*.tmp",
        "*.temp",
        "~*",
        "Thumbs.db",
        "desktop.ini",
        "*.log",
        ".DS_Store",
        "*.bak",
        "*.old",
        "*.dmp",
        "*.crdownload",
        "*.part",
        "thumbcache_*.db",
        "*.stackdump",
        "*.pyc",
        "__pycache__",
        "*.orig",
    ]

    # Directories considered always safe to clean (glob-style)
    SAFE_TO_DELETE_DIRS: list[str] = [
        "C:/Windows/Temp",
        "C:/Users/*/AppData/Local/Temp",
        "/mnt/c/Windows/Temp",
        "/mnt/c/Users/*/AppData/Local/Temp",
    ]

    # Category → matching patterns
    CATEGORIES: dict[str, list[str]] = {
        "temp":        ["*.tmp", "*.temp", "*.crdownload", "*.part"],
        "logs":        ["*.log", "*.dmp", "*.stackdump"],
        "thumbnails":  ["Thumbs.db", "thumbcache_*.db", ".DS_Store", "desktop.ini"],
        "old_backups": ["*.bak", "*.old", "*.orig"],
        "tilde_junk":  ["~*"],
        "pycache":     ["*.pyc", "__pycache__"],
    }

    # ── Public API ─────────────────────────────────────────────────────────────

    def is_junk(self, path: Path) -> bool:
        """Return True if the file matches any junk pattern."""
        name = path.name
        return any(fnmatch.fnmatch(name, pat) for pat in self.JUNK_PATTERNS)

    def get_junk_files(self, directory: Path, recursive: bool = True) -> list[Path]:
        """Return all junk files under *directory*. Skips permission errors."""
        junk: list[Path] = []
        try:
            walker = directory.rglob("*") if recursive else directory.glob("*")
            for entry in walker:
                try:
                    if entry.is_file() and not entry.is_symlink() and self.is_junk(entry):
                        junk.append(entry)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError) as exc:
            logger.debug("[JUNK_DETECTOR] scan error dir=%s err=%r", directory, exc)
        return junk

    def categorize(self, files: list[Path]) -> dict[str, list[Path]]:
        """
        Categorize files into: temp, logs, thumbnails, old_backups,
        tilde_junk, pycache, other.
        """
        result: dict[str, list[Path]] = {cat: [] for cat in self.CATEGORIES}
        result["other"] = []

        for f in files:
            placed = False
            for cat, patterns in self.CATEGORIES.items():
                if any(fnmatch.fnmatch(f.name, pat) for pat in patterns):
                    result[cat].append(f)
                    placed = True
                    break
            if not placed:
                result["other"].append(f)

        for cat, matched in result.items():
            if matched:
                logger.info("[CLEANER_CATEGORY_FOUND] category=%s count=%d", cat, len(matched))

        return result

    def is_safe_dir(self, path: Path) -> bool:
        """Return True if the path is in a known always-safe directory."""
        path_str = str(path)
        return any(fnmatch.fnmatch(path_str, pat) for pat in self.SAFE_TO_DELETE_DIRS)
