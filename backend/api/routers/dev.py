"""Dev router — Code Assistant Mode API endpoints."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.cognitive_state import cognitive_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


class DevQueryRequest(BaseModel):
    text: str
    source: str = "text"
    active_project: Optional[str] = None
    active_file: Optional[str] = None


class DevQueryResponse(BaseModel):
    output: str
    intent: str
    model: str
    source: str
    code_mode: bool


@router.post("/query", response_model=DevQueryResponse)
async def dev_query(req: DevQueryRequest) -> DevQueryResponse:
    """Non-streaming dev query — returns full response."""
    from src.ai_operator.agents.dev_agent import DevAgent
    agent = DevAgent()
    result = agent.run(
        req.text,
        active_project=req.active_project or cognitive_state.active_project,
        active_file=req.active_file or cognitive_state.active_file,
    )
    return DevQueryResponse(
        output=result.output,
        intent=result.metadata.get("intent", "unknown"),
        model=result.metadata.get("model", "local"),
        source=result.metadata.get("source", "local"),
        code_mode=cognitive_state.code_mode,
    )


@router.post("/stream")
async def dev_stream(req: DevQueryRequest) -> StreamingResponse:
    """Streaming dev query — yields SSE tokens progressively."""
    from src.ai_operator.agents.dev_agent import DevAgent

    agent = DevAgent()
    ctx = {
        "active_project": req.active_project or cognitive_state.active_project,
        "active_file": req.active_file or cognitive_state.active_file,
    }

    def event_gen():
        full = []
        try:
            for token in agent.stream(req.text, **ctx):
                full.append(token)
                yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'full_text': ''.join(full)})}\n\n"
        except Exception as exc:
            logger.error("dev_stream error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/status")
async def dev_status():
    """Return current code mode status for the frontend."""
    return {
        "code_mode": cognitive_state.code_mode,
        "active_project": cognitive_state.active_project,
        "active_file": cognitive_state.active_file,
        "active_ui_mode": cognitive_state.active_ui_mode,
    }
