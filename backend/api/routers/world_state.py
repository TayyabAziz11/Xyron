"""World State Engine — Reasoning Context API."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/world", tags=["world_state"])


@router.get("/state")
async def get_state(refresh: bool = False):
    """The full Reasoning Context snapshot — the primary reasoning context for Xyron."""
    from ..services.world_state import world_state
    return world_state.get_context(refresh=refresh)


@router.get("/timeline")
async def get_timeline(limit: int = 20):
    from ..services.activity_timeline import activity_timeline
    return {"events": activity_timeline.to_list(limit)}


@router.get("/entities")
async def get_entities(limit: int = 10):
    from ..services.context_stack import context_stack
    entities = context_stack.recent(limit)
    return {
        "entities": [{"type": e.type, "display": e.display, "value": e.value, "source": e.source}
                     for e in entities],
        "summary": {k: (v.display if hasattr(v, "display") else v)
                    for k, v in context_stack.to_summary().items() if k != "recent"},
    }


@router.get("/goal")
async def get_goal():
    from ..services.goal_tracker import goal_tracker
    return {"current": goal_tracker.get_goal(), "history": goal_tracker.history()}


@router.get("/focus")
async def get_focus():
    from ..services.world_state import world_state
    ctx = world_state.get_context(refresh=True)
    return ctx["focus_graph"]


@router.post("/refresh")
async def refresh_now():
    """Force an immediate full Perception Engine tick (browser/desktop/selection/sensors)."""
    from ..services.perception import perception_engine
    from ..services.world_state import world_state
    await perception_engine.refresh_now()
    return world_state.get_context()


@router.post("/vision")
async def request_vision(reason: str = "manual_request"):
    """
    Explicit last-resort vision capture — never triggered automatically.
    Callers should check GET /world/state first; only call this if Browser
    Perception and Desktop Perception both came back empty.
    """
    from ..config import settings
    from ..services.perception import perception_engine
    result = await perception_engine.request_vision(reason, settings.openai_api_key or "")
    if not result:
        return {"captured": False, "reason": "throttled_or_unavailable"}
    return {"captured": True, **result}
