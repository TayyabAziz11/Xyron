"""
3-tier file search for WSL2/Windows.

Tier 1: Everything CLI (es.exe) — <100ms when available
Tier 2: Windows Search Index (OLE/ADODB via PowerShell) — ~2s, indexed
Tier 3: PowerShell Get-ChildItem — brute-force per accessible drive, depth 6
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from .drives import get_drives
from .path_translator import safe_win_path, win_to_wsl
from .ps_runner import ToolResult, run_ps

# ── Everything CLI paths ──────────────────────────────────────────────────────

_ES_PATHS = [
    "/mnt/c/Program Files/Everything/es.exe",
    "/mnt/c/Program Files (x86)/Everything/es.exe",
]
_ES_EXE: str | None = next((p for p in _ES_PATHS if os.path.isfile(p)), None)


# ── Shared result builder ─────────────────────────────────────────────────────

def _result_dict(
    win_path: str,
    source: str,
    is_dir: bool | None = None,
) -> dict[str, Any]:
    """Build a normalised result dict from a Windows path."""
    win_path = win_path.strip().rstrip("\\").rstrip("/")
    sep = "\\" if "\\" in win_path else "/"
    name = win_path.split(sep)[-1] if sep in win_path else win_path
    wsl = win_to_wsl(win_path)
    if is_dir is None:
        is_dir = os.path.isdir(wsl) if wsl.startswith("/mnt/") else False
    return {"path": win_path, "name": name, "is_dir": is_dir, "wsl_path": wsl, "source": source}


# ── Tier 1: Everything CLI ────────────────────────────────────────────────────

async def _tier1_everything(query: str, max_results: int) -> list[dict[str, Any]]:
    if not _ES_EXE:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            _ES_EXE, "-n", str(max_results), query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            raw_out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return []
        lines = raw_out.decode("utf-8", errors="replace").strip().splitlines()
        return [_result_dict(ln, "everything") for ln in lines if ln.strip()]
    except Exception:
        return []


# ── Tier 2: Windows Search Index (ADODB) ─────────────────────────────────────

async def _tier2_winindex(query: str, max_results: int) -> list[dict[str, Any]]:
    safe_q = query.replace("'", "''")
    script = (
        "$conn = New-Object -ComObject ADODB.Connection; "
        "$conn.Open(\"Provider=Search.CollatorDSO;Extended Properties='Application=Windows'\"); "
        "$rs = New-Object -ComObject ADODB.Recordset; "
        f"$rs.Open(\"SELECT TOP {max_results} System.ItemPathDisplay "
        f"FROM SystemIndex WHERE System.FileName LIKE '%{safe_q}%'\", $conn); "
        "$paths = [System.Collections.Generic.List[string]]::new(); "
        "while (-not $rs.EOF) { "
        "    $v = $rs.Fields[\"System.ItemPathDisplay\"].Value; "
        "    if ($v) { $paths.Add($v) }; "
        "    $rs.MoveNext() }; "
        "$rs.Close(); $conn.Close(); "
        "@{paths = @($paths)} | ConvertTo-Json -Compress"
    )
    result = await run_ps(script, timeout=8.0, parse_json=True)
    if not result.success:
        return []
    paths = result.data.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    elif not isinstance(paths, list):
        return []
    return [_result_dict(p, "winindex") for p in paths if isinstance(p, str) and p.strip()]


# ── Tier 3: Get-ChildItem per drive ──────────────────────────────────────────

async def _gci_one_drive(
    win_path: str, query: str, per_drive_max: int
) -> list[dict[str, Any]]:
    safe_drive = safe_win_path(win_path)
    safe_q = query.replace("'", "''").replace("[", "`[").replace("]", "`]")
    script = (
        f"$f = @(Get-ChildItem -Path '{safe_drive}' -Recurse -Depth 6 "
        f"-Filter '*{safe_q}*' -ErrorAction SilentlyContinue "
        f"| Select-Object -First {per_drive_max} FullName,PSIsContainer); "
        "@{items = $f} | ConvertTo-Json -Compress"
    )
    result = await run_ps(script, timeout=12.0, parse_json=True)
    if not result.success:
        return []
    items = result.data.get("items") or []
    if isinstance(items, dict):
        items = [items]
    elif not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full = (item.get("FullName") or "").strip()
        if not full:
            continue
        out.append(_result_dict(full, "powershell", is_dir=bool(item.get("PSIsContainer"))))
    return out


async def _tier3_powershell(query: str, max_results: int) -> list[dict[str, Any]]:
    drives = await get_drives()
    accessible = [d for d in drives if d.wsl_accessible]
    if not accessible:
        return []
    per_drive = max(1, max_results // len(accessible))
    tasks = [_gci_one_drive(d.win_path, query, per_drive) for d in accessible]
    try:
        chunks = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        return []
    out: list[dict[str, Any]] = []
    for chunk in chunks:
        if isinstance(chunk, list):
            out.extend(chunk)
    return out[:max_results]


# ── Public API ────────────────────────────────────────────────────────────────

async def search_files(query: str, max_results: int = 20) -> ToolResult:
    """
    Search for files by name using 3 tiers in priority order.

    Returns ToolResult with data = {results, source, count} where each result
    is {path, name, is_dir, wsl_path, source}.
    """
    tiers = [
        (_tier1_everything, "everything"),
        (_tier2_winindex,   "winindex"),
        (_tier3_powershell, "powershell"),
    ]
    for tier_fn, label in tiers:
        results = await tier_fn(query, max_results)
        if results:
            return ToolResult.ok(
                f"Found {len(results)} file(s) via {label}",
                spoken=f"Found {len(results)} results",
                data={"results": results, "source": label, "count": len(results)},
            )

    msg = f"No files matching '{query}' found"
    return ToolResult.failure(
        msg,
        spoken=msg,
        error_code="NOT_FOUND",
        data={"results": [], "source": "none", "count": 0},
    )


async def find_file(name: str) -> ToolResult:
    """
    Find the first file matching name (substring match).
    Returns ToolResult with data = {path, name, is_dir, wsl_path, source}.
    """
    result = await search_files(name, max_results=1)
    if not result.success or not result.data.get("results"):
        msg = f"'{name}' not found"
        return ToolResult.failure(msg, spoken=msg, error_code="NOT_FOUND", data={})
    first = result.data["results"][0]
    return ToolResult.ok(
        first["path"],
        spoken=f"Found {first['name']} at {first['wsl_path']}",
        data=first,
    )
