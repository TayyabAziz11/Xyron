"""
Volume and brightness control for Windows/WSL2.

Volume:     NirCmd (primary) → Core Audio API via PowerShell (fallback).
Brightness: WMI WmiMonitorBrightnessMethods (laptop panels only).
"""
from __future__ import annotations

import asyncio
import os

from .ps_runner import ToolResult, run_ps

# ── NirCmd discovery ──────────────────────────────────────────────────────────

_NIRCMD_PATHS = [
    "/mnt/c/Program Files/nircmd/nircmd.exe",
    "/mnt/c/Windows/nircmd.exe",
    "/mnt/c/tools/nircmd.exe",
]
_NIRCMD: str | None = next((p for p in _NIRCMD_PATHS if os.path.isfile(p)), None)


# ── Core Audio COM preamble ───────────────────────────────────────────────────

def _core_audio_setup() -> str:
    """PS preamble: defines COM interfaces and obtains IAudioEndpointVolume."""
    return r"""Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    int N0();
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int dwClsCtx, int pActivationParams, out IAudioEndpointVolume aev);
}
[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
    int N0(); int N1(); int N2(); int N3();
    [PreserveSig] int SetMasterVolumeLevelScalar(float fLevel, Guid ctx);
    int N5();
    [PreserveSig] int GetMasterVolumeLevelScalar(out float pfLevel);
    int N7(); int N8(); int N9(); int N10();
    [PreserveSig] int GetMute(out bool pbMute);
    [PreserveSig] int SetMute(bool bMute, Guid ctx);
}
"@ -ErrorAction SilentlyContinue
$et = [Type]::GetTypeFromCLSID([Guid]"BCDE0395-E52F-467C-8E3D-C4579291692E")
$en = [Activator]::CreateInstance($et) -as [IMMDeviceEnumerator]
$dev = $null; [void]$en.GetDefaultAudioEndpoint(0, 1, [ref]$dev)
$g = [Guid]"5CDF2C82-841E-4546-9722-0CF74078229A"
$aev = $null; [void]$dev.Activate([ref]$g, 23, 0, [ref]$aev)"""


# ── NirCmd async runner ───────────────────────────────────────────────────────

async def _run_nircmd(*args: str) -> ToolResult:
    if not _NIRCMD:
        return ToolResult.failure("nircmd not found", error_code="NIRCMD_MISSING")
    try:
        proc = await asyncio.create_subprocess_exec(
            _NIRCMD, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ToolResult.failure("nircmd timed out", error_code="NIRCMD_TIMEOUT")
        if proc.returncode != 0:
            return ToolResult.failure(f"nircmd exit {proc.returncode}")
        return ToolResult.ok("ok")
    except Exception as exc:
        return ToolResult.failure(str(exc))


# ── Volume ────────────────────────────────────────────────────────────────────

async def get_volume() -> ToolResult:
    """
    Return current system volume level.
    data = {level: int (0–100), muted: bool}
    """
    script = (
        _core_audio_setup() + "\n"
        "$v = 0.0; [void]$aev.GetMasterVolumeLevelScalar([ref]$v)\n"
        "$m = $false; [void]$aev.GetMute([ref]$m)\n"
        "@{level=[int]([math]::Round($v*100)); muted=[bool]$m} | ConvertTo-Json -Compress"
    )
    result = await run_ps(script, timeout=8.0, parse_json=True)
    if not result.success:
        return result
    level = int(result.data.get("level", 0))
    muted = bool(result.data.get("muted", False))
    spoken = "Muted" if muted else f"Volume is {level} percent"
    return ToolResult.ok(
        f"Volume: {level}% (muted: {muted})",
        spoken=spoken,
        data={"level": level, "muted": muted},
    )


async def set_volume(level: int) -> ToolResult:
    """
    Set system volume. level: 0–100.
    Verifies actual level after setting (within 5%).
    """
    level = max(0, min(100, level))

    # NirCmd primary
    if _NIRCMD:
        r = await _run_nircmd("setsysvolume", str(int(level * 655.35)))
        if r.success:
            verify = await get_volume()
            if verify.success:
                actual = verify.data.get("level", level)
                if abs(actual - level) <= 5:
                    return ToolResult.ok(
                        f"Volume set to {actual}%",
                        spoken=f"Volume set to {actual} percent",
                        data={"level": actual},
                    )
            # nircmd ran but verification failed or off — fall through to PS

    # PS Core Audio fallback
    f_level = round(level / 100.0, 6)
    script = (
        _core_audio_setup() + "\n"
        f"[void]$aev.SetMasterVolumeLevelScalar([float]{f_level}, [Guid]::Empty); Write-Output 'ok'"
    )
    r = await run_ps(script, timeout=10.0)
    if not r.success:
        return ToolResult.failure(
            f"Failed to set volume: {r.message}",
            spoken="Could not set volume",
            error_code=r.error_code or "VOLUME_ERROR",
        )
    verify = await get_volume()
    actual = verify.data.get("level", level) if verify.success else level
    return ToolResult.ok(
        f"Volume set to {actual}%",
        spoken=f"Volume set to {actual} percent",
        data={"level": actual},
    )


async def mute() -> ToolResult:
    """Mute system audio."""
    if _NIRCMD:
        r = await _run_nircmd("mutesysvolume", "1")
        if r.success:
            return ToolResult.ok("Muted", spoken="Muted", data={"muted": True})
    script = _core_audio_setup() + "\n[void]$aev.SetMute($true, [Guid]::Empty); Write-Output 'ok'"
    r = await run_ps(script, timeout=8.0)
    if not r.success:
        return r
    return ToolResult.ok("Muted", spoken="Muted", data={"muted": True})


async def unmute() -> ToolResult:
    """Unmute system audio."""
    if _NIRCMD:
        r = await _run_nircmd("mutesysvolume", "0")
        if r.success:
            return ToolResult.ok("Unmuted", spoken="Unmuted", data={"muted": False})
    script = _core_audio_setup() + "\n[void]$aev.SetMute($false, [Guid]::Empty); Write-Output 'ok'"
    r = await run_ps(script, timeout=8.0)
    if not r.success:
        return r
    return ToolResult.ok("Unmuted", spoken="Unmuted", data={"muted": False})


async def volume_up(step: int = 10) -> ToolResult:
    """Increase volume by step percent (default 10)."""
    current = await get_volume()
    if not current.success:
        return current
    return await set_volume(min(100, current.data.get("level", 50) + step))


async def volume_down(step: int = 10) -> ToolResult:
    """Decrease volume by step percent (default 10)."""
    current = await get_volume()
    if not current.success:
        return current
    return await set_volume(max(0, current.data.get("level", 50) - step))


# ── Brightness ────────────────────────────────────────────────────────────────

async def get_brightness() -> ToolResult:
    """
    Return current display brightness (laptop panels only).
    data = {level: int (0–100)}
    """
    script = (
        "$b = (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness "
        "-ErrorAction SilentlyContinue | Select-Object -First 1).CurrentBrightness; "
        "if ($b -ne $null) { @{level=[int]$b} | ConvertTo-Json -Compress } "
        "else { Write-Error 'NOT_SUPPORTED' }"
    )
    result = await run_ps(script, timeout=8.0, parse_json=True)
    if not result.success:
        msg = "Brightness control not available on external monitors"
        return ToolResult.failure(msg, spoken=msg, error_code="BRIGHTNESS_NOT_SUPPORTED")
    level = int(result.data.get("level", 0))
    return ToolResult.ok(
        f"Brightness: {level}%",
        spoken=f"Brightness is {level} percent",
        data={"level": level},
    )


async def set_brightness(level: int) -> ToolResult:
    """
    Set display brightness (laptop panels only). level: 0–100.
    Returns a clear failure on external monitors.
    """
    level = max(0, min(100, level))
    script = (
        "$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        f"if ($m) {{ $m.WmiSetBrightness(1, {level}); Write-Output 'ok' }} "
        "else { Write-Error 'NOT_SUPPORTED' }"
    )
    result = await run_ps(script, timeout=8.0)
    if not result.success:
        msg = "Brightness control not available on external monitors"
        return ToolResult.failure(msg, spoken=msg, error_code="BRIGHTNESS_NOT_SUPPORTED")
    return ToolResult.ok(
        f"Brightness set to {level}%",
        spoken=f"Brightness set to {level} percent",
        data={"level": level},
    )
