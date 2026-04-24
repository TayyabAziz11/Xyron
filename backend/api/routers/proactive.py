"""Proactive assistant SSE stream — Feature #7"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..services.proactive_service import proactive_service

router = APIRouter(prefix="/api/v1/proactive", tags=["proactive"])


@router.get("/stream")
async def notification_stream():
    """Server-Sent Events stream of proactive notifications."""

    async def _generate():
        yield "data: {\"type\": \"connected\"}\n\n"
        while True:
            n = proactive_service.poll()
            if n:
                payload = json.dumps({
                    "type":     "notification",
                    "category": n.category,
                    "priority": n.priority,
                    "message":  n.message,
                    "ts":       n.ts,
                })
                yield f"data: {payload}\n\n"
            else:
                await asyncio.sleep(2)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/status")
async def status():
    return {"running": proactive_service._running}


@router.post("/start")
async def start():
    from ..config import settings
    proactive_service.start(settings.openai_api_key)
    return {"ok": True}


@router.post("/stop")
async def stop():
    proactive_service.stop()
    return {"ok": True}
