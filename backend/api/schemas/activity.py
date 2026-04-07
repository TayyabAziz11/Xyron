"""Activity and audit log schemas."""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ActivityEvent(BaseModel):
    """A single activity event from the audit log."""
    model_config = ConfigDict(use_enum_values=True)

    id: str
    event_type: str
    description: str
    status: str = "info"  # success | error | warning | info
    timestamp: str
    source: Optional[str] = None
    actor: Optional[str] = None
    target: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class LogEntry(BaseModel):
    """Raw audit log entry as stored on disk."""
    model_config = ConfigDict(use_enum_values=True)

    timestamp: str
    action_type: str
    actor: str = "claude_code"
    target: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    approval_status: Optional[str] = None
    approval_ref: Optional[str] = None
    approved_by: Optional[str] = None
    result: str = "success"
    error: Optional[str] = None
