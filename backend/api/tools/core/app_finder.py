"""
4-source app discovery with fuzzy matching for WSL2/Windows.

Sources (built once at startup via build_app_index()):
  1. Start Menu .lnk shortcuts (ProgramData + APPDATA)
  2. Registry Uninstall keys (HKLM x86/x64 + HKCU)
  3. PATH commands (Get-Command)
  4. Store apps (Get-AppxPackage → shell:AppsFolder URI)

find_app(name) searches the index with exact → starts-with → contains → fuzzy fallback.
launch_app(name) resolves via find_app then dispatches the right Start-Process form.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import time
from typing import Any

from .ps_runner import ToolResult, run_ps

logger = logging.getLogger(__name__)

# ── App index ─────────────────────────────────────────────────────────────────

_APP_INDEX: dict[str, dict[str, str]] = {}   # name.lower() → {name, path, source}
_APP_INDEX_BUILT: bool = False
_BUILD_LOCK = asyncio.Lock()


# ── PowerShell source scripts ─────────────────────────────────────────────────

_PS_START_MENU = r"""
$dirs = @(
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
)
$items = [System.Collections.Generic.List[hashtable]]::new()
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) { continue }
    Get-ChildItem -Path $d -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
        $items.Add(@{name=$_.BaseName; path=$_.FullName; source='startmenu'})
    }
}
@{items=@($items)} | ConvertTo-Json -Compress -Depth 3
"""

_PS_REGISTRY = r"""
$keys = @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$items = [System.Collections.Generic.List[hashtable]]::new()
foreach ($k in $keys) {
    Get-ItemProperty -Path $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } | ForEach-Object {
        $path = if ($_.DisplayIcon) { $_.DisplayIcon -replace '"','' -replace ',\d+$','' }
                elseif ($_.InstallLocation) { $_.InstallLocation }
                else { '' }
        $items.Add(@{name=$_.DisplayName; path=$path; source='registry'})
    }
}
@{items=@($items)} | ConvertTo-Json -Compress -Depth 3
"""

_PS_PATH_CMDS = r"""
$items = [System.Collections.Generic.List[hashtable]]::new()
Get-Command * -ErrorAction SilentlyContinue | Where-Object { $_.CommandType -in 'Application','ExternalScript' } | ForEach-Object {
    $items.Add(@{name=$_.Name -replace '\.\w+$',''; path=$_.Source; source='path'})
}
@{items=@($items)} | ConvertTo-Json -Compress -Depth 3
"""

_PS_STORE_APPS = r"""
$items = [System.Collections.Generic.List[hashtable]]::new()
Get-AppxPackage -ErrorAction SilentlyContinue | ForEach-Object {
    $uri = "shell:AppsFolder\$($_.PackageFamilyName)!App"
    $friendly = ($_.Name -replace '^Microsoft\.','' -replace '^Windows\.','')
    $items.Add(@{name=$friendly; path=$uri; source='store'})
}
@{items=@($items)} | ConvertTo-Json -Compress -Depth 3
"""


# ── Index builder ─────────────────────────────────────────────────────────────

def _ingest(raw_items: Any, entries: dict[str, dict[str, str]]) -> int:
    """Parse a list of {name, path, source} dicts into the index."""
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return 0
    added = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name: str = (item.get("name") or "").strip()
        path: str = (item.get("path") or "").strip()
        source: str = (item.get("source") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in entries:
            entries[key] = {"name": name, "path": path, "source": source}
            added += 1
    return added


async def _run_source(script: str, label: str, timeout: float) -> list[dict]:
    result = await run_ps(script, timeout=timeout, parse_json=True)
    if not result.success:
        logger.debug("app_finder source %s failed: %s", label, result.message)
        return []
    items = result.data.get("items") or result.data.get("result") or []
    if isinstance(items, dict):
        items = [items]
    return items if isinstance(items, list) else []


async def build_app_index() -> None:
    """
    Populate _APP_INDEX from 4 Windows sources.
    Safe to call multiple times — rebuilds if already built.
    Designed to run as asyncio.create_task() at startup.
    """
    global _APP_INDEX, _APP_INDEX_BUILT

    async with _BUILD_LOCK:
        t0 = time.perf_counter()
        entries: dict[str, dict[str, str]] = {}

        sources = [
            (_PS_START_MENU, "startmenu",  20.0),
            (_PS_REGISTRY,   "registry",   20.0),
            (_PS_PATH_CMDS,  "path",       15.0),
            (_PS_STORE_APPS, "store",      20.0),
        ]

        results = await asyncio.gather(
            *[_run_source(script, label, timeout) for script, label, timeout in sources],
            return_exceptions=True,
        )

        for chunk in results:
            if isinstance(chunk, list):
                _ingest(chunk, entries)

        _APP_INDEX = entries
        _APP_INDEX_BUILT = True
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("app_finder: indexed %d apps in %.0f ms", len(entries), elapsed)


# ── find_app ──────────────────────────────────────────────────────────────────

def _search_index(query: str) -> tuple[dict[str, str] | None, str]:
    """
    Search _APP_INDEX.  Returns (entry, match_type) or (None, '').
    Priority: exact → starts_with → contains → fuzzy (cutoff 0.6).
    """
    q = query.lower().strip()

    # 1. Exact
    if q in _APP_INDEX:
        return _APP_INDEX[q], "exact"

    # 2. Starts-with
    for key, entry in _APP_INDEX.items():
        if key.startswith(q):
            return entry, "starts_with"

    # 3. Contains
    for key, entry in _APP_INDEX.items():
        if q in key:
            return entry, "contains"

    # 4. Fuzzy
    matches = difflib.get_close_matches(q, list(_APP_INDEX.keys()), n=1, cutoff=0.6)
    if matches:
        return _APP_INDEX[matches[0]], "fuzzy"

    return None, ""


async def find_app(name: str) -> ToolResult:
    """
    Find an installed application by name.

    If the index hasn't been built yet, builds it inline (one-time cost).
    Returns ToolResult with data = {name, path, source, match_type}.
    """
    if not _APP_INDEX_BUILT:
        await build_app_index()

    entry, match_type = _search_index(name)
    if entry is None:
        msg = f"No application matching '{name}' found"
        return ToolResult.failure(msg, spoken=msg, error_code="APP_NOT_FOUND", data={})

    return ToolResult.ok(
        f"Found '{entry['name']}' via {entry['source']} ({match_type})",
        spoken=f"Found {entry['name']}",
        data={
            "name":       entry["name"],
            "path":       entry["path"],
            "source":     entry["source"],
            "match_type": match_type,
        },
    )


# ── launch_app ────────────────────────────────────────────────────────────────

def _is_store_uri(path: str) -> bool:
    return path.startswith("shell:AppsFolder")


async def _verify_launched(process_name: str) -> bool:
    """Return True if a process matching name appeared within 2 s."""
    safe = process_name.replace("'", "''")
    script = (
        f"$p = Get-Process -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.Name -like '*{safe}*' }}; "
        "if ($p) { 'running' } else { 'not_found' }"
    )
    for _ in range(2):
        await asyncio.sleep(1)
        r = await run_ps(script, timeout=5.0)
        if r.success and "running" in r.message:
            return True
    return False


async def launch_app(name: str) -> ToolResult:
    """
    Find and launch an application by name.

    Launch strategy:
      - Store URI  → Start-Process "shell:AppsFolder\\..."
      - .lnk/.exe  → Start-Process "path"
      - Fallback   → Start-Process name directly (works for built-ins like notepad)
    """
    found = await find_app(name)

    if found.success:
        app_name  = found.data["name"]
        app_path  = found.data["path"]
        app_src   = found.data["source"]

        if _is_store_uri(app_path):
            safe_path = app_path.replace("'", "''")
            script = f"Start-Process '{safe_path}'; Write-Output 'launched'"
        elif app_path:
            safe_path = app_path.replace("'", "''")
            script = f"Start-Process '{safe_path}'; Write-Output 'launched'"
        else:
            # registry entry with no path — try name directly
            safe_name = app_name.replace("'", "''")
            script = f"Start-Process '{safe_name}'; Write-Output 'launched'"
    else:
        # Last resort: try Start-Process with raw name
        app_name = name
        app_src  = "direct"
        safe_name = name.replace("'", "''")
        script = f"Start-Process '{safe_name}' -ErrorAction Stop; Write-Output 'launched'"

    result = await run_ps(script, timeout=10.0, risk="medium")
    if not result.success:
        msg = f"Failed to launch '{app_name}': {result.message}"
        return ToolResult.failure(msg, spoken=f"Could not open {app_name}", error_code="LAUNCH_FAILED")

    # Best-effort verification (non-blocking — don't fail if we can't confirm)
    proc_name = app_name.split()[0].replace(".exe", "").replace(".lnk", "")
    confirmed = await _verify_launched(proc_name)

    spoken = f"Opened {app_name}"
    return ToolResult.ok(
        f"Launched '{app_name}' (confirmed={confirmed})",
        spoken=spoken,
        data={
            "name":      app_name,
            "path":      app_path if found.success else name,
            "source":    app_src,
            "confirmed": confirmed,
        },
        risk="medium",
    )
