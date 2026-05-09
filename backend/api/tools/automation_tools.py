"""
Desktop automation tools — PowerShell-based mouse/keyboard control + workflow runner.

Works from WSL2 (calls powershell.exe) or native Windows.
Does NOT require PyAutoGUI (which needs a display server).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)

# ── Platform detection (reuse system_tools approach) ─────────────────────────

_ON_WINDOWS = sys.platform == "win32"
_ON_WSL = (
    sys.platform == "linux"
    and "microsoft" in Path("/proc/version").read_text().lower()
    if Path("/proc/version").exists() else False
)


def _find_powershell() -> str:
    """Return path to powershell.exe, preferring PowerShell 7 (pwsh) on WSL2."""
    candidates = [
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
    ]
    if _ON_WSL:
        for c in candidates:
            if Path(c).exists():
                return c
        return "powershell.exe"  # hope it's in PATH
    return "powershell.exe"


_POWERSHELL = _find_powershell()


def _powershell(script: str, timeout: int = 15) -> tuple[bool, str]:
    """Run a PowerShell script on Windows from WSL2 or natively."""
    try:
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        success = result.returncode == 0
        return success, out if out else err
    except subprocess.TimeoutExpired:
        return False, "PowerShell script timed out"
    except FileNotFoundError:
        return False, "powershell.exe not found — desktop automation requires Windows/WSL2"
    except Exception as exc:
        return False, str(exc)


# ── SendKeys mapping ──────────────────────────────────────────────────────────

_SENDKEYS_MAP: Dict[str, str] = {
    "ctrl+c":          "^c",
    "ctrl+v":          "^v",
    "ctrl+x":          "^x",
    "ctrl+z":          "^z",
    "ctrl+y":          "^y",
    "ctrl+a":          "^a",
    "ctrl+s":          "^s",
    "ctrl+w":          "^w",
    "ctrl+t":          "^t",
    "ctrl+n":          "^n",
    "ctrl+f":          "^f",
    "ctrl+p":          "^p",
    "ctrl+r":          "^r",
    "ctrl+l":          "^l",
    "ctrl+enter":      "^{ENTER}",
    "ctrl+shift+n":    "^+n",
    "ctrl+shift+t":    "^+t",
    "ctrl+shift+v":    "^+v",
    "alt+tab":         "%{TAB}",
    "alt+f4":          "%{F4}",
    "alt+enter":       "%{ENTER}",
    "shift+tab":       "+{TAB}",
    "shift+enter":     "+{ENTER}",
    "enter":           "{ENTER}",
    "return":          "{ENTER}",
    "escape":          "{ESCAPE}",
    "esc":             "{ESCAPE}",
    "tab":             "{TAB}",
    "space":           " ",
    "backspace":       "{BACKSPACE}",
    "delete":          "{DELETE}",
    "home":            "{HOME}",
    "end":             "{END}",
    "pageup":          "{PGUP}",
    "pagedown":        "{PGDN}",
    "up":              "{UP}",
    "down":            "{DOWN}",
    "left":            "{LEFT}",
    "right":           "{RIGHT}",
    "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
    "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
    "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
    "copy":   "^c",
    "paste":  "^v",
    "cut":    "^x",
    "undo":   "^z",
    "redo":   "^y",
    "save":   "^s",
    "select all": "^a",
    "find":   "^f",
    "refresh": "{F5}",
    "new tab": "^t",
    "close tab": "^w",
    "new window": "^n",
}


def _to_sendkeys(keys: str) -> str:
    """Convert human-readable key combo to PowerShell SendKeys format."""
    normalized = keys.lower().strip().replace(" ", "")
    # Check exact map
    if normalized in _SENDKEYS_MAP:
        return _SENDKEYS_MAP[normalized]
    # Also check with spaces
    keys_lower = keys.lower().strip()
    if keys_lower in _SENDKEYS_MAP:
        return _SENDKEYS_MAP[keys_lower]
    # Try removing spaces between words
    return _SENDKEYS_MAP.get(normalized, keys)


# ── Tool executors ────────────────────────────────────────────────────────────

def _exec_desktop_type(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Type text at the current cursor position using SendKeys."""
    text = params.get("text", "").strip()
    if not text:
        return ToolResult(success=False, text="No text provided.", spoken="What should I type?")

    # Escape SendKeys special chars: +^%~{}[]()
    escaped = (text
               .replace("{", "{{}")
               .replace("}", "{}}")
               .replace("+", "{+}")
               .replace("^", "{^}")
               .replace("%", "{%}")
               .replace("~", "{~}")
               .replace("(", "{(}")
               .replace(")", "{)}")
               .replace("[", "{[}")
               .replace("]", "{]}")
               )

    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 200
[System.Windows.Forms.SendKeys]::SendWait('{escaped}')
"""
    ok, msg = _powershell(script)
    return ToolResult(
        success=ok,
        text=f"Typed: {text}" if ok else msg,
        spoken=f"Typed it." if ok else f"Couldn't type: {msg}",
    )


def _exec_desktop_hotkey(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Press a keyboard shortcut on the active window."""
    keys = params.get("keys", "").strip()
    if not keys:
        return ToolResult(success=False, text="No keys specified.", spoken="Which keys should I press?")

    sendkeys = _to_sendkeys(keys)
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('{sendkeys}')
"""
    ok, msg = _powershell(script)
    return ToolResult(
        success=ok,
        text=f"Pressed {keys}" if ok else msg,
        spoken=f"Pressed {keys}." if ok else f"Couldn't press {keys}: {msg}",
    )


def _exec_desktop_click(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Click at exact screen coordinates (x, y)."""
    x = int(params.get("x", 0))
    y = int(params.get("y", 0))
    button = params.get("button", "left").lower()

    if button == "right":
        down_flag, up_flag = "0x0008", "0x0010"
    elif button == "middle":
        down_flag, up_flag = "0x0020", "0x0040"
    else:
        down_flag, up_flag = "0x0002", "0x0004"

    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class MouseInput {{
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
}}
"@
[MouseInput]::SetCursorPos({x}, {y})
Start-Sleep -Milliseconds 80
[MouseInput]::mouse_event({down_flag}, 0, 0, 0, 0)
Start-Sleep -Milliseconds 50
[MouseInput]::mouse_event({up_flag}, 0, 0, 0, 0)
"""
    ok, msg = _powershell(script)
    return ToolResult(
        success=ok,
        text=f"Clicked ({x}, {y})" if ok else msg,
        spoken=f"Clicked." if ok else f"Click failed: {msg}",
    )


def _exec_desktop_scroll(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Scroll the mouse wheel up or down."""
    direction = params.get("direction", "down").lower()
    amount = int(params.get("amount", 3))
    delta = -120 * amount if direction == "down" else 120 * amount

    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ScrollInput {{
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
}}
"@
[ScrollInput]::mouse_event(0x0800, 0, 0, {delta}, 0)
"""
    ok, msg = _powershell(script)
    return ToolResult(
        success=ok,
        text=f"Scrolled {direction} {amount}x" if ok else msg,
        spoken=f"Scrolled {direction}." if ok else f"Scroll failed: {msg}",
    )


def _exec_desktop_focus_app(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Bring a running application window to the foreground."""
    app_name = params.get("app_name", "").strip()
    if not app_name:
        return ToolResult(success=False, text="App name required.", spoken="Which app should I focus?")

    script = f"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WindowFocus {{
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -match '{app_name}' }} | Select-Object -First 1
if ($proc) {{
  [WindowFocus]::ShowWindow($proc.MainWindowHandle, 9)
  [WindowFocus]::SetForegroundWindow($proc.MainWindowHandle)
  Write-Output "focused"
}} else {{
  Write-Error "App not found"
  exit 1
}}
"""
    ok, msg = _powershell(script)
    return ToolResult(
        success=ok,
        text=f"Focused {app_name}" if ok else msg,
        spoken=f"Focused {app_name}." if ok else f"Couldn't find {app_name} window.",
    )


def _exec_desktop_screenshot(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Capture the full Windows desktop as a base64 PNG."""
    import base64

    # Save to a temp Windows path
    if _ON_WSL:
        win_tmp = "C:\\Windows\\Temp\\xyron_screenshot.png"
        wsl_tmp = "/mnt/c/Windows/Temp/xyron_screenshot.png"
    else:
        win_tmp = os.path.join(tempfile.gettempdir(), "xyron_screenshot.png")
        wsl_tmp = win_tmp

    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$g.Dispose()
$bmp.Save('{win_tmp}')
$bmp.Dispose()
Write-Output "ok"
"""
    ok, msg = _powershell(script, timeout=20)
    if not ok:
        return ToolResult(success=False, text=msg, spoken="Screenshot failed.")

    try:
        img_bytes = Path(wsl_tmp).read_bytes()
        b64 = base64.b64encode(img_bytes).decode()
        return ToolResult(
            success=True,
            text=f"data:image/png;base64,{b64}",
            spoken="Screenshot captured.",
            data={"base64": b64, "format": "png"},
        )
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Couldn't read screenshot file.")


def _exec_run_workflow(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Execute a named multi-step workflow."""
    name = params.get("name", "").strip()
    variables: dict = params.get("variables", {}) or {}

    if not name:
        return ToolResult(success=False, text="Workflow name required.", spoken="Which workflow should I run?")

    try:
        from ..services.automation_workflow_service import automation_workflow_service as workflow_service
        result = workflow_service.execute(name, variables, ctx)
        return ToolResult(
            success=result.get("success", False),
            text=result.get("text", ""),
            spoken=result.get("spoken", "Workflow complete."),
            data=result,
        )
    except Exception as exc:
        logger.exception("run_workflow failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="Workflow failed.")


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    name="desktop_type",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_type",
            "description": "Type text at the current keyboard focus position on the desktop. Use when user says 'type X', 'write X', 'enter X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"},
                },
                "required": ["text"],
            },
        },
    },
    executor=_exec_desktop_type,
    risk="medium",
    category="automation",
)

registry.register(
    name="desktop_hotkey",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_hotkey",
            "description": "Press a keyboard shortcut on the active window. Use for 'press ctrl+c', 'press enter', 'copy', 'paste', 'undo', 'save'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Key combo e.g. 'ctrl+c', 'alt+tab', 'enter', 'escape'"},
                },
                "required": ["keys"],
            },
        },
    },
    executor=_exec_desktop_hotkey,
    risk="low",
    category="automation",
)

registry.register(
    name="desktop_click",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_click",
            "description": "Click at exact screen pixel coordinates (x, y). Use only when coordinates are explicitly known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x":      {"type": "integer", "description": "Horizontal pixel position"},
                    "y":      {"type": "integer", "description": "Vertical pixel position"},
                    "button": {"type": "string",  "enum": ["left", "right", "middle"], "description": "Mouse button"},
                },
                "required": ["x", "y"],
            },
        },
    },
    executor=_exec_desktop_click,
    risk="medium",
    category="automation",
)

registry.register(
    name="desktop_scroll",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_scroll",
            "description": "Scroll the mouse wheel up or down on the active window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction"},
                    "amount":    {"type": "integer", "description": "Number of scroll steps (default 3)"},
                },
                "required": ["direction"],
            },
        },
    },
    executor=_exec_desktop_scroll,
    risk="low",
    category="automation",
)

registry.register(
    name="desktop_focus_app",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_focus_app",
            "description": "Bring a running application window to the front / foreground.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "App window title or process name"},
                },
                "required": ["app_name"],
            },
        },
    },
    executor=_exec_desktop_focus_app,
    risk="low",
    category="automation",
)

registry.register(
    name="desktop_screenshot",
    definition={
        "type": "function",
        "function": {
            "name": "desktop_screenshot",
            "description": "Capture the full desktop as a PNG image.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    executor=_exec_desktop_screenshot,
    risk="low",
    category="automation",
)

registry.register(
    name="run_workflow",
    definition={
        "type": "function",
        "function": {
            "name": "run_workflow",
            "description": "Execute a pre-defined multi-step workflow by name. Use for complex tasks like 'open youtube and play a video', 'compose Gmail', 'google maps directions'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":      {"type": "string", "description": "Workflow name e.g. 'youtube_search', 'gmail_compose'"},
                    "variables": {"type": "object", "description": "Variables to inject e.g. {\"query\": \"lofi music\"}"},
                },
                "required": ["name"],
            },
        },
    },
    executor=_exec_run_workflow,
    risk="low",
    category="automation",
)
