"""Activity log endpoints — audit trail of agent actions."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from ..schemas.activity import ActivityEvent
from ..schemas.common import ApiResponse
from ..services.activity_service import activity_service

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


@router.get("", response_model=ApiResponse[List[ActivityEvent]])
async def list_activity(limit: int = 50, days: int = 7) -> ApiResponse[List[ActivityEvent]]:
    """
    Return activity events from JSON log files in Logs/.

    Query params:
        limit: max number of events to return (default 50)
        days:  how many days back to read (default 7)
    """
    events = activity_service.list_events(limit=limit, days=days)
    return ApiResponse(data=events)


@router.get("/{event_id}", response_model=ApiResponse[ActivityEvent])
async def get_activity(event_id: str) -> ApiResponse[ActivityEvent]:
    """Get a single activity event by ID."""
    event = activity_service.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return ApiResponse(data=event)
