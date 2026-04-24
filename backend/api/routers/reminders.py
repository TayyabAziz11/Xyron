"""Reminder endpoints — set, list, cancel, and poll fired reminders."""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.reminder_service import reminder_service, parse_reminder_text

router = APIRouter(prefix="/api/v1/reminders", tags=["reminders"])
logger = logging.getLogger(__name__)


class _ReminderCreate(BaseModel):
    text:    str   # natural language: "remind me to check emails in 30 minutes"
    seconds: int | None = None   # override duration (optional)


@router.post("")
async def create_reminder(body: _ReminderCreate) -> dict:
    """Create a reminder from natural language text."""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    message, seconds = parse_reminder_text(body.text)
    if body.seconds is not None:
        seconds = body.seconds

    if not seconds or seconds <= 0:
        raise HTTPException(
            status_code=400,
            detail="Could not parse a duration. Try: 'remind me to X in 5 minutes'",
        )

    r = reminder_service.add(message=message, seconds=seconds)
    mins = seconds // 60
    secs = seconds % 60
    time_str = f"{mins} minute{'s' if mins != 1 else ''}" if mins else f"{secs} second{'s' if secs != 1 else ''}"
    return {
        "success": True,
        "reminder": r.to_dict(),
        "spoken": f"Got it — I'll remind you to {message} in {time_str}.",
    }


@router.get("/fired")
async def get_fired() -> dict:
    """Poll for reminders that have fired since last call."""
    fired = reminder_service.pop_fired()
    return {"success": True, "fired": fired}


@router.get("")
async def list_reminders() -> dict:
    """List all active (not yet fired) reminders."""
    return {"success": True, "reminders": reminder_service.list_active()}


@router.delete("/{reminder_id}")
async def cancel_reminder(reminder_id: str) -> dict:
    """Cancel a pending reminder."""
    ok = reminder_service.cancel(reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Reminder not found or already fired")
    return {"success": True, "cancelled": reminder_id}
