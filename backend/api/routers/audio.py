"""Audio health endpoints."""
from __future__ import annotations

import logging
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def audio_health(recover: bool = False) -> dict:
    """
    Check WSLg / PulseAudio health.

    Pass ?recover=true to attempt soft recovery before reporting.

    Returns:
        ok                  — true if mic + speaker + daemon are all reachable
        pulse_server_exists — /mnt/wslg/PulseServer socket is present
        sources_count       — number of PulseAudio input sources (microphones)
        sinks_count         — number of PulseAudio output sinks (speakers)
        recovery_available  — soft recovery can be tried (socket exists)
        suggested_action    — human-readable fix if ok=false, else null
    """
    from ..services.audio_health_service import audio_health_service as _ahs
    try:
        if recover:
            result = _ahs.recover()
        else:
            result = _ahs.check()
        return {
            "ok":                   result.ok,
            "pulse_server_path":    result.pulse_server_path,
            "pulse_server_exists":  result.pulse_server_exists,
            "pulse_server_env":     result.pulse_server_env,
            "pulse_env_match":      result.pulse_env_match,
            "pactl_available":      result.pactl_available,
            "pactl_connected":      result.pactl_connected,
            "sources_count":        result.sources_count,
            "sinks_count":          result.sinks_count,
            "recovery_available":   result.recovery_available,
            "recovery_attempted":   result.recovery_attempted,
            "suggested_action":     result.suggested_action,
            "detail":               result.detail,
        }
    except Exception as exc:
        logger.exception("[AUDIO_HEALTH_FAIL] endpoint error: %s", exc)
        return {
            "ok":                  False,
            "pulse_server_exists": False,
            "sources_count":       0,
            "sinks_count":         0,
            "recovery_available":  False,
            "suggested_action":    "Audio health check failed — restart the backend.",
            "detail":              str(exc),
        }


@router.post("/recover")
async def audio_recover() -> dict:
    """Trigger soft audio recovery (refreshes PULSE_SERVER env, prods daemon)."""
    from ..services.audio_health_service import audio_health_service as _ahs
    try:
        result = _ahs.recover()
        return {
            "ok":               result.ok,
            "sources_count":    result.sources_count,
            "sinks_count":      result.sinks_count,
            "suggested_action": result.suggested_action,
            "detail":           result.detail,
        }
    except Exception as exc:
        logger.exception("[AUDIO_RECOVERY_FAIL] endpoint error: %s", exc)
        return {"ok": False, "detail": str(exc)}
