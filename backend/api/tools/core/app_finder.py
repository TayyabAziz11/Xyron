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
import os
import subprocess
import time
from typing import Any

from .ps_runner import ToolResult, run_ps

logger = logging.getLogger(__name__)

# ── WSL2 interop launcher ─────────────────────────────────────────────────────

_CMDEXE = "/mnt/c/Windows/System32/cmd.exe"
if not os.path.isfile(_CMDEXE):
    _CMDEXE = "cmd.exe"

# Hardcoded aliases for apps the dynamic index misses or mis-names
_HARDCODED_APPS: dict[str, dict[str, str]] = {
    "microsoft store": {"name": "Microsoft Store", "path": r"shell:AppsFolder\Microsoft.WindowsStore_8wekyb3d8bbwe!App", "source": "hardcoded"},
    "windows store":   {"name": "Microsoft Store", "path": r"shell:AppsFolder\Microsoft.WindowsStore_8wekyb3d8bbwe!App", "source": "hardcoded"},
    "store":           {"name": "Microsoft Store", "path": r"shell:AppsFolder\Microsoft.WindowsStore_8wekyb3d8bbwe!App", "source": "hardcoded"},
    "microsoft edit":  {"name": "Microsoft Edit",  "path": "edit.exe",                                                    "source": "hardcoded"},
    "edit":            {"name": "Microsoft Edit",  "path": "edit.exe",                                                    "source": "hardcoded"},
}


def _launch_via_interop(win_path: str, fallback_name: str = "") -> bool:
    """
    Launch a Windows app via WSL2 interop daemon — routes to the correct desktop session.
    Uses cmd.exe /c start "" path, same as system.py:_popen() approach.
    """
    try:
        target = win_path or fallback_name
        if not target:
            return False
        subprocess.Popen(
            [_CMDEXE, "/c", "start", "", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


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

# Verbs that mean a query is a command sentence, not an app name — if one of
# these leaked through intent routing (e.g. "install instagram from the
# store"), it must never be fuzzy-matched against installed apps.
_COMMAND_VERB_WORDS = frozenset({
    "install", "download", "get", "setup", "add", "grab", "fetch", "uninstall",
})


def _search_index(query: str) -> tuple[dict[str, str] | None, str]:
    """
    Search _APP_INDEX.  Returns (entry, match_type) or (None, '').
    Priority: hardcoded → exact → starts_with → contains → fuzzy (cutoff 0.72,
    guarded against garbage/compound phrases — see below).
    """
    q = query.lower().strip()

    # 0. Hardcoded aliases (Microsoft Store, Edit, etc.)
    if q in _HARDCODED_APPS:
        return _HARDCODED_APPS[q], "exact"

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

    # 4. Fuzzy — defense in depth. A garbage/compound phrase (e.g. a full
    # command sentence like "microsoft store and install instagram" that
    # leaked through intent routing) must never silently resolve to an
    # unrelated installed app (this previously launched "Microsoft Visual
    # Studio Installer" for that exact phrase). Real app names are short and
    # don't contain command verbs, so skip fuzzy entirely for anything that
    # looks like a sentence, and require token overlap on anything left.
    q_words = q.split()
    if len(q_words) > 3 or any(w in _COMMAND_VERB_WORDS for w in q_words):
        logger.info("[APP_FINDER_FUZZY_SKIPPED] query=%r reason=sentence_like", query)
        return None, ""

    matches = difflib.get_close_matches(q, list(_APP_INDEX.keys()), n=1, cutoff=0.72)
    if matches:
        matched_key = matches[0]
        # Token-overlap guard only applies to multi-word queries (e.g. "vs
        # code" matching "visual studio code" should share a real word). For
        # single-word queries this would break legitimate typo correction
        # ("crome" -> "chrome") since the whole point of fuzzy matching there
        # is that no token is identical — the ratio/cutoff above is the guard.
        if len(q_words) > 1:
            q_tokens = {w for w in q_words if len(w) >= 4}
            m_tokens = {w for w in matched_key.split() if len(w) >= 4}
            if q_tokens and not (q_tokens & m_tokens):
                logger.info(
                    "[APP_FINDER_FUZZY_REJECTED] query=%r candidate=%r reason=no_token_overlap",
                    query, matched_key,
                )
                return None, ""
        return _APP_INDEX[matched_key], "fuzzy"

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

async def launch_app(name: str) -> ToolResult:
    """
    Find and launch an application by name via WSL2 interop.

    Uses cmd.exe /c start "" path — routes through the correct Windows desktop session.
    Falls back to launching the raw name when find_app returns nothing.
    """
    found = await find_app(name)

    if found.success:
        app_name = found.data["name"]
        app_path = found.data["path"]
        app_src  = found.data["source"]
    else:
        app_name = name
        app_path = ""
        app_src  = "direct"

    ok = _launch_via_interop(app_path, app_name)
    if not ok:
        msg = f"Failed to launch '{app_name}'"
        return ToolResult.failure(msg, spoken=f"Could not open {app_name}", error_code="LAUNCH_FAILED")

    spoken = f"Opened {app_name}"
    return ToolResult.ok(
        f"Launched '{app_name}'",
        spoken=spoken,
        data={"name": app_name, "path": app_path, "source": app_src},
        risk="medium",
    )
