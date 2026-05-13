"""Environment monitoring router — live system metrics, CPU stress, and code editor detection."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import psutil
from fastapi import APIRouter
from pydantic import BaseModel

from ..services.cognitive_state import cognitive_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/environment", tags=["environment"])

CODE_EDITORS = [
    "code", "cursor", "vim", "nvim", "pycharm",
    "webstorm", "intellij", "sublime", "atom", "emacs",
]


class EnvironmentStatus(BaseModel):
    cpu_percent: float
    ram_percent: float
    battery_percent: Optional[float]
    battery_charging: Optional[bool]
    active_window: str
    timestamp: float


def _is_code_editor_active(active_window: str) -> bool:
    return any(editor in active_window.lower() for editor in CODE_EDITORS)


# ── VS Code workspace detection (non-blocking, cached) ────────────────────────

_workspace_cache: dict = {"project": None, "file": None, "ts": 0.0}
_WORKSPACE_TTL = 5.0  # seconds


def _detect_vscode_workspace() -> tuple[Optional[str], Optional[str]]:
    """Return (project_name, active_file) from VS Code storage.json, cached."""
    now = time.time()
    if now - _workspace_cache["ts"] < _WORKSPACE_TTL:
        return _workspace_cache["project"], _workspace_cache["file"]

    project: Optional[str] = None
    active_file: Optional[str] = None

    try:
        # VS Code stores recent workspaces in storage.json
        candidates = [
            Path.home() / ".config" / "Code" / "User" / "workspaceStorage",
            Path("/mnt/c/Users") if Path("/mnt/c/Users").exists() else None,
        ]
        storage_json = Path.home() / ".config" / "Code" / "storage.json"
        if storage_json.exists():
            data = json.loads(storage_json.read_text())
            recently_opened = data.get("openedPathsList", {}).get("workspaces3", [])
            if recently_opened:
                raw = recently_opened[0]
                if isinstance(raw, str):
                    project = Path(raw.replace("file://", "")).name or raw
                elif isinstance(raw, dict):
                    folder = raw.get("folderUri", raw.get("workspace", ""))
                    project = Path(folder.replace("file://", "")).name or folder
    except Exception:
        pass

    try:
        # Try xdotool window title to extract filename (e.g. "main.py — myproject")
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0:
            title = result.stdout.strip()
            if " — " in title or " - " in title:
                sep = " — " if " — " in title else " - "
                parts = title.split(sep)
                if len(parts) >= 2:
                    candidate_file = parts[0].strip()
                    if "." in candidate_file and not candidate_file.startswith("●"):
                        active_file = candidate_file.lstrip("● ").strip()
                    if not project and len(parts) >= 2:
                        project = parts[1].strip()
    except Exception:
        pass

    _workspace_cache["project"] = project
    _workspace_cache["file"] = active_file
    _workspace_cache["ts"] = now
    return project, active_file


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
    while not _stop_event.wait(3):
        try:
            cpu = psutil.cpu_percent(interval=0.5)

            # CPU stress detection
            if cpu > 85 and not _cpu_stressed:
                cognitive_state.update(active_ui_mode="stressed")
                _cpu_stressed = True
                logger.info("[EnvMonitor] CPU %.1f%% > 85 → stressed", cpu)
            elif cpu < 75 and _cpu_stressed:
                cognitive_state.update(active_ui_mode="default")
                _cpu_stressed = False
                logger.info("[EnvMonitor] CPU %.1f%% < 75 → default", cpu)

            # Code editor detection
            active_window = "unknown"
            try:
                active_window = _get_active_window()
            except Exception:
                pass

            in_code_editor = _is_code_editor_active(active_window)
            if in_code_editor != cognitive_state.code_mode:
                if in_code_editor:
                    project, active_file = _detect_vscode_workspace()
                    cognitive_state.update(
                        code_mode=True,
                        active_ui_mode="focus",
                        active_project=project,
                        active_file=active_file,
                    )
                    logger.info("[EnvMonitor] Code editor active → code_mode=True project=%s", project)
                else:
                    cognitive_state.update(code_mode=False)
                    logger.info("[EnvMonitor] Code editor inactive → code_mode=False")
            elif in_code_editor:
                # Refresh workspace info each loop while in editor
                project, active_file = _detect_vscode_workspace()
                if project != cognitive_state.active_project or active_file != cognitive_state.active_file:
                    cognitive_state.update(active_project=project, active_file=active_file)

        except Exception as exc:
            logger.warning("[EnvMonitor] loop error: %s", exc)


threading.Thread(
    target=_monitor_loop, daemon=True, name="env-monitor",
).start()
