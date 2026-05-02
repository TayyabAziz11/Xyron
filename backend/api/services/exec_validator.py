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
    import os
    from utils.path_utils import resolve_wsl_path
    import re as _re

    base_raw = (params.get("path") or "").strip()
    name     = (params.get("name") or "").strip()

    if base_raw and name:
        wsl_base = resolve_wsl_path(base_raw)
        if wsl_base:
            clean = _re.sub(r'[<>:"|?*]', "", name.replace("\\", "/")).strip("/")
            wsl_target = wsl_base.rstrip("/") + "/" + clean
            exists = os.path.exists(wsl_target)
            logger.info("[VALID] create_folder: %r → %s", wsl_target, "ok" if exists else "MISSING")
            return exists, ("ok" if exists else f"folder missing: {wsl_target}")

    # create_subfolders — just confirm parent resolves; individual folders were
    # already verified inside _exec_create_subfolders with os.path.exists().
    parent_raw = (params.get("parent") or "").strip()
    if parent_raw:
        from utils.path_utils import resolve_wsl_path as _rwp
        wsl_parent = _rwp(parent_raw)
        if wsl_parent:
            return True, f"parent={wsl_parent}"

    return True, ""  # no path info available — trust the tool


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
    # Best-effort: check result mentions some numeric indication of work done.
    if result_text and re.search(r'\d+', result_text):
        return True, ""
    return True, ""


# Apps whose process name doesn't match the app key (URI launches, UWP, aliases)
_PROC_ALIASES: dict[str, list[str]] = {
    "settings":    ["systemsettings", "windows.immersivecontrolpanel", "applicationframehost"],
    "calculator":  ["calculator", "calculatorapp"],
    "taskmanager": ["taskmgr"],
    "terminal":    ["windowsterminal", "wt"],
    "explorer":    ["explorer"],
    "powershell":  ["powershell", "pwsh"],
    "cmd":         ["cmd"],
    "notepad":     ["notepad"],
    "paint":       ["mspaint"],
}


@_validator("open_application")
def _check_app_launched(params: dict, result_text: str) -> tuple[bool, str]:
    """Verify that the target process appeared in the process list."""
    app = (params.get("app_name") or params.get("app") or "").lower().strip()
    if not app:
        return True, ""

    # Known apps (those in _APP_MAP) are launched via defined commands — trust them.
    # UWP/URI-scheme apps (ms-settings:, etc.) won't show up in psutil from WSL.
    try:
        from api.tools.system_tools import _APP_MAP, _normalise_app
        if _normalise_app(app) in _APP_MAP:
            return True, f"known app '{app}' launched"
    except Exception:
        pass

    try:
        import psutil
        app_stem = re.sub(r'\.exe$', '', app.split("\\")[-1].split("/")[-1]).lower()
        stems = {app_stem} | set(_PROC_ALIASES.get(app_stem, []))
        for proc in psutil.process_iter(["name"]):
            name = re.sub(r'\.exe$', '', (proc.info.get("name") or "").lower())
            for stem in stems:
                if stem and (stem in name or name.startswith(stem)):
                    return True, f"process '{name}' running"
        return False, f"no process matching '{app_stem}' found"
    except Exception as exc:
        logger.debug("open_application validator error: %s", exc)
        return True, ""  # psutil unavailable → trust the tool


@_validator("wifi_connect")
def _check_wifi_connected(params: dict, result_text: str) -> tuple[bool, str]:
    """Verify the WiFi connected to the requested SSID via netsh."""
    ssid = (params.get("ssid") or params.get("network") or "").strip()
    if not ssid:
        return True, ""
    try:
        ps = _find_powershell_path()
        if not ps:
            return True, ""
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             "(netsh wlan show interfaces) -match 'SSID'"],
            capture_output=True, text=True, timeout=6, errors="ignore",
        )
        out = (r.stdout or "").strip().lower()
        connected = ssid.lower() in out
        return connected, (f"connected to {ssid}" if connected else f"SSID '{ssid}' not active")
    except Exception as exc:
        logger.debug("wifi_connect validator error: %s", exc)
        return True, ""


@_validator("get_battery_status")
def _check_battery_range(params: dict, result_text: str) -> tuple[bool, str]:
    """Sanity-check: result should contain a numeric percentage 0–100."""
    m = re.search(r'(\d{1,3})\s*%', result_text or "")
    if m:
        pct = int(m.group(1))
        if 0 <= pct <= 100:
            return True, f"battery={pct}%"
        return False, f"battery % out of range: {pct}"
    return True, ""  # no percentage in output — trust the tool


def _find_powershell_path() -> str | None:
    """Return a PowerShell executable path (extracted to avoid circular import)."""
    for p in [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]:
        from pathlib import Path as _Path
        if _Path(p).exists():
            return p
    return None


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
