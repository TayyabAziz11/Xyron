"""
CDP Environment Doctor (Phase 4.11.1).

Diagnoses and repairs the Windows-side Chrome DevTools Protocol bridge
before any controlled browser workflow runs. See `cdp_config.py` for the
port-collision root cause this exists to detect and fix.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import cdp_config

logger = logging.getLogger("api.services.cdp_environment_doctor")

_REPO_ROOT_WIN = "E:\\Xyron"  # /mnt/e/Xyron on the WSL2 side
_REPAIR_SCRIPT_WIN = _REPO_ROOT_WIN + r"\scripts\windows\repair_cdp_bridge.ps1"
_REPAIR_RESULT_PATH_WIN = r"C:\XyronBrowserProfile\cdp_repair_result.json"
_REPAIR_RESULT_PATH_WSL = Path("/mnt/c/XyronBrowserProfile/cdp_repair_result.json")
_ELEVATION_TIMEOUT_S = 90.0


@dataclass
class CDPDiagnosis:
    timestamp: float = field(default_factory=time.time)
    chrome_found: bool = False
    chrome_path: Optional[str] = None
    xyron_chrome_pids: list = field(default_factory=list)
    local_port_listening: bool = False
    bridge_port_listening: bool = False
    portproxy_rules_raw: str = ""
    collision_detected: bool = False
    collision_port: Optional[int] = None
    firewall_rule_present: bool = False
    firewall_rule_port: Optional[int] = None
    wsl_can_reach_bridge: bool = False
    healthy: bool = False
    repair_required: bool = False
    repair_reasons: list = field(default_factory=list)


_INSPECT_PS1 = r"""
$ErrorActionPreference = 'SilentlyContinue'
$chromePath = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$chromeFound = Test-Path $chromePath
$xyronPids = @(Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
    Where-Object { $_.CommandLine -like '*{profile_dir}*' } |
    Select-Object -ExpandProperty ProcessId)
$localListening = [bool](Get-NetTCPConnection -LocalPort {chrome_local_port} -State Listen -ErrorAction SilentlyContinue)
$bridgeListening = [bool](Get-NetTCPConnection -LocalPort {bridge_port} -State Listen -ErrorAction SilentlyContinue)
$portproxyRaw = (netsh interface portproxy show v4tov4 | Out-String)
$fw = Get-NetFirewallRule -DisplayName '{firewall_rule}' -ErrorAction SilentlyContinue
$fwPresent = [bool]$fw
$fwPort = $null
if ($fw) {
    $portFilter = $fw | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
    if ($portFilter) { $fwPort = $portFilter.LocalPort }
}
$result = [ordered]@{
    chrome_found      = $chromeFound
    chrome_path       = $chromePath
    xyron_pids        = $xyronPids
    local_listening   = $localListening
    bridge_listening  = $bridgeListening
    portproxy_raw     = $portproxyRaw
    firewall_present  = $fwPresent
    firewall_port     = $fwPort
}
$result | ConvertTo-Json -Compress -Depth 4
"""


def _parse_portproxy_collision(raw: str, chrome_local_port: int) -> tuple[bool, Optional[int]]:
    """A collision exists if any v4tov4 rule LISTENS on chrome_local_port
    (regardless of listen address) — that's the exact configuration that
    blocks Chrome's own bind()."""
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] in ("0.0.0.0", "127.0.0.1", "*"):
            try:
                listen_port = int(parts[1])
            except ValueError:
                continue
            if listen_port == chrome_local_port:
                return True, listen_port
    return False, None


async def _run_powershell(script: str, timeout_s: float = 15.0) -> str:
    ps = cdp_config.powershell_exe()
    if not ps:
        raise RuntimeError("powershell_not_found")
    proc = await asyncio.create_subprocess_exec(
        "/init", ps, "-NoProfile", "-Command", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return stdout.decode(errors="ignore")


async def _check_wsl_can_reach_bridge(host: str, port: int, timeout_s: float = 4.0) -> bool:
    url = f"http://{host}:{port}/json/version"

    def _fetch() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as resp:
                return resp.status == 200
        except Exception:
            return False

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


class CDPEnvironmentDoctor:
    async def diagnose(self) -> CDPDiagnosis:
        cfg = cdp_config.get_config()
        logger.info("[CDP_DOCTOR_START] chrome_local_port=%d bridge_port=%d",
                    cfg.chrome_local_port, cfg.bridge_port)
        diag = CDPDiagnosis()

        script = (
            _INSPECT_PS1
            .replace("{profile_dir}", cfg.profile_dir.replace("\\", "\\\\"))
            .replace("{chrome_local_port}", str(cfg.chrome_local_port))
            .replace("{bridge_port}", str(cfg.bridge_port))
            .replace("{firewall_rule}", cdp_config.FIREWALL_RULE_NAME)
        )
        try:
            raw_out = await _run_powershell(script, timeout_s=15.0)
            data = json.loads(raw_out.strip() or "{}")
        except Exception as exc:
            logger.warning("[CDP_DOCTOR_INSPECT_FAILED] error=%r", str(exc)[:200])
            data = {}

        diag.chrome_found = bool(data.get("chrome_found"))
        diag.chrome_path = data.get("chrome_path")
        xyron_pids = data.get("xyron_pids") or []
        diag.xyron_chrome_pids = xyron_pids if isinstance(xyron_pids, list) else [xyron_pids]
        diag.local_port_listening = bool(data.get("local_listening"))
        diag.bridge_port_listening = bool(data.get("bridge_listening"))
        diag.portproxy_rules_raw = data.get("portproxy_raw", "") or ""
        diag.firewall_rule_present = bool(data.get("firewall_present"))
        fw_port = data.get("firewall_port")
        diag.firewall_rule_port = int(fw_port) if fw_port else None

        logger.info("[CDP_CHROME_FOUND] found=%s path=%s", diag.chrome_found, diag.chrome_path)
        logger.info("[CDP_PORTPROXY_STATE] rules=%r", diag.portproxy_rules_raw.strip()[:500])
        logger.info("[CDP_FIREWALL_STATE] present=%s port=%s",
                    diag.firewall_rule_present, diag.firewall_rule_port)

        diag.collision_detected, diag.collision_port = _parse_portproxy_collision(
            diag.portproxy_rules_raw, cfg.chrome_local_port,
        )
        if diag.collision_detected:
            logger.warning("[CDP_PORT_COLLISION_FOUND] port=%d", diag.collision_port)

        diag.wsl_can_reach_bridge = await _check_wsl_can_reach_bridge(cfg.windows_host, cfg.bridge_port)
        logger.info("[CDP_BRIDGE_TEST] host=%s port=%d reachable=%s",
                    cfg.windows_host, cfg.bridge_port, diag.wsl_can_reach_bridge)

        reasons = []
        if not diag.chrome_found:
            reasons.append("chrome_not_installed")
        if diag.collision_detected:
            reasons.append(f"portproxy_collides_on_port_{diag.collision_port}")
        if not diag.firewall_rule_present or diag.firewall_rule_port != cfg.bridge_port:
            reasons.append("firewall_rule_missing_or_wrong_port")
        if not diag.wsl_can_reach_bridge and diag.xyron_chrome_pids:
            # Only a repair signal if Chrome is actually running and still
            # unreachable — if Chrome simply isn't running yet, that's a
            # normal "not started" state, not a bridge fault.
            reasons.append("bridge_unreachable_with_chrome_running")

        diag.repair_reasons = reasons
        diag.repair_required = bool(reasons) and diag.chrome_found
        diag.healthy = diag.chrome_found and not diag.collision_detected and (
            diag.firewall_rule_present and diag.firewall_rule_port == cfg.bridge_port
        )

        if diag.healthy:
            logger.info("[CDP_DOCTOR_HEALTHY]")
        elif diag.repair_required:
            logger.warning("[CDP_DOCTOR_REPAIR_REQUIRED] reasons=%s", reasons)
        return diag

    async def repair(self, diag: Optional[CDPDiagnosis] = None) -> dict:
        """Runs the elevated repair script (one UAC prompt) and reports
        the machine-readable result. Never crashes on denial/timeout —
        returns a structured failure instead."""
        cfg = cdp_config.get_config()
        logger.info("[CDP_REPAIR_REQUESTED] old_port=%d preferred_port=%d",
                    cfg.chrome_local_port, cfg.bridge_port)

        ps = cdp_config.powershell_exe()
        if not ps:
            logger.error("[CDP_REPAIR_FAILED] reason=powershell_not_found")
            return {"success": False, "errors": ["powershell_not_found"]}

        try:
            if _REPAIR_RESULT_PATH_WSL.exists():
                _REPAIR_RESULT_PATH_WSL.unlink()
        except Exception:
            pass

        inner_args = (
            f"-NoProfile -ExecutionPolicy Bypass -File '{_REPAIR_SCRIPT_WIN}' "
            f"-OldPort {cfg.chrome_local_port} -ChromeLocalPort {cfg.chrome_local_port} "
            f"-PreferredPort {cfg.bridge_port} -ResultPath '{_REPAIR_RESULT_PATH_WIN}'"
        )
        # `-FilePath` here is for the *inner* Start-Process, which is
        # resolved by the already-native Windows PowerShell process — it
        # must be a Windows-style reference ("powershell.exe", resolved via
        # PATH), never the WSL mount path (`ps`, `/mnt/c/...`) used for the
        # *outer* `/init` invocation below. Reusing `ps` here was the exact
        # bug that made the first live repair attempt fail silently
        # (Start-Process couldn't resolve a `/mnt/c/...` path and the
        # elevated process never started, producing no result file).
        elevate_script = (
            f"Start-Process -FilePath 'powershell.exe' -ArgumentList \"{inner_args}\" "
            f"-Verb RunAs -Wait"
        )

        logger.info("[CDP_UAC_PROMPT_REQUESTED]")
        logger.info("[CDP_REPAIR_SCRIPT_STARTED] script=%s", _REPAIR_SCRIPT_WIN)
        try:
            proc = await asyncio.create_subprocess_exec(
                "/init", ps, "-NoProfile", "-Command", elevate_script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=_ELEVATION_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("[CDP_REPAIR_FAILED] reason=elevation_timeout_or_denied")
            return {"success": False, "errors": ["elevation_timeout_or_denied"]}
        except Exception as exc:
            logger.warning("[CDP_REPAIR_FAILED] reason=%r", str(exc)[:200])
            return {"success": False, "errors": [str(exc)[:200]]}

        result = await self._read_repair_result()
        if result is None:
            logger.warning("[CDP_REPAIR_FAILED] reason=no_result_file_denied_or_failed")
            return {"success": False, "errors": ["no_result_file_produced"]}

        if result.get("success"):
            new_port = result.get("bridge_port")
            if new_port:
                cdp_config.persist_bridge_port(int(new_port))
            logger.info("[CDP_PORTPROXY_REPAIRED] bridge_port=%s", new_port)
            logger.info("[CDP_FIREWALL_REPAIRED] bridge_port=%s", new_port)
            logger.info("[CDP_REPAIR_SUCCESS] bridge_port=%s", new_port)
        else:
            logger.warning("[CDP_REPAIR_FAILED] errors=%s", result.get("errors"))
        return result

    async def _read_repair_result(self, attempts: int = 5, delay_s: float = 1.0) -> Optional[dict]:
        for _ in range(attempts):
            if _REPAIR_RESULT_PATH_WSL.exists():
                try:
                    return json.loads(_REPAIR_RESULT_PATH_WSL.read_text())
                except Exception:
                    pass
            await asyncio.sleep(delay_s)
        return None


doctor = CDPEnvironmentDoctor()
