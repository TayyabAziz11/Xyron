"""Cognition router — exposes live CognitiveState for the frontend."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
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
