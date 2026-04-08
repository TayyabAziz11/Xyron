"""Command schemas — command intake and result tracking."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
import uuid


class CommandStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class CommandSubmit(BaseModel):
    """Payload for submitting a new command."""
    text: str = Field(..., min_length=1, max_length=4096, description="Natural language command text")


class CommandIntent(BaseModel):
    """Classified intent from the command text."""
    agent: str
    skill: str
    confidence: str


class Command(BaseModel):
    """A submitted command with lifecycle state."""
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    status: CommandStatus = CommandStatus.queued
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    agent: Optional[str] = None  # which agent handled it
    intent: Optional[CommandIntent] = None  # classified intent
    assistant_response: Optional[str] = None  # spoken-friendly response for TTS
    draft_id: Optional[str] = None  # associated draft (if command produced one)


class CommandResult(BaseModel):
    """Result of command execution."""
    model_config = ConfigDict(use_enum_values=True)

    command_id: str
    status: CommandStatus
    result: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
