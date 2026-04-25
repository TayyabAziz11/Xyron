"""
Execution validator — lightweight post-execution state checks.

Validates that tools actually had their intended effect, not just that the
Python code ran without exception. Only runs for tools where silent failure
is a real risk. All other tools pass through untouched.
"""
from __future__ import annotations

import re
import subprocess
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_VALIDATORS: dict[str, Callable] = {}


def _validator(*tool_names: str):
    def decorator(fn: Callable) -> Callable:
        for name in tool_names:
            _VALIDATORS[name] = fn
        return fn
    return decorator


# ── Per-tool validators ────────────────────────────────────────────────────────

def _run_ps1(script: str, ps1_path: str, ps1_win: str, timeout: int = 8) -> str:
    """Write script to a temp PS1 file and run it. Returns stdout stripped."""
    from api.tools.system_tools import _find_powershell
    ps = _find_powershell()
    if not ps:
        return ""
    Path(ps1_path).write_text(script)
    r = subprocess.run(
        [ps, "-NonInteractive", "-NoProfile", "-File", ps1_win],
        capture_output=True, text=True, timeout=timeout, errors="ignore",
    )
    return (r.stdout or "").strip()


def _read_windows_volume() -> int | None:
    """Read current Windows master volume % via the existing GET_VOLUME PS1."""
    try:
        from api.tools.system_tools import _GET_VOLUME_PS1
        out = _run_ps1(_GET_VOLUME_PS1,
                       "/mnt/c/Windows/Temp/_xyron_vol_check.ps1",
                       "C:\\Windows\\Temp\\_xyron_vol_check.ps1")
        if "VOL:" in out:
            line = next(l for l in out.splitlines() if "VOL:" in l)
            parts = dict(p.split(":", 1) for p in line.split("|"))
            return int(parts["VOL"])
    except Exception as exc:
        logger.debug("volume read-back failed: %s", exc)
    return None


def _read_windows_brightness() -> int | None:
    """Read current laptop brightness % via WMI."""
    _PS = (
        "try {"
        " $b=(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness"
        " -ErrorAction Stop).CurrentBrightness;"
        " Write-Output \"BRI:$b\""
        "} catch { Write-Output \"BRI:ERR\" }"
    )
    try:
        from api.tools.system_tools import _find_cmdexe
        cmd = _find_cmdexe()
        if not cmd:
            return None
        r = subprocess.run(
            [cmd, "/c", f'powershell -NoProfile -NonInteractive -Command "{_PS}"'],
            capture_output=True, text=True, timeout=6, errors="ignore",
        )
        out = (r.stdout or "").strip()
        for line in out.splitlines():
            if line.startswith("BRI:") and line[4:].isdigit():
                return int(line[4:])
    except Exception as exc:
        logger.debug("brightness read-back failed: %s", exc)
    return None


@_validator("volume_control")
def _check_volume(params: dict, result_text: str) -> tuple[bool, str]:
    action = params.get("action", "up")
    if action != "set":
        # up/down/mute: direction-only — hard to validate without before-snapshot
        return True, ""
    requested = params.get("level")
    if requested is None:
        return True, ""
    actual = _read_windows_volume()
    if actual is None:
        return True, ""  # read-back unavailable → trust the tool
    ok = abs(actual - int(requested)) <= 5
    detail = f"volume={actual}% (requested {requested}%)"
    return ok, detail


@_validator("brightness_control")
def _check_brightness(params: dict, result_text: str) -> tuple[bool, str]:
    action = params.get("action", "up")
    actual = _read_windows_brightness()
    if actual is None:
        return True, ""  # external monitor or WMI unavailable → trust
    if action == "set":
        requested = params.get("level")
        if requested is None:
            return True, ""
        ok = abs(actual - int(requested)) <= 5
        return ok, f"brightness={actual}% (requested {requested}%)"
    # up/down: just confirm brightness is not stuck at 0 or 100 when going the wrong way
    if action == "up" and actual == 0:
        return False, "brightness still at 0% after increase"
    if action == "down" and actual == 100:
        return False, "brightness still at 100% after decrease"
    return True, f"brightness={actual}%"


@_validator("create_folder", "create_subfolders")
def _check_folder_created(params: dict, result_text: str) -> tuple[bool, str]:
    path = params.get("path") or params.get("name") or params.get("folder_path")
    if path:
        p = Path(path)
        exists = p.exists()
        return exists, ("created" if exists else f"folder missing: {path}")
    return True, ""


@_validator("delete_file", "move_file")
def _check_file_gone(params: dict, result_text: str) -> tuple[bool, str]:
    src = params.get("path") or params.get("source") or params.get("src")
    if src and Path(src).exists():
        return False, f"file still exists: {src}"
    return True, ""


@_validator("write_file")
def _check_file_written(params: dict, result_text: str) -> tuple[bool, str]:
    path = params.get("path")
    if path:
        p = Path(path)
        return p.exists(), ("written" if p.exists() else f"file missing: {path}")
    return True, ""


@_validator("clear_temp_files", "empty_recycle_bin")
def _check_cleanup(params: dict, result_text: str) -> tuple[bool, str]:
    # These are best-effort — check that result_text mentions bytes/files freed
    if result_text and re.search(r'\d+', result_text):
        return True, ""
    return True, ""  # pass through; can't easily validate without re-scanning


# ── Public API ─────────────────────────────────────────────────────────────────

def validate(tool_name: str, params: dict, result_text: str) -> tuple[bool, str]:
    """
    Returns (confirmed: bool, detail: str).
    confirmed=True means the tool's effect was verified (or no validator exists).
    detail is a short human-readable note for logging.
    """
    fn = _VALIDATORS.get(tool_name)
    if fn is None:
        return True, ""
    try:
        return fn(params, result_text)
    except Exception as exc:
        logger.debug("validator %s error: %s", tool_name, exc)
        return True, ""  # validation error → trust the tool
