"""
explorer_context.py — Phase 1.5: resolves the folder open in the focused
Windows Explorer window, via Shell.Application COM automation.

window_context only gives a window title (e.g. "Downloads"), not a real
path, and there can be several Explorer windows open at once — so this
matches the *foreground* window's HWND against every open Explorer window's
Shell.Application entry to get its actual `.Document.Folder.Self.Path`.

Cheap early-out: does nothing (no PowerShell call) unless the foreground
process is actually explorer.exe, so this never costs anything on the
common "browser/IDE/voice session" path.

Cached 2s (mirrors window_context's own cache) to bound repeated-call cost.

Queries route through ps_session.py's warm persistent PowerShell process
(~30ms/call) rather than spawning a fresh powershell.exe per query
(~400ms) — this module originally did the latter; migrated once
desktop_perception.py (Phase 2) needed the same warm-session pattern for
UI Automation and it made sense to stop paying the cold-spawn cost here too.

Logs: [EXPLORER_FOLDER_RESOLVED] [EXPLORER_FOLDER_QUERY_MS] [EXPLORER_FOLDER_FAIL]
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL = 2.0

# ps_session's warm process treats piped stdin like an interactive REPL —
# genuinely multi-line text (real newlines) confuses its command-boundary
# detection and hangs until read-timeout (confirmed empirically). Every
# script routed through ps_session.run_ps() must be ONE semicolon-joined
# logical line. -MemberDefinition (not -TypeDefinition/here-string, which
# can't be flattened) + SilentlyContinue makes the P/Invoke declaration
# idempotent across repeated calls on the same warm process.
_PS_SCRIPT = (
    'Add-Type -MemberDefinition \'[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();\' '
    '-Name Win32 -Namespace XyronExplorer -ErrorAction SilentlyContinue; '
    '$h = [XyronExplorer.Win32]::GetForegroundWindow(); '
    '$shell = New-Object -ComObject Shell.Application; '
    '$found = $false; '
    'foreach ($w in $shell.Windows()) { '
    'try { if ([IntPtr]$w.HWND -eq $h) { Write-Output "PATH:$($w.Document.Folder.Self.Path)"; $found = $true; break } } catch {} '
    '}; '
    'if (-not $found) { Write-Output "NONE" }'
)


class _ExplorerContextService:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: Optional[Path] = None
        self._cached_at = 0.0

    def get_focused_folder(self, window: Optional[dict] = None) -> Optional[Path]:
        """Returns the real path of the focused Explorer window, or None."""
        if window is None:
            try:
                from .window_context import window_context
                window = window_context.get_active_window()
            except Exception:
                window = None

        if not window or (window.get("proc_name") or "").lower() != "explorer":
            return None

        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at < _CACHE_TTL:
                return self._cached

        t0 = time.monotonic()
        path = self._query()
        ms = (time.monotonic() - t0) * 1000
        logger.debug("[EXPLORER_FOLDER_QUERY_MS] ms=%.0f found=%s", ms, path is not None)

        with self._lock:
            self._cached = path
            self._cached_at = now
        return path

    def _query(self) -> Optional[Path]:
        try:
            from .ps_session import run_ps
            ok, out = run_ps(_PS_SCRIPT, timeout=6)
            if not ok:
                return None
            out = out.strip()
            if out.startswith("PATH:"):
                win_path = out[len("PATH:"):].strip()
                if win_path:
                    fs_path = _win_to_wsl_path(win_path)
                    logger.info("[EXPLORER_FOLDER_RESOLVED] path=%s", fs_path)
                    return fs_path
            return None
        except Exception as exc:
            logger.debug("[EXPLORER_FOLDER_FAIL] %s", exc)
            return None


def _win_to_wsl_path(win_path: str) -> Path:
    """C:\\Users\\x\\Downloads -> /mnt/c/Users/x/Downloads"""
    if len(win_path) >= 2 and win_path[1] == ":":
        letter = win_path[0].lower()
        rest = win_path[2:].replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{letter}/{rest}")
    return Path(win_path.replace("\\", "/"))


explorer_context = _ExplorerContextService()
