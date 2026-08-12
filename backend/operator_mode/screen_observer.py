"""
Phase 2 — Screen Observer.

Captures current desktop state (active window, open windows, screen size)
without AI inference. Uses existing system tools via PowerShell bridge.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScreenState:
    active_window:  str        # title of foreground window
    active_process: str        # process name (e.g. "chrome.exe")
    open_windows:   list[str]  # all visible window titles
    screen_width:   int = 1920
    screen_height:  int = 1080


def _ps(script: str, timeout: int = 5) -> tuple[bool, str]:
    """Run PowerShell script via WSL bridge, return (ok, stdout)."""
    try:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
               "-Command", script]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.returncode == 0), r.stdout.strip()
    except Exception as exc:
        logger.debug("[SCREEN_OBSERVER] ps error: %s", exc)
        return False, ""


class ScreenObserver:
    """Lightweight desktop state observer. No AI, no screenshot analysis."""

    def capture(self) -> ScreenState:
        """
        Return current desktop state.
        Falls back to empty state if PowerShell is unavailable.
        """
        logger.info("[SCREEN_CAPTURE] capturing desktop state")
        active_win, active_proc = self._get_active_window()
        open_wins = self._get_open_windows()
        w, h = self._get_screen_size()
        state = ScreenState(
            active_window=active_win,
            active_process=active_proc,
            open_windows=open_wins,
            screen_width=w,
            screen_height=h,
        )
        logger.info("[SCREEN_ACTIVE_WINDOW] window=%r process=%r", active_win, active_proc)
        logger.info("[SCREEN_ANALYSIS] open_windows=%d width=%d height=%d",
                    len(open_wins), w, h)
        return state

    def _get_active_window(self) -> tuple[str, str]:
        script = (
            "$hwnd = [System.Console]::get_OutputEncoding(); "
            "Add-Type -Name Win32 -Namespace GetActiveWin -MemberDefinition '"
            "[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();'"
            " 2>$null; "
            "$proc = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
            "$_.MainWindowHandle -eq [GetActiveWin.Win32]::GetForegroundWindow() } "
            "| Select-Object -First 1; "
            "if ($proc) { Write-Output ($proc.MainWindowTitle + '|' + $proc.ProcessName) } "
            "else { Write-Output '|' }"
        )
        ok, out = _ps(script, timeout=3)
        if ok and out and "|" in out:
            parts = out.split("|", 1)
            return parts[0].strip(), parts[1].strip()
        # Simpler fallback
        ok2, out2 = _ps(
            "Get-Process | Where-Object {$_.MainWindowHandle -ne 0} "
            "| Sort-Object CPU -Descending | Select-Object -First 1 "
            "| ForEach-Object { $_.MainWindowTitle + '|' + $_.ProcessName }",
            timeout=3,
        )
        if ok2 and out2 and "|" in out2:
            parts = out2.split("|", 1)
            return parts[0].strip(), parts[1].strip()
        return "", ""

    def _get_open_windows(self) -> list[str]:
        ok, out = _ps(
            "Get-Process | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne ''} "
            "| Select-Object -ExpandProperty MainWindowTitle",
            timeout=3,
        )
        if ok and out:
            return [line.strip() for line in out.splitlines() if line.strip()]
        return []

    def _get_screen_size(self) -> tuple[int, int]:
        ok, out = _ps(
            "Add-Type -AssemblyName System.Windows.Forms 2>$null; "
            "$s = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
            "Write-Output ($s.Width.ToString() + 'x' + $s.Height.ToString())",
            timeout=3,
        )
        if ok and out and "x" in out:
            try:
                w, h = out.strip().split("x")
                return int(w), int(h)
            except Exception:
                pass
        return 1920, 1080

    def is_app_open(self, app_name: str) -> bool:
        """Check if an application window containing app_name is visible."""
        state = self.capture()
        name_lower = app_name.lower()
        return any(
            name_lower in w.lower()
            for w in state.open_windows + [state.active_window]
        )

    def find_window(self, app_name: str) -> str | None:
        """Return the first open window title matching app_name, or None."""
        name_lower = app_name.lower()
        state = self.capture()
        for w in state.open_windows:
            if name_lower in w.lower():
                return w
        return None


screen_observer = ScreenObserver()
