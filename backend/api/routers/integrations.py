"""Integration status endpoints — check which services are connected."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter

from ..schemas.integration import Integration
from ..schemas.common import ApiResponse
from ..services.integration_service import integration_service

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


@router.get("", response_model=ApiResponse[List[Integration]])
async def list_integrations() -> ApiResponse[List[Integration]]:
    """
    Return status for all known integrations.

    Status is derived by checking whether credential files exist in .secrets/
    and whether MCP server directories exist.
    """
    integrations = integration_service.list_integrations()
    return ApiResponse(data=integrations)


@router.get("/{integration_id}", response_model=ApiResponse[Integration])
async def get_integration(integration_id: str) -> ApiResponse[Integration]:
    """Get status for a single integration by ID."""
    from fastapi import HTTPException

    integration = integration_service.get(integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail=f"Integration {integration_id} not found")
    return ApiResponse(data=integration)
