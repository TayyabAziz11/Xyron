"""
Xyron — FastAPI application entry point.

Mounts all routers under /api/v1 with CORS configured for the web dashboard.
Agent registry is initialized at startup.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stdout,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import health, commands, approvals, activity, integrations, workflows, events, voice, drafts, system, tasks, reminders, history, macros, notes, meeting, proactive, automation, memory

logger = logging.getLogger(__name__)


def _init_agent_registry() -> None:
    """Register all agents with the command router at startup."""
    try:
        # Ensure the src/ package is importable
        src_path = settings.repo_root / "backend" / "src"
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        import ai_operator.agents.registry  # noqa: F401 — side-effect: registers agents
        logger.info("Agent registry initialized successfully")
    except Exception as exc:
        logger.warning("Agent registry init failed (non-fatal): %s", exc)


app = FastAPI(
    title="Xyron API",
    description=(
        "Backend API for the Xyron dashboard — "
        "command intake, approvals, activity, and integrations."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
async def startup() -> None:
    _init_agent_registry()
    # Pre-warm local Whisper model so first voice transcription has no cold-start delay
    try:
        import threading as _threading
        from .routers.voice import _get_local_whisper_model as _warmup_whisper
        _threading.Thread(target=_warmup_whisper, daemon=True, name="whisper-warmup").start()
    except Exception:
        pass
    # Start background services
    try:
        from .config import settings as _s
        from .services.screen_context_service import screen_context_service
        from .services.proactive_service import proactive_service
        from .tools import browser_tools  # noqa: F401 — registers browser tools
        if _s.openai_api_key and _s.openai_api_key.startswith("sk-"):
            screen_context_service.start(_s.openai_api_key)
            proactive_service.start(_s.openai_api_key)
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).warning("Background service startup failed: %s", _exc)


# CORS — allow the Next.js dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers under /api/v1
app.include_router(health.router)
app.include_router(commands.router)
app.include_router(approvals.router)
app.include_router(activity.router)
app.include_router(integrations.router)
app.include_router(workflows.router)
app.include_router(events.router)
app.include_router(voice.router)
app.include_router(drafts.router)
app.include_router(system.router)
app.include_router(tasks.router)
app.include_router(reminders.router)
app.include_router(history.router)
app.include_router(macros.router)
app.include_router(notes.router)
app.include_router(meeting.router)
app.include_router(proactive.router)
app.include_router(automation.router, prefix="/api/v1/automation", tags=["automation"])
app.include_router(memory.router)
