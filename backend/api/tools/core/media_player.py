"""
Local media file search and playback for WSL2/Windows.

Searches video files via the 3-tier file index, ranks matches,
then opens the best match using Windows Start-Process.

This module NEVER touches Xyron's audio pipeline, voiceRuntime,
HTMLAudioElement, or any TTS path.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .file_search import search_files
from .path_translator import safe_win_path, wsl_to_win
from .ps_runner import ToolResult, run_ps

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".m4v"}

# Tags that signal the end of a clean movie title in a filename
_TITLE_STOP_RE = re.compile(
    r'[\.\s_]('
    r'\d{4}|'                        # year: 2022
    r'\d{3,4}p|'                     # resolution: 720p, 1080p
    r'bluray|blu.ray|bdrip|dvdrip|webrip|web.dl|hdtv|'
    r'x264|x265|h264|h265|hevc|avc|xvid|'
    r'aac|ac3|dts|mp3|flac|'
    r'hindi|english|japanese|urdu|dual|multi|'
    r'10bit|hdr|sdr|'
    r'yts|yify|vegamovies|rarbg|'
    r'esubs|subs|sub'
    r')',
    re.IGNORECASE,
)

def _clean_title(stem: str) -> str:
    """Extract a human-readable title from a messy release filename stem.

    'Suzume.2022.720p.10Bit.BluRay.Hindi-Japanes.x265.ESubs-Vegamovies.hot'
    → 'Suzume'
    """
    m = _TITLE_STOP_RE.search(stem)
    if m:
        raw = stem[:m.start()]
    else:
        raw = stem
    # Replace dots, underscores, hyphens with spaces and strip
    clean = re.sub(r'[._-]', ' ', raw).strip()
    # Collapse multiple spaces
    clean = re.sub(r'\s{2,}', ' ', clean)
    return clean or stem


_ACTION_WORDS = {
    "play", "open", "find", "and", "the", "that", "a",
    "movie", "film", "video", "file", "media", "local",
    "named", "called", "please", "can", "you",
}

_TITLE_PATTERNS = [
    # "movie/film named/called Suzume"
    re.compile(r'(?:movie|film|video)\s+(?:named|called)\s+(.+?)$', re.I),
    # "play/open Suzume movie"
    re.compile(r'(?:play|open)\s+(.+?)\s+(?:movie|film|video|file)\s*$', re.I),
    # "play the movie Suzume"
    re.compile(r'play\s+(?:the\s+|that\s+)?(?:movie|film)\s+(.+?)$', re.I),
    # "find and play Suzume"
    re.compile(r'find\s+and\s+play\s+(.+?)$', re.I),
    # "play/open Suzume" (bare — after all more-specific patterns tried)
    re.compile(r'(?:play|open)\s+(?:the\s+|that\s+)?(.+?)$', re.I),
]


def _clean_query(query: str) -> str:
    """Strip voice command words to extract the actual media title."""
    q = query.strip()
    for pat in _TITLE_PATTERNS:
        m = pat.search(q)
        if m:
            raw = m.group(1).strip()
            # Drop trailing noise words
            raw = re.sub(r'\s+(movie|film|video|file|media)\s*$', '', raw, flags=re.I).strip()
            if raw:
                return raw
    # Fallback: strip known action words
    words = [w for w in q.split() if w.lower() not in _ACTION_WORDS]
    return " ".join(words) or q


def _video_score(result: dict[str, Any], query_lower: str) -> int:
    """Return relevance score (>=0) for a file result; -1 means skip (not a video)."""
    name: str = result.get("name", "")
    name_lower = name.lower()
    pp = PurePosixPath(name_lower)

    if pp.suffix not in VIDEO_EXTENSIONS:
        return -1  # not a video — exclude

    stem = pp.stem
    score = 10  # base: it's a video

    if stem == query_lower:
        score += 100  # exact title match (e.g. "suzume" == "suzume")
    elif query_lower in stem:
        score += 50   # stem contains query (e.g. "suzume" in "suzume_1080p")
    elif query_lower in name_lower:
        score += 20   # full name contains query

    return score


async def play_media_file(query: str, media_type: str = "video") -> ToolResult:
    """
    Find the best matching local video file and open it via Windows Start-Process.

    Lookup order:
      Tier 0 — media_index (in-memory, <1 ms)
      Tier 1/2/3 — search_files (Everything SDK → WinSearch → PowerShell find)

    Returns:
      success=True                      → file launched, spoken = "Playing <title>."
      error_code="not_found"            → no video match found
      error_code="multiple_matches"     → data.matches = [{index, name, path}]
      error_code="access_denied"        → file found, Start-Process denied
    """
    # Clean up query so search works on the title, not the full voice command
    raw_query = query
    query = _clean_query(query)
    query_lower = query.lower()

    logger.info("[MEDIA_PLAY_REQUEST] raw=%r cleaned=%r media_type=%r",
                raw_query, query, media_type)

    # ── Tier-0: Fast media index ──────────────────────────────────────────────
    try:
        from api.services.media_index import media_index as _midx
        _idx_win = _midx.lookup(query_lower)
        if _idx_win:
            _idx_wsl = ""
            if len(_idx_win) >= 3 and _idx_win[1] == ":":
                _idx_wsl = "/mnt/" + _idx_win[0].lower() + "/" + _idx_win[3:].replace("\\", "/")
            if _idx_wsl and not os.path.isfile(_idx_wsl):
                logger.info("[MEDIA_INDEX_HIT] stale entry skipped path=%r", _idx_win)
            else:
                safe_p = safe_win_path(_idx_win)
                _script = (
                    f"$p = Start-Process -FilePath '{safe_p}' -PassThru "
                    f"-ErrorAction SilentlyContinue; if ($p) {{ $p.Id }} else {{ 'FAILED' }}"
                )
                _res = await run_ps(_script, timeout=12.0)
                _pid = (_res.message or "").strip()
                if _res.success and _pid not in ("", "FAILED"):
                    raw_stem = Path(_idx_win.replace("\\", "/")).stem
                    title = _clean_title(raw_stem)
                    spoken = f"Playing {title}."
                    logger.info("[MEDIA_LAUNCH_SUCCESS] source=index path=%r pid=%s", _idx_win, _pid)
                    return ToolResult.ok(
                        f"Opened {title} from index",
                        spoken=spoken,
                        data={"path": _idx_win, "wsl_path": _idx_wsl,
                              "filename": Path(_idx_win).name, "title": title,
                              "source": "media_index"},
                    )
                logger.info("[MEDIA_LAUNCH_FAIL] source=index path=%r output=%r", _idx_win, _pid)
    except Exception as _ie:
        logger.debug("[MEDIA_INDEX] lookup error: %s", _ie)

    # ── Tier-1/2/3 file search ────────────────────────────────────────────────
    search_result = await search_files(query, max_results=40)
    logger.info("[MEDIA_SEARCH] raw_results=%d source=%s",
                search_result.data.get("count", 0),
                search_result.data.get("source", "none"))

    raw_results: list[dict[str, Any]] = (
        search_result.data.get("results", []) if search_result.success else []
    )

    # ── Filter to video files and rank ────────────────────────────────────────
    scored: list[tuple[int, dict[str, Any]]] = []
    for r in raw_results:
        s = _video_score(r, query_lower)
        if s >= 0:
            scored.append((s, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    logger.info("[MEDIA_MATCH] video_matches=%d", len(scored))

    if not scored:
        msg = f"I couldn't find any video named '{query}'."
        logger.info("[MEDIA_OPEN_FAIL] not_found query=%r", query)
        return ToolResult.failure(
            msg, spoken=msg, error_code="not_found",
            data={"query": query, "matches": []},
        )

    top_score, best = scored[0]

    # ── Decide: play immediately vs ask for disambiguation ────────────────────
    if len(scored) > 1:
        second_score = scored[1][0]
        # Play immediately only if there is a single exact-title match
        exact_count = sum(1 for sc, _ in scored if sc >= 110)
        if exact_count != 1:
            # Multiple equally good matches — ask the user
            matches = [
                {
                    "index":    i + 1,
                    "name":     r["name"],
                    "path":     r.get("path", ""),
                    "wsl_path": r.get("wsl_path", ""),
                }
                for i, (_, r) in enumerate(scored[:5])
            ]
            names = ", ".join(m["name"] for m in matches[:3])
            msg = f"I found multiple videos: {names}. Which one should I play?"
            logger.info("[MEDIA_MATCH] multiple_matches count=%d", len(matches))
            return ToolResult.failure(
                msg, spoken=msg, error_code="multiple_matches",
                data={"query": query, "matches": matches},
            )
        # There is exactly one exact match — use it
        best = next(r for sc, r in scored if sc >= 110)

    # ── Verify file exists (WSL path) ─────────────────────────────────────────
    wsl_path: str = best.get("wsl_path") or ""
    win_path: str = best.get("path") or ""

    if wsl_path.startswith("/mnt/") and not os.path.isfile(wsl_path):
        msg = f"I found '{best['name']}' but the file no longer exists."
        logger.info("[MEDIA_OPEN_FAIL] file_missing wsl_path=%r", wsl_path)
        return ToolResult.failure(
            msg, spoken=msg, error_code="not_found",
            data={"query": query, "path": win_path},
        )

    # Build Windows path for Start-Process
    if not win_path or len(win_path) < 3 or win_path[1] != ":":
        if wsl_path.startswith("/mnt/"):
            win_path = wsl_to_win(wsl_path)

    safe_path = safe_win_path(win_path)
    # Use -PassThru so PowerShell returns the process object's PID on success
    # and nothing (empty) on failure — lets us verify the player actually launched.
    script = (
        f"$p = Start-Process -FilePath '{safe_path}' -PassThru "
        f"-ErrorAction SilentlyContinue; if ($p) {{ $p.Id }} else {{ 'FAILED' }}"
    )

    logger.info("[MEDIA_MATCH] launching win_path=%r", win_path)
    result = await run_ps(script, timeout=12.0)
    pid_str = (result.message or "").strip()

    if not result.success or pid_str in ("", "FAILED"):
        msg = f"I found '{best['name']}', but Windows couldn't open it."
        logger.info("[MEDIA_LAUNCH_FAIL] source=search path=%r output=%r",
                    win_path, pid_str)
        return ToolResult.failure(
            msg, spoken=msg, error_code="access_denied",
            data={"query": query, "path": win_path, "error": pid_str},
        )

    raw_stem = PurePosixPath(best["name"]).stem
    title = _clean_title(raw_stem)
    spoken = f"Playing {title}."
    logger.info("[MEDIA_LAUNCH_SUCCESS] source=search path=%r pid=%s spoken=%r",
                win_path, pid_str, spoken)

    return ToolResult.ok(
        f"Opened {best['name']} at {win_path}",
        spoken=spoken,
        data={
            "path":     win_path,
            "wsl_path": wsl_path,
            "filename": best["name"],
            "title":    title,
            "pid":      pid_str,
        },
    )
