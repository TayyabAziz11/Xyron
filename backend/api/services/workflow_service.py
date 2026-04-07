"""
Workflow Service — tracks workflow instances.

Workflows are derived from command history and log files.
In v1, active workflows are inferred from in-memory commands.
Future: persist to SQLite or a proper store.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from ..schemas.workflow import Workflow, WorkflowStatus
from .command_service import command_service
from ..schemas.command import CommandStatus


def _command_to_workflow(cmd) -> Workflow:
    """Convert a Command to a Workflow for display purposes."""
    status_map = {
        CommandStatus.queued: WorkflowStatus.queued,
        CommandStatus.running: WorkflowStatus.active,
        CommandStatus.completed: WorkflowStatus.completed,
        CommandStatus.failed: WorkflowStatus.failed,
    }
    wf_status = status_map.get(cmd.status, WorkflowStatus.queued)

    return Workflow(
        id=cmd.id,
        name=cmd.text[:80] + ("..." if len(cmd.text) > 80 else ""),
        type="command",
        status=wf_status,
        started_at=cmd.created_at,
        completed_at=cmd.updated_at if cmd.status in (CommandStatus.completed, CommandStatus.failed) else None,
        current_step=cmd.status.value,
        agent=cmd.agent,
        command_id=cmd.id,
        error=cmd.error,
    )


class WorkflowService:
    """Workflow state derived from command history."""

    def list_workflows(self, status_filter: str = "all") -> List[Workflow]:
        """Return workflow instances derived from command history."""
        commands = command_service.list_recent(limit=100)
        workflows = [_command_to_workflow(cmd) for cmd in commands]

        if status_filter != "all":
            try:
                target_status = WorkflowStatus(status_filter)
                workflows = [w for w in workflows if w.status == target_status]
            except ValueError:
                pass  # Invalid status filter — return all

        return workflows

    def get(self, workflow_id: str) -> Optional[Workflow]:
        cmd = command_service.get(workflow_id)
        if not cmd:
            return None
        return _command_to_workflow(cmd)


# Module-level singleton
workflow_service = WorkflowService()
