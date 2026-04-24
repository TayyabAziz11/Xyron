"""
Screen Tools — clipboard, screenshot + GPT-4o Vision, typing, window control.

All operations delegate to PowerShell on Windows/WSL2 so no additional
native dependencies are required beyond what's already in the environment.
"""
from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)

# ── Platform ──────────────────────────────────────────────────────────────────

def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False

_ON_WSL = _is_wsl()


def _ps(script: str, timeout: int = 8) -> tuple[bool, str]:
    """Run a PowerShell script, return (success, stdout/stderr)."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0 and not out:
            return False, err or "PowerShell error"
        return True, out
    except Exception as exc:
        return False, str(exc)


# ── Clipboard ─────────────────────────────────────────────────────────────────

def _exec_read_clipboard(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Read the current Windows clipboard text content."""
    ok, text = _ps("Get-Clipboard")
    if not ok or not text:
        return ToolResult(success=False, text="Clipboard is empty or unreadable.",
                          spoken="The clipboard appears to be empty right now.")
    # Trim for speech (clipboard can be huge)
    spoken = text[:300].strip()
    if len(text) > 300:
        spoken = spoken.rstrip(".") + "… (content continues)"
    return ToolResult(success=True, text=text, spoken=f"Your clipboard contains: {spoken}",
                      data={"clipboard": text})


def _exec_write_clipboard(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Write text to the Windows clipboard."""
    content = params.get("text", "").strip()
    if not content:
        return ToolResult(success=False, text="No text provided.", spoken="What should I copy to the clipboard?")
    safe = content.replace("'", "''")
    ok, err = _ps(f"Set-Clipboard -Value '{safe}'")
    if ok:
        return ToolResult(success=True, text=f"Copied to clipboard: {content[:80]}",
                          spoken="Done — copied to clipboard.")
    return ToolResult(success=False, text=err, spoken="Couldn't write to the clipboard.")


# ── Screenshot + GPT-4o Vision ────────────────────────────────────────────────

_SCREENSHOT_LOCK = threading.Lock()

def _exec_read_screen(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Take a screenshot and describe it using GPT-4o Vision."""
    openai_key = ctx.get("openai_key", "")
    if not openai_key:
        # Fallback: read directly from settings (handles stale ctx)
        try:
            from ..config import settings as _s
            openai_key = _s.openai_api_key or ""
        except Exception:
            pass
    if not openai_key:
        return ToolResult(success=False, text="OpenAI key required for screen reading.",
                          spoken="I need an OpenAI API key to read your screen.")

    with _SCREENSHOT_LOCK:
        # Save screenshot to a temp Windows path accessible from WSL
        tmp_win = "C:\\Windows\\Temp\\ai_operator_screen.png"
        tmp_wsl = "/mnt/c/Windows/Temp/ai_operator_screen.png"

        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$b=New-Object System.Drawing.Bitmap($s.Width,$s.Height);"
            "$g=[System.Drawing.Graphics]::FromImage($b);"
            "$g.CopyFromScreen($s.Location,[System.Drawing.Point]::Empty,$s.Size);"
            f"$b.Save('{tmp_win}');"
            "$g.Dispose();$b.Dispose()"
        )
        ok, err = _ps(ps_script, timeout=15)
        if not ok:
            return ToolResult(success=False, text=f"Screenshot failed: {err}",
                              spoken="I had trouble capturing your screen.")

        try:
            img_bytes = Path(tmp_wsl).read_bytes()
        except Exception as exc:
            return ToolResult(success=False, text=str(exc),
                              spoken="I took the screenshot but couldn't read the file.")

    # Send to GPT-4o Vision
    b64 = base64.b64encode(img_bytes).decode()
    question = params.get("question", "Describe what is on this screen briefly.")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}},
                    {"type": "text", "text": question},
                ],
            }],
            max_tokens=300,
        )
        description = (resp.choices[0].message.content or "").strip()
        # Spoken version — trim to 2 sentences
        sentences = [s.strip() for s in description.split(". ") if s.strip()]
        spoken = ". ".join(sentences[:3]) + "." if sentences else description
        return ToolResult(success=True, text=description, spoken=spoken,
                          data={"description": description})
    except Exception as exc:
        logger.warning("GPT-4o Vision failed: %s", exc)
        return ToolResult(success=False, text=str(exc),
                          spoken="I couldn't analyse the screen right now.")


# ── Type text ─────────────────────────────────────────────────────────────────

def _exec_type_text(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Type text into the currently focused window using SendKeys."""
    text = params.get("text", "").strip()
    if not text:
        return ToolResult(success=False, text="No text to type.", spoken="What should I type?")

    # Escape special SendKeys characters: + ^ % ~ ( ) { } [ ]
    special = r"\+\^%~(){}[]"
    escaped = ""
    for ch in text:
        if ch in "+^%~(){}[]":
            escaped += "{" + ch + "}"
        else:
            escaped += ch

    safe = escaped.replace("'", "''")
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        f"[System.Windows.Forms.SendKeys]::SendWait('{safe}')"
    )
    ok, err = _ps(ps_script, timeout=10)
    if ok:
        preview = text[:60] + ("…" if len(text) > 60 else "")
        return ToolResult(success=True, text=f"Typed: {text}",
                          spoken=f"Done — typed: {preview}")
    return ToolResult(success=False, text=err, spoken="Couldn't type that. The window may not have focus.")


# ── Window control ────────────────────────────────────────────────────────────

def _exec_switch_window(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Bring a window to the foreground by application name or window title."""
    app = params.get("app", "").strip()
    if not app:
        return ToolResult(success=False, text="App name required.", spoken="Which app should I switch to?")
    safe = app.replace("'", "''")
    ps_script = (
        "$shell = New-Object -ComObject WScript.Shell;"
        f"$result = $shell.AppActivate('{safe}');"
        "if (-not $result) { exit 1 }"
    )
    ok, _ = _ps(ps_script)
    if ok:
        return ToolResult(success=True, text=f"Switched to {app}", spoken=f"Switched to {app}.")
    # Fallback: try starting the process (if it's not running, launch it)
    return ToolResult(success=False, text=f"Window '{app}' not found.",
                      spoken=f"I couldn't find a window for {app}. Is it open?")


def _exec_minimize_window(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Minimize the currently active window."""
    ok, err = _ps(
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.SendKeys]::SendWait('% n')"
    )
    if ok:
        return ToolResult(success=True, text="Minimized active window", spoken="Minimized.")
    return ToolResult(success=False, text=err, spoken="Couldn't minimize the window.")


def _exec_close_window(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Close the currently active window (Alt+F4)."""
    ok, err = _ps(
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.SendKeys]::SendWait('%{F4}')"
    )
    if ok:
        return ToolResult(success=True, text="Closed active window", spoken="Closed the window.")
    return ToolResult(success=False, text=err, spoken="Couldn't close the window.")


def _exec_maximize_window(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Maximize the currently active window."""
    ok, err = _ps(
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.SendKeys]::SendWait('% x')"
    )
    if ok:
        return ToolResult(success=True, text="Maximized active window", spoken="Maximized.")
    return ToolResult(success=False, text=err, spoken="Couldn't maximize the window.")


# ── Register all tools ────────────────────────────────────────────────────────

registry.register(
    name="read_clipboard",
    definition={"type": "function", "function": {
        "name": "read_clipboard",
        "description": "Read the current content of the Windows clipboard. Use when user says 'what's on my clipboard', 'read clipboard', 'what did I copy'.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    executor=_exec_read_clipboard, risk="low", category="system",
)

registry.register(
    name="write_clipboard",
    definition={"type": "function", "function": {
        "name": "write_clipboard",
        "description": "Write text to the Windows clipboard.",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string", "description": "Text to copy"}},
                       "required": ["text"]},
    }},
    executor=_exec_write_clipboard, risk="low", category="system",
)

registry.register(
    name="read_screen",
    definition={"type": "function", "function": {
        "name": "read_screen",
        "description": "Take a screenshot and describe what's on the screen using AI vision. Use when user says 'what's on my screen', 'read the screen', 'what does this say', 'describe my screen'.",
        "parameters": {"type": "object",
                       "properties": {"question": {"type": "string", "description": "Specific question about the screen content"}},
                       "required": []},
    }},
    executor=_exec_read_screen, risk="low", category="system",
)

registry.register(
    name="type_text",
    definition={"type": "function", "function": {
        "name": "type_text",
        "description": "Type text into the currently focused window. Use when user says 'type this', 'write this', 'type for me'. Requires a window to have keyboard focus.",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string", "description": "Exact text to type"}},
                       "required": ["text"]},
    }},
    executor=_exec_type_text, risk="medium", category="system",
)

registry.register(
    name="switch_window",
    definition={"type": "function", "function": {
        "name": "switch_window",
        "description": "Switch to (bring to foreground) an open application window. Use when user says 'switch to X', 'go to X', 'bring up X' (when X is already open).",
        "parameters": {"type": "object",
                       "properties": {"app": {"type": "string", "description": "App name or window title"}},
                       "required": ["app"]},
    }},
    executor=_exec_switch_window, risk="low", category="system",
)

registry.register(
    name="minimize_window",
    definition={"type": "function", "function": {
        "name": "minimize_window",
        "description": "Minimize the currently active/focused window.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    executor=_exec_minimize_window, risk="low", category="system",
)

registry.register(
    name="close_window",
    definition={"type": "function", "function": {
        "name": "close_window",
        "description": "Close the currently active window (equivalent to Alt+F4).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    executor=_exec_close_window, risk="medium", category="system",
)

registry.register(
    name="maximize_window",
    definition={"type": "function", "function": {
        "name": "maximize_window",
        "description": "Maximize the currently active window.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    executor=_exec_maximize_window, risk="low", category="system",
)
