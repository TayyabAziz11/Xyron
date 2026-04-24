"""Voice notes with semantic search — Feature #10"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.notes_service import notes_service

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


class NoteCreate(BaseModel):
    text:       str
    session_id: str = ""


class NoteSearch(BaseModel):
    query: str
    limit: int = 5


@router.post("")
async def create_note(body: NoteCreate, request: Request):
    from ..config import settings
    note = notes_service.add(body.text, settings.openai_api_key, body.session_id)
    return {"note": note, "spoken": f"Note saved: {body.text[:80]}"}


@router.get("")
async def list_notes(limit: int = 20):
    return {"notes": notes_service.list_recent(limit)}


@router.post("/search")
async def search_notes(body: NoteSearch, request: Request):
    from ..config import settings
    results = notes_service.semantic_search(body.query, settings.openai_api_key, body.limit)
    spoken  = notes_service.summarize_search_for_speech(results)
    return {"results": results, "spoken": spoken}


@router.delete("/{note_id}")
async def delete_note(note_id: int):
    ok = notes_service.delete(note_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"ok": True}
