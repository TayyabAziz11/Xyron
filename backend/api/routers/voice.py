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

_src_path = Path(__file__).parent.parent.parent / "src"
_voice_root = Path(__file__).parent.parent.parent


def _ensure_paths() -> None:
    for p in [str(_src_path), str(_voice_root)]:
        if p not in sys.path:
            sys.path.insert(0, p)


# ── Transcription ─────────────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Accept WebM/WAV/MP3/OGG audio from browser push-to-talk.

    Priority:
      1. OpenAI Whisper API  — best accuracy, handles WebM natively (no ffmpeg needed)
      2. faster-whisper local — needs ffmpeg for WebM; works well with WAV
      3. Empty result        — never crashes, returns helpful note

    Returns: {"success": true, "data": {"text": "...", "language": "en", "engine": "openai|local"}}
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # ── 1. OpenAI Whisper API (primary) ──────────────────────────────────────
    try:
        from api.config import settings
        _ensure_paths()
        if settings.openai_api_key and settings.openai_api_key.startswith("sk-"):
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            # Determine suffix — OpenAI accepts webm, wav, mp3, ogg, m4a, mp4
            ct = (audio.content_type or "").lower()
            suffix = (
                ".webm" if "webm" in ct else
                ".mp3"  if "mp3"  in ct else
                ".ogg"  if "ogg"  in ct else
                ".wav"
            )

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = Path(tmp.name)

            try:
                with open(tmp_path, "rb") as f:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language="en",
                        response_format="json",
                    )
                text = (result.text or "").strip()
                logger.info("OpenAI Whisper transcribed: %r (%d chars)", text[:60], len(text))
                return {
                    "success": True,
                    "data": {"text": text, "language": "en", "engine": "openai"},
                }
            finally:
                tmp_path.unlink(missing_ok=True)

    except Exception as exc:
        logger.warning("OpenAI Whisper failed, trying local: %s", exc)

    # ── 2. faster-whisper local (fallback) ───────────────────────────────────
    try:
        from faster_whisper import WhisperModel

        suffix = ".webm" if "webm" in (audio.content_type or "") else ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                str(tmp_path),
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            logger.info("Local Whisper transcribed: %r", text[:60])
            return {
                "success": True,
                "data": {"text": text, "language": info.language, "engine": "local"},
            }
        finally:
            tmp_path.unlink(missing_ok=True)

    except ImportError:
        logger.warning("faster-whisper not installed — no local STT fallback")
    except Exception as exc:
        logger.error("Local Whisper error: %s", exc)

    # ── 3. Total fallback ─────────────────────────────────────────────────────
    return {
        "success": True,
        "data": {
            "text": "",
            "language": "en",
            "engine": "none",
            "note": "No STT engine available. Set OPENAI_API_KEY or install faster-whisper.",
        },
    }


# ── Synthesis ─────────────────────────────────────────────────────────────────

@router.post("/synthesize")
async def synthesize_text(request: Request):
    """Convert text to WAV speech audio.

    Body: {"text": "...", "rate": 165, "volume": 0.9}
    Returns: audio/wav binary — 503 if espeak-ng not installed.
    """
    _ensure_paths()
    try:
        from voice.tts_service import synthesize_speech, is_tts_available
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"TTS module not found: {exc}")

    body = await request.json()
    text   = body.get("text", "").strip()
    rate   = int(body.get("rate", 165))
    volume = float(body.get("volume", 0.9))

    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if not is_tts_available():
        raise HTTPException(
            status_code=503,
            detail="TTS unavailable — run: sudo apt-get install espeak-ng && pip install pyttsx3",
        )

    audio_bytes = synthesize_speech(text, rate=rate, volume=volume)
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis produced no audio")

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/tts-info")
async def tts_info():
    """Return TTS engine metadata and availability."""
    _ensure_paths()
    try:
        from voice.tts_service import get_tts_info
        return {"success": True, "data": get_tts_info()}
    except ImportError:
        return {"success": True, "data": {"available": False, "engine": None}}
