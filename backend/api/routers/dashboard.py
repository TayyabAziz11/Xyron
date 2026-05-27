"""
Bundled dashboard state endpoint.

GET /api/v1/dashboard/state — returns health + cognition + environment +
metrics + commands + approvals + tasks + takeover + brain in a single call.

Frontend should poll this once every 5s instead of letting each component
poll its own endpoint independently.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


async def _safe(coro, fallback: Any = None) -> Any:
    """Run an async callable; return fallback on any error."""
    try:
        return await asyncio.wait_for(coro, timeout=2.0)
    except Exception:
        return fallback


@router.get("/state")
async def dashboard_state() -> dict:
    """
    Single-call bundle for all dashboard polling.

    Replaces separate polls of:
      /api/v1/health
      /api/v1/cognition/state
      /api/v1/environment/status
      /api/v1/system/metrics
      /api/v1/commands
      /api/v1/approvals
      /api/v1/tasks
      /api/v1/takeover/status
      /api/v1/brain/state
    """

    async def _health() -> dict:
        return {"status": "ok"}

    async def _cognition() -> dict:
        try:
            from cognition.cognitive_state import cognitive_state as _cs
            return {
                "attention":          _cs.attention_state,
                "mood_state":         _cs.mood_state,
                "mood_bias":          _cs.mood_bias,
                "last_user_emotion":  _cs.last_user_emotion,
                "emotion_intensity":  _cs.emotion_intensity,
                "active_goal":        _cs.active_goal,
                "current_task":       _cs.current_task,
                "context_summary":    _cs.context_summary,
                "active_ui_mode":     _cs.active_ui_mode,
                "turn_count":         _cs.turn_count,
                "last_updated":       _cs.last_updated,
                "code_mode":          getattr(_cs, "code_mode", False),
                "active_project":     getattr(_cs, "active_project", None),
                "active_file":        getattr(_cs, "active_file", None),
            }
        except Exception:
            return {}

    async def _environment() -> dict:
        try:
            from api.services.window_context import window_context as _wctx
            import psutil
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory().percent
            batt = psutil.sensors_battery()
            aw   = _wctx.get_active_window() or {}
            return {
                "cpu_percent":      cpu,
                "ram_percent":      ram,
                "battery_percent":  round(batt.percent, 1) if batt else None,
                "battery_charging": batt.power_plugged if batt else None,
                "active_window":    aw.get("title", "") if isinstance(aw, dict) else str(aw),
                "timestamp":        int(time.time()),
            }
        except Exception:
            return {}

    async def _commands() -> list:
        try:
            from api.services.command_service import command_service as _cs
            items = list(_cs.queue.values()) if hasattr(_cs, "queue") else []
            return [
                {
                    "id":     c.get("id"),
                    "text":   c.get("text"),
                    "status": c.get("status"),
                }
                for c in items[-20:]
            ]
        except Exception:
            return []

    async def _approvals() -> list:
        try:
            from api.services.approval_service import approval_service as _ap
            return _ap.list_pending()
        except Exception:
            return []

    async def _tasks() -> list:
        try:
            from api.services.task_service import task_service as _ts
            tasks = _ts.list_tasks() if hasattr(_ts, "list_tasks") else []
            return tasks[:50]
        except Exception:
            return []

    async def _takeover() -> dict:
        try:
            from cognition.cognitive_state import cognitive_state as _cs
            return {"active": getattr(_cs, "takeover_active", False)}
        except Exception:
            return {"active": False}

    async def _brain() -> dict:
        try:
            from brain.brain_state import brain_state as _bs
            return {
                "current_topic":  _bs.current_topic,
                "topic_history":  list(_bs.topic_history)[-5:],
                "emotion":        _bs.current_emotion,
            }
        except Exception:
            return {}

    (
        health, cognition, environment,
        commands, approvals, tasks, takeover, brain,
    ) = await asyncio.gather(
        _safe(_health(),    {}),
        _safe(_cognition(), {}),
        _safe(_environment(), {}),
        _safe(_commands(),  []),
        _safe(_approvals(), []),
        _safe(_tasks(),     []),
        _safe(_takeover(),  {"active": False}),
        _safe(_brain(),     {}),
    )

    return {
        "ts":          int(time.time() * 1000),
        "health":      health,
        "cognition":   cognition,
        "environment": environment,
        "commands":    commands,
        "approvals":   approvals,
        "tasks":       tasks,
        "takeover":    takeover,
        "brain":       brain,
    }
