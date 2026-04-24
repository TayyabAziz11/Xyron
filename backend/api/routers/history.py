"""Voice-to-action history router — Feature #2"""
from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.history_service import history_service

router = APIRouter(prefix="/api/v1/history", tags=["history"])


class HistoryQuery(BaseModel):
    date:    str | None = None
    keyword: str | None = None
    limit:   int        = 50


@router.get("")
async def list_history(
    date:    str | None = Query(None),
    keyword: str | None = Query(None),
    limit:   int        = Query(50),
):
    if date:
        return {"entries": history_service.query_by_date(date)}
    if keyword:
        return {"entries": history_service.query_keyword(keyword, limit)}
    return {"entries": history_service.query_recent(limit)}


@router.get("/today")
async def today():
    today_str = datetime.now().strftime("%Y-%m-%d")
    entries   = history_service.query_by_date(today_str)
    spoken    = history_service.summarize_for_speech(today_str)
    return {"date": today_str, "entries": entries, "spoken_summary": spoken}


@router.get("/summary")
async def summary(date: str | None = Query(None)):
    d      = date or datetime.now().strftime("%Y-%m-%d")
    spoken = history_service.summarize_for_speech(d)
    return {"date": d, "spoken_summary": spoken}
