"""
Phase 3 Agent REST Router — /api/v1/agents/

Endpoints for agent control, status, and history.
Long-running tasks are managed by AgentRuntime; voice_ws.py streams progress.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LaunchRequest(BaseModel):
    goal: str
    agent_type: str = "auto"  # "browser" | "coding" | "automation" | "personality" | "auto"
    context: dict[str, Any] = {}

class AgentActionRequest(BaseModel):
    task_id: Optional[str] = None  # None = active task


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_runtime():
    from api.agents.agent_runtime import agent_runtime
    return agent_runtime


def _get_personality():
    try:
        from api.agents.personality.personality_engine import personality_engine
        return personality_engine
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def agent_status():
    """Get status of all active agents."""
    try:
        rt = _get_runtime()
        active = rt.get_active()
        all_tasks = [
            {
                "task_id": t.task_id,
                "goal": t.goal,
                "agent_type": t.agent_type.value,
                "status": t.status.value,
                "progress_pct": t.progress_pct,
                "elapsed_s": round(t.elapsed_s(), 1),
                "result_summary": t.result_summary,
            }
            for t in rt._tasks.values()
        ]
        return {
            "active_task": {
                "task_id": active.task_id,
                "goal": active.goal,
                "status": active.status.value,
                "progress_pct": active.progress_pct,
                "progress_text": active.progress_text(),
            } if active else None,
            "tasks": all_tasks,
        }
    except Exception as exc:
        logger.exception("[AGENTS_ROUTER] status error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get details of a specific agent task."""
    rt = _get_runtime()
    task = rt.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    steps = []
    if task.plan:
        for s in task.plan.steps:
            steps.append({
                "index": s.index,
                "description": s.description,
                "status": s.status.value,
                "result": s.result,
                "error": s.error,
            })
    return {
        "task_id": task.task_id,
        "goal": task.goal,
        "agent_type": task.agent_type.value,
        "status": task.status.value,
        "progress_pct": task.progress_pct,
        "elapsed_s": round(task.elapsed_s(), 1),
        "result_summary": task.result_summary,
        "error_message": task.error_message,
        "steps": steps,
    }


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running agent task."""
    rt = _get_runtime()
    success = await rt.cancel(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already terminal")
    return {"ok": True, "task_id": task_id, "action": "cancelled"}


@router.post("/{task_id}/pause")
async def pause_task(task_id: str):
    """Pause a running agent task."""
    rt = _get_runtime()
    success = await rt.pause(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not pauseable")
    return {"ok": True, "task_id": task_id, "action": "paused"}


@router.post("/{task_id}/resume")
async def resume_task(task_id: str):
    """Resume a paused agent task."""
    rt = _get_runtime()
    success = await rt.resume(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found or not paused")
    return {"ok": True, "task_id": task_id, "action": "resumed"}


@router.post("/{task_id}/approve")
async def approve_task_action(task_id: str):
    """Approve a pending action (form submit, delete, booking, etc.)."""
    rt = _get_runtime()
    task = rt.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.metadata["approved"] = True
    # Resume if waiting for approval
    await rt.resume(task_id)
    return {"ok": True, "task_id": task_id, "action": "approved"}


@router.post("/{task_id}/deny")
async def deny_task_action(task_id: str):
    """Deny a pending action — cancels the agent task."""
    rt = _get_runtime()
    task = rt.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.metadata["approved"] = False
    await rt.cancel(task_id)
    return {"ok": True, "task_id": task_id, "action": "denied"}


@router.get("/personality/mode")
async def get_personality_mode():
    """Get current personality mode."""
    pe = _get_personality()
    if not pe:
        return {"mode": "default", "error": "Personality engine not available"}
    return {"mode": pe.mode.value}


@router.post("/personality/mode")
async def set_personality_mode(body: dict):
    """Set personality mode."""
    mode_name = body.get("mode", "default")
    pe = _get_personality()
    if not pe:
        raise HTTPException(status_code=503, detail="Personality engine not available")
    try:
        from api.agents.personality.personality_engine import PersonalityMode
        mode = PersonalityMode(mode_name.lower())
        msg = pe.set_mode(mode)
        return {"ok": True, "mode": mode.value, "message": msg}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode_name}")


# ── Phase 4: Coordinator endpoints ────────────────────────────────────────────

@router.post("/{task_id}/node/{node_id}/approve")
async def approve_coordinator_node(task_id: str, node_id: str):
    """Approve a pending coordinator workflow node (e.g., booking, form submission)."""
    rt = _get_runtime()
    task = rt.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.metadata[f"approved_{node_id}"] = True
    logger.info("[COORDINATOR_APPROVAL_ACCEPTED] task_id=%s node_id=%s", task_id, node_id)
    return {"ok": True, "task_id": task_id, "node_id": node_id, "action": "node_approved"}


@router.post("/{task_id}/node/{node_id}/deny")
async def deny_coordinator_node(task_id: str, node_id: str):
    """Deny a pending coordinator workflow node."""
    rt = _get_runtime()
    task = rt.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.metadata[f"approved_{node_id}"] = False
    logger.info("[COORDINATOR_APPROVAL_REJECTED] task_id=%s node_id=%s", task_id, node_id)
    return {"ok": True, "task_id": task_id, "node_id": node_id, "action": "node_denied"}


@router.get("/registry/capabilities")
async def get_agent_capabilities():
    """Get all registered agent capabilities."""
    try:
        from api.agents.coordinator.agent_registry import agent_registry
        return {
            "agents": [
                {
                    "agent_id": cap.agent_id,
                    "display_name": cap.display_name,
                    "agent_type": cap.agent_type,
                    "capabilities": cap.capabilities,
                    "safety_level": cap.safety_level,
                    "can_run_parallel": cap.can_run_parallel,
                }
                for cap in agent_registry.all()
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Registry unavailable: {exc}")


@router.get("/memory/session")
async def get_collaboration_memory():
    """Get current session collaboration memory (debug endpoint)."""
    try:
        from api.agents.coordinator.collaboration_memory import collaboration_memory
        return {"session_memory": collaboration_memory.get_all_session()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
