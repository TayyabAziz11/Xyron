"""Voice transcription and synthesis endpoints."""
from __future__ import annotations
import logging
import sys
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import Response

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


@router.post("/synthesize")
async def synthesize_text(request: Request):
    """
    Convert text to speech audio (WAV).

    Body: {"text": "...", "rate": 165, "volume": 0.9}
    Returns: audio/wav binary stream

    Requires pyttsx3 + espeak-ng installed.
    Returns 503 if TTS is not available.
    """
    # Ensure the voice package is importable
    _voice_root = Path(__file__).parent.parent.parent
    if str(_voice_root) not in sys.path:
        sys.path.insert(0, str(_voice_root))

    try:
        from voice.tts_service import synthesize_speech, is_tts_available
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"TTS module not found: {exc}")

    body = await request.json()
    text = body.get("text", "").strip()
    rate = body.get("rate", 165)
    volume = body.get("volume", 0.9)

    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if not is_tts_available():
        raise HTTPException(
            status_code=503,
            detail="TTS not available. Install: sudo apt-get install espeak-ng && pip install pyttsx3",
        )

    audio_bytes = synthesize_speech(text, rate=int(rate), volume=float(volume))
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/tts-info")
async def tts_info():
    """Return TTS engine availability and metadata."""
    _voice_root = Path(__file__).parent.parent.parent
    if str(_voice_root) not in sys.path:
        sys.path.insert(0, str(_voice_root))

    try:
        from voice.tts_service import get_tts_info
        return {"success": True, "data": get_tts_info()}
    except ImportError:
        return {"success": True, "data": {"available": False, "engine": None}}
