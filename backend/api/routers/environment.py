"""Environment monitoring router — live system metrics and CPU stress detection."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Optional

import psutil
from fastapi import APIRouter
from pydantic import BaseModel

from ..services.cognitive_state import cognitive_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/environment", tags=["environment"])


class EnvironmentStatus(BaseModel):
    cpu_percent: float
    ram_percent: float
    battery_percent: Optional[float]
    battery_charging: Optional[bool]
    active_window: str
    timestamp: float


def _get_active_window() -> str:
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["wmctrl", "-l"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().splitlines()
            if lines:
                parts = lines[0].split(None, 3)
                if len(parts) >= 4:
                    return parts[3]
    except Exception:
        pass
    return "unknown"


@router.get("/status", response_model=EnvironmentStatus)
async def get_environment_status() -> EnvironmentStatus:
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory().percent

    battery = psutil.sensors_battery()
    battery_percent: Optional[float] = None
    battery_charging: Optional[bool] = None
    if battery is not None:
        battery_percent = round(battery.percent, 1)
        battery_charging = battery.power_plugged

    active_window = "unknown"
    try:
        active_window = _get_active_window()
    except Exception:
        pass

    return EnvironmentStatus(
        cpu_percent=round(cpu, 1),
        ram_percent=round(ram, 1),
        battery_percent=battery_percent,
        battery_charging=battery_charging,
        active_window=active_window,
        timestamp=time.time(),
    )


# ── Background CPU stress monitor ─────────────────────────────────────────────

_stop_event = threading.Event()
_cpu_stressed = False


def _monitor_loop() -> None:
    global _cpu_stressed
    while not _stop_event.wait(10):
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            if cpu > 85 and not _cpu_stressed:
                cognitive_state.update(active_ui_mode="stressed")
                _cpu_stressed = True
                logger.info("[EnvMonitor] CPU %.1f%% > 85 → cognitive_state=stressed", cpu)
            elif cpu < 75 and _cpu_stressed:
                cognitive_state.update(active_ui_mode="default")
                _cpu_stressed = False
                logger.info("[EnvMonitor] CPU %.1f%% < 75 → cognitive_state=default", cpu)
        except Exception as exc:
            logger.warning("[EnvMonitor] loop error: %s", exc)


threading.Thread(
    target=_monitor_loop, daemon=True, name="env-monitor",
).start()
