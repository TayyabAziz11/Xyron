"""Server-Sent Events for real-time command status updates."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..services.command_service import command_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/events", tags=["events"])


async def _command_event_stream(command_id: str) -> AsyncIterator[str]:
    """Yield SSE events for a command until it reaches terminal state."""
    max_polls = 120  # max 60 seconds at 0.5s intervals
    for _ in range(max_polls):
        cmd = command_service.get(command_id)
        if cmd is None:
            yield f"event: error\ndata: {json.dumps({'error': 'command_not_found'})}\n\n"
            return

        payload = {
            "id": cmd.id,
            "status": cmd.status,
            "result": cmd.result,
            "intent": cmd.intent.model_dump() if cmd.intent else None,
        }
        yield f"event: status\ndata: {json.dumps(payload)}\n\n"

        if cmd.status in ("completed", "failed"):
            yield f"event: done\ndata: {json.dumps({'command_id': command_id})}\n\n"
            return

        await asyncio.sleep(0.5)

    yield f"event: timeout\ndata: {json.dumps({'command_id': command_id})}\n\n"


@router.get("/commands/{command_id}")
async def stream_command_events(command_id: str):
    """SSE stream — emits status events until command completes or fails.

    Events:
      status  — periodic status update with current command state
      done    — command reached terminal state (completed/failed)
      error   — command not found
      timeout — polling limit reached (60s)

    Usage (browser):
      const es = new EventSource('/api/v1/events/commands/<id>')
      es.addEventListener('status', e => console.log(JSON.parse(e.data)))
      es.addEventListener('done', () => es.close())
    """
    return StreamingResponse(
        _command_event_stream(command_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
