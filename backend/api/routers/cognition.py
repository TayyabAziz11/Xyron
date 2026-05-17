"""Cognition router — exposes live CognitiveState and memory endpoints."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from cognition.cognitive_state import cognitive_state
from ..services.cognitive_state import VALID_EMOTIONS, VALID_ATTENTION

router = APIRouter(prefix="/api/v1/cognition", tags=["cognition"])


class CognitiveStateResponse(BaseModel):
    attention: str
    mood_bias: str
    last_user_emotion: str
    emotion_intensity: float
    active_goal: Optional[str]
    current_task: Optional[str]
    context_summary: str
    active_ui_mode: str
    turn_count: int
    last_updated: float
    # Phase 8 — Code Assistant Mode
    code_mode: bool = False
    active_project: Optional[str] = None
    active_file: Optional[str] = None
    # Emotional cognition
    mood_state: str = "CALM"
    emotion_label: str = "calmness"
    emotion_energy: float = 0.5


def _build_response() -> CognitiveStateResponse:
    snap = cognitive_state.snapshot()
    return CognitiveStateResponse(
        attention=snap["attention"],
        mood_bias=snap["mood_bias"],
        last_user_emotion=snap["last_user_emotion"],
        emotion_intensity=snap["emotion_intensity"],
        active_goal=snap["active_goal"],
        current_task=snap["current_task"],
        context_summary=snap["context_summary"],
        active_ui_mode=snap["active_ui_mode"],
        turn_count=snap["turn_count"],
        last_updated=snap["last_updated"],
        code_mode=snap.get("code_mode", False),
        active_project=snap.get("active_project"),
        active_file=snap.get("active_file"),
        mood_state=snap.get("mood_state", "CALM"),
        emotion_label=snap.get("emotion_label", "calmness"),
        emotion_energy=snap.get("emotion_energy", 0.5),
    )


@router.get("/state", response_model=CognitiveStateResponse)
async def get_cognitive_state() -> CognitiveStateResponse:
    return _build_response()


@router.patch("/state")
async def patch_cognitive_state(payload: dict[str, Any]) -> CognitiveStateResponse:
    """Dev/test endpoint — set any cognitive state field directly."""
    allowed = {
        "attention", "mood_bias", "last_user_emotion", "emotion_intensity",
        "active_goal", "current_task", "context_summary", "active_ui_mode",
        "code_mode", "active_project", "active_file",
    }
    for key, value in payload.items():
        if key not in allowed:
            continue
        cognitive_state.update(**{key: value})
    return _build_response()


# ── Convenience endpoints ─────────────────────────────────────────────────────

class EmotionUpdate(BaseModel):
    emotion: str
    intensity: float = 0.7


@router.post("/emotion")
async def set_emotion(body: EmotionUpdate) -> CognitiveStateResponse:
    """Set emotion + intensity in one call. Validates against known emotions."""
    if body.emotion not in VALID_EMOTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown emotion '{body.emotion}'. Valid: {sorted(VALID_EMOTIONS)}",
        )
    intensity = max(0.0, min(1.0, body.intensity))
    cognitive_state.update(last_user_emotion=body.emotion, emotion_intensity=intensity)
    return _build_response()


class AttentionUpdate(BaseModel):
    attention: str


@router.post("/attention")
async def set_attention(body: AttentionUpdate) -> CognitiveStateResponse:
    """Set attention state. Validates against known states."""
    if body.attention not in VALID_ATTENTION:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown attention '{body.attention}'. Valid: {sorted(VALID_ATTENTION)}",
        )
    cognitive_state.update(attention=body.attention)
    return _build_response()


# ── Memory endpoints ──────────────────────────────────────────────────────────

class RememberRequest(BaseModel):
    text: str
    metadata: dict[str, Any] = {}
    role: str = "user"
    session_id: str = "api"


class RememberResponse(BaseModel):
    id: str
    stored: bool


@router.post("/memory/remember", response_model=RememberResponse)
async def memory_remember(body: RememberRequest) -> RememberResponse:
    """Store text in semantic (ChromaDB) and episodic (SQLite) memory."""
    try:
        from cognition.memory.memory_bridge import memory_bridge
        memory_bridge.add(
            text=body.text,
            role=body.role,
            session_id=body.session_id,
            metadata=body.metadata,
        )
        from cognition.memory.semantic_store import semantic_store
        count = semantic_store.count()
        return RememberResponse(id=f"stored:{count}", stored=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class RecallResult(BaseModel):
    id: str
    text: str
    metadata: dict[str, Any]
    distance: float


@router.get("/memory/recall", response_model=list[RecallResult])
async def memory_recall(
    query: str = Query(..., description="Semantic search query"),
    n: int = Query(5, ge=1, le=20),
) -> list[RecallResult]:
    """Return top-n semantically similar memories."""
    try:
        from cognition.memory.memory_bridge import memory_bridge
        results = memory_bridge.recall(query, n)
        return [RecallResult(**r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Goal endpoints ────────────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    description: str
    priority: int = 3
    deadline: Optional[float] = None


class GoalResponse(BaseModel):
    id: str
    description: str
    created_at: float
    deadline: Optional[float]
    priority: int
    status: str
    sub_goals: list[str]


def _goal_to_response(g: Any) -> GoalResponse:
    return GoalResponse(
        id=g.id,
        description=g.description,
        created_at=g.created_at,
        deadline=g.deadline,
        priority=g.priority,
        status=g.status,
        sub_goals=g.sub_goals,
    )


@router.get("/goals", response_model=list[GoalResponse])
async def get_goals() -> list[GoalResponse]:
    """Return all active goals ordered by priority."""
    try:
        from cognition.goals import goal_tracker
        return [_goal_to_response(g) for g in goal_tracker.get_active_goals()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/goals", response_model=GoalResponse, status_code=201)
async def create_goal(body: GoalRequest) -> GoalResponse:
    """Create a new goal and update cognitive_state.active_goal."""
    try:
        from cognition.goals import goal_tracker
        goal = goal_tracker.set_goal(
            description=body.description,
            priority=body.priority,
            deadline=body.deadline,
        )
        cognitive_state.update(active_goal=goal.description)
        return _goal_to_response(goal)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/goals/{goal_id}/complete", response_model=GoalResponse)
async def complete_goal(goal_id: str) -> GoalResponse:
    """Mark a goal as completed."""
    try:
        from cognition.goals import goal_tracker
        goal_tracker.complete_goal(goal_id)
        goal = goal_tracker.get_goal(goal_id)
        if not goal:
            raise HTTPException(status_code=404, detail=f"Goal {goal_id!r} not found")
        # Clear active_goal if it was this one
        snap = cognitive_state.snapshot()
        if snap.get("active_goal") == goal.description:
            next_goal = goal_tracker.prioritize()
            cognitive_state.update(active_goal=next_goal.description if next_goal else None)
        return _goal_to_response(goal)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Mood state endpoints ──────────────────────────────────────────────────────

class MoodStateResponse(BaseModel):
    state:    str
    theme:    dict
    held_sec: float


@router.get("/mood", response_model=MoodStateResponse)
async def get_mood_state() -> MoodStateResponse:
    """Return live mood state machine snapshot including UI theme."""
    try:
        from cognition.mood_state_machine import mood_machine
        snap = mood_machine.snapshot()
        return MoodStateResponse(**snap)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class MoodForceRequest(BaseModel):
    state: str


@router.post("/mood/force")
async def force_mood_state(body: MoodForceRequest) -> MoodStateResponse:
    """Force the mood state machine to a specific state (dev/test)."""
    try:
        from cognition.mood_state_machine import mood_machine, MoodState
        state = MoodState(body.state.upper())
        mood_machine.force(state)
        return MoodStateResponse(**mood_machine.snapshot())
    except ValueError:
        from cognition.mood_state_machine import MoodState
        valid = [s.value for s in MoodState]
        raise HTTPException(status_code=422, detail=f"Invalid state. Valid: {valid}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Relationship memory endpoints ─────────────────────────────────────────────

@router.get("/relationship/events")
async def get_relationship_events(n: int = Query(20, ge=1, le=100)) -> list[dict]:
    """Return recent emotionally significant events."""
    try:
        from memory.relationship_memory import relationship_memory
        return relationship_memory.export_recent(n)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class RelationshipEventRequest(BaseModel):
    event_type: str
    description: str
    metadata: dict = {}


@router.post("/relationship/events", status_code=201)
async def record_relationship_event(body: RelationshipEventRequest) -> dict:
    """Manually record a relationship event."""
    try:
        from memory.relationship_memory import relationship_memory
        relationship_memory.record(body.event_type, body.description, body.metadata)
        return {"status": "recorded"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Reflection endpoints ──────────────────────────────────────────────────────

@router.get("/reflection/latest")
async def reflection_latest() -> list[dict]:
    """Return the most recent reflection insight stored in semantic memory."""
    try:
        from cognition.memory.semantic_store import semantic_store
        return semantic_store.recall("reflection session summary", n=1)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/reflection/trigger")
async def reflection_trigger() -> dict:
    """Manually trigger one reflection cycle (for testing)."""
    try:
        from cognition.reflection import reflection_engine
        result = await reflection_engine.run_reflection_cycle()
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
