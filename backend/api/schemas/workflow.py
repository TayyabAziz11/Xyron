"""Workflow schemas — task lifecycle and orchestration state."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class WorkflowStatus(str, Enum):
    active = "active"
    completed = "completed"
    failed = "failed"
    queued = "queued"
    paused = "paused"


class Workflow(BaseModel):
    """A workflow instance with lifecycle tracking."""
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    type: str  # e.g. "email_draft", "linkedin_post", "odoo_invoice"
    status: WorkflowStatus = WorkflowStatus.queued
    started_at: str
    completed_at: Optional[str] = None
    current_step: Optional[str] = None
    agent: Optional[str] = None
    command_id: Optional[str] = None  # linked command if triggered via API
    error: Optional[str] = None
