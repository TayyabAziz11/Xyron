"""
Drive detection for WSL2 — authoritative source for all drive-aware tools.

Strategy:
  1. Win32_LogicalDisk WMI (via run_ps) — authoritative Windows drive list
  2. Cross-check /mnt/<letter> in WSL — marks dead mounts wsl_accessible=False
  3. 30-second cache (asyncio.Lock) — avoids repeated 800ms PS cold-starts

Dead-mount problem: WSL keeps /mnt/e even after the E volume is removed.
Checking /mnt/ alone returns stale entries; WMI is always current.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from .ps_runner import ToolResult, run_ps

# ── Constants ─────────────────────────────────────────────────────────────────

_DRIVE_TYPE_MAP: dict[int, str] = {
    0: "Unknown",
    1: "No Root Directory",
    2: "Removable",
    3: "Local Fixed",
    4: "Network",
    5: "CD/DVD",
    6: "RAM Disk",
}

_WMI_SCRIPT = (
    "Get-WmiObject Win32_LogicalDisk "
    "| Select-Object DeviceID,VolumeName,FreeSpace,Size,DriveType "
    "| ConvertTo-Json -Compress"
)

_CACHE_TTL = 30.0  # seconds


# ── DriveInfo ─────────────────────────────────────────────────────────────────

@dataclass
class DriveInfo:
    letter:         str    # "C:"
    win_path:       str    # "C:\\"
    wsl_path:       str    # "/mnt/c"
    label:          str    # volume label, "" if none
    free_gb:        float
    total_gb:       float
    drive_type:     str    # "Local Fixed", "Removable", etc.
    wsl_accessible: bool


# ── Internal cache ────────────────────────────────────────────────────────────

import asyncio as _asyncio

_cache_lock = _asyncio.Lock()
_cached_drives: list[DriveInfo] = []
_cache_expires: float = 0.0


def _can_read(path: str) -> bool:
    """Return True if path is a directory with at least one entry (not a dead WSL mount)."""
    try:
        return os.path.isdir(path) and bool(os.listdir(path))
    except OSError:
        return False


def _parse_drives(raw: object) -> list[DriveInfo]:
    """
    Convert WMI JSON output to DriveInfo list.
    WMI returns a dict (single drive) or list (multiple) — normalise both.
    """
    if isinstance(raw, dict):
        rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        return []

    result: list[DriveInfo] = []
    for row in rows:
        device_id: str = row.get("DeviceID") or ""
        if not device_id:
            continue

        letter = device_id.upper().rstrip("\\").rstrip("/")  # "C:"
        if not letter.endswith(":"):
            continue

        drive_letter_char = letter[0].lower()               # "c"
        wsl_path = f"/mnt/{drive_letter_char}"
        win_path = f"{letter}\\"

        size_bytes  = row.get("Size")  or 0
        free_bytes  = row.get("FreeSpace") or 0
        label       = row.get("VolumeName") or ""
        drive_type_id = int(row.get("DriveType") or 0)
        drive_type  = _DRIVE_TYPE_MAP.get(drive_type_id, "Unknown")

        # Removable drives with no media report null Size/FreeSpace
        total_gb = round(size_bytes / 1_073_741_824, 2) if size_bytes else 0.0
        free_gb  = round(free_bytes / 1_073_741_824, 2) if free_bytes else 0.0

        # WSL accessible = mount exists AND has at least one entry
        wsl_accessible = _can_read(wsl_path)

        result.append(DriveInfo(
            letter=letter,
            win_path=win_path,
            wsl_path=wsl_path,
            label=label,
            free_gb=free_gb,
            total_gb=total_gb,
            drive_type=drive_type,
            wsl_accessible=wsl_accessible,
        ))

    return result


# ── Public API ────────────────────────────────────────────────────────────────

async def get_drives(force: bool = False) -> list[DriveInfo]:
    """
    Return all Windows drives with WSL accessibility status.

    Results are cached for 30 seconds.  Pass force=True to bypass the cache
    (e.g., after a USB is inserted or a network drive is mapped).
    """
    global _cached_drives, _cache_expires

    async with _cache_lock:
        now = time.monotonic()
        if not force and now < _cache_expires and _cached_drives:
            return list(_cached_drives)

        result = await run_ps(_WMI_SCRIPT, timeout=10.0, parse_json=True)
        if not result.success:
            # Return stale cache on failure rather than raising
            return list(_cached_drives)

        raw = result.data.get("result", result.data)
        _cached_drives = _parse_drives(raw)
        _cache_expires = time.monotonic() + _CACHE_TTL
        return list(_cached_drives)


async def drive_exists(letter: str) -> tuple[bool, Optional[DriveInfo]]:
    """
    Check whether a drive letter exists and is accessible.

    Args:
        letter: Drive letter with or without colon, any case ("C", "c:", "C:").

    Returns:
        (True, DriveInfo) if found, (False, None) otherwise.
    """
    normalised = letter.upper().rstrip(":") + ":"  # → "C:"
    drives = await get_drives()
    for d in drives:
        if d.letter == normalised:
            return True, d
    return False, None


async def get_drive_info(letter: str) -> ToolResult:
    """
    Return drive details as a ToolResult.

    On failure, the message names the available drives so the caller can
    surface a helpful voice response:
      "D: not available. Available: C:, E:"
    """
    normalised = letter.upper().rstrip(":") + ":"
    drives = await get_drives()

    for d in drives:
        if d.letter == normalised:
            spoken = (
                f"{d.letter} has {d.free_gb} GB free"
                if d.wsl_accessible
                else f"{d.letter} is not accessible from WSL"
            )
            return ToolResult.ok(
                f"{d.letter} ({d.drive_type}) — {d.free_gb}/{d.total_gb} GB free",
                spoken=spoken,
                data={
                    "letter":         d.letter,
                    "win_path":       d.win_path,
                    "wsl_path":       d.wsl_path,
                    "label":          d.label,
                    "free_gb":        d.free_gb,
                    "total_gb":       d.total_gb,
                    "drive_type":     d.drive_type,
                    "wsl_accessible": d.wsl_accessible,
                },
            )

    available = ", ".join(d.letter for d in drives)
    msg = f"{normalised} not available. Available: {available}"
    return ToolResult.failure(
        msg,
        spoken=msg,
        error_code="DRIVE_NOT_FOUND",
        data={"requested": normalised, "available": [d.letter for d in drives]},
    )
