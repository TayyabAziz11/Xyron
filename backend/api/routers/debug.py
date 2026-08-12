"""
Debug inspection API for Xyron voice pipeline.

Endpoints:
  GET  /api/v1/debug/last-command          last trace record
  GET  /api/v1/debug/session               current session state + uptime
  GET  /api/v1/debug/replay/{trace_id}     full trace by ID (last 100 stored)
  GET  /api/v1/debug/audio                 microphone / audio pipeline state
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])

# Populated at runtime by voice_ws via update_session_state().
_session_state: dict[str, Any] = {
    "session_active":   False,
    "wake_connected":   False,
    "voice_connected":  False,
    "current_state":    "idle",
    "session_start_ts": None,
}

# Populated at runtime by voice_ws via update_audio_state().
_audio_state: dict[str, Any] = {
    "frontend_connected":    False,
    "voice_ws_connected":    False,
    "mic_active":            False,
    "audio_chunks_received": 0,
    "last_audio_timestamp":  None,
}


def update_session_state(**kwargs: Any) -> None:
    """Called by voice_ws lifecycle events to keep session state current."""
    _session_state.update(kwargs)


def is_voice_session_connected() -> bool:
    """
    True for the full lifetime of a connected voice session (wake or manual),
    not just mid-turn. voice_activity.is_active() only covers the narrower
    per-turn processing window; background warmup work (Problem 4) needs to
    stay paused for the whole session, including idle "Listening" gaps
    between turns where a wake word or fresh command could land any moment.
    """
    return bool(_session_state.get("voice_connected"))


def update_audio_state(**kwargs: Any) -> None:
    """Called by voice_ws binary-frame handler to track mic chunk flow."""
    _audio_state.update(kwargs)


@router.get("/last-command")
async def last_command() -> dict[str, Any]:
    """Return the most recently processed command trace."""
    from api.services.tracer import trace_store
    rec = trace_store.last()
    if not rec:
        return {"status": "no_commands_yet"}
    return rec.to_dict()


@router.get("/session")
async def session_status() -> dict[str, Any]:
    """Return current session health state."""
    from api.services.tracer import trace_store
    uptime_s = None
    if _session_state.get("session_start_ts"):
        uptime_s = round(time.time() - _session_state["session_start_ts"], 1)
    return {
        **_session_state,
        "uptime_s":         uptime_s,
        "current_trace_id": trace_store.current_trace_id(),
    }


@router.get("/replay/{trace_id}")
async def replay_trace(trace_id: str) -> dict[str, Any]:
    """Return full execution history for a stored trace ID."""
    from api.services.tracer import trace_store
    rec = trace_store.get(trace_id)
    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"Trace {trace_id!r} not found (last 100 traces kept)",
        )
    return rec.to_dict()


@router.get("/audio")
async def audio_debug() -> dict[str, Any]:
    """Return microphone acquisition and audio pipeline state."""
    last_ts = _audio_state.get("last_audio_timestamp")
    last_ago_s = round(time.time() - last_ts, 1) if last_ts else None
    return {
        **_audio_state,
        "last_audio_ago_s": last_ago_s,
    }


@router.get("/operator")
async def operator_status() -> dict[str, Any]:
    """Return operator mode status, registered skills, and last execution info."""
    try:
        from operator_mode.operator_engine import get_debug_info
        return get_debug_info()
    except Exception as exc:
        return {
            "enabled": False,
            "error": str(exc),
            "registered_skills": [],
            "last_operator_trace": "",
            "last_fallback_reason": "",
        }


@router.get("/performance")
async def performance_stats() -> dict[str, Any]:
    """Return last 20 voice command performance records with per-stage timings."""
    try:
        from api.services.perf_budget import perf_budget as _pb
        return {"records": _pb.last_n(20), "count": len(_pb.last_n(20))}
    except Exception as exc:
        return {"error": str(exc), "records": []}
