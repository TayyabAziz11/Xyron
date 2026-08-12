"""
multi_monitor_manager.py — Perception Engine: display topology.

Tracks monitor count, primary monitor, mouse-cursor monitor, and the
foreground window's monitor, so Vision Perception can capture "only the
relevant monitor" instead of the whole desktop.

Uses the same PowerShell-bridge pattern as window_context.py/ps_session.py
(WSL2 has no native access to Windows display APIs) — single Add-Type call
against System.Windows.Forms, routed through the warm ps_session so this
costs ~30ms instead of a ~400ms cold subprocess spawn. Monitor topology
changes rarely, so callers should cache this (event_dispatcher.py refreshes
it on a much longer interval than window/browser polling).

Logs: [MULTI_MONITOR_REFRESH] [MULTI_MONITOR_FAIL]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ps_session's warm persistent process treats piped stdin like an
# interactive REPL — genuinely multi-line text (real newlines, even without
# a here-string) confuses its command-boundary detection and hangs until
# read-timeout (confirmed empirically: a bare two-statement, two-line
# command reproduces it). Every script routed through ps_session.run_ps()
# must therefore be ONE semicolon-joined logical line. -MemberDefinition
# (not -TypeDefinition/here-string, which also can't be flattened) plus
# -ErrorAction SilentlyContinue makes the P/Invoke declaration idempotent
# across repeated calls on the same warm process.
_PS_SCRIPT = (
    'Add-Type -AssemblyName System.Windows.Forms; '
    'Add-Type -MemberDefinition \'[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();\' '
    '-Name Win32 -Namespace XyronMonitors -ErrorAction SilentlyContinue; '
    '$screens = [System.Windows.Forms.Screen]::AllScreens; '
    '$mouse = [System.Windows.Forms.Cursor]::Position; '
    '$fgHwnd = [XyronMonitors.Win32]::GetForegroundWindow(); '
    '$fgScreen = [System.Windows.Forms.Screen]::FromHandle($fgHwnd); '
    '$result = @(); '
    'for ($i = 0; $i -lt $screens.Length; $i++) { '
    '$s = $screens[$i]; $b = $s.Bounds; '
    '$mouseHere = ($mouse.X -ge $b.Left -and $mouse.X -lt $b.Right -and $mouse.Y -ge $b.Top -and $mouse.Y -lt $b.Bottom); '
    '$fgHere = ($s.DeviceName -eq $fgScreen.DeviceName); '
    '$result += [PSCustomObject]@{index=$i; primary=$s.Primary; device=$s.DeviceName; '
    'left=$b.Left; top=$b.Top; width=$b.Width; height=$b.Height; '
    'has_mouse=$mouseHere; has_foreground_window=$fgHere} '
    '}; '
    '$result | ConvertTo-Json -Compress'
)


@dataclass
class MonitorInfo:
    index: int
    primary: bool
    device: str
    left: int
    top: int
    width: int
    height: int
    has_mouse: bool
    has_foreground_window: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index, "primary": self.primary, "device": self.device,
            "bounds": {"left": self.left, "top": self.top, "width": self.width, "height": self.height},
            "has_mouse": self.has_mouse, "has_foreground_window": self.has_foreground_window,
        }


def get_monitors() -> list[MonitorInfo]:
    """Query current display topology. Returns [] on any failure (headless/no display)."""
    try:
        from api.services.ps_session import run_ps
        ok, out = run_ps(_PS_SCRIPT, timeout=8)
        if not ok or not out.strip():
            logger.debug("[MULTI_MONITOR_FAIL] ok=%s out=%r", ok, out[:200])
            return []
        data = json.loads(out)
        if isinstance(data, dict):  # single-monitor systems return an object, not a list
            data = [data]
        monitors = [
            MonitorInfo(
                index=m["index"], primary=m["primary"], device=m.get("device", ""),
                left=m["left"], top=m["top"], width=m["width"], height=m["height"],
                has_mouse=m["has_mouse"], has_foreground_window=m["has_foreground_window"],
            )
            for m in data
        ]
        logger.debug("[MULTI_MONITOR_REFRESH] count=%d", len(monitors))
        return monitors
    except Exception:
        logger.debug("[MULTI_MONITOR_FAIL]", exc_info=True)
        return []


def get_primary_index(monitors: Optional[list[MonitorInfo]] = None) -> int:
    monitors = monitors if monitors is not None else get_monitors()
    for m in monitors:
        if m.primary:
            return m.index
    return 0


def get_foreground_monitor_index(monitors: Optional[list[MonitorInfo]] = None) -> int:
    """The monitor Vision Perception should capture — where the foreground window lives."""
    monitors = monitors if monitors is not None else get_monitors()
    for m in monitors:
        if m.has_foreground_window:
            return m.index
    return get_primary_index(monitors)
