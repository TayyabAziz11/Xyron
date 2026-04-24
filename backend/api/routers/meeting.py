"""Real-time meeting assistant router — Feature #6"""
from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile, File
from pydantic import BaseModel

from ..services.meeting_service import meeting_service

router = APIRouter(prefix="/api/v1/meeting", tags=["meeting"])


class TranscriptChunk(BaseModel):
    text:    str
    speaker: str = "participant"


@router.post("/start")
async def start_session():
    meeting_service.start_session()
    return {"ok": True, "message": "Meeting session started. I'm now listening in the background."}


@router.post("/stop")
async def stop_session():
    meeting_service.stop_session()
    return {"ok": True, "words_captured": meeting_service.word_count()}


@router.post("/chunk")
async def add_chunk(body: TranscriptChunk):
    """Push a transcribed chunk into the meeting buffer."""
    meeting_service.add_chunk(body.text, body.speaker)
    return {"ok": True}


@router.post("/chunk-audio")
async def add_audio_chunk(audio: UploadFile = File(...)):
    """Transcribe an audio chunk and add it to the meeting buffer."""
    import tempfile
    from pathlib import Path
    data = await audio.read()
    suffix = Path(audio.filename or "chunk.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp_path = f.name
    try:
        from openai import OpenAI
        from ..config import settings
        client = OpenAI(api_key=settings.openai_api_key)
        with open(tmp_path, "rb") as af:
            result = client.audio.transcriptions.create(model="whisper-1", file=af)
        text = result.text or ""
    except Exception as exc:
        text = ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    if text:
        meeting_service.add_chunk(text)
    return {"ok": True, "transcribed": text}


@router.post("/summarize")
async def summarize(request: Request):
    body        = await request.json()
    last_minutes = body.get("last_minutes", 10)
    from ..config import settings
    summary = await meeting_service.summarize(settings.openai_api_key, last_minutes)
    return {"summary": summary, "spoken": summary}


@router.get("/status")
async def status():
    return {
        "active":       meeting_service.is_active,
        "word_count":   meeting_service.word_count(),
        "transcript":   meeting_service.get_transcript(5),
    }
