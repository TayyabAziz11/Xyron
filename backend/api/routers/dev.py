"""
Dev router — Phase 8 Code Assistant + Phase 9 Persistent Dev Intelligence endpoints.

[DEV_CONTEXT] log prefix for context-enriched responses.
All dev/* modules are lazy-imported so a missing dependency never crashes startup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.cognitive_state import cognitive_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


# ── Shared helpers ────────────────────────────────────────────────────────────

def _active_project() -> str | None:
    return cognitive_state.active_project

def _active_file() -> str | None:
    return cognitive_state.active_file


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 8 — existing endpoints (preserved)
# ═══════════════════════════════════════════════════════════════════════════════

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
    """Non-streaming dev query — returns full response with injected context."""
    from src.ai_operator.agents.dev_agent import DevAgent
    agent = DevAgent()
    t0 = time.monotonic()
    result = agent.run(
        req.text,
        active_project=req.active_project or _active_project(),
        active_file=req.active_file or _active_file(),
    )
    logger.info("[DEV_CONTEXT] query intent=%s latency_ms=%d project=%s file=%s action=query",
                result.metadata.get("intent"), int((time.monotonic() - t0) * 1000),
                _active_project(), _active_file())
    return DevQueryResponse(
        output=result.output,
        intent=result.metadata.get("intent", "unknown"),
        model=result.metadata.get("model", "local"),
        source=result.metadata.get("source", "local"),
        code_mode=cognitive_state.code_mode,
    )


@router.post("/stream")
async def dev_stream(req: DevQueryRequest) -> StreamingResponse:
    """Streaming dev query — yields SSE tokens with full workspace context."""
    from src.ai_operator.agents.dev_agent import DevAgent
    agent = DevAgent()
    ctx = {
        "active_project": req.active_project or _active_project(),
        "active_file":    req.active_file or _active_file(),
    }

    def event_gen():
        full: list[str] = []
        try:
            for token in agent.stream(req.text, **ctx):
                full.append(token)
                yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'full_text': ''.join(full)})}\n\n"
        except Exception as exc:
            logger.error("[DEV_CONTEXT] stream error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/status")
async def dev_status():
    """Current code-mode status for the frontend."""
    return {
        "code_mode":      cognitive_state.code_mode,
        "active_project": cognitive_state.active_project,
        "active_file":    cognitive_state.active_file,
        "active_ui_mode": cognitive_state.active_ui_mode,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 9 — Task 1: Active File Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/active-file-context")
async def active_file_context(
    file: Optional[str] = Query(None, description="Override file path"),
    project: Optional[str] = Query(None, description="Override project path"),
):
    """Analyze the currently active file and return structured context."""
    t0 = time.monotonic()
    file_path    = file    or _active_file()
    project_path = project or _active_project()

    try:
        from dev.file_intelligence import analyze_file
        result = await asyncio.to_thread(analyze_file, file_path, project_path)

        # Side-effect: record file visit in project memory
        if file_path and project_path:
            try:
                from dev.project_memory import record_file_visit
                await asyncio.to_thread(record_file_visit, project_path, file_path)
            except Exception:
                pass

        logger.info("[DEV_CONTEXT] action=active_file_context file=%s lang=%s latency_ms=%d cache_hit=%s project=%s",
                    file_path, result.get("language"), int((time.monotonic() - t0) * 1000),
                    result.get("cache_hit"), project_path)
        return result
    except Exception as exc:
        logger.error("[DEV_CONTEXT] active_file_context error: %s", exc)
        return {"project": project_path, "file": file_path, "language": "unknown",
                "framework": "unknown", "summary": "", "imports": [],
                "symbols": [], "todos": [], "issues": [], "hash": None}


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 9 — Task 2: Project Memory
# ═══════════════════════════════════════════════════════════════════════════════

class ProjectMemoryUpdateRequest(BaseModel):
    project_path: Optional[str] = None
    updates: dict[str, Any] = {}


class SessionSummaryRequest(BaseModel):
    project_path: Optional[str] = None
    summary: str


@router.get("/project-memory")
async def get_project_memory(
    project: Optional[str] = Query(None, description="Project path (defaults to active)"),
):
    """Return persistent project memory for the active or given project."""
    project_path = project or _active_project()
    if not project_path:
        return {"error": "no active project"}
    try:
        from dev.project_memory import get_memory
        return await asyncio.to_thread(get_memory, project_path)
    except Exception as exc:
        logger.error("[PROJECT_MEMORY] get endpoint error: %s", exc)
        return {"error": str(exc)}


@router.post("/project-memory/update")
async def update_project_memory(req: ProjectMemoryUpdateRequest):
    """Update mutable fields in project memory."""
    project_path = req.project_path or _active_project()
    if not project_path:
        return {"error": "no active project"}
    try:
        from dev.project_memory import update_memory
        return await asyncio.to_thread(update_memory, project_path, req.updates)
    except Exception as exc:
        logger.error("[PROJECT_MEMORY] update endpoint error: %s", exc)
        return {"error": str(exc)}


@router.post("/project-memory/session-summary")
async def add_project_session_summary(req: SessionSummaryRequest):
    """Append a session summary to project memory."""
    project_path = req.project_path or _active_project()
    if not project_path:
        return {"error": "no active project"}
    try:
        from dev.project_memory import add_session_summary
        await asyncio.to_thread(add_session_summary, project_path, req.summary)
        return {"ok": True}
    except Exception as exc:
        return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 9 — Task 3 & 7: Terminal Intelligence + Terminal Output Capture
# ═══════════════════════════════════════════════════════════════════════════════

class TerminalAnalyzeRequest(BaseModel):
    output: str
    command: str = ""
    project: Optional[str] = None
    cwd: Optional[str] = None
    exit_code: Optional[int] = None


@router.post("/analyze-terminal")
async def analyze_terminal(req: TerminalAnalyzeRequest):
    """Classify a terminal error and return diagnosis + fix suggestions."""
    project = req.project or _active_project()
    try:
        from dev.terminal_intelligence import analyze_terminal as _analyze
        result = await asyncio.to_thread(_analyze, req.output, req.command, project, req.cwd)
        return result
    except Exception as exc:
        logger.error("[TERMINAL_INTEL] endpoint error: %s", exc)
        return {"detected": False, "error": str(exc)}


@router.post("/terminal-output")
async def terminal_output(req: TerminalAnalyzeRequest):
    """
    Receive terminal output from a frontend/terminal wrapper.
    Analyzes errors and stores the latest event for the project.
    """
    project = req.project or _active_project()
    try:
        from dev.terminal_intelligence import analyze_terminal as _analyze
        result = await asyncio.to_thread(_analyze, req.output, req.command, project, req.cwd)
        logger.info("[TERMINAL_INTEL] terminal-output received command=%r exit_code=%s project=%s detected=%s",
                    req.command[:50], req.exit_code, project, result.get("detected"))
        return {"received": True, "analysis": result}
    except Exception as exc:
        logger.error("[TERMINAL_INTEL] terminal-output error: %s", exc)
        return {"received": False, "error": str(exc)}


@router.get("/latest-terminal-error")
async def latest_terminal_error(
    project: Optional[str] = Query(None),
):
    """Return the most recent terminal error analysis for the active project."""
    project_path = project or _active_project()
    if not project_path:
        return None
    try:
        from dev.terminal_intelligence import get_latest_error
        return get_latest_error(project_path)
    except Exception as exc:
        return {"error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 9 — Task 4: Dev Observer SSE stream
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/observer-stream")
async def observer_stream():
    """SSE stream of passive dev insights from the background observer."""
    try:
        from dev.dev_observer import observer_sse_stream
        return StreamingResponse(observer_sse_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except Exception as exc:
        logger.error("[DEV_OBSERVER] stream endpoint error: %s", exc)
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"]),
            media_type="text/event-stream",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 9 — Task 9: Safety — patch preview stub
# ═══════════════════════════════════════════════════════════════════════════════

class PatchPreviewRequest(BaseModel):
    file_path: str
    patch: str
    description: str = ""


@router.post("/patch-preview")
async def patch_preview(req: PatchPreviewRequest):
    """
    Show a patch preview. Auto-application is disabled — confirmation required.
    Returns the patch for the frontend to display before any action.
    """
    logger.info("[DEV_CONTEXT] patch_preview requested file=%s action=preview", req.file_path)
    return {
        "preview": req.patch,
        "file_path": req.file_path,
        "description": req.description,
        "requires_confirmation": True,
        "auto_apply": False,
        "message": "Review the patch above. Confirm to apply.",
    }
