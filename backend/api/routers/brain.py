"""
Brain API — endpoints for brain state, capabilities, agents, memory, and status.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/brain", tags=["brain"])
logger = logging.getLogger(__name__)


# ── GET /api/v1/brain/status ──────────────────────────────────────────────────

@router.get("/status")
async def brain_status():
    """Current brain state snapshot — used by the Command Center brain panel."""
    try:
        from brain.brain_state import brain_state
        from brain.capability_registry import capability_registry
        snap = brain_state.snapshot()
        return {
            "ok":              True,
            "autonomy_level":  snap.get("autonomy_level", 2),
            "autonomy_label":  brain_state.autonomy_label,
            "current_mode":    snap.get("current_mode", "voice"),
            "active_goal":     snap.get("active_goal"),
            "current_emotion": snap.get("current_emotion", "neutral"),
            "current_focus":   snap.get("current_focus"),
            "last_agent_used": snap.get("last_agent_used"),
            "active_agents":   snap.get("active_agents", []),
            "identity_mode":   snap.get("identity_mode", "PUBLIC"),
            "session_count":   snap.get("session_count", 0),
            "total_commands":  snap.get("total_commands", 0),
            "last_decision":   snap.get("last_decision", {}),
            "confidence_avg":  _avg(snap.get("confidence_history", [])),
            "capability_summary": capability_registry.summary(),
        }
    except Exception as exc:
        logger.warning("[BRAIN_API] status error: %s", exc)
        return {"ok": False, "error": str(exc)}


# ── GET /api/v1/brain/capabilities ────────────────────────────────────────────

@router.get("/capabilities")
async def brain_capabilities(mode: str = "PUBLIC"):
    """Full capability registry in the requested fidelity mode."""
    try:
        from brain.capability_registry import capability_registry
        return {
            "ok":           True,
            "mode":         mode.upper(),
            "capabilities": capability_registry.all(mode.upper()),
            "summary":      capability_registry.summary(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /api/v1/brain/agents ──────────────────────────────────────────────────

@router.get("/agents")
async def brain_agents():
    """All registered brain agents with their IDs, names, and capabilities."""
    try:
        from agents.registry import brain_agent_registry
        return {
            "ok":     True,
            "agents": brain_agent_registry.all(),
            "count":  len(brain_agent_registry.ids()),
        }
    except Exception as exc:
        logger.warning("[BRAIN_API] agents error: %s", exc)
        return {"ok": False, "error": str(exc), "agents": []}


# ── GET /api/v1/brain/memory/recent ──────────────────────────────────────────

@router.get("/memory/recent")
async def brain_memory_recent(n: int = 20, type: str | None = None):
    """Recent brain memories, optionally filtered by type."""
    try:
        from brain.memory_system import brain_memory
        valid_types = {"episodic", "semantic", "procedural", "relationship", "project"}
        mem_type = type if type in valid_types else None
        records = brain_memory.recent(n=min(n, 100), mem_type=mem_type)
        return {
            "ok":     True,
            "count":  len(records),
            "total":  brain_memory.count(),
            "memories": [r.to_dict() for r in records],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /api/v1/brain/memory/search ─────────────────────────────────────────

class _SearchBody(BaseModel):
    query: str
    n: int = 5


@router.post("/memory/search")
async def brain_memory_search(body: _SearchBody):
    """Semantic search across brain memories."""
    try:
        from brain.memory_system import brain_memory
        records = brain_memory.search(body.query, n=body.n)
        return {"ok": True, "results": [r.to_dict() for r in records]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /api/v1/brain/state ──────────────────────────────────────────────────

class _StateUpdateBody(BaseModel):
    autonomy_level:  int  | None = None
    current_mode:    str  | None = None
    active_goal:     str  | None = None
    identity_mode:   str  | None = None
    current_focus:   str  | None = None


@router.post("/state")
async def brain_state_update(body: _StateUpdateBody):
    """Update mutable brain state fields."""
    try:
        from brain.brain_state import brain_state
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if "autonomy_level" in updates:
            lvl = int(updates["autonomy_level"])
            if not 0 <= lvl <= 4:
                raise HTTPException(status_code=400, detail="autonomy_level must be 0-4")
        if updates:
            brain_state.update(**updates)
        return {"ok": True, "updated": list(updates.keys())}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /api/v1/brain/parse ───────────────────────────────────────────────────

@router.get("/parse")
async def brain_parse(text: str):
    """Debug endpoint — parse a transcript through the semantic layer."""
    try:
        from brain.semantic_understanding import semantic_understanding
        frame = semantic_understanding.parse(text, use_llm=False)
        return {"ok": True, "frame": {
            "route":      frame.route,
            "intent":     frame.intent,
            "target":     frame.target,
            "confidence": frame.confidence,
            "emotion":    frame.emotion_hint,
            "tier":       frame.tier,
            "requires_confirmation": frame.requires_confirmation,
            "reason":     frame.reason,
        }}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _avg(values: list) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 3)
