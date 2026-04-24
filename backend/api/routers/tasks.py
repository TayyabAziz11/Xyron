"""Task router — multi-step task planning and execution endpoints."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.task_service import task_service, Task
from ..config import settings

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


class _TaskCreate(BaseModel):
    goal:       str
    session_id: Optional[str] = None
    history:    List[dict]    = []


@router.post("", response_model=Task)
async def create_task(body: _TaskCreate) -> Task:
    """Plan and execute a multi-step task from a natural-language goal."""
    if not body.goal.strip():
        raise HTTPException(status_code=400, detail="goal is required")

    key = settings.openai_api_key if settings.openai_api_key.startswith("sk-") else None
    task = task_service.create_and_run(
        goal=body.goal.strip(),
        history=body.history or None,
        openai_key=key,
    )
    return task


@router.get("/{task_id}", response_model=Task)
async def get_task(task_id: str) -> Task:
    """Poll task status and step progress."""
    task = task_service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/{task_id}")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running task."""
    ok = task_service.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found or already finished")
    return {"cancelled": True}


@router.get("", response_model=List[Task])
async def list_tasks(limit: int = 10) -> List[Task]:
    """List recent tasks (newest first)."""
    return task_service.list_recent(limit=min(limit, 50))
