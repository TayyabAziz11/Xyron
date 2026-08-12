"""
Performance budget system — records per-command timing and emits warnings.

Budgets (ms):
    simple command total  < 2000
    app open total        < 3000
    folder open total     < 3000
    youtube search total  < 5000
    stt                   < 1500
    tts                   < 2000
    router                < 200
    normalize             < 50

Usage:
    from api.services.perf_budget import perf_budget
    rec = perf_budget.start("open_application")
    rec.set("stt", 820)
    rec.set("router", 12)
    rec.set("tool", 350)
    rec.set("tts", 980)
    perf_budget.finish(rec)

GET /api/v1/debug/performance  → last 20 records
"""
from __future__ import annotations

import collections
import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

logger = logging.getLogger(__name__)

# Per-stage budgets in milliseconds
_STAGE_BUDGETS: Dict[str, float] = {
    "stt":       1500,
    "normalize": 50,
    "router":    200,
    "tool":      2000,
    "tts":       2000,
}

# Per-command-type total budgets in milliseconds
_TOTAL_BUDGETS: Dict[str, float] = {
    "simple":    2000,
    "app_open":  3000,
    "folder_open": 3000,
    "youtube":   5000,
    "default":   4000,
}


def _classify_command(tool_name: str) -> str:
    if not tool_name:
        return "default"
    t = tool_name.lower()
    if "youtube" in t or "search_youtube" in t:
        return "youtube"
    if "open_application" in t or "smart_open" in t:
        return "app_open"
    if "open_directory" in t or "open_drive" in t:
        return "folder_open"
    if t in ("volume_control", "get_volume", "mute_unmute", "get_battery_status",
             "take_screenshot", "local_clock_time", "local_clock_date",
             "get_live_system_metrics"):
        return "simple"
    return "default"


@dataclass
class PerfRecord:
    command_type: str
    tool_name: str
    transcript: str
    start_t: float = field(default_factory=time.monotonic)
    stages: Dict[str, float] = field(default_factory=dict)
    total_ms: float = 0.0
    warnings: list = field(default_factory=list)

    def set(self, stage: str, ms: float) -> None:
        self.stages[stage] = ms
        budget = _STAGE_BUDGETS.get(stage)
        if budget and ms > budget:
            msg = f"stage={stage} ms={ms:.0f} budget={budget:.0f}"
            self.warnings.append(msg)
            logger.warning("[SLOW_STAGE_WARNING] %s", msg)

    def to_dict(self) -> dict:
        return {
            "tool_name":    self.tool_name,
            "command_type": self.command_type,
            "transcript":   self.transcript[:80],
            "stages_ms":    {k: round(v) for k, v in self.stages.items()},
            "total_ms":     round(self.total_ms),
            "warnings":     self.warnings,
        }


class PerfBudget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Deque[PerfRecord] = collections.deque(maxlen=20)

    def start(self, tool_name: str = "", transcript: str = "") -> PerfRecord:
        ctype = _classify_command(tool_name)
        return PerfRecord(command_type=ctype, tool_name=tool_name, transcript=transcript)

    def finish(self, rec: PerfRecord, override_tool: str = "") -> None:
        if override_tool:
            rec.tool_name    = override_tool
            rec.command_type = _classify_command(override_tool)
        rec.total_ms = (time.monotonic() - rec.start_t) * 1000
        budget = _TOTAL_BUDGETS.get(rec.command_type, _TOTAL_BUDGETS["default"])
        if rec.total_ms > budget:
            logger.warning(
                "[SLOW_COMMAND_WARNING] tool=%s total=%.0f budget=%.0f stages=%s",
                rec.tool_name, rec.total_ms, budget,
                {k: round(v) for k, v in rec.stages.items()},
            )
        else:
            logger.info(
                "[PERF_BUDGET_OK] tool=%s total=%.0fms budget=%.0fms",
                rec.tool_name, rec.total_ms, budget,
            )
        with self._lock:
            self._records.append(rec)

    def last_n(self, n: int = 20) -> list:
        with self._lock:
            return [r.to_dict() for r in list(self._records)[-n:]]


perf_budget = PerfBudget()
