"""Automation router — HTTP endpoints for direct desktop/browser automation calls from the renderer."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional

router = APIRouter()


class ScrollBody(BaseModel):
    direction: str = "down"
    amount: int = 3


class TypeBody(BaseModel):
    text: str


class HotkeyBody(BaseModel):
    keys: str


class WorkflowBody(BaseModel):
    name: str
    variables: Optional[Dict[str, Any]] = None


@router.post("/scroll")
async def automation_scroll(body: ScrollBody):
    from api.tools import registry
    result = registry.execute(
        "desktop_scroll",
        {"direction": body.direction, "amount": body.amount},
        {},
    )
    return {"success": result.success, "spoken": result.spoken}


@router.post("/type")
async def automation_type(body: TypeBody):
    if not body.text:
        raise HTTPException(status_code=400, detail="'text' is required")
    from api.tools import registry
    result = registry.execute("desktop_type", {"text": body.text}, {})
    return {"success": result.success, "spoken": result.spoken}


@router.post("/hotkey")
async def automation_hotkey(body: HotkeyBody):
    if not body.keys:
        raise HTTPException(status_code=400, detail="'keys' is required")
    from api.tools import registry
    result = registry.execute("desktop_hotkey", {"keys": body.keys}, {})
    return {"success": result.success, "spoken": result.spoken}


@router.post("/screenshot")
async def automation_screenshot():
    from api.tools import registry
    result = registry.execute("desktop_screenshot", {}, {})
    return {"success": result.success, "data": result.data, "spoken": result.spoken}


@router.post("/workflow")
async def automation_workflow(body: WorkflowBody):
    if not body.name:
        raise HTTPException(status_code=400, detail="'name' is required")
    from api.services.automation_workflow_service import automation_workflow_service
    result = automation_workflow_service.execute(body.name, body.variables or {}, {})
    return result


@router.get("/workflows")
async def list_workflows():
    from api.services.automation_workflow_service import automation_workflow_service
    return {"workflows": automation_workflow_service.list_workflows()}
