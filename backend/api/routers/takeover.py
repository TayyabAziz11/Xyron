"""
takeover.py — REST API for Xyron takeover mode state.

GET  /api/v1/takeover/status    — current state (frontend polls this)
POST /api/v1/takeover/activate  — programmatic activate
POST /api/v1/takeover/deactivate
"""
from __future__ import annotations

import logging
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/takeover", tags=["takeover"])


class TakeoverStatusResponse(BaseModel):
    active:         bool
    source:         str
    autonomy_level: int
    started_at:     float | None
    last_action:    str | None


class ActivateRequest(BaseModel):
    source: str = "api"


@router.get("/status", response_model=TakeoverStatusResponse)
def get_status() -> TakeoverStatusResponse:
    from brain.takeover_service import takeover_service as _ts
    s = _ts.get_takeover_state()
    return TakeoverStatusResponse(
        active=s.active,
        source=s.source,
        autonomy_level=s.autonomy_level,
        started_at=s.started_at,
        last_action=s.last_action,
    )


@router.post("/activate", response_model=TakeoverStatusResponse)
def activate(req: ActivateRequest) -> TakeoverStatusResponse:
    from brain.takeover_service import takeover_service as _ts
    s = _ts.activate_takeover(source=req.source)
    return TakeoverStatusResponse(
        active=s.active,
        source=s.source,
        autonomy_level=s.autonomy_level,
        started_at=s.started_at,
        last_action=s.last_action,
    )


@router.post("/deactivate", response_model=TakeoverStatusResponse)
def deactivate() -> TakeoverStatusResponse:
    from brain.takeover_service import takeover_service as _ts
    s = _ts.deactivate_takeover()
    return TakeoverStatusResponse(
        active=s.active,
        source=s.source,
        autonomy_level=s.autonomy_level,
        started_at=s.started_at,
        last_action=s.last_action,
    )
