"""
Safety Layer — blocks destructive or restricted operations before they reach the OS.

Philosophy:
  • Allow:  reading, opening, listing, info queries, creating NEW files/folders in user space
  • Block:  modifying system files, accessing protected OS paths, running arbitrary shell commands
  • Audit:  log all medium/high-risk operations

This module is intentionally simple and conservative.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# ── Blocked base paths ─────────────────────────────────────────────────────────

_BLOCKED_WIN_PREFIXES = [
    r"C:\Windows",
    r"C:\Windows\System32",
    r"C:\Windows\SysWOW64",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData\Microsoft",
    r"C:\Recovery",
    r"C:\$Recycle.Bin",
    r"C:\Boot",
]

_BLOCKED_LINUX_PREFIXES = [
    "/proc",
    "/sys",
    "/boot",
    "/dev",
    "/run",
    "/snap",
]

# These specific paths are always blocked regardless of platform
_BLOCKED_ABSOLUTE = {
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\Windows\System32\config",
    r"C:\pagefile.sys",
    r"C:\hiberfil.sys",
}

# ── Blocked file extensions for write ─────────────────────────────────────────

_BLOCKED_WRITE_EXTS = {
    ".sys", ".dll", ".exe", ".bat", ".cmd", ".msi", ".reg",
    ".vbs", ".ps1", ".sh",  # script types that could be run
}

_ALLOWED_WRITE_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".html", ".htm", ".xml", ".log", ".conf", ".env",
    ".py",   # allow writing Python scripts (user owns them)
    "",       # extensionless files
}


def _normalise(path: str) -> str:
    """Lowercase and normalise separators for comparison."""
    return path.strip().replace("/", "\\").lower()


def is_safe_path(path: str) -> bool:
    """
    Return True if it is safe to open/read/list this path.
    Returns False for system directories, critical OS paths, or empty paths.
    """
    if not path or not path.strip():
        return False

    norm = _normalise(path)

    # Absolute blocks
    for blocked in _BLOCKED_ABSOLUTE:
        if norm == _normalise(blocked):
            return False

    # Windows prefix blocks
    for prefix in _BLOCKED_WIN_PREFIXES:
        if norm.startswith(_normalise(prefix)):
            return False

    # Linux prefix blocks
    for prefix in _BLOCKED_LINUX_PREFIXES:
        if path.startswith(prefix):
            return False

    return True


def is_safe_write(path: str) -> bool:
    """
    Return True if writing to this path is allowed.
    Stricter than is_safe_path — also blocks executable/script extensions.
    """
    if not is_safe_path(path):
        return False

    ext = Path(path).suffix.lower()
    if ext in _BLOCKED_WRITE_EXTS:
        return False

    return True


def assess_risk(tool: str, params: dict) -> str:
    """Return effective risk level for a tool call."""
    _TOOL_RISK: dict[str, str] = {
        "open_directory":   "low",
        "list_directory":   "low",
        "open_file":        "low",
        "open_application": "low",
        "search_files":     "low",
        "system_info":      "low",
        "search_web":       "low",
        "search_youtube":   "low",
        "open_url":         "low",
        "get_summary":      "low",
        "list_approvals":   "low",
        "general_query":    "low",
        "create_folder":    "medium",
        "write_file":       "medium",
        "compose_email":    "medium",
        "create_post":      "medium",
    }
    return _TOOL_RISK.get(tool, "medium")
