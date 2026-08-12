"""
workspace_context.py — Phase 1.5: detects the current project/workspace.

Best-effort: resolves a real folder path only for title-based IDEs (VS Code,
Visual Studio) by parsing the window title and matching it against a known
project folder in fs_index (preferring folders that look like a project —
contain .git/.vscode — and the most recently modified match on ambiguity).

Creative tools (Photoshop, Illustrator, Premiere, Blender, Figma Desktop)
don't expose an open-folder path in their window title, so for those we
only report the app identity — still useful as an extension-affinity signal
for the "active application context" tier, just not a resolved root.

Logs: [WORKSPACE_DETECTED] [WORKSPACE_ROOT_RESOLVED] [WORKSPACE_ROOT_AMBIGUOUS]
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)

# proc_name (as normalized by window_context — lowercased, ".exe" stripped) -> app label
_WORKSPACE_APPS: dict[str, str] = {
    "code": "vscode",
    "devenv": "visual_studio",
    "photoshop": "photoshop",
    "illustrator": "illustrator",
    "premiere pro": "premiere",
    "adobe premiere pro": "premiere",
    "blender": "blender",
    "figma": "figma",
}

# Extensions each app typically works with — used for tier-6 affinity boosting.
APP_EXTENSIONS: dict[str, set[str]] = {
    "vscode": {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".html", ".css", ".yaml", ".yml"},
    "visual_studio": {".cs", ".cpp", ".h", ".sln", ".csproj"},
    "photoshop": {".psd", ".psb"},
    "illustrator": {".ai", ".eps"},
    "premiere": {".prproj"},
    "blender": {".blend"},
    "figma": {".fig"},
}

# Title-parsing apps only — these show "<file> — <folder>" style titles.
_TITLE_PARSE_APPS = {"vscode", "visual_studio"}

_TITLE_SPLIT_RE = re.compile(r"\s+[—–-]\s+")


class WorkspaceInfo(TypedDict):
    app: str
    root: Optional[Path]
    raw_title: str


def _extract_candidate_name(title: str, app: str) -> Optional[str]:
    """
    VS Code: "file.py — myproject — Visual Studio Code" or "myproject — Visual Studio Code"
    Visual Studio: "Solution1 - Microsoft Visual Studio" (file — project — Visual Studio)
    Strip the trailing app-name segment, keep the last remaining segment.
    """
    if not title:
        return None
    segments = [s.strip() for s in _TITLE_SPLIT_RE.split(title) if s.strip()]
    # Drop trailing "Visual Studio Code" / "Microsoft Visual Studio" segment if present.
    segments = [s for s in segments if "visual studio" not in s.lower()]
    if not segments:
        return None
    candidate = segments[-1].lstrip("● ").strip()
    return candidate or None


def get_active_workspace(window: Optional[dict] = None) -> Optional[WorkspaceInfo]:
    """
    Returns {"app": str, "root": Path|None, "raw_title": str} for the
    foreground workspace app, or None if the foreground app isn't a
    recognized workspace tool. *window* lets callers reuse an
    already-fetched window_context result instead of querying twice.
    """
    if window is None:
        try:
            from .window_context import window_context
            window = window_context.get_active_window()
        except Exception:
            window = None

    if not window:
        return None

    proc = (window.get("proc_name") or "").lower()
    app = _WORKSPACE_APPS.get(proc)
    if not app:
        return None

    title = window.get("title") or ""
    logger.debug("[WORKSPACE_DETECTED] app=%s title=%r", app, title)

    root: Optional[Path] = None
    if app in _TITLE_PARSE_APPS:
        candidate_name = _extract_candidate_name(title, app)
        if candidate_name:
            root = _resolve_folder_name(candidate_name)

    return {"app": app, "root": root, "raw_title": title}


def _resolve_folder_name(name: str) -> Optional[Path]:
    """Match a bare folder name (from a window title) to a real path via fs_index."""
    try:
        from .fs_index import _get_thread_conn, fs_index
        conn = _get_thread_conn(fs_index._db_path)
        rows = conn.execute(
            "SELECT path, modified_time FROM entries WHERE type = 'folder' AND lowercase_name = ? "
            "ORDER BY modified_time DESC LIMIT 10",
            (name.lower(),),
        ).fetchall()
    except Exception:
        return None

    if not rows:
        return None
    if len(rows) == 1:
        logger.info("[WORKSPACE_ROOT_RESOLVED] name=%r path=%s", name, rows[0][0])
        return Path(rows[0][0])

    # Ambiguous — prefer a folder that looks like an actual project root.
    for path_str, _ in rows:
        p = Path(path_str)
        if (p / ".git").is_dir() or (p / ".vscode").is_dir():
            logger.info("[WORKSPACE_ROOT_RESOLVED] name=%r path=%s (project marker)", name, p)
            return p

    logger.debug("[WORKSPACE_ROOT_AMBIGUOUS] name=%r candidates=%d — using most recent", name, len(rows))
    return Path(rows[0][0])
