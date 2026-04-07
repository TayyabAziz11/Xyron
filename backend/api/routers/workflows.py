"""Workflow state endpoints — track agent task lifecycle."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from ..schemas.workflow import Workflow, WorkflowStatus
from ..schemas.common import ApiResponse
from ..services.workflow_service import workflow_service

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.get("", response_model=ApiResponse[List[Workflow]])
async def list_workflows(status: str = "all") -> ApiResponse[List[Workflow]]:
    """
    Return workflow instances.

    Query params:
        status: all | active | completed | failed | queued
    """
    workflows = workflow_service.list_workflows(status_filter=status)
    return ApiResponse(data=workflows)


@router.get("/{workflow_id}", response_model=ApiResponse[Workflow])
async def get_workflow(workflow_id: str) -> ApiResponse[Workflow]:
    """Get a single workflow by ID."""
    wf = workflow_service.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
    return ApiResponse(data=wf)
