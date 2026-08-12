"""
selection_tracker.py — Perception Engine: "what does 'this' refer to right now".

Aggregates selection signals from every perception source into one
current_selection field, in priority order (most specific/reliable first):

  1. Browser DOM selection       (browser_perception's selected_text)
  2. Desktop UI Automation       (desktop_perception's selected_text/selected_item)
  3. Explorer selected files     (Shell.Application COM — same approach as
                                  explorer_context.py's folder-path lookup,
                                  extended to .SelectedItems())
  4. Clipboard content           (last resort — NOT a live selection, just
                                  "the last thing the user copied"; only
                                  used when nothing else has a signal)

This is what lets "explain this" / "translate this" / "summarize this"
resolve without the user repeating context — feeds current_selection in
World State, consumed by whatever handles those commands (not this module's
job — Perception observes, it doesn't decide what "explain this" should do).

Logs: [SELECTION_TRACKER_REFRESH] [SELECTION_TRACKER_SOURCE]
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# See explorer_context.py / multi_monitor_manager.py for why this must be
# a single semicolon-joined line (ps_session's warm process hangs on
# genuinely multi-line piped input).
_EXPLORER_SELECTION_PS = (
    'Add-Type -MemberDefinition \'[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();\' '
    '-Name Win32 -Namespace XyronSelection -ErrorAction SilentlyContinue; '
    '$h = [XyronSelection.Win32]::GetForegroundWindow(); '
    '$shell = New-Object -ComObject Shell.Application; '
    'foreach ($w in $shell.Windows()) { '
    'try { if ([IntPtr]$w.HWND -eq $h) { '
    '$items = $w.Document.SelectedItems(); $names = @(); '
    'foreach ($item in $items) { $names += $item.Path }; '
    'if ($names.Length -gt 0) { Write-Output ($names -join "|") }; break '
    '} } catch {} '
    '}'
)


def _explorer_selection() -> list[str]:
    try:
        from api.services.ps_session import run_ps
        ok, out = run_ps(_EXPLORER_SELECTION_PS, timeout=6)
        if ok and out.strip():
            return [p for p in out.strip().split("|") if p]
    except Exception:
        logger.debug("[SELECTION_TRACKER] explorer selection query failed", exc_info=True)
    return []


def _clipboard_text() -> Optional[str]:
    try:
        from api.services.ps_session import run_ps
        ok, out = run_ps("Get-Clipboard", timeout=5)
        if ok and out.strip():
            return out.strip()
    except Exception:
        pass
    return None


def refresh(
    browser_snapshot: Optional[dict] = None,
    desktop_snapshot: Optional[dict] = None,
    window: Optional[dict] = None,
) -> Optional[dict]:
    """
    Returns {"type": ..., "value": ..., "source": ...} for the highest-priority
    selection signal available, or None if nothing is selected anywhere.
    """
    if browser_snapshot and browser_snapshot.get("selected_text"):
        text = browser_snapshot["selected_text"].strip()
        if text:
            logger.debug("[SELECTION_TRACKER_SOURCE] source=browser")
            return {"type": "text", "value": text[:1000], "source": "browser"}

    if desktop_snapshot:
        if desktop_snapshot.get("selected_text"):
            logger.debug("[SELECTION_TRACKER_SOURCE] source=desktop_text")
            return {"type": "text", "value": desktop_snapshot["selected_text"][:1000], "source": "desktop"}
        if desktop_snapshot.get("selected_item"):
            logger.debug("[SELECTION_TRACKER_SOURCE] source=desktop_item")
            return {"type": "item", "value": desktop_snapshot["selected_item"], "source": "desktop"}

    proc = (window.get("proc_name") or "").lower() if window else ""
    if proc == "explorer":
        paths = _explorer_selection()
        if paths:
            logger.debug("[SELECTION_TRACKER_SOURCE] source=explorer count=%d", len(paths))
            return {
                "type": "file" if len(paths) == 1 else "files",
                "value": paths[0] if len(paths) == 1 else paths,
                "source": "explorer",
            }

    clip = _clipboard_text()
    if clip:
        logger.debug("[SELECTION_TRACKER_SOURCE] source=clipboard")
        return {"type": "clipboard", "value": clip[:1000], "source": "clipboard"}

    return None
