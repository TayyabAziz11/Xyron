"""
desktop_perception.py — Perception Engine: native Windows application observation.

Used when Browser Perception has nothing (foreground app isn't Chrome, or
Chrome isn't CDP-connected). Prefers Windows UI Automation over OCR/vision —
per the Phase 2 brief, OCR/vision are last resorts, never the first attempt
for a native app.

UI Automation isn't reachable from WSL2 Linux Python directly (no COM), so
this follows the same PowerShell-bridge pattern as window_context.py, but
routed through ps_session's *warm* persistent process (~30ms/call) instead
of spawning a fresh powershell.exe per query (~400ms) — window_context.py
and my own Phase 1.5 explorer_context.py both still pay that cold-spawn
cost; ps_session.py already existed for exactly this reason
(see screen_context_agent.py) and desktop_perception.py is the first
consumer to use it directly rather than duplicating the pattern.

Scope note: this is a *generic* extractor (focused control name/type,
selected text via TextPattern, window-title document parsing) rather than
15 per-app custom parsers. Office/creative apps show a "filename - App Name"
title pattern workspace_context.py already parses for VS Code/Visual
Studio — generalized here to Word/Excel/PowerPoint/Notepad/Photoshop/etc.
Per-app deep integration (e.g. reading actual Word paragraph content) is
future work, flagged rather than attempted for all 15 named apps.

Logs: [DESKTOP_PERCEPTION_REFRESH] [DESKTOP_PERCEPTION_FAIL]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Apps that show "<document> - <App Name>" or "<document> — <App Name>" in
# their window title — same shape workspace_context.py already parses for
# VS Code, generalized to the rest of the Phase 2 app list.
_DOCUMENT_TITLE_APPS = {
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint",
    "notepad": "Notepad", "notepad++": "Notepad++",
    "photoshop": "Photoshop", "illustrator": "Illustrator",
    "premiere pro": "Premiere Pro", "blender": "Blender",
}

_TITLE_SPLIT_RE = re.compile(r"\s+[—–-]\s+")

# See explorer_context.py for why this must be a single semicolon-joined
# line (ps_session's warm process hangs on genuinely multi-line piped input).
_UI_AUTOMATION_PS = (
    'Add-Type -AssemblyName UIAutomationClient; Add-Type -AssemblyName UIAutomationTypes; '
    'try { '
    '$el = [System.Windows.Automation.AutomationElement]::FocusedElement; '
    'if ($el -eq $null) { Write-Output \'{}\' } else { '
    '$name = $el.Current.Name; $controlType = $el.Current.ControlType.ProgrammaticName; $className = $el.Current.ClassName; '
    '$selectedText = $null; '
    'try { $textPattern = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern); '
    '$ranges = $textPattern.GetSelection(); if ($ranges.Length -gt 0) { $selectedText = $ranges[0].GetText(500) } } catch {}; '
    '$selectedItem = $null; '
    'try { $selPattern = $el.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern); '
    'if ($selPattern.Current.IsSelected) { $selectedItem = $name } } catch {}; '
    'if (-not $selectedItem) { '
    'try { $container = $el.GetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern); '
    '$sel = $container.Current.GetSelection(); if ($sel.Length -gt 0) { $selectedItem = $sel[0].Current.Name } } catch {} '
    '}; '
    '$result = [PSCustomObject]@{focused_name=$name; control_type=$controlType; class_name=$className; '
    'selected_text=$selectedText; selected_item=$selectedItem}; '
    '$result | ConvertTo-Json -Compress '
    '} '
    '} catch { Write-Output \'{}\' }'
)


def _parse_document_from_title(title: str, proc_name: str) -> Optional[dict]:
    app_label = _DOCUMENT_TITLE_APPS.get(proc_name)
    if not app_label or not title:
        return None
    segments = [s.strip() for s in _TITLE_SPLIT_RE.split(title) if s.strip()]
    segments = [s for s in segments if app_label.lower() not in s.lower()]
    if not segments:
        return None
    doc_name = segments[0].lstrip("*● ").strip()
    if not doc_name:
        return None
    return {"name": doc_name, "app": app_label}


def get_ui_automation_snapshot() -> dict:
    """Focused-control snapshot via UI Automation. Returns {} on any failure."""
    try:
        from api.services.ps_session import run_ps
        ok, out = run_ps(_UI_AUTOMATION_PS, timeout=6)
        if not ok or not out.strip():
            logger.debug("[DESKTOP_PERCEPTION_FAIL] ok=%s out=%r", ok, out[:200])
            return {}
        return json.loads(out)
    except Exception:
        logger.debug("[DESKTOP_PERCEPTION_FAIL]", exc_info=True)
        return {}


def refresh(window: Optional[dict] = None) -> dict:
    """
    Observe the current native application state. *window* lets callers
    reuse an already-fetched window_context result instead of querying twice.
    Returns {} if there's no foreground window at all.
    """
    if window is None:
        try:
            from api.services.window_context import window_context
            window = window_context.get_active_window()
        except Exception:
            window = None
    if not window:
        return {}

    proc = (window.get("proc_name") or "").lower()
    title = window.get("title") or ""

    document = _parse_document_from_title(title, proc)
    ui = get_ui_automation_snapshot()

    task = None
    if document:
        task = f"working on {document['name']} in {document['app']}"
    elif ui.get("selected_item"):
        task = f"browsing {ui['selected_item']}"

    logger.debug("[DESKTOP_PERCEPTION_REFRESH] app=%s document=%s focused=%s",
                 proc, document, ui.get("focused_name"))

    return {
        "document": document,
        "focused_control": {
            "name": ui.get("focused_name"), "control_type": ui.get("control_type"),
            "class_name": ui.get("class_name"),
        } if ui else None,
        "selected_text": ui.get("selected_text"),
        "selected_item": ui.get("selected_item"),
        "task": task,
    }
