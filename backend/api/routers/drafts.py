"""Draft management endpoints — create, list, confirm, reject, execute."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.draft_service import draft_service
from ..services.draft_executor import execute_draft

router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


class EditDraftBody(BaseModel):
    content: str
    title:   Optional[str] = None


@router.get("")
def list_drafts(status: Optional[str] = None, limit: int = 20):
    """List all drafts or filter by status (draft|confirmed|executed|rejected)."""
    if status:
        all_d = draft_service.list_all(limit=100)
        items = [d for d in all_d if d.status == status][:limit]
    else:
        items = draft_service.list_all(limit=limit)
    return {"success": True, "data": [d.model_dump() for d in items]}


@router.get("/pending")
def list_pending():
    """List all drafts awaiting review."""
    items = draft_service.list_pending()
    return {"success": True, "data": [d.model_dump() for d in items]}


@router.get("/{draft_id}")
def get_draft(draft_id: str):
    draft = draft_service.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    return {"success": True, "data": draft.model_dump()}


@router.patch("/{draft_id}")
def edit_draft(draft_id: str, body: EditDraftBody):
    """Edit draft content before confirming."""
    draft = draft_service.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status != "draft":
        raise HTTPException(400, f"Draft already {draft.status} — cannot edit")
    updates: dict = {"content": body.content}
    if body.title:
        updates["title"] = body.title
    updated = draft_service.update(draft_id, **updates)
    return {"success": True, "data": updated.model_dump(), "message": "Draft updated"}


@router.post("/{draft_id}/confirm")
def confirm_draft(draft_id: str):
    """Confirm a draft — this executes the action (posts, sends, etc.)."""
    draft = draft_service.get(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")

    result = execute_draft(draft_id)
    spoken = result.get("spoken", "Action completed.")

    return {
        "success": result["success"],
        "data": {
            "draft": draft_service.get(draft_id).model_dump(),
            "execution": result,
            "spoken_response": spoken,
        },
        "message": result.get("message", "Done"),
    }


@router.post("/{draft_id}/reject")
def reject_draft(draft_id: str):
    """Reject a draft — marks it as rejected without executing."""
    draft = draft_service.reject(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found")
    return {
        "success": True,
        "data": draft.model_dump(),
        "message": "Draft rejected",
    }
