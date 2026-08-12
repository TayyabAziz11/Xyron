"""
Centralized Chrome DevTools Protocol (CDP) configuration (Phase 4.11.1).

Single source of truth for every port/path/timeout involved in the
Windows-Chrome-via-CDP bridge, replacing the values that used to be
hardcoded/duplicated across `browser_workspace.py`.

Root cause this module exists to prevent recurring (confirmed live,
Phase 4.11.1): the Windows `netsh portproxy` bridge and Chrome's own
DevTools listener were both configured to use port 9222. Windows only
lets one listener own a port number, portproxy (a persistent OS service)
wins the race, and Chrome silently falls back to binding `[::1]:9222`
(IPv6 loopback only) — a socket the IPv4 bridge can never reach. Fix:
Chrome always binds its own local port (9222); the bridge listens
externally on a *different* port (9223, or the next free port up to
9230) and forwards to Chrome's local port. The two must never share a
port number again — hence one central place that owns both values.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("api.services.cdp_config")

CDP_CHROME_LOCAL_PORT = int(os.environ.get("XYRON_CDP_LOCAL_PORT", "9222"))
CDP_BRIDGE_PORT_PREFERRED = int(os.environ.get("XYRON_CDP_BRIDGE_PORT", "9223"))
CDP_PORT_RANGE = list(range(9223, 9231))  # 9223–9230, preferred port first if present

CDP_CONNECT_TIMEOUT_MS = 8_000
CDP_RETRY_COUNT = 4
CDP_RETRY_DELAY_S = 2.0

FIREWALL_RULE_NAME = "XyronCDPBridge"

_STATE_DIR = Path.home() / ".xyron"
_STATE_PATH = _STATE_DIR / "cdp_bridge_state.json"

_POWERSHELL_CANDIDATES = [
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
]
_WIN_CHROME_PATHS = [
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]

_FALLBACK_GATEWAY_IP = "172.25.224.1"


def _default_profile_dir() -> str:
    override = os.environ.get("XYRON_CDP_PROFILE_DIR")
    if override:
        return override
    return r"C:\XyronBrowserProfile"


def powershell_exe() -> Optional[str]:
    return next((p for p in _POWERSHELL_CANDIDATES if Path(p).exists()), None)


def find_windows_chrome_exe() -> Optional[str]:
    for p in _WIN_CHROME_PATHS:
        if Path(p).exists():
            return p.replace("/mnt/c/", "C:/").replace("/", "\\")
    return None


def get_wsl_gateway_ip() -> str:
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=3,
        )
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return _FALLBACK_GATEWAY_IP


@dataclass
class CDPConfig:
    chrome_local_port: int = CDP_CHROME_LOCAL_PORT
    bridge_port: int = CDP_BRIDGE_PORT_PREFERRED
    windows_host: str = ""
    profile_dir: str = field(default_factory=_default_profile_dir)
    connect_timeout_ms: int = CDP_CONNECT_TIMEOUT_MS
    retry_count: int = CDP_RETRY_COUNT
    retry_delay_s: float = CDP_RETRY_DELAY_S
    port_range: list = field(default_factory=lambda: list(CDP_PORT_RANGE))

    @property
    def endpoint(self) -> str:
        return f"http://{self.windows_host}:{self.bridge_port}"


_config: Optional[CDPConfig] = None
_config_lock = asyncio.Lock()


def _load_persisted_bridge_port() -> Optional[int]:
    try:
        if _STATE_PATH.exists():
            data = json.loads(_STATE_PATH.read_text())
            port = data.get("bridge_port")
            if isinstance(port, int):
                return port
    except Exception:
        pass
    return None


def persist_bridge_port(port: int) -> None:
    """Survives backend restarts — Part 3/10 requirement that a
    successful repair never needs to happen twice."""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps({"bridge_port": port, "updated_at": time.time()}))
    except Exception as exc:
        logger.warning("[CDP_CONFIG] could not persist bridge_port=%d error=%r", port, exc)
    if _config is not None:
        _config.bridge_port = port


def get_config() -> CDPConfig:
    """Synchronous accessor — safe to call repeatedly, only does real
    work (host discovery) once per process."""
    global _config
    if _config is not None:
        return _config

    cfg = CDPConfig()
    persisted_port = _load_persisted_bridge_port()
    if persisted_port:
        cfg.bridge_port = persisted_port

    cfg.windows_host = get_wsl_gateway_ip()
    logger.info("[CDP_WINDOWS_HOST_DISCOVERED] host=%s", cfg.windows_host)
    logger.info("[CDP_LOCAL_PORT] port=%d", cfg.chrome_local_port)
    logger.info("[CDP_BRIDGE_PORT] port=%d source=%s", cfg.bridge_port,
                "persisted" if persisted_port else "default")
    logger.info(
        "[CDP_CONFIG_LOADED] chrome_local_port=%d bridge_port=%d windows_host=%s profile_dir=%s",
        cfg.chrome_local_port, cfg.bridge_port, cfg.windows_host, cfg.profile_dir,
    )
    _config = cfg
    return cfg


def reset_config_cache() -> None:
    """Test/repair hook — forces the next get_config() to re-discover."""
    global _config
    _config = None
