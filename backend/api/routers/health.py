"""Health and status endpoints."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter

from ..config import settings
from ..schemas.common import ApiResponse

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=ApiResponse[dict])
async def health() -> ApiResponse[dict]:
    """Basic liveness check."""
    return ApiResponse(data={"status": "ok", "service": "ai-operator-api"})


@router.get("/status", response_model=ApiResponse[dict])
async def status() -> ApiResponse[dict]:
    """Detailed system status — checks Python env, MCP servers, directories."""
    checks: dict = {}

    # Python environment
    checks["python_version"] = sys.version.split()[0]
    checks["python_executable"] = sys.executable

    # Key directories
    checks["repo_root"] = str(settings.repo_root)
    checks["repo_root_exists"] = settings.repo_root.exists()
    checks["logs_dir_exists"] = settings.logs_dir.exists()
    checks["pending_approval_dir_exists"] = settings.pending_approval_dir.exists()
    checks["secrets_dir_exists"] = settings.secrets_dir.exists()

    # MCP servers
    mcp_dir = settings.mcp_servers_dir
    checks["mcp_servers_dir_exists"] = mcp_dir.exists()
    if mcp_dir.exists():
        checks["mcp_servers"] = [d.name for d in mcp_dir.iterdir() if d.is_dir()]
    else:
        checks["mcp_servers"] = []

    # Skills package
    skills_dir = settings.repo_root / "backend" / "src" / "ai_operator" / "skills"
    checks["skills_dir_exists"] = skills_dir.exists()

    # Overall health
    all_ok = checks["repo_root_exists"]
    checks["healthy"] = all_ok

    return ApiResponse(
        data=checks,
        message="System healthy" if all_ok else "Some checks failed",
    )
