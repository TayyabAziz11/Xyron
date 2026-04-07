"""Approval schemas — human-in-the-loop approval pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApprovalItem(BaseModel):
    """A plan or action awaiting human approval."""
    model_config = ConfigDict(use_enum_values=True)

    id: str
    title: str
    description: str
    action_type: str = "plan_execution"
    risk_level: str = "medium"  # low | medium | high
    status: ApprovalStatus = ApprovalStatus.pending
    created_at: str
    file_path: Optional[str] = None
    related_plan: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ApprovalAction(BaseModel):
    """Action to take on an approval (approve or reject)."""
    note: Optional[str] = Field(None, description="Optional note from the reviewer")
