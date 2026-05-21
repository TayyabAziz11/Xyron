"""
WSL2 ↔ Windows path translation.

All functions are synchronous — wslpath is a kernel syscall (<1 ms) and
does not benefit from asyncio wrapping.  Use these directly in tool
executors before building PowerShell scripts.

Primary:  /usr/bin/wslpath (official WSL2 binary, handles all edge cases)
Fallback: manual regex (used only when wslpath is unavailable or times out)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_WSLPATH = "/usr/bin/wslpath"
_WSLPATH_AVAILABLE = os.path.isfile(_WSLPATH)

_POWERSHELL_EXE = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

# ── Internal helpers ──────────────────────────────────────────────────────────

def _run_wslpath(flag: str, path: str, timeout: float = 0.5) -> str | None:
    """Run wslpath with the given flag; return stripped output or None on failure."""
    if not _WSLPATH_AVAILABLE:
        return None
    try:
        r = subprocess.run(
            [_WSLPATH, flag, path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _expand_win_env_vars(path: str) -> str:
    """
    Expand Windows %ENVVAR% patterns (e.g. %APPDATA%) using PowerShell.
    Falls back to the original string on any error.
    """
    if "%" not in path:
        return path
    try:
        r = subprocess.run(
            [_POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command",
             f"[System.Environment]::ExpandEnvironmentVariables('{path}')"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    logger.warning("Failed to expand Windows env vars in path: %s", path)
    return path


def _is_unc(path: str) -> bool:
    return path.startswith("\\\\") or path.startswith("//")


def _is_win_path(path: str) -> bool:
    """True if path looks like a Windows absolute path (drive letter or UNC)."""
    return bool(re.match(r"^[A-Za-z]:[/\\]", path)) or _is_unc(path)


def _is_wsl_path(path: str) -> bool:
    """True if path starts with / (Unix/WSL style)."""
    return path.startswith("/")


# ── Manual fallbacks ──────────────────────────────────────────────────────────

def _manual_win_to_wsl(path: str) -> str:
    """C:\\foo\\bar → /mnt/c/foo/bar  (regex fallback, no wslpath)."""
    normalised = path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):(/.*)?$", normalised)
    if m:
        drive = m.group(1).lower()
        rest = (m.group(2) or "").rstrip("/") or ""
        return f"/mnt/{drive}{rest}"
    return path


def _manual_wsl_to_win(path: str) -> str:
    """/mnt/c/foo/bar → C:\\foo\\bar  (regex fallback, no wslpath)."""
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        drive = m.group(1).upper()
        rest = (m.group(2) or "").replace("/", "\\")
        return f"{drive}:{rest}"
    return path


# ── Public API ────────────────────────────────────────────────────────────────

def win_to_wsl(win_path: str) -> str:
    """
    Convert a Windows path to a WSL path.

    - Expands %APPDATA% and other Windows env vars before converting.
    - UNC paths (\\\\server\\share) are returned unchanged with a warning.
    - Handles spaces and unicode transparently (wslpath does this natively).
    - Falls back to manual regex if wslpath times out or is unavailable.
    """
    win_path = win_path.strip()

    if _is_unc(win_path):
        logger.warning("UNC path cannot be reliably converted to WSL: %s", win_path)
        return win_path

    # Expand Windows environment variables
    win_path = _expand_win_env_vars(win_path)

    result = _run_wslpath("-u", win_path)
    if result is not None:
        return result

    logger.debug("wslpath unavailable or timed out, using manual fallback for: %s", win_path)
    return _manual_win_to_wsl(win_path)


def wsl_to_win(wsl_path: str) -> str:
    """
    Convert a WSL/Linux path to a Windows path.

    - Paths not starting with /mnt/ (e.g. /home/user/file) cannot be
      translated to Windows and are returned as-is.
    - Falls back to manual regex if wslpath times out or is unavailable.
    """
    wsl_path = wsl_path.strip()

    if not wsl_path.startswith("/mnt/"):
        return wsl_path

    result = _run_wslpath("-w", wsl_path)
    if result is not None:
        return result

    logger.debug("wslpath unavailable or timed out, using manual fallback for: %s", wsl_path)
    return _manual_wsl_to_win(wsl_path)


def normalize_path(path: str) -> str:
    """
    Return a WSL-style path suitable for Python os operations.

    - Windows path  → convert with win_to_wsl
    - WSL path      → return as-is
    - Unrecognised  → return as-is
    """
    path = path.strip()
    if _is_win_path(path):
        return win_to_wsl(path)
    return path


def safe_win_path(path: str) -> str:
    """
    Escape single quotes for PowerShell injection safety.

    In PowerShell single-quoted strings, a literal ' must be doubled to ''.
    Always wrap the result in single quotes on the PS side:
        f"Get-Item '{safe_win_path(user_path)}'"
    """
    return path.replace("'", "''")
