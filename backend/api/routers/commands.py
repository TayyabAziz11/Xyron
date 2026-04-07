"""Command intake endpoints — submit and track natural language commands."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from ..schemas.command import Command, CommandStatus, CommandSubmit
from ..schemas.common import ApiResponse
from ..services.command_service import command_service

router = APIRouter(prefix="/api/v1/commands", tags=["commands"])


@router.post("", response_model=ApiResponse[Command])
async def submit_command(payload: CommandSubmit) -> ApiResponse[Command]:
    """
    Submit a natural language command for processing.

    Commands are queued in-memory and acknowledged immediately.
    Actual agent execution happens asynchronously (not yet wired).
    """
    cmd = command_service.submit(payload.text)
    return ApiResponse(
        data=cmd,
        message=f"Command queued with id {cmd.id}",
    )


@router.get("", response_model=ApiResponse[List[Command]])
async def list_commands(limit: int = 20) -> ApiResponse[List[Command]]:
    """Return recent commands (most recent first, in-memory only)."""
    commands = command_service.list_recent(limit=limit)
    return ApiResponse(data=commands)


@router.get("/{command_id}", response_model=ApiResponse[Command])
async def get_command(command_id: str) -> ApiResponse[Command]:
    """Get a single command by ID."""
    cmd = command_service.get(command_id)
    if not cmd:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    return ApiResponse(data=cmd)
