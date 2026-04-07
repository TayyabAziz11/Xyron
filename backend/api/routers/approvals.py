"""Approval pipeline endpoints — read pending approvals, approve/reject."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from ..schemas.approval import ApprovalAction, ApprovalItem
from ..schemas.common import ApiResponse
from ..services.approval_service import approval_service

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.get("", response_model=ApiResponse[List[ApprovalItem]])
async def list_approvals(status: str = "all") -> ApiResponse[List[ApprovalItem]]:
    """
    Return approval items from the Pending_Approval directory.

    Query params:
        status: all | pending | approved | rejected
    """
    items = approval_service.list_approvals(status_filter=status)
    return ApiResponse(data=items)


@router.get("/{approval_id}", response_model=ApiResponse[ApprovalItem])
async def get_approval(approval_id: str) -> ApiResponse[ApprovalItem]:
    """Get a single approval item by ID."""
    item = approval_service.get(approval_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return ApiResponse(data=item)


@router.post("/{approval_id}/approve", response_model=ApiResponse[ApprovalItem])
async def approve(approval_id: str, action: ApprovalAction = ApprovalAction()) -> ApiResponse[ApprovalItem]:
    """Approve a pending action — moves file to Approved/ directory."""
    item = approval_service.approve(approval_id, note=action.note)
    if not item:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return ApiResponse(data=item, message="Approval accepted")


@router.post("/{approval_id}/reject", response_model=ApiResponse[ApprovalItem])
async def reject(approval_id: str, action: ApprovalAction = ApprovalAction()) -> ApiResponse[ApprovalItem]:
    """Reject a pending action — moves file to Rejected/ directory."""
    item = approval_service.reject(approval_id, note=action.note)
    if not item:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return ApiResponse(data=item, message="Approval rejected")
