"""
Register core system tools (drives, file search, media, app finder) in the tool registry.

Async core functions are bridged to the sync registry.execute() interface via asyncio.run().
This is safe because registry.execute() is always called from a ThreadPoolExecutor thread
(via loop.run_in_executor in every router endpoint), so no event loop is running in that thread.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .registry import registry
from .core.ps_runner import ToolResult


def _run(coro: Any) -> ToolResult:
    """Run a coroutine from a sync thread-pool context."""
    return asyncio.run(coro)


# ── Drives ────────────────────────────────────────────────────────────────────

def _detect_drives(params: dict, ctx: dict) -> ToolResult:
    from .core.drives import get_drives
    drives = _run(get_drives())
    data = [
        {
            "letter":         d.letter,
            "label":          d.label,
            "free_gb":        d.free_gb,
            "total_gb":       d.total_gb,
            "drive_type":     d.drive_type,
            "wsl_accessible": d.wsl_accessible,
        }
        for d in drives
    ]
    letters = ", ".join(d.letter for d in drives)
    return ToolResult.ok(
        f"Found {len(drives)} drives: {letters}",
        spoken=f"Found {len(drives)} drives",
        data={"drives": data, "count": len(drives)},
    )


def _drive_exists(params: dict, ctx: dict) -> ToolResult:
    from .core.drives import drive_exists
    letter = str(params.get("letter", "C"))
    exists, info = _run(drive_exists(letter))
    if exists and info:
        return ToolResult.ok(
            f"{info.letter} exists ({info.drive_type}, {info.free_gb} GB free)",
            spoken=f"Drive {info.letter} is available with {info.free_gb} GB free",
            data={"exists": True, "letter": info.letter, "free_gb": info.free_gb, "total_gb": info.total_gb},
        )
    return ToolResult.failure(
        f"Drive {letter} not found",
        error_code="DRIVE_NOT_FOUND",
        data={"exists": False, "letter": letter},
    )


# ── File search ───────────────────────────────────────────────────────────────

def _search_files(params: dict, ctx: dict) -> ToolResult:
    from .core.file_search import search_files
    query       = str(params.get("query", ""))
    max_results = int(params.get("max_results", 20))
    return _run(search_files(query, max_results))


def _find_file(params: dict, ctx: dict) -> ToolResult:
    from .core.file_search import find_file
    name = str(params.get("name", ""))
    return _run(find_file(name))


# ── Media: volume ─────────────────────────────────────────────────────────────

def _get_volume(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import get_volume
    return _run(get_volume())


def _set_volume(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import set_volume
    level = int(params.get("level", 50))
    return _run(set_volume(level))


def _volume_up(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import volume_up
    step = int(params.get("step", 10))
    return _run(volume_up(step))


def _volume_down(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import volume_down
    step = int(params.get("step", 10))
    return _run(volume_down(step))


# ── Media: brightness ─────────────────────────────────────────────────────────

def _get_brightness(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import get_brightness
    return _run(get_brightness())


def _set_brightness(params: dict, ctx: dict) -> ToolResult:
    from .core.media_control import set_brightness
    level = int(params.get("level", 50))
    return _run(set_brightness(level))


# ── App finder ────────────────────────────────────────────────────────────────

def _find_app(params: dict, ctx: dict) -> ToolResult:
    from .core.app_finder import find_app
    name = str(params.get("name", ""))
    return _run(find_app(name))


def _launch_app(params: dict, ctx: dict) -> ToolResult:
    from .core.app_finder import launch_app
    name = str(params.get("name", ""))
    return _run(launch_app(name))


# ── Registrations ─────────────────────────────────────────────────────────────

registry.register(
    "detect_drives",
    {
        "type": "function",
        "function": {
            "name": "detect_drives",
            "description": "List all Windows drives with free/total space and WSL accessibility.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    _detect_drives,
    risk="low",
    category="system",
)

registry.register(
    "drive_exists",
    {
        "type": "function",
        "function": {
            "name": "drive_exists",
            "description": "Check whether a drive letter exists and is accessible from WSL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "letter": {"type": "string", "description": "Drive letter, e.g. 'C' or 'E:'"},
                },
                "required": ["letter"],
            },
        },
    },
    _drive_exists,
    risk="low",
    category="system",
)

registry.register(
    "search_files",
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by name across all Windows drives (3-tier: Everything → WinIndex → PowerShell).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "Filename or partial name to search for"},
                    "max_results": {"type": "integer", "description": "Maximum results to return (default 20)"},
                },
                "required": ["query"],
            },
        },
    },
    _search_files,
    risk="low",
    category="system",
)

registry.register(
    "find_file",
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "Find the first file matching a name (substring). Returns path, wsl_path, and source tier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename or partial name to find"},
                },
                "required": ["name"],
            },
        },
    },
    _find_file,
    risk="low",
    category="system",
)

registry.register(
    "get_volume",
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current system volume level (0-100) and mute status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    _get_volume,
    risk="low",
    category="system",
)

registry.register(
    "set_volume",
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the system volume to a specific level (0-100).",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Volume level 0 to 100"},
                },
                "required": ["level"],
            },
        },
    },
    _set_volume,
    risk="low",
    category="system",
)

registry.register(
    "volume_up",
    {
        "type": "function",
        "function": {
            "name": "volume_up",
            "description": "Increase system volume by a step amount (default 10 percent).",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer", "description": "Step size in percent (default 10)"},
                },
                "required": [],
            },
        },
    },
    _volume_up,
    risk="low",
    category="system",
)

registry.register(
    "volume_down",
    {
        "type": "function",
        "function": {
            "name": "volume_down",
            "description": "Decrease system volume by a step amount (default 10 percent).",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer", "description": "Step size in percent (default 10)"},
                },
                "required": [],
            },
        },
    },
    _volume_down,
    risk="low",
    category="system",
)

registry.register(
    "get_brightness",
    {
        "type": "function",
        "function": {
            "name": "get_brightness",
            "description": "Get current display brightness (0-100). Laptop panels only; returns error for external monitors.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    _get_brightness,
    risk="low",
    category="system",
)

registry.register(
    "set_brightness",
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set display brightness (0-100). Laptop panels only; returns clear error for external monitors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Brightness level 0 to 100"},
                },
                "required": ["level"],
            },
        },
    },
    _set_brightness,
    risk="low",
    category="system",
)

registry.register(
    "find_app",
    {
        "type": "function",
        "function": {
            "name": "find_app",
            "description": "Find an installed Windows application by name using exact/fuzzy matching across Start Menu, Registry, PATH, and Store.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "App name or partial name to search for"},
                },
                "required": ["name"],
            },
        },
    },
    _find_app,
    risk="low",
    category="system",
)

registry.register(
    "launch_app",
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "Find and launch a Windows application by name. Handles Store URIs, .lnk shortcuts, and .exe paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "App name to find and launch"},
                },
                "required": ["name"],
            },
        },
    },
    _launch_app,
    risk="medium",
    category="system",
)
