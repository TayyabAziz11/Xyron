"""
fs_search.py — Smart Filesystem Search Engine for Xyron

Ranking priority:
  1. Exact name match
  2. Recent usage (last_opened_ts)
  3. Access frequency (access_count)
  4. Shallower path (shorter = closer to root)
  5. Fuzzy similarity score

Every successful open call triggers record_open() which persists learning
back to the SQLite index so future queries rank this path higher.

Log prefixes: [FS_SEARCH] [FS_SEARCH_HIT] [FS_SEARCH_RANK] [FS_SEARCH_FUZZY]
              [FS_OPEN_SUCCESS] [FS_OPEN_FAIL]
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Junk path components that should be de-ranked
_JUNK_PARTS = frozenset({
    "appdata", "cache", "temp", "temporary internet files",
    "node_modules", "programdata", "$recycle.bin", ".tmp",
    "windows", "system32", "syswow64", "winsxs",
})

# Suffixes that hint at folder intent in voice queries
_FOLDER_SUFFIXES = {
    " folders", " folder", " directories", " directory", " dir",
}
_FILE_SUFFIXES = {
    " files", " file",
}


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", name.lower()).strip()


def _is_junk(p: Path) -> bool:
    return any(part.lower() in _JUNK_PARTS for part in p.parts)


def _wsl_to_win(path_str: str) -> str:
    """Convert /mnt/e/foo -> E:\\foo"""
    m = re.match(r"^/mnt/([a-z])(?:/(.*))?$", path_str)
    if m:
        letter = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\")
        return f"{letter}:\\{rest}" if rest else f"{letter}:\\"
    return path_str.replace("/", "\\")


# ---------------------------------------------------------------------------
# Search result type
# ---------------------------------------------------------------------------

class SearchResult:
    __slots__ = ("path", "score", "source", "win_path")

    def __init__(self, path: Path, score: float, source: str):
        self.path     = path
        self.score    = score
        self.source   = source    # "exact" | "ranked" | "fuzzy"
        self.win_path = _wsl_to_win(str(path))

    def __repr__(self) -> str:
        return f"SearchResult(name={self.path.name!r}, score={self.score:.2f}, source={self.source})"


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search(
    query: str,
    type_filter: Optional[str] = None,   # "folder" | "file" | None
    drive: Optional[str] = None,
    limit: int = 5,
) -> List[SearchResult]:
    """
    Unified search: exact → ranked → fuzzy.
    Returns SearchResult list, best match first.

    Automatically strips "folder"/"file" suffixes from query so voice commands
    like "open python folder" work without specifying type separately.
    """
    query = query.strip()
    if not query:
        return []

    # Strip voice intent suffixes
    q_lower = query.lower()
    for sfx in _FOLDER_SUFFIXES:
        if q_lower.endswith(sfx):
            query = query[:-len(sfx)].strip()
            if type_filter is None:
                type_filter = "folder"
            break
    for sfx in _FILE_SUFFIXES:
        if q_lower.endswith(sfx):
            query = query[:-len(sfx)].strip()
            if type_filter is None:
                type_filter = "file"
            break

    if not query:
        return []

    logger.info("[FS_SEARCH] query=%r type=%s drive=%s", query, type_filter, drive)

    try:
        from api.services.fs_index import fs_index
        if not fs_index.is_ready:
            logger.debug("[FS_SEARCH] index not ready yet — skipping fast path")
            return []

        # Phase 1: ranked search (covers exact + substring + scored)
        ranked = fs_index.search_ranked(query, type_filter=type_filter, drive=drive, limit=limit * 3)

        results: List[SearchResult] = []
        for score, path in ranked:
            if _is_junk(path):
                score *= 0.3
            results.append(SearchResult(path, score, "ranked"))

        # Phase 2: fuzzy fallback if no ranked results
        if not results:
            logger.debug("[FS_SEARCH_FUZZY] no ranked hits — trying fuzzy")
            fuzzy_paths = fs_index.search_fuzzy(query, type_filter=type_filter, drive=drive, limit=limit)
            for p in fuzzy_paths:
                score = 0.5 if _is_junk(p) else 0.7
                results.append(SearchResult(p, score, "fuzzy"))
            if results:
                logger.info("[FS_SEARCH_FUZZY] query=%r found=%d top=%s",
                            query, len(results), results[0].path.name)

        # De-duplicate and sort
        seen: set[str] = set()
        deduped: List[SearchResult] = []
        for r in sorted(results, key=lambda x: -x.score):
            if str(r.path) not in seen:
                seen.add(str(r.path))
                deduped.append(r)

        top = deduped[:limit]
        if top:
            logger.info("[FS_SEARCH_HIT] query=%r top=%s score=%.2f source=%s",
                        query, top[0].path.name, top[0].score, top[0].source)
        else:
            logger.info("[FS_SEARCH] query=%r — no results", query)
        return top

    except Exception as exc:
        logger.error("[FS_SEARCH] error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Open + learn
# ---------------------------------------------------------------------------

def open_path(
    result: SearchResult,
    query: str = "",
) -> Tuple[bool, str, str]:
    """
    Verify accessibility → open via Explorer → record usage.

    Returns (success, win_path, error_type).
    error_type: "" | "not_found" | "access_denied" | "drive_unavailable" | "open_failed"

    Logs [FS_OPEN_SUCCESS] or [FS_OPEN_FAIL].
    """
    path = result.path
    win_path = result.win_path

    # Verify accessibility before speaking
    ok, err_type = _verify(path)
    if not ok:
        logger.warning("[FS_OPEN_FAIL] path=%s error=%s", win_path, err_type)
        return False, win_path, err_type

    # Open via Windows Explorer
    cmd_exe = _find_cmd_exe()
    if not cmd_exe:
        logger.error("[FS_OPEN_FAIL] cmd.exe not found")
        return False, win_path, "open_failed"

    try:
        subprocess.Popen(
            [cmd_exe, "/c", "start", "", win_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.error("[FS_OPEN_FAIL] subprocess error: %s", exc)
        return False, win_path, "open_failed"

    # Record this successful open for learning
    try:
        from api.services.fs_index import fs_index
        fs_index.record_open(path)
    except Exception:
        pass

    logger.info("[FS_OPEN_SUCCESS] path=%s query=%r score=%.2f", win_path, query, result.score)
    return True, win_path, ""


def _verify(path: Path) -> Tuple[bool, str]:
    """
    Return (accessible, error_type).
    error_type: "ok" | "not_found" | "access_denied" | "drive_unavailable"
    """
    try:
        if not path.exists():
            # Check if the drive itself is missing
            parts = path.parts
            if len(parts) >= 3 and parts[1] == "mnt":
                drive_root = Path(f"/{parts[1]}/{parts[2]}")
                if not drive_root.exists():
                    return False, "drive_unavailable"
            return False, "not_found"
        if path.is_dir():
            os.listdir(str(path))
        return True, "ok"
    except PermissionError:
        return False, "access_denied"
    except OSError:
        # Some Windows mounts fail listdir on WSL side but Explorer still works
        return True, "ok"


def _find_cmd_exe() -> Optional[str]:
    for candidate in [
        "/mnt/c/Windows/System32/cmd.exe",
        "/mnt/c/WINDOWS/system32/cmd.exe",
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Voice response builder
# ---------------------------------------------------------------------------

def voice_response(
    success: bool,
    name: str,
    error_type: str = "",
    matches: Optional[List[SearchResult]] = None,
    drive_suffix: str = "",
) -> str:
    """Return a short, natural voice response string."""
    if success:
        return f"Opened {name}{drive_suffix}."
    if error_type == "not_found":
        return f"I couldn't find {name!r} on your system."
    if error_type == "access_denied":
        return f"I found {name}, but Windows denied access."
    if error_type == "drive_unavailable":
        return "That drive is currently unavailable."
    if error_type == "multiple_matches" and matches:
        names = ", ".join(m.path.name for m in matches[:3])
        return f"I found multiple matches: {names}. Which one did you mean?"
    if error_type == "open_failed":
        return "I found the folder but couldn't open it."
    return f"I couldn't find {name!r}."
