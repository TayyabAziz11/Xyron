"""Browser / CDP bridge health and repair endpoints (Phase 4.11.1).

Non-blocking readiness surface for the Windows-Chrome CDP bridge — never
gates basic Xyron startup or direct (non-browser) commands, only informs
controlled browser-agent workflows.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..schemas.common import ApiResponse
from ..services import cdp_config
from ..services.cdp_environment_doctor import doctor as cdp_doctor

router = APIRouter(prefix="/api/v1/browser", tags=["browser"])


def _readiness_state(diagnosis) -> str:
    if not diagnosis.chrome_found:
        return "not_configured"
    if diagnosis.healthy:
        return "healthy"
    if diagnosis.repair_required:
        return "repair_required"
    return "unavailable"


@router.get("/cdp/health", response_model=ApiResponse[dict])
async def cdp_health() -> ApiResponse[dict]:
    """Read-only diagnosis — never triggers a repair or UAC prompt."""
    diagnosis = await cdp_doctor.diagnose()
    cfg = cdp_config.get_config()
    data = {
        "cdp_bridge": _readiness_state(diagnosis),
        "healthy": diagnosis.healthy,
        "repair_required": diagnosis.repair_required,
        "repair_reasons": diagnosis.repair_reasons,
        "chrome_found": diagnosis.chrome_found,
        "chrome_path": diagnosis.chrome_path,
        "xyron_chrome_pids": diagnosis.xyron_chrome_pids,
        "collision_detected": diagnosis.collision_detected,
        "collision_port": diagnosis.collision_port,
        "bridge_port": cfg.bridge_port,
        "chrome_local_port": cfg.chrome_local_port,
        "wsl_can_reach_bridge": diagnosis.wsl_can_reach_bridge,
        "firewall_rule_present": diagnosis.firewall_rule_present,
        "firewall_rule_port": diagnosis.firewall_rule_port,
    }
    return ApiResponse(data=data)


@router.post("/cdp/repair", response_model=ApiResponse[dict])
async def cdp_repair() -> ApiResponse[dict]:
    """Triggers the elevated repair flow — one Windows UAC prompt. Blocks
    until the user approves/denies it or a timeout elapses; never crashes
    on denial, returns a structured failure instead."""
    diagnosis = await cdp_doctor.diagnose()
    if diagnosis.healthy:
        return ApiResponse(data={"success": True, "already_healthy": True})
    result = await cdp_doctor.repair(diagnosis)
    return ApiResponse(data=result, message="Repair succeeded" if result.get("success") else "Repair failed")
