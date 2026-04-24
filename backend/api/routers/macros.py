"""Voice macros router — Feature #5"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.macro_service import macro_service

router = APIRouter(prefix="/api/v1/macros", tags=["macros"])


class MacroCreate(BaseModel):
    trigger: str
    name:    str
    steps:   list[dict]


class MacroToggle(BaseModel):
    active: bool


@router.get("")
async def list_macros():
    return {"macros": macro_service.list_all()}


@router.post("")
async def create_macro(body: MacroCreate):
    m = macro_service.create(body.trigger, body.name, body.steps)
    return {"macro": m}


@router.delete("/{macro_id}")
async def delete_macro(macro_id: int):
    ok = macro_service.delete(macro_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Macro not found")
    return {"ok": True}


@router.patch("/{macro_id}/toggle")
async def toggle_macro(macro_id: int, body: MacroToggle):
    ok = macro_service.toggle(macro_id, body.active)
    if not ok:
        raise HTTPException(status_code=404, detail="Macro not found")
    return {"ok": True}


@router.post("/match")
async def match_macro(body: dict):
    """Check if text matches a macro trigger — returns macro or null."""
    text  = body.get("text", "")
    macro = macro_service.match(text)
    return {"macro": macro}
