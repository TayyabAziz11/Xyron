"""Integration schemas — connected services and their status."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class IntegrationStatus(str, Enum):
    connected = "connected"
    disconnected = "disconnected"
    not_configured = "not_configured"
    error = "error"


class Integration(BaseModel):
    """A connected (or disconnected) third-party integration."""
    model_config = ConfigDict(use_enum_values=True)

    id: str
    name: str
    description: str
    status: IntegrationStatus = IntegrationStatus.not_configured
    last_sync: Optional[str] = None
    icon: Optional[str] = None
    credential_file: Optional[str] = None  # which file gates this integration
    mcp_server: Optional[str] = None  # which MCP server backs this
