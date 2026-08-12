"""
Active window context — detects the currently focused Windows application.

Provides get_active_window() which returns:
    {"title": str, "proc_name": str, "pid": int}

On WSL2 uses a PowerShell subprocess (cached 2s to avoid startup overhead).
On native Windows uses win32gui directly (~1ms).

Callers inject the result into ctx["active_window"] so tools like
close_window / minimize_window can resolve "it" / "this window" to the
correct process without the user having to name it.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── PowerShell script — P/Invoke GetForegroundWindow (WSL2 path) ─────────────

_PS_SCRIPT = r"""
try {
Add-Type -TypeDefinition @"
using System;using System.Runtime.InteropServices;using System.Text;
public class _WinFG {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder s,int c);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
}
"@ -ErrorAction Stop
$h=[_WinFG]::GetForegroundWindow()
$sb=New-Object System.Text.StringBuilder(256)
[_WinFG]::GetWindowText($h,$sb,256)|Out-Null
$xpid=[uint32]0;[_WinFG]::GetWindowThreadProcessId($h,[ref]$xpid)|Out-Null
$p=Get-Process -Id ([int]$xpid) -ErrorAction SilentlyContinue
Write-Output "TITLE:$($sb.ToString())|PROC:$($p.Name)|PID:$xpid"
} catch { Write-Output "ERR:$($_.Exception.Message)" }
"""

_CACHE_TTL = 2.0   # seconds — balance freshness vs subprocess overhead


class _WindowContextService:

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._cached  : dict | None = None
        self._cached_at: float      = 0.0
        self._ps_path : str | None  = None
        self._ps1_path = "/mnt/c/Windows/Temp/_xyron_fgwin.ps1"
        self._ps1_win  = "C:\\Windows\\Temp\\_xyron_fgwin.ps1"
        self._ps1_written = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_active_window(self) -> dict | None:
        """
        Returns {"title": str, "proc_name": str, "pid": int} or None.
        Result is cached for CACHE_TTL seconds.
        """
        now = time.monotonic()
        with self._lock:
            if self._cached and now - self._cached_at < _CACHE_TTL:
                logger.debug("[WINDOW_CTX_CACHE_HIT] age_ms=%.0f", (now - self._cached_at) * 1000)
                return dict(self._cached)

        t0 = time.monotonic()
        result = self._query()
        ms = (time.monotonic() - t0) * 1000
        logger.debug("[WINDOW_CTX_QUERY_MS] ms=%.0f proc=%s", ms,
                     (result or {}).get("proc_name", "?"))

        with self._lock:
            self._cached    = result
            self._cached_at = now
        return dict(result) if result else None

    def invalidate(self) -> None:
        """Force next call to re-query (call after any window-changing operation)."""
        with self._lock:
            self._cached_at = 0.0

    # ── Native Windows path (win32gui) ─────────────────────────────────────────

    def _query_win32(self) -> dict | None:
        try:
            import win32gui   # type: ignore
            import win32process  # type: ignore
            import psutil
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = ""
            try:
                proc_name = psutil.Process(pid).name()
            except Exception:
                pass
            return {"title": title, "proc_name": proc_name, "pid": pid}
        except ImportError:
            return None  # win32gui not available (WSL2 Python)
        except Exception as exc:
            logger.debug("win32gui error: %s", exc)
            return None

    # ── WSL2 PowerShell path ───────────────────────────────────────────────────

    def _find_powershell(self) -> str | None:
        if self._ps_path:
            return self._ps_path
        for p in [
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe",
        ]:
            if Path(p).exists():
                self._ps_path = p
                return p
        return None

    def _ensure_ps1(self) -> None:
        if self._ps1_written:
            return
        try:
            Path(self._ps1_path).write_text(_PS_SCRIPT, encoding="utf-8")
            self._ps1_written = True
        except Exception:
            pass

    def _query_ps(self) -> dict | None:
        ps = self._find_powershell()
        if not ps:
            return None
        self._ensure_ps1()
        try:
            r = subprocess.run(
                [ps, "-NonInteractive", "-NoProfile", "-File", self._ps1_win],
                capture_output=True, text=True, timeout=6, errors="ignore",
            )
            out = (r.stdout or "").strip()
            if not out or out.startswith("ERR:"):
                return None
            # Parse "TITLE:...|PROC:...|PID:..."
            parts = dict(p.split(":", 1) for p in out.split("|") if ":" in p)
            pid_str = parts.get("PID", "0").strip()
            return {
                "title":     parts.get("TITLE", "").strip(),
                "proc_name": parts.get("PROC", "").strip().lower().replace(".exe", ""),
                "pid":       int(pid_str) if pid_str.isdigit() else 0,
            }
        except Exception as exc:
            logger.debug("window context PS query error: %s", exc)
            return None

    def _query(self) -> dict | None:
        # Try native first (no subprocess cost on Windows Python)
        result = self._query_win32()
        if result:
            return result
        # WSL2 fallback
        return self._query_ps()


window_context = _WindowContextService()
