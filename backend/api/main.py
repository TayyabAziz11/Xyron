"""
AI Operator — FastAPI application entry point.

Mounts all routers under /api/v1 with CORS configured for the web dashboard.
Agent registry is initialized at startup.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import health, commands, approvals, activity, integrations, workflows

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
    title="AI Operator API",
    description=(
        "Backend API for the AI Operator dashboard — "
        "command intake, approvals, activity, and integrations."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
async def startup() -> None:
    _init_agent_registry()


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
