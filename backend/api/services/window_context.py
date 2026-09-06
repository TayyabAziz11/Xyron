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
import time
import threading

logger = logging.getLogger(__name__)

# ── PowerShell command — P/Invoke GetForegroundWindow (WSL2 path) ────────────
# Submitted through ps_session.py's persistent session (see _query_ps below),
# which wraps this in its own try/catch — no outer try/catch needed here.
#
# Single line, no here-string: ps_session feeds commands over an
# *interactive* stdin pipe (a real .ps1 file loaded via `-File` doesn't have
# this problem, but that's not how ps_session works). A here-string's
# closing `'@` must be the first thing on its own line, which is fragile
# over a piped interactive session — sending one here caused the session's
# parser to never see a complete statement, so the sentinel was never
# printed and every call ran out the full timeout, killing and restarting
# the shared powershell.exe process every single time. A single-line
# Add-Type call with a PSTypeName existence check (so it only compiles once
# per process) avoids that entirely — verified directly against a live
# ps_session: 501ms on first call (Add-Type compile), ~18ms on repeat calls.
#
# PowerShell double-quoted strings escape an embedded " by doubling it
# ("") — backslash is NOT an escape character in PowerShell and doubling
# is required here, since Add-Type's C# body is itself inside a "..."
# string.

_WINFG_PS_CMD = (
    "if (-not ([System.Management.Automation.PSTypeName]'XyronForegroundWindowNative').Type) { "
    "Add-Type -TypeDefinition "
    '"using System;using System.Runtime.InteropServices;using System.Text;'
    "public class XyronForegroundWindowNative{"
    '[DllImport(""user32.dll"")]public static extern IntPtr GetForegroundWindow();'
    '[DllImport(""user32.dll"",CharSet=CharSet.Unicode)]public static extern int GetWindowText(IntPtr h,StringBuilder s,int c);'
    '[DllImport(""user32.dll"")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);'
    '}" '
    "}; "
    "$h=[XyronForegroundWindowNative]::GetForegroundWindow(); "
    "$sb=New-Object System.Text.StringBuilder(256); "
    "[XyronForegroundWindowNative]::GetWindowText($h,$sb,256)|Out-Null; "
    "$xpid=[uint32]0; "
    "[XyronForegroundWindowNative]::GetWindowThreadProcessId($h,[ref]$xpid)|Out-Null; "
    "$p=Get-Process -Id ([int]$xpid) -ErrorAction SilentlyContinue; "
    'Write-Output "TITLE:$($sb.ToString())|PROC:$($p.Name)|PID:$xpid"'
)

_CACHE_TTL = 3.5   # seconds — balance freshness vs PS round-trip overhead
# Was 2.0s; a live-observed cold/near-cold PS Add-Type round-trip costs
# ~300-500ms (window_context.py's own docstring above), and this gets
# queried up to 4x per verify retry loop (verifier_v2.py) plus once per
# "close/minimize/switch to" style command via context_resolver — a slightly
# longer window trims redundant round-trips without materially harming the
# freshness this cache exists for.


class _WindowContextService:

    def __init__(self) -> None:
        self._lock    = threading.Lock()
        self._cached  : dict | None = None
        self._cached_at: float      = 0.0

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

    # ── WSL2 PowerShell path — persistent session ────────────────────────────
    # Routed through ps_session.py's single reusable powershell.exe process
    # instead of spawning a fresh subprocess.run([powershell.exe, ...]) per
    # call. get_fresh() (screen_context_agent.py) bypasses the _CACHE_TTL
    # cache above on every screen query, so this path used to run cold on
    # every single query — a fresh cross-VM process spawn costs 1-5s under
    # load (observed: [SCREEN_AGENT_QUERY] ms=5037), dwarfing everything
    # else in the turn. ps_session's persistent process cuts that to ~30ms.

    def _query_ps(self) -> dict | None:
        try:
            from api.services.ps_session import ps_session
        except Exception as exc:
            logger.debug("window context ps_session import error: %s", exc)
            return None
        try:
            ok, out = ps_session.run(_WINFG_PS_CMD, timeout=6)
            out = (out or "").strip()
            # ok=False means ps_session itself failed (busy/timeout/died) —
            # `out` is then a human-readable message like "Command timed
            # out" or "PowerShell busy", not PS output, and must NOT be
            # parsed: it contains no ":" so it silently parsed into an
            # empty-but-truthy {"title": "", "proc_name": "", "pid": 0}
            # instead of None, which is what caused "I can't tell what's
            # on your screen" instead of a clear failure.
            if not ok:
                logger.debug("window context PS query failed: %s", out)
                return None
            if not out or out.startswith("ERROR:") or out.startswith("ERR:"):
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
