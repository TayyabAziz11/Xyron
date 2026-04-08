"""Voice transcription endpoint — accepts audio blob, returns transcript."""
from __future__ import annotations
import logging
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
logger = logging.getLogger(__name__)


@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Accept WebM/WAV audio blob from browser push-to-talk.

    Returns: {"success": true, "data": {"text": "...", "language": "en"}}

    Note: requires faster-whisper installed.
    Falls back to empty transcript if whisper not available.
    """
    try:
        audio_bytes = await audio.read()

        # Try to use faster-whisper
        try:
            from faster_whisper import WhisperModel

            # Save to temp file
            suffix = ".webm" if "webm" in (audio.content_type or "") else ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                model = WhisperModel("base", device="cpu", compute_type="int8")
                segments, info = model.transcribe(
                    tmp_path,
                    beam_size=5,
                    vad_filter=True,
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
                return {"success": True, "data": {"text": text, "language": info.language}}
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        except ImportError:
            logger.warning("faster-whisper not available, returning empty transcript")
            return {
                "success": True,
                "data": {
                    "text": "",
                    "language": "en",
                    "note": "Install faster-whisper for real transcription: pip install faster-whisper",
                },
            }
    except Exception as exc:
        logger.error("Transcription error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
