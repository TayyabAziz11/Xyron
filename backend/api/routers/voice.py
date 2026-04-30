"""Voice transcription, synthesis, and streaming AI response endpoints."""
from __future__ import annotations
import json
import logging
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from ..constants.prompts import CORE_IDENTITY

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])
logger = logging.getLogger(__name__)

# ── Lazy service imports (avoid circular deps at module load) ─────────────────
def _get_history():
    from ..services.history_service import history_service
    return history_service

def _get_macro():
    from ..services.macro_service import macro_service
    return macro_service

def _get_notes():
    from ..services.notes_service import notes_service
    return notes_service

def _get_screen_ctx():
    from ..services.screen_context_service import screen_context_service
    return screen_context_service

def _get_proactive():
    from ..services.proactive_service import proactive_service
    return proactive_service

_src_path    = Path(__file__).parent.parent.parent / "src"
_voice_root  = Path(__file__).parent.parent.parent


def _ensure_paths() -> None:
    for p in [str(_src_path), str(_voice_root)]:
        if p not in sys.path:
            sys.path.insert(0, p)


# ── Text cleaning for speech ──────────────────────────────────────────────────

def _clean_for_speech(text: str, max_chars: int = 300) -> str:
    """Strip markdown, code, URLs and truncate so TTS sounds natural."""
    if not text:
        return ""
    t = text.strip()
    # Code blocks
    t = re.sub(r"```[\s\S]*?```", "See the screen for code.", t)
    t = re.sub(r"`[^`]+`", "", t)
    # Markdown headers
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    # Links → keep visible text
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Raw URLs
    t = re.sub(r"https?://\S+", "", t)
    # Bold / italic
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*",   r"\1", t)
    t = re.sub(r"__([^_]+)__",   r"\1", t)
    # Bullets / numbered lists
    t = re.sub(r"^\s*[-*\u2022]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\d+\.\s+",         "", t, flags=re.MULTILINE)
    # Collapse whitespace
    t = re.sub(r"\n+", ". ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    # Trim to first N chars while keeping complete sentences
    if len(t) > max_chars:
        cut = t[:max_chars].rfind(".")
        t   = t[:cut + 1] if cut > max_chars // 3 else t[:max_chars]
    return t.strip()


# ── Whisper local model cache (avoids 2-3s cold-start on first transcription) ─
_local_whisper_model = None


def _get_local_whisper_model():
    global _local_whisper_model
    if _local_whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info("Loading local Whisper 'base' model...")
        _local_whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("Local Whisper model ready.")
    return _local_whisper_model


# ── Transcription ─────────────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Accept WebM/WAV/MP3/OGG audio from browser push-to-talk.

    Priority:
      1. OpenAI Whisper API  — best accuracy, handles WebM natively
      2. faster-whisper local — fallback, needs ffmpeg for WebM
      3. Empty result         — never crashes

    Returns: {"success": true, "data": {"text": "...", "engine": "openai|local"}}
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # ── Minimum-size gate ─────────────────────────────────────────────────────
    # WebM/OGG containers have ~200–400 bytes of headers with no actual audio.
    # WAV with <0.1 s at 16kHz/16-bit = <3200 bytes of PCM.
    # Sending sub-threshold clips to Whisper wastes quota and always returns
    # 'audio_too_short'. Silently drop them instead.
    _MIN_AUDIO_BYTES = 4000
    if len(audio_bytes) < _MIN_AUDIO_BYTES:
        logger.debug("Audio clip too small (%d bytes) — skipping transcription", len(audio_bytes))
        return {"success": True, "data": {"text": "", "language": "en", "engine": "none"}}

    # ── 1. OpenAI Whisper API ─────────────────────────────────────────────────
    try:
        from ..config import settings
        _ensure_paths()
        if settings.openai_api_key and settings.openai_api_key.startswith("sk-"):
            from openai import OpenAI, BadRequestError
            client = OpenAI(api_key=settings.openai_api_key)

            ct     = (audio.content_type or "").lower()
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
                        response_format="verbose_json",
                    )
                text = (result.text or "").strip()
                raw_lang = getattr(result, "language", "en") or "en"
                lang = raw_lang if raw_lang in ("en", "ur") else "en"
                logger.info("OpenAI Whisper: %r lang=%s (%d chars)", text[:60], lang, len(text))
                return {"success": True, "data": {"text": text, "language": lang, "engine": "openai"}}
            except BadRequestError as bre:
                # 'audio_too_short' or similar API-level validation errors
                # are not real failures — just silence or a mic blip.
                err_code = getattr(bre, "code", "") or ""
                if "too_short" in str(err_code) or "too_short" in str(bre).lower():
                    logger.debug("Whisper: audio too short (%d bytes) — treating as silence", len(audio_bytes))
                    return {"success": True, "data": {"text": "", "language": "en", "engine": "none"}}
                logger.warning("OpenAI Whisper API error: %s", bre)
            finally:
                tmp_path.unlink(missing_ok=True)

    except Exception as exc:
        logger.warning("OpenAI Whisper failed: %s", exc)

    # ── 2. faster-whisper local (optional) ───────────────────────────────────
    try:
        suffix = ".webm" if "webm" in (audio.content_type or "") else ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            model = _get_local_whisper_model()
            segments, info = model.transcribe(
                str(tmp_path),
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            logger.info("Local Whisper: %r", text[:60])
            return {"success": True, "data": {"text": text, "language": info.language, "engine": "local"}}
        finally:
            tmp_path.unlink(missing_ok=True)

    except ImportError:
        pass  # faster-whisper not installed — OpenAI is the primary engine
    except Exception as exc:
        logger.error("Local Whisper error: %s", exc)

    # ── 3. Empty fallback ─────────────────────────────────────────────────────
    return {"success": True, "data": {"text": "", "language": "en", "engine": "none"}}


# ── Synthesis ─────────────────────────────────────────────────────────────────

@router.post("/synthesize")
async def synthesize_text(request: Request):
    """Convert text to speech audio.

    Priority:
      1. OpenAI TTS API (tts-1, nova voice) — real-time model, low latency, MP3
      2. pyttsx3 / espeak-ng               — offline fallback, WAV

    Body:   {"text": "...", "rate": 165, "volume": 0.9, "voice": "nova"}
    Returns: audio/mpeg or audio/wav binary
    """
    _ensure_paths()
    body   = await request.json()
    text   = body.get("text", "").strip()
    voice  = body.get("voice", "nova")   # OpenAI voice name
    speed  = float(body.get("speed", 1.0))

    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Always clean the text before speaking
    text = _clean_for_speech(text, max_chars=4000)
    if not text:
        raise HTTPException(status_code=400, detail="text is empty after cleaning")

    # ── 1. OpenAI TTS (natural quality) ──────────────────────────────────────
    try:
        from ..config import settings
        if settings.openai_api_key and settings.openai_api_key.startswith("sk-"):
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            # Clamp speed to OpenAI's supported range
            speed = max(0.25, min(4.0, speed))

            tts_resp = client.audio.speech.create(
                model="tts-1",  # Real-time model: lower latency, near-identical quality for voice assistant
                voice=voice,
                input=text,
                response_format="mp3",
                speed=speed,
            )
            mp3_bytes = tts_resp.content
            logger.info("OpenAI TTS (%s): %d chars → %d bytes", voice, len(text), len(mp3_bytes))
            return Response(
                content=mp3_bytes,
                media_type="audio/mpeg",
                headers={"Cache-Control": "no-cache", "X-TTS-Engine": "openai"},
            )
    except Exception as exc:
        logger.warning("OpenAI TTS failed, falling back to pyttsx3: %s", exc)

    # ── 2. pyttsx3 / espeak-ng fallback ──────────────────────────────────────
    try:
        from voice.tts_service import synthesize_speech, is_tts_available
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"TTS module not found: {exc}")

    if not is_tts_available():
        raise HTTPException(
            status_code=503,
            detail="TTS unavailable. Set OPENAI_API_KEY for quality voice, or: sudo apt-get install espeak-ng",
        )

    rate       = int(body.get("rate", 165))
    volume     = float(body.get("volume", 0.9))
    wav_bytes  = synthesize_speech(text, rate=rate, volume=volume)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS produced no audio")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-cache", "X-TTS-Engine": "pyttsx3"},
    )


@router.post("/synthesize-stream")
async def synthesize_stream(request: Request):
    """Stream TTS audio bytes directly from OpenAI with zero buffering.

    Bytes are piped as they arrive from OpenAI — the client can start playing
    before the full audio is generated (used with MediaSource API on frontend).

    Body:   {"text": "...", "voice": "nova", "speed": 1.0}
    Returns: audio/mpeg stream
    """
    _ensure_paths()
    body  = await request.json()
    text  = body.get("text", "").strip()
    voice = body.get("voice", "nova")
    speed = float(body.get("speed", 1.0))

    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    text = _clean_for_speech(text, max_chars=4000)
    if not text:
        raise HTTPException(status_code=400, detail="text is empty after cleaning")

    try:
        from ..config import settings
        if not (settings.openai_api_key and settings.openai_api_key.startswith("sk-")):
            raise HTTPException(status_code=503, detail="OpenAI API key required for streaming TTS")

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        speed  = max(0.25, min(4.0, speed))

        async def _stream_bytes():
            async with client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice=voice,
                input=text,
                response_format="mp3",
                speed=speed,
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=4096):
                    yield chunk

        return StreamingResponse(
            _stream_bytes(),
            media_type="audio/mpeg",
            headers={
                "Cache-Control":     "no-cache",
                "X-Accel-Buffering": "no",
                "X-TTS-Engine":      "openai-stream",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Streaming TTS error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/tts-info")
async def tts_info():
    """Return TTS engine availability and active engine."""
    _ensure_paths()
    info: dict = {"openai_tts": False, "pyttsx3": False, "active_engine": "none"}

    try:
        from ..config import settings
        if settings.openai_api_key and settings.openai_api_key.startswith("sk-"):
            info["openai_tts"]    = True
            info["active_engine"] = "openai"
    except Exception:
        pass

    try:
        from voice.tts_service import is_tts_available
        info["pyttsx3"] = is_tts_available()
        if info["active_engine"] == "none" and info["pyttsx3"]:
            info["active_engine"] = "pyttsx3"
    except Exception:
        pass

    return {"success": True, "data": info}


# ── Streaming AI response ─────────────────────────────────────────────────────

class _ConvTurn(BaseModel):
    role: str   # "user" | "assistant"
    text: str

class _RespondStreamBody(BaseModel):
    text:             str
    history:          list[_ConvTurn] = []
    session_id:       str             = ""   # optional — used for server-side memory
    use_tools:        bool            = True  # attempt tool calling before streaming
    personality_mode: str             = ""   # e.g. "boss", "friendly", "professional", "work", "chill"
    active_profile:   str             = ""   # named voice profile (overrides personality_mode)
    language:         str             = "en" # detected language: "en" or "ur"

# Sentence-boundary split: flush when whitespace follows sentence-ending punctuation
_SENT_RE = re.compile(r'(?<=[.!?])\s+')

# Per-mode personality addons injected into the system prompt
_PERSONALITY_ADDONS: dict[str, str] = {
    "boss": (
        "\n\nPERSONALITY — BOSS MODE: "
        "Address the user as 'boss' naturally in every response — not every sentence, "
        "but at least once per reply, usually at the start or end. "
        "Examples: 'On it, boss.', 'Right away, boss.', 'Here's what I found, boss.', 'Done, boss.' "
        "Be confident, efficient, and show you take every request seriously. "
        "Never skip the 'boss' address — this is the user's preferred style."
    ),
    "friendly": (
        "\n\nPERSONALITY — FRIENDLY MODE: "
        "Be especially warm, encouraging, and casual. Use phrases like 'Sure!', 'Absolutely!', "
        "'Great question!', 'Happy to help!' — keep it upbeat and approachable."
    ),
    "professional": (
        "\n\nPERSONALITY — PROFESSIONAL MODE: "
        "Be precise and concise. Skip pleasantries. Deliver information efficiently. "
        "Use clear, formal language. No filler words."
    ),
    # ── Feature #4: Multi-voice profiles ─────────────────────────────────────
    "work": (
        "\n\nPERSONALITY — WORK MODE: "
        "You are focused, efficient, and professional. Prioritize task completion. "
        "Skip small talk. Use bullet-style verbal summaries: quick, numbered if multiple points. "
        "Always ask if there's a next step after completing a task."
    ),
    "chill": (
        "\n\nPERSONALITY — CHILL MODE: "
        "You are relaxed, conversational, and easygoing. Use casual language — 'sure', 'cool', 'no worries'. "
        "Feel free to add a light joke or observation occasionally. Don't rush. Take it easy."
    ),
    "focus": (
        "\n\nPERSONALITY — FOCUS MODE: "
        "Ultra-minimal responses. Answer in one sentence max. No pleasantries, no filler. "
        "Speak only what is essential. The user is deep in work — minimize interruption."
    ),
    "assistant": (
        "\n\nPERSONALITY — ASSISTANT MODE: "
        "Be warm, natural, and conversational — like a helpful friend, not a robot. "
        "Use everyday language. It's fine to say 'sure', 'got it', 'no problem', 'here you go'. "
        "Keep answers short and human. A little personality is welcome."
    ),
}

# ── Voice profile → TTS voice mapping (Feature #4) ───────────────────────────
_PROFILE_VOICES: dict[str, str] = {
    "work":         "onyx",      # deep, authoritative
    "chill":        "shimmer",   # soft, relaxed
    "focus":        "echo",      # neutral, minimal
    "boss":         "onyx",
    "friendly":     "nova",
    "professional": "alloy",
    "assistant":    "nova",
}

# ── Profile switch voice commands (Feature #4) ────────────────────────────────
_PROFILE_SWITCH_RE = re.compile(
    r"switch\s+to\s+(?P<profile>work|chill|focus|boss|friendly|professional|assistant)\s*mode",
    re.IGNORECASE,
)

# ── Chill mode action trigger ─────────────────────────────────────────────────
# Distinct from profile switch — these phrases activate chill mode AND open media
_CHILL_TRIGGER_RE = re.compile(
    r'\b(?:chill\s+(?:mode|time|out)|let(?:\'s|s)?\s+chill|relax\s+(?:mode|time)|vibe\s+(?:mode|time)|kick\s+back|chilling\s+time|chill\s+vibes?|i\s+want\s+to\s+chill|time\s+to\s+chill|chill\s+for\s+a\s+bit|i\'?m\s+tired|wanna\s+chill|feeling\s+(?:tired|lazy|bored))\b',
    re.IGNORECASE,
)

# ── Chill mode follow-up (recommendations) ───────────────────────────────────
_CHILL_FOLLOWUP_RE = re.compile(
    r'\b(?:yes|sure|yeah|yep|tell\s+me|suggest|recommend|what\s+should\s+(?:i\s+)?watch|'
    r'any\s+good|good\s+pick|what(?:\'s|s)\s+good|show\s+me\s+some)\b',
    re.IGNORECASE,
)

_CHILL_RECOMMENDATIONS = (
    "Alright, here are a few good picks. "
    "For Netflix: Arcane Season 2, Black Mirror, or Squid Game. "
    "For YouTube: some lo-fi music, a trending tech breakdown, or a mini-documentary. "
    "Or just say 'play lo-fi' and I'll open it for you."
)

# ── Morning Mode ──────────────────────────────────────────────────────────────
_MORNING_RE = re.compile(
    r'\b(?:good\s+morning|morning\s+routine|start\s+my\s+day|wake\s+up\s+mode|rise\s+and\s+shine)\b',
    re.IGNORECASE,
)

# ── Jarvis / Home Mode ────────────────────────────────────────────────────────
_JARVIS_HOME_RE = re.compile(
    r"(?:xyron[\s,]+)?i(?:'m|\\s+am)\s+(?:home|back)"
    r"|i\s+just\s+(?:got|arrived)\s+(?:home|back)"
    r"|welcome\s+me\s+home"
    r"|i(?:'ve|\\s+have)\s+arrived\s+home",
    re.IGNORECASE,
)

# ── Entertainment Mode 2.0 ────────────────────────────────────────────────────
_ENTERTAIN_RE = re.compile(
    r'\bplay\s+(?:something\s+(?:funny|interesting|cool|random|viral)|'
    r'(?:trending|popular|viral)\s+(?:music|videos?|content)|'
    r'(?:some\s+)?(?:lo[\-\s]?fi|lofi|relaxing|ambient)\s+music|'
    r'(?:a\s+)?(?:funny|comedy|meme)\s+(?:video|clip)|'
    r'trending\s+music|(?:me\s+)?(?:a\s+)?documentary)\b',
    re.IGNORECASE,
)
_ENTERTAIN_FUNNY_RE   = re.compile(r'\b(?:funny|comedy|meme|stand.?up)\b', re.IGNORECASE)
_ENTERTAIN_LOFI_RE    = re.compile(r'\b(?:lo[\-\s]?fi|lofi|relaxing|ambient|jazz|acoustic)\b', re.IGNORECASE)
_ENTERTAIN_TRENDING_RE = re.compile(r'\b(?:trending|popular|viral)\b', re.IGNORECASE)

# ── System control: shutdown / restart / sleep / hibernate / lock ─────────────
_SHUTDOWN_RE  = re.compile(r'\b(?:shut\s*(?:down|off)|power\s*(?:off|down))\b', re.IGNORECASE)
_RESTART_RE   = re.compile(r'\b(?:restart|reboot)\b', re.IGNORECASE)
_SLEEP_RE     = re.compile(r'\b(?:sleep|go\s+to\s+sleep|suspend|put\s+(?:the\s+)?(?:pc|computer|system)\s+to\s+sleep)\b', re.IGNORECASE)
_HIBERNATE_RE = re.compile(r'\bhibernate\b', re.IGNORECASE)
_LOCK_RE      = re.compile(r'\b(?:lock(?:\s+(?:the\s+)?(?:screen|computer|pc|system))?|lock\s+screen)\b', re.IGNORECASE)

# ── Process management ────────────────────────────────────────────────────────
_LIST_PROCS_RE = re.compile(
    r'\b(?:(?:list|show|what(?:\'s|\s+are)?|display)\s+(?:(?:all|running|active)\s+)?(?:processes?|tasks?|apps?))'
    r'|task\s*manager|what(?:\'s|\s+is)\s+(?:using|eating|consuming)\s+(?:my\s+)?(?:memory|ram|cpu)'
    r'|top\s+processes?\b',
    re.IGNORECASE,
)
_KILL_PROC_RE = re.compile(
    r'\b(?:kill|stop|end|terminate|close|force(?:\s+quit)?)\s+(?:process\s+|task\s+)?'
    r'(?P<proc>[A-Za-z0-9][A-Za-z0-9_\-\.\s]{0,40}?)(?:\s+process|\s+app|\s+task)?\s*$',
    re.IGNORECASE,
)
_STARTUP_LIST_RE = re.compile(
    r'\b(?:(?:show|list|what(?:\'s|\s+are)?|display)\s+(?:my\s+)?startup\s+(?:apps?|programs?|items?))'
    r'|what\s+(?:apps?|programs?)\s+(?:run|start|launch)\s+(?:on\s+)?(?:startup|boot|start)'
    r'|what\s+(?:runs?|starts?|launches?)\s+(?:on\s+)?(?:startup|boot|start)'
    r'|(?:apps?|programs?)\s+(?:that\s+)?(?:run|start)\s+(?:on\s+)?(?:startup|boot)'
    r'|startup\s+(?:apps?|programs?|items?)\b',
    re.IGNORECASE,
)
_STARTUP_DISABLE_RE = re.compile(
    r'\b(?:disable|remove|stop)\s+(?P<app>[A-Za-z0-9][A-Za-z0-9_\-\.\s]{0,50}?)'
    r'\s+(?:from\s+)?(?:startup|boot|autostart|auto[- ]start)\b',
    re.IGNORECASE,
)

# ── Display control ───────────────────────────────────────────────────────────
_RESOLUTION_RE = re.compile(
    r'\b(?:set|change|switch)\s+(?:the\s+)?(?:screen\s+|display\s+)?resolution\s+to\s+'
    r'(?P<w>\d{3,4})\s*[xX×]\s*(?P<h>\d{3,4})'
    r'|(?P<preset>1080p|720p|4k|2k|1440p|fhd|hd|uhd)\b',
    re.IGNORECASE,
)
_REFRESH_RATE_RE = re.compile(
    r'\b(?:set|change|switch)\s+(?:the\s+)?(?:monitor\s+|display\s+|screen\s+)?refresh\s+rate\s+to\s+'
    r'(?P<rate>\d{2,3})\s*(?:hz|hertz)?\b'
    r'|\b(?P<rate2>\d{2,3})\s*hz\s+(?:refresh\s+rate|mode)\b',
    re.IGNORECASE,
)
_VDESK_CREATE_RE = re.compile(
    r'\b(?:create|add|new|open)\s+(?:a\s+)?(?:new\s+)?virtual\s+desktop\b'
    r'|\bnew\s+desktop\b',
    re.IGNORECASE,
)
_VDESK_SWITCH_RE = re.compile(
    r'\b(?:switch|go|move)\s+to\s+(?:the\s+)?(?P<dir>next|previous|prev|left|right)\s+(?:virtual\s+)?desktop\b'
    r'|\b(?P<dir2>next|previous|prev)\s+desktop\b',
    re.IGNORECASE,
)
_SCREENSHOT_RE = re.compile(
    r'\b(?:take\s+(?:a\s+)?screenshot|capture\s+(?:the\s+)?(?:screen|screenshot)|screenshot(?:\s+this)?)\b',
    re.IGNORECASE,
)

# ── Network / WiFi ────────────────────────────────────────────────────────────
_WIFI_LIST_RE = re.compile(
    r'\b(?:(?:show|list|scan|find|what(?:\'s|\s+are)?)\s+(?:available\s+)?(?:wifi|wi-fi|wireless)\s+(?:networks?|connections?|hotspots?)?)'
    r'|available\s+(?:wifi|wi-fi|networks?)'
    r'|(?:wifi|wi-fi)\s+(?:list|networks?|scan)\b'
    r'|\b(?:show|find|get|list)\s+me\s+(?:(?:near(?:by|est)?|available|closest)\s+)?(?:wifi|wi-fi|wireless)\b'
    r'|\b(?:wifi|wi-fi)\s+(?:near\s+me|nearby|around\s+here)\b',
    re.IGNORECASE,
)
_WIFI_CONNECT_RE = re.compile(
    r'\b(?:connect\s+to|join)\s+(?:wifi\s+|wi-fi\s+|network\s+)?["\']?(?P<ssid>[A-Za-z0-9][A-Za-z0-9_\-\.\s]{0,60}?)["\']?\s*$',
    re.IGNORECASE,
)
_WIFI_DISCONNECT_RE = re.compile(
    r'\b(?:disconnect|turn\s+off|disable)\s+(?:from\s+)?(?:wifi|wi-fi|wireless|the\s+network)\b'
    r'|(?:wifi|wi-fi)\s+(?:off|disconnect)\b',
    re.IGNORECASE,
)
_SPEED_TEST_RE = re.compile(
    r'\b(?:(?:run|do|check|test)\s+(?:(?:a|my|the)\s+)?(?:internet\s+|network\s+)?speed\s*test)'
    r'|(?:how\s+fast\s+is\s+(?:my\s+)?(?:internet|connection|wifi|wi-fi))'
    r'|(?:internet|network|wifi|connection)\s+speed\b',
    re.IGNORECASE,
)
_IP_INFO_RE = re.compile(
    r'\b(?:what(?:\'s|\s+is)\s+my\s+(?:ip|ip\s+address|public\s+ip|local\s+ip))'
    r'|(?:show|get|find)\s+(?:my\s+)?(?:ip|ip\s+address)'
    r'|my\s+(?:ip|ip\s+address)\b',
    re.IGNORECASE,
)
_FLUSH_DNS_RE = re.compile(
    r'\b(?:flush|clear|reset|purge)\s+(?:the\s+)?dns(?:\s+cache)?\b'
    r'|dns\s+(?:flush|reset|clear)\b',
    re.IGNORECASE,
)

# ── Date / time ──────────────────────────────────────────────────────────────
_DATE_TIME_RE = re.compile(
    r'\b(?:what(?:\'?s|\s+is)\s+(?:the\s+)?(?:current\s+)?(?:time|date|day)'
    r'|what\s+(?:time|date|day)\s+is\s+it'
    r'|(?:tell\s+me\s+(?:the\s+)?(?:time|date|day))'
    r'|(?:current\s+(?:time|date|day))'
    r'|(?:today(?:\'?s)?\s+date)'
    r'|(?:what\s+day\s+(?:is\s+(?:it|today)))'
    r'|whats\s+the\s+(?:time|date))\b',
    re.IGNORECASE,
)

# ── Battery & power ───────────────────────────────────────────────────────────
_BATTERY_RE = re.compile(
    r'\b(?:(?:how(?:\'?s|\s+is)\s+(?:my\s+)?(?:battery|charge))'
    r'|(?:battery\s+(?:level|status|percentage|life|percent|remaining|left|charge)?)'
    r'|(?:(?:how\s+much|what(?:\'?s|\s+is))\s+(?:the\s+|my\s+)?battery)'
    r'|(?:is\s+(?:my\s+)?(?:battery|laptop)\s+(?:charging|charged|full|low))'
    r'|(?:my\s+battery))\b',
    re.IGNORECASE,
)
_POWER_PLAN_RE = re.compile(
    r'\b(?:(?:switch|change|set|enable|use|activate)\s+(?:to\s+)?'
    r'(?P<plan>balanced|performance|high\s+performance|power\s+saver|saver|battery\s+saver)\s+(?:mode|plan)?)'
    r'|(?P<plan2>balanced|performance|high\s+performance|power\s+saver|saver)\s+(?:power\s+)?(?:mode|plan)\b',
    re.IGNORECASE,
)
_SCHED_SHUTDOWN_RE = re.compile(
    r'\b(?:shutdown|shut\s*down|turn\s+off|power\s+off)\s+(?:the\s+(?:pc|computer|system)\s+)?'
    r'in\s+(?P<n>\d+)\s+(?P<unit>minute|min|hour|hr)s?\b',
    re.IGNORECASE,
)

# ── Storage / disk ────────────────────────────────────────────────────────────
_DISK_USAGE_RE = re.compile(
    r'\b(?:(?:how\s+much\s+(?:disk\s+|storage\s+)?(?:space|room))'
    r'|(?:disk\s+(?:usage|space|storage))'
    r'|(?:storage\s+(?:usage|space))'
    r'|(?:(?:how\s+full|how\s+much)\s+(?:is\s+)?(?:\w+\s+)?drive)\b)',
    re.IGNORECASE,
)
_RECYCLE_BIN_RE = re.compile(
    r'\b(?:empty|clear|delete|clean)\s+(?:the\s+)?(?:recycle\s+bin|trash|recycling\s+bin)\b'
    r'|(?:recycle\s+bin|trash)\s+(?:empty|clear)\b',
    re.IGNORECASE,
)
_TEMP_SIZE_RE = re.compile(
    r'\b(?:(?:how\s+(?:big|large|much)\s+(?:is\s+)?(?:the\s+)?temp(?:orary)?\s+(?:folder|files?|folder))'
    r'|(?:temp(?:orary)?\s+files?\s+size)'
    r'|(?:size\s+of\s+temp(?:orary)?\s+(?:folder|files?)))\b',
    re.IGNORECASE,
)
_CLEAR_TEMP_RE = re.compile(
    r'\b(?:(?:clear|clean\s+up?|delete|remove)\s+(?:(?:all\s+)?temp(?:orary)?\s+files?|junk\s+files?))'
    r'|(?:clean\s+(?:up\s+)?(?:my\s+)?(?:temp|temporary|junk))\b',
    re.IGNORECASE,
)

# ── Audio ─────────────────────────────────────────────────────────────────────
_GET_VOLUME_RE = re.compile(
    r'\b(?:what(?:\'s|\s+is)\s+(?:the\s+)?(?:current\s+)?(?:volume|sound\s+level))'
    r'|(?:(?:current|how\s+(?:loud|quiet))\s+(?:is\s+(?:the\s+)?)?volume)'
    r'|volume\s+level\b',
    re.IGNORECASE,
)
_AUDIO_DEVICES_RE = re.compile(
    r'\b(?:(?:list|show|what(?:\'s|\s+are)?|display)\s+(?:(?:all|my|available)\s+)?'
    r'(?:audio|sound)\s+(?:devices?|outputs?|inputs?|speakers?|headphones?))'
    r'|(?:audio|sound)\s+devices?\b',
    re.IGNORECASE,
)
_SET_AUDIO_RE = re.compile(
    r'\b(?:(?:set|switch|change|use)\s+(?:(?:default\s+)?(?:audio|sound)\s+(?:to|device\s+to)|to))\s+'
    r'["\']?(?P<device>[A-Za-z0-9][A-Za-z0-9_\-\.\s]{0,60}?)["\']?\s*$'
    r'|switch\s+(?:audio|sound|output)\s+to\s+(?P<device2>[A-Za-z0-9].{0,60}?)\s*$',
    re.IGNORECASE,
)

# ── System maintenance ────────────────────────────────────────────────────────
_CLEAR_CLIPBOARD_RE = re.compile(
    r'\b(?:clear|wipe|empty|clean)\s+(?:(?:my\s+)?(?:the\s+)?)?clipboard\b',
    re.IGNORECASE,
)
_UPTIME_RE = re.compile(
    r'\b(?:(?:how\s+long\s+(?:has|have)\s+(?:the\s+)?(?:pc|computer|system|laptop)\s+been\s+(?:on|running|up))'
    r'|(?:system\s+uptime|uptime)'
    r'|(?:when\s+(?:was|did)\s+(?:the\s+)?(?:pc|computer|system)\s+(?:started?|booted?|turned\s+on)))\b',
    re.IGNORECASE,
)
_DISK_CLEANUP_RE = re.compile(
    r'\b(?:(?:run|open|launch|start)\s+(?:windows\s+)?disk\s+clean(?:up)?)'
    r'|(?:free\s+up\s+(?:disk\s+)?space)'
    r'|(?:clean\s+(?:up\s+(?:the\s+)?)?(?:disk|drive|c\s+drive))\b',
    re.IGNORECASE,
)
_WIN_UPDATES_RE = re.compile(
    r'\b(?:(?:check\s+(?:for\s+)?(?:windows\s+)?updates?)'
    r'|(?:any\s+(?:windows\s+|pending\s+)?updates?)'
    r'|(?:is\s+(?:windows|my\s+(?:pc|system))\s+(?:up\s+to\s+date|updated?))'
    r'|windows\s+update)\b',
    re.IGNORECASE,
)

# ── Smart open: play video / show picture / open named folder or file ─────────
_PLAY_MEDIA_RE = re.compile(
    r'\b(?:play|watch)\s+(?:the\s+|that\s+|my\s+|this\s+|a\s+)?(.+?)(?:\s+(?:video|movie|film|clip|song|music|audio))?\s*$',
    re.IGNORECASE,
)
# Matches "open [my] IT course folder" — name comes BEFORE the word "folder"
_OPEN_NAMED_FOLDER_SUFFIX_RE = re.compile(
    r'\b(?:open|show(?:\s+me)?|go\s+to|navigate\s+to)\s+'
    r'(?:(?:the|that|my|this)\s+)?'
    r'(.+?)\s+folder\s*$',
    re.IGNORECASE,
)
# Matches "open folder IT course" / "open folder named IT course" — name comes AFTER
_OPEN_NAMED_FOLDER_PREFIX_RE = re.compile(
    r'\b(?:open|show(?:\s+me)?|go\s+to|navigate\s+to)\s+'
    r'(?:(?:the|that|my|this)\s+)?'
    r'folder\s+(?:named\s+|called\s+)?'
    r'(.+?)\s*$',
    re.IGNORECASE,
)


def _OPEN_NAMED_FOLDER_RE_match(text: str):
    """Match both "open X folder" and "open folder X" patterns. Returns match or None."""
    return _OPEN_NAMED_FOLDER_SUFFIX_RE.search(text) or _OPEN_NAMED_FOLDER_PREFIX_RE.search(text)


# Keep the old name as a shim so existing callers that use .search() still work
class _FolderRE:
    def search(self, text: str):
        return _OPEN_NAMED_FOLDER_RE_match(text)


_OPEN_NAMED_FOLDER_RE = _FolderRE()
_OPEN_NAMED_FILE_RE = re.compile(
    r'\b(?:open|show(?:\s+me)?)\s+(?:the\s+|that\s+|my\s+|this\s+)?(.+?)\s+(?:picture|photo|image|pic|video|movie|film|clip|file|document|doc)\b',
    re.IGNORECASE,
)

_SYS_CONFIRM_RE = re.compile(
    r'\b(?:yes|confirm|proceed|go\s+ahead|do\s+it|sure|okay|ok|yep|affirmative)\b',
    re.IGNORECASE,
)

# ── System control: volume ────────────────────────────────────────────────────
_VOLUME_UP_RE   = re.compile(
    r'\b(?:(?:turn\s+)?volume\s+up|increase\s+(?:the\s+)?(?:volume|sound)|louder|raise\s+(?:the\s+)?volume)\b',
    re.IGNORECASE,
)
_VOLUME_DOWN_RE = re.compile(
    r'\b(?:(?:turn\s+)?volume\s+down|decrease\s+(?:the\s+)?(?:volume|sound)|quieter|lower\s+(?:the\s+)?volume|softer)\b',
    re.IGNORECASE,
)
_MUTE_RE   = re.compile(
    r'\bmute(?:\s+(?:the\s+)?(?:volume|sound|audio|system|pc))?\b',
    re.IGNORECASE,
)
_UNMUTE_RE = re.compile(
    r'\b(?:unmute|un\s*mute|turn\s+(?:the\s+)?(?:volume|sound|audio)\s+(?:back\s+)?on)\b',
    re.IGNORECASE,
)
_SET_VOLUME_RE = re.compile(
    r'\b(?:set|put|change|make|increase|decrease|turn)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(\d{1,3})\s*%?'
    r'|\bvolume\s+(?:to|at)\s+(\d{1,3})\s*%?',
    re.IGNORECASE,
)

# ── System control: brightness ────────────────────────────────────────────────
_BRIGHTNESS_UP_RE   = re.compile(
    r'\b(?:(?:turn\s+)?brightness\s+up|increase\s+(?:the\s+)?brightness|brighter|raise\s+(?:the\s+)?(?:brightness|screen))\b',
    re.IGNORECASE,
)
_BRIGHTNESS_DOWN_RE = re.compile(
    r'\b(?:(?:turn\s+)?brightness\s+down|decrease\s+(?:the\s+)?brightness|dimmer?|lower\s+(?:the\s+)?(?:brightness|screen))\b',
    re.IGNORECASE,
)
_SET_BRIGHTNESS_RE = re.compile(
    r'\b(?:set|put|change|make|increase|decrease|turn)\s+(?:the\s+)?brightness\s+(?:to|at)\s+(\d{1,3})\s*%?'
    r'|\bbrightness\s+(?:to|at)\s+(\d{1,3})\s*%?',
    re.IGNORECASE,
)

# ── Voice macro patterns (Feature #5) ────────────────────────────────────────
# Matched BEFORE GPT — zero LLM overhead for known macros

# ── Voice note patterns (Feature #10) ────────────────────────────────────────
_NOTE_SAVE_RE  = re.compile(
    r"^(?:note|note that|note\s*:|remember note|save note|jot down)[:\s]+(?P<note>.+)",
    re.IGNORECASE,
)
_NOTE_FIND_RE  = re.compile(
    r"what\s+did\s+i\s+(?:say|note|write)\s+about\s+(?P<topic>.+)|"
    r"find\s+(?:my\s+)?notes?\s+(?:about|on)\s+(?P<topic2>.+)|"
    r"search\s+(?:my\s+)?notes?\s+for\s+(?P<topic3>.+)",
    re.IGNORECASE,
)

# ── History query patterns (Feature #2) ──────────────────────────────────────
_HISTORY_RE = re.compile(
    r"what\s+did\s+i\s+do\s+(?P<when>today|yesterday|this morning|last night)|"
    r"(?:show|tell me|what(?:'s| is| was))\s+(?:my\s+)?(?:history|activity|commands?)\s*(?:for\s+)?(?P<date>[0-9\-]+)?",
    re.IGNORECASE,
)

# ── Meeting assistant patterns (Feature #6) ───────────────────────────────────
_MEETING_START_RE   = re.compile(r"start\s+(?:meeting|recording|transcrib)", re.IGNORECASE)
_MEETING_STOP_RE    = re.compile(r"stop\s+(?:meeting|recording|transcrib)", re.IGNORECASE)
_MEETING_SUMMARY_RE = re.compile(r"summarize\s+(?:what\s+was\s+said|the\s+meeting|meeting\s+so\s+far)", re.IGNORECASE)

# ── Ollama fallback (Feature #8) ─────────────────────────────────────────────
_OLLAMA_URL = "http://localhost:11434/api/generate"
_OLLAMA_MODEL = "llama3"

_VOICE_SYSTEM_PROMPT = (
    CORE_IDENTITY + "\n\n"
    "You are Xyron — a sharp, warm voice assistant. Think of yourself as a smart friend who happens to know everything. "
    "You talk like a real person: natural, relaxed, occasionally a touch of humour. Never stiff, never robotic. "
    "\n\nHOW TO RESPOND:"
    "\n• Short and direct — one sentence for simple things, two or three only when truly needed."
    "\n• Jump straight to the answer. Zero preamble ('Sure!', 'Of course!', 'Great question!' are all banned)."
    "\n• Zero markdown — no bullets, no headers, no bold. You're talking, not writing a doc."
    "\n• Mirror the user's tone — casual chat gets casual replies; sharp questions get sharp answers."
    "\n• Contractions always: 'I'll', 'you're', 'it's', 'can't'. Never sound like a manual."
    "\n\nPERSONALITY:"
    "\n• Casual questions ('how are you?', 'bored?') → reply like a friend would: 'Doing great! What do you need?'"
    "\n• Capability questions → be specific and confident: 'I can open any app, play YouTube, check prices, read news, answer questions, create folders — just say the word.'"
    "\n• Never say 'I'm just an AI' or hedge with 'I might be wrong'. Be confident."
    "\n• You CAN open apps, files, and system settings. Never claim you can't do something you actually can."
    "\n\nLANGUAGE:"
    "\n• Always reply in the same language the user speaks. If they write or speak in Urdu, reply fully in Urdu (use Urdu script — e.g. 'آپ کیسے ہیں؟'). If English, reply in English. Never mix unless the user does."
    "\n\nDATA RULES:"
    "\n• Tool results → always speak the real numbers/names/values. Never vague confirmations."
    "\n• Hardware/specs questions → use system_info tool data exactly, never guess."
    "\n• Context memory → if user says 'tell me more' or 'what about X', continue that topic."
    "\n• For local system queries always prefer system_info/system_health tools over web search."
)

# Keywords that must always route to system_info (never search_web or news)
_SYSTEM_INFO_KEYWORDS = frozenset([
    "cpu", "processor", "ram", "memory", "gigabyte", "gb ram", "cores",
    "my specs", "system specs", "system info", "computer specs", "laptop specs",
    "my computer", "my laptop", "my pc", "my machine", "my system",
    "what os", "which os", "operating system", "windows version",
    "disk space", "storage space", "hard drive space", "free space",
    "how much ram", "how much memory", "how much storage",
    "what cpu", "which cpu", "what processor", "which processor",
    "hardware", "specifications", "storage left", "space left",
    "drive space", "c drive space", "d drive space", "e drive space", "disk usage",
    "storage info", "system details", "computer info", "laptop info",
    # Broader patterns
    "tell my system", "tell me my system", "tell me about my",
    "my storage", "my ram", "my cpu", "my processor", "my memory",
    "my drives", "my drive", "my hard drive", "my ssd",
    "what's my ram", "what's my cpu", "what's my storage", "what's my specs",
    "whats my ram", "whats my cpu", "whats my storage", "whats my specs",
    "show my", "check my system",
    "how is my computer", "how is my laptop", "how is my pc",
])

# Keywords that force system_health tool
_SYSTEM_HEALTH_KEYWORDS = frozenset([
    "cpu usage", "cpu percent", "cpu load", "ram usage", "memory usage",
    "how is my system", "system performance", "is my computer slow",
    "is my laptop slow", "disk full", "disk usage", "system status",
    "how fast is my", "performance", "system load", "running slow",
])

# Phrases where "tell me more" / "tell me" should continue the last system tool.
# Only triggered for SHORT queries (≤ 6 words) so "tell me the news" doesn't match.
_CONTINUE_PHRASES = frozenset([
    "tell me more", "more details", "more info", "elaborate", "go on",
    "continue", "what else", "more about that", "give me more",
    "expand on that", "more information", "and", "yes", "okay", "ok",
    "please", "go ahead", "tell me", "keep going", "i see", "interesting",
    "and what else", "what about", "continue please", "say more",
])

_SYSTEM_TOOL_NAMES = frozenset([
    "system_info", "system_health", "get_running_apps",
    "volume_control", "brightness_control",
    "shutdown_system", "restart_system", "sleep_system", "hibernate_system", "lock_system",
    # extended tools
    "list_processes", "kill_process", "get_startup_apps", "disable_startup_app",
    "set_display_resolution", "set_refresh_rate", "virtual_desktop_create", "virtual_desktop_switch",
    "take_screenshot",
    "wifi_list", "wifi_connect", "wifi_disconnect", "network_speed_test", "get_ip_info", "flush_dns",
    "get_battery_status", "set_power_plan", "schedule_shutdown",
    "get_disk_usage", "empty_recycle_bin", "get_temp_files_size", "clear_temp_files",
    "get_volume", "mute_unmute", "list_audio_devices", "set_default_audio",
    "clear_clipboard", "get_uptime", "run_disk_cleanup", "check_windows_updates",
    "open_wifi_panel", "smart_open",
])

# Tools with pre-built spoken output — no GPT narration needed
_DIRECT_SPOKEN_TOOLS = frozenset({
    "system_info", "system_health", "create_folder", "create_subfolders",
    "open_directory", "open_application", "open_file", "list_directory",
    "minimize_window", "maximize_window", "close_window", "switch_window",
    "write_clipboard", "read_clipboard", "type_text",
    "kill_app", "open_system_settings",
    "volume_control", "brightness_control",
    "shutdown_system", "restart_system", "sleep_system", "hibernate_system", "lock_system",
    # extended tools
    "list_processes", "kill_process", "get_startup_apps", "disable_startup_app",
    "set_display_resolution", "set_refresh_rate", "virtual_desktop_create", "virtual_desktop_switch",
    "take_screenshot",
    "wifi_list", "wifi_connect", "wifi_disconnect", "network_speed_test", "get_ip_info", "flush_dns",
    "get_battery_status", "set_power_plan", "schedule_shutdown",
    "get_disk_usage", "empty_recycle_bin", "get_temp_files_size", "clear_temp_files",
    "get_volume", "mute_unmute", "list_audio_devices", "set_default_audio",
    "clear_clipboard", "get_uptime", "run_disk_cleanup", "check_windows_updates",
    "open_wifi_panel", "smart_open",
    # file operations — spoken result is already clear
    "delete_file", "move_file", "write_file", "search_files",
    "open_url", "open_drive",
    # desktop automation — result confirms the action
    "desktop_click", "desktop_focus_app", "desktop_hotkey",
    "desktop_scroll", "desktop_type", "desktop_screenshot",
    # browser — result confirms navigation
    "browser_navigate", "browser_click", "browser_fill",
    "browser_close", "browser_screenshot",
    # system info
    "get_running_apps",
})

# ── Retry + fallback config ───────────────────────────────────────────────────

# Tools that are safe to retry once on failure (idempotent or near-idempotent)
_RETRYABLE_TOOLS: dict[str, bool] = {
    "network_speed_test": True,
    "wifi_list": True,
    "get_ip_info": True,
    "read_inbox": True,
    "get_summary": True,
    "search_web": True,
    "open_url": True,
}

# Fallback: if tool X fails validation, try tool Y instead
# Format: tool_name → (fallback_tool, params_transformer)
_TOOL_FALLBACKS: dict[str, tuple] = {
    "open_application": ("smart_open", lambda p: {"query": p.get("app_name", p.get("name", "")), "type": "file"}),
    "open_file":        ("smart_open", lambda p: {"query": p.get("path", "").split("/")[-1].split("\\")[-1]}),
    "open_directory":   ("smart_open", lambda p: {"query": p.get("path", "").split("/")[-1].split("\\")[-1], "type": "folder"}),
}

# ── Intent classification ─────────────────────────────────────────────────────

_CONV_BYPASS_WORDS = frozenset([
    "haha", "hahaha", "lol", "lmao", "rofl", "omg", "nice", "cool", "wow",
    "okay", "ok", "sure", "fine", "alright", "yep", "yeah", "nope",
    "interesting", "really", "seriously", "i see", "got it",
])

_CONV_BYPASS_RE = re.compile(
    r"^(?:"
    r"haha+|lol|lmao|rofl|omg|nice|cool|wow"
    r"|you(?:\'re| are)\s+(?:funny|smart|dumb|stupid|silly|cool|amazing|great|awesome)"
    r"|that(?:\'s| is)\s+(?:funny|great|cool|awesome|silly|hilarious)"
    r"|(?:tell|say)\s+(?:me\s+)?(?:a\s+)?joke"
    r"|say\s+something\s+(?:funny|random|cool)"
    r"|make\s+me\s+laugh"
    r")$",
    re.IGNORECASE,
)

def _is_pure_conversation(text: str) -> bool:
    """Return True for casual chat inputs that need no tool routing."""
    lower = text.lower().strip().rstrip("!?.,")
    if len(lower.split()) <= 2 and lower in _CONV_BYPASS_WORDS:
        return True
    return bool(_CONV_BYPASS_RE.match(lower))


_CONV_SYSTEM_PROMPT = (
    CORE_IDENTITY + "\n\n"
    "You are Xyron, a witty and warm AI friend. The user is having casual conversation — "
    "no tasks, just chat. Respond naturally, warmly, with light humour when fitting. "
    "Keep it short: 1-2 sentences max. Be spontaneous and genuine. No markdown, no preamble."
)



def _is_system_info_query(text: str) -> bool:
    """Return True when the query is clearly about local hardware/OS specs."""
    lower = text.lower()
    return any(kw in lower for kw in _SYSTEM_INFO_KEYWORDS)


def _is_system_health_query(text: str) -> bool:
    """Return True when the query is about live CPU/RAM/disk usage percentages."""
    lower = text.lower()
    return any(kw in lower for kw in _SYSTEM_HEALTH_KEYWORDS)


def _is_continue_phrase(text: str) -> bool:
    lower = text.lower().strip()
    # Only treat as a continuation if the query is very short (ambiguous follow-up).
    # "tell me the news" (5 words) should NOT trigger this; "tell me" (2 words) should.
    word_count = len(lower.split())
    if word_count > 6:
        return False
    return any(lower.startswith(p) or lower == p for p in _CONTINUE_PHRASES)


# ── Memory: explicit "remember that X" detection ─────────────────────────────

_REMEMBER_RE = re.compile(
    r'^\s*(?:please\s+)?(?:remember|note|save|keep\s+in\s+mind)\s+(?:that\s+|this:\s*)?(.{3,200})',
    re.IGNORECASE,
)

# ── Personality evolution: "be more casual / formal / professional" ──────────
_PERSONALITY_RE = re.compile(
    r'\b(?:(?:please\s+)?be\s+(?:more\s+)?|from\s+now\s+on\s+(?:please\s+)?be\s+(?:more\s+)?|'
    r'talk\s+(?:to\s+me\s+)?(?:more\s+)?|(?:sound|act|speak)\s+(?:more\s+)?)(?P<style>casual|formal|professional|friendly|chill|relaxed|serious|concise|brief|detailed|warm|funny|cool|energetic)\b',
    re.IGNORECASE,
)

# ── Memory: "what do you remember" / "what do you know about me" ─────────────

_QUERY_MEMORY_RE = re.compile(
    r'\b(?:what(?:\s+do)?\s+you\s+(?:remember|know|recall|have\s+saved)|'
    r'what(?:\'?s|\s+is)\s+(?:saved|stored|in\s+your\s+memory)|'
    r'what\s+(?:do\s+)?you\s+know\s+about\s+me|'
    r'(?:tell\s+me\s+)?what\s+you\s+(?:remember|know|have\s+on\s+me)|'
    r'(?:do\s+you\s+)?(?:remember|recall)\s+(?:me|my\s+name|who\s+i\s+am))\b',
    re.IGNORECASE,
)

# ── Compound/multi-step request detection ─────────────────────────────────────
# "open Spotify and then play jazz", "search X and also open Y"

_COMPOUND_RE = re.compile(
    r'\b(?:and\s+then\s+(?:open|launch|start|search|play|find|go|check)|'
    r'then\s+(?:open|launch|start|search|play|find|go\s+to)|'
    r'after\s+that\s+(?:open|search|play|launch)|'
    r'also\s+(?:open|search|play|find|launch))\b',
    re.IGNORECASE,
)

# ── Wikipedia quick-fact routing ─────────────────────────────────────────────
# "what is Python", "who is Elon Musk", "tell me about the moon"
_WIKI_RE = re.compile(
    r'^(?:what(?:\'?s|\s+is)\s+(?:a\s+|an\s+|the\s+)?(?P<topic1>[^?]{3,80})\??|'
    r'who(?:\'?s|\s+is)\s+(?P<topic2>[^?]{3,80})\??|'
    r'tell\s+me\s+about\s+(?:the\s+)?(?P<topic3>[^?]{3,80})\??|'
    r'(?:define|definition\s+of|explain)\s+(?P<topic4>[^?]{3,80})\??)$',
    re.IGNORECASE,
)

# Guard phrases — these look like wiki queries but should go to GPT instead.
_WIKI_EXCLUDE_RE = re.compile(
    r'\b(?:weather|news|today|latest|current|price|stock|rate|bitcoin|crypto|my\s+name|your\s+name|you|time|date|doing|feel|think|want|need'
    r'|battery|charge|charging|wifi|volume|brightness|cpu|ram|disk|storage|uptime|ip\s+address|speed\s+test|my\s+(?:battery|volume|screen|laptop|pc|system|cpu|ram|disk))\b',
    re.IGNORECASE,
)


def _extract_wiki_topic(text: str) -> str | None:
    m = _WIKI_RE.match(text.strip())
    if not m:
        return None
    if _WIKI_EXCLUDE_RE.search(text):
        return None
    topic = (m.group("topic1") or m.group("topic2") or m.group("topic3") or m.group("topic4") or "").strip()
    topic = topic.rstrip("?.,!").strip()
    return topic or None


# ── Clipboard routing ─────────────────────────────────────────────────────────
_CLIPBOARD_READ_RE  = re.compile(r'\b(?:what(?:\'?s|\s+is)\s+(?:on\s+)?(?:my\s+)?clipboard|read\s+(?:my\s+)?clipboard|paste|show\s+clipboard)\b', re.IGNORECASE)
_CLIPBOARD_WRITE_RE = re.compile(r'\bcopy\s+(?:this\s+)?(?:to\s+(?:my\s+)?clipboard\s+)?["\']?(.{3,200}?)["\']?\s*(?:to\s+clipboard)?$', re.IGNORECASE)


# ── Screen / vision routing ───────────────────────────────────────────────────
_SCREEN_RE = re.compile(
    r'\b(?:what(?:\'?s|\s+is)\s+on\s+(?:my\s+)?screen|read\s+(?:my\s+)?screen|'
    r'what(?:\s+do)?\s+(?:i|you)\s+see\s+on\s+(?:my\s+)?screen|'
    r'describe\s+(?:my\s+)?screen|take\s+a\s+screenshot|screenshot)\b',
    re.IGNORECASE,
)

# ── Typing routing ────────────────────────────────────────────────────────────
_TYPE_RE = re.compile(
    r'^(?:type|write|type\s+this|write\s+this|type\s+for\s+me|type\s+out)\s*[:\-]?\s*["\']?(.{2,300}?)["\']?\s*$',
    re.IGNORECASE,
)

# ── Window control routing ────────────────────────────────────────────────────
_WIN_MINIMIZE_RE = re.compile(r'\b(?:minimize|minimise)\b(?:\s+(?:this|window|current|active))?\b', re.IGNORECASE)
_WIN_MAXIMIZE_RE = re.compile(r'\b(?:maximize|maximise|fullscreen|full\s+screen)\b(?:\s+(?:this|window|current|active))?\b', re.IGNORECASE)
_WIN_CLOSE_RE    = re.compile(r'\b(?:close|quit)\s+(?:this\s+)?(?:window|app|application|tab)?\b', re.IGNORECASE)
_WIN_SWITCH_RE   = re.compile(r'\b(?:switch\s+to|go\s+to|bring\s+up|focus|alt.?tab)\s+(?P<app>.+?)(?:\s+(?:please|now|window))?$', re.IGNORECASE)

# ── Reminder routing ──────────────────────────────────────────────────────────
_REMINDER_RE = re.compile(
    r'\b(?:remind\s+me\s+(?:to\s+|about\s+)?(.+?)(?:\s+in\s+(\d+\s*(?:second|minute|hour|min|sec|hr)s?)|\s+(?:at|after)\s+(.+))$'
    r'|set\s+(?:a\s+)?reminder\s+(?:to\s+|for\s+)?(.+?)(?:\s+in\s+(\d+\s*(?:second|minute|hour|min|sec|hr)s?)|\s+(?:at|after)\s+(.+))$)',
    re.IGNORECASE,
)

# ── Gmail routing ─────────────────────────────────────────────────────────────
_GMAIL_READ_RE = re.compile(
    r'\b(?:read\s+(?:my\s+)?(?:emails?|inbox|messages?)|check\s+(?:my\s+)?(?:emails?|inbox)|any\s+(?:new\s+)?emails?|what(?:\'?s|\s+is)\s+in\s+my\s+(?:inbox|emails?))\b',
    re.IGNORECASE,
)
_GMAIL_SEND_RE = re.compile(
    r'\b(?:send\s+(?:an?\s+)?email\s+to|email\s+(?P<to1>[^\s,]+@[^\s,]+)|write\s+(?:an?\s+)?email\s+to)\s+(.+)',
    re.IGNORECASE,
)

# ── Calendar routing ──────────────────────────────────────────────────────────
_CAL_READ_RE = re.compile(
    r'\b(?:what(?:\'?s|\s+is)\s+(?:on\s+)?(?:my\s+)?(?:calendar|schedule|agenda)|'
    r'(?:any\s+)?(?:meetings?|events?|appointments?)\s+(?:today|tomorrow|this\s+week)|'
    r'(?:show|check)\s+(?:my\s+)?(?:calendar|schedule)|my\s+schedule(?:\s+for\s+today)?)\b',
    re.IGNORECASE,
)
_CAL_CREATE_RE = re.compile(
    r'\b(?:add\s+(?:a\s+)?(?:meeting|event|appointment)|'
    r'(?:create|schedule|put|book)\s+(?:a\s+)?(?:meeting|event|appointment)|'
    r'schedule\s+(?:a\s+)?(?:call|meeting|event)\s+(?:at|for|with))\b',
    re.IGNORECASE,
)

# ── Open-app routing ──────────────────────────────────────────────────────────
# Uses re.search (not match) so "Hey, can you open settings?" works too.

_OPEN_RE = re.compile(
    r'\b(?:open|launch|start|run|pull\s+up|bring\s+up)\s+(?:(?:up|out)\s+)?(?:the\s+)?(.+?)(?:\s+(?:for me|please|now))?$'
    r'|(?:can\s+you|could\s+you|would\s+you|please)\s+(?:open|launch|start)\s+(?:the\s+)?(.+?)(?:\s+(?:for me|please|now))?$',
    re.IGNORECASE,
)


def _is_open_command(text: str) -> bool:
    """Return True when user explicitly says 'open/launch/start <app>' (anywhere in sentence)."""
    return bool(_OPEN_RE.search(text.strip()))


_DRIVE_RE = re.compile(
    r'\b(?:open|show|go\s+to|navigate\s+to|bring\s+up)\s+(?:the\s+)?([a-z])\s*(?:drive|disk|:)',
    re.IGNORECASE,
)

def _extract_drive_path(text: str) -> str | None:
    """Return e.g. 'C:\\' if text is 'open C drive', else None."""
    m = _DRIVE_RE.search(text.strip())
    if m:
        return f"{m.group(1).upper()}:\\"
    return None


def _extract_app_name(text: str) -> str | None:
    m = _OPEN_RE.search(text.strip())
    if not m:
        return None
    # Group 1 = verb-first pattern, Group 2 = polite "can you open X" pattern
    raw = (m.group(1) or m.group(2) or "").strip()
    # Truncate at first sentence boundary — handles "Can you open settings? Settings of my laptop."
    for sep in ('?', '!', '.', ','):
        if sep in raw:
            raw = raw[:raw.index(sep)]
    raw = raw.strip().rstrip(".,!?;:")
    return raw or None


# ── Explicit search routing ───────────────────────────────────────────────────
# ONLY these prefixes trigger search_web. Ambient factual questions ("what is X")
# are answered by GPT directly — they no longer open Google.

_SEARCH_RE = re.compile(
    r'^(?:search|google|search\s+for|google\s+for|google\s+search|'
    r'search\s+(?:the\s+)?(?:web|internet|online)|'
    r'find\s+(?:on(?:line|\s+the\s+internet|\s+google))|'
    r'look\s+up\s+online|search\s+on\s+google)\s+(.+)$',
    re.IGNORECASE,
)


def _is_explicit_search(text: str) -> bool:
    """Return True ONLY for explicit web search requests — never for factual questions."""
    return bool(_SEARCH_RE.match(text.strip()))


def _extract_search_query(text: str) -> str:
    m = _SEARCH_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


# ── Folder / directory routing ────────────────────────────────────────────────

_CREATE_FOLDER_RE = re.compile(
    r'\b(?:create|make|add)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\b',
    re.IGNORECASE,
)

_SUBFOLDER_RE = re.compile(
    r'\b(?:create|make|add)\s+(?:(?P<count>\d+)\s+)?sub\s*folders?\b',
    re.IGNORECASE,
)

_OPEN_THIS_RE = re.compile(
    r'\b(?:open|show|go\s+to)\s+(?:this|the|that|it)\s*(?:folder|directory|one)?\s*$',
    re.IGNORECASE,
)

_FOLDER_LOC_RE = re.compile(
    r'\b(?:in|on|at|inside|under)\s+(?:the\s+)?'
    r'(?:(?P<drive>[a-e])\s*(?:\s+drive|\s+disk|:)|'
    r'(?P<special>desktop|documents?|downloads?|pictures?))\b',
    re.IGNORECASE,
)

# Matches: "called X", "named X", "with name X", "name it X", "call it X"
# NOTE: uses "called"/"named" (past tense) and "name it"/"call it" forms only
# to avoid matching bare "name" or "call" as verbs in other contexts.
_FOLDER_NAME_EXPLICIT_RE = re.compile(
    r'\b(?:(?:name|call)\s+it|called|named|with\s+(?:this\s+)?name)\s+["\']?'
    r'(?P<name>[a-zA-Z0-9_\-\.]{1,60}(?:\s+[a-zA-Z0-9_\-\.]{1,40}){0,2}?)["\']?'
    r'(?=\s+(?:and|in|on|at|inside|$)|\s*$)',
    re.IGNORECASE,
)

# Combined "name it X and create it in Y" or "call it X on desktop" — one-shot voice commands
_NAME_AND_CREATE_RE = re.compile(
    r'\b(?:name|call)\s+it\s+(?P<name>[a-zA-Z0-9_\-\.]+)'
    r'(?:.*\bcreate\s+(?:it\s+)?(?:in|on)\s+|.*\b(?:in|on)\s+(?:[a-e]\s+drive|desktop|documents?|downloads?))',
    re.IGNORECASE,
)

# ── Delete file/folder ───────────────────────────────────────────────────────
_DELETE_FILE_RE = re.compile(
    r'\b(?:delete|remove|erase)\s+(?:(?:the\s+)?(?:folder|directory|file|item)\s+)?'
    r'(?P<target>[a-zA-Z0-9_\-\.\s]{1,120}?)(?:\s+(?:folder|directory|file))?\s*$',
    re.IGNORECASE,
)


def _extract_delete_target(text: str) -> str:
    """Extract the file/folder name from a delete command."""
    # Multi-word name fragment: word(s) with hyphens/underscores, stopping before stop-words.
    # Handles both "s-games" (hyphen) and "s games" (Whisper splits hyphenated words).
    _MWORD = (
        r'[A-Za-z0-9]+(?:[-_\.][A-Za-z0-9]+)*'
        r'(?:\s+(?!(?:in|on|at|inside|under|for|and|folder|directory|drive|the|a)\b)'
        r'[A-Za-z0-9]+(?:[-_\.][A-Za-z0-9]+)*)*'
    )
    # 1. Handle "name is X" / "name as X" / "named as X" / "the name should be X"
    m_named = re.search(
        r'\b(?:(?:the\s+)?name\s+should\s+be\s+|should\s+be\s+named\s+|'
        r'name\s+is\s+|name\s+as\s+|named\s+(?:as\s+)?|'
        r'with\s+(?:the\s+)?name\s+(?:of\s+)?|called\s+)'
        r'["\']?(' + _MWORD + r')["\']?'
        r'(?=\s+(?:in|on|at|inside|under|and)\b|\s*[.,!?]|\Z)',
        text, re.IGNORECASE,
    )
    if m_named:
        return _strip_punct(m_named.group(1))
    # 2. Standard "delete <type> <name>" — skip if next word is an article or "name" keyword
    m = re.search(
        r'\b(?:delete|remove|erase)\s+(?:(?:the\s+)?(?:folder|directory|file|item)\s+)?'
        r'(?!(?:in|on|at|inside|under|from|the|name)\b)'
        r'["\']?(' + _MWORD + r')["\']?'
        r'(?=\s+(?:in|on|at|inside|under|from)\b|\s*[.,!?]|$)',
        text.strip(), re.IGNORECASE,
    )
    if not m:
        return ""
    return _strip_punct(m.group(1) or "")


# Matches "inside <folder>", "in folder <name>", "under <folder>" for subfolder support
_PARENT_FOLDER_RE = re.compile(
    r'\b(?:inside|in\s+(?:the\s+)?folder|under)\s+["\']?(?P<parent>[a-zA-Z0-9_\-\.]+)["\']?'
    r'(?=\s|$)',
    re.IGNORECASE,
)


def _extract_folder_location(text: str) -> str:
    m = _FOLDER_LOC_RE.search(text)
    if not m:
        return ""
    drive = m.group("drive")
    special = m.group("special")
    if drive:
        return f"{drive.upper()}:\\"
    return special.lower() if special else ""


def _extract_parent_folder(text: str) -> str:
    """Extract parent folder name from 'inside X' / 'in folder X' / 'under X'."""
    m = _PARENT_FOLDER_RE.search(text)
    if not m:
        return ""
    return m.group("parent").strip()


def _clean_folder_name(raw: str) -> str:
    """Strip stop words and junk from a parsed folder name candidate."""
    # Remove trailing filler phrases
    raw = re.sub(
        r'\s+(?:and\s+create\s+it|for\s+me|please|in\s+\w+|on\s+\w+|at\s+\w+|inside\s+\w+).*$',
        '', raw, flags=re.IGNORECASE,
    ).strip()
    # If it still contains stop words, take only the first token
    if re.search(r'\b(?:and|create|in|on|inside|at)\b', raw, re.IGNORECASE):
        raw = raw.split()[0]
    return raw.strip()


def _strip_punct(s: str) -> str:
    """Strip trailing/leading sentence punctuation from an extracted name."""
    return s.strip().strip(".,!?;:'\"").strip()


# Unified "name as/is/named/called/should be" pattern — specific alternatives first.
# Covers Pakistani-English variants: "name is X", "name as X", "named as X",
# "the name should be X", "should be named X".
# Supports multi-word names (e.g. Whisper splits "s-games" → "s games").
_NAME_AS_PAT = re.compile(
    r'\b(?:(?:the\s+)?name\s+should\s+be\s+|should\s+be\s+named\s+|'
    r'name\s+is\s+|name\s+as\s+|named\s+as\s+|named\s+|'
    r'with\s+(?:the\s+)?name\s+(?:of\s+)?|called\s+|call\s+it\s+|name\s+it\s+)'
    r'["\']?'
    r'([A-Za-z0-9]+(?:[-_\.][A-Za-z0-9]+)*'
    r'(?:\s+(?!(?:in|on|at|inside|under|for|and|folder|directory|drive|the|a)\b)'
    r'[A-Za-z0-9]+(?:[-_\.][A-Za-z0-9]+)*)*)'
    r'["\']?'
    r'(?=\s+(?:in|on|at|inside|under|for|and)\b|\s*[.,]|\Z)',
    re.IGNORECASE,
)


def _extract_folder_name(text: str) -> str:
    # 1. "name as X" / "name is X" / "named as X" / "named X" / "called X" / "name it X"
    #    Must run BEFORE _FOLDER_NAME_EXPLICIT_RE which also matches "named"/"called"
    #    but captures "as X" instead of just "X".
    m = _NAME_AS_PAT.search(text)
    if m:
        return _strip_punct(m.group(1))
    # 2. Combined "name it X and create it in Y" / "call it X on desktop"
    m_nac = _NAME_AND_CREATE_RE.search(text)
    if m_nac:
        return _strip_punct(_clean_folder_name(m_nac.group("name")))
    # 3. "create [Name] folder" — word immediately before 'folder'
    m2 = re.search(
        r'\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?["\']?([A-Za-z0-9][a-zA-Z0-9_\-\. ]{0,50}?)["\']?\s+(?:folder|directory)\b',
        text, re.IGNORECASE,
    )
    if m2:
        candidate = _strip_punct(m2.group(1))
        if candidate.lower() not in ("new", "a", "the", "this", "some", "my"):
            return _clean_folder_name(candidate)
    # 4. "create folder NAME" — word(s) after 'folder', stop at preposition
    #    Negative lookahead skips when a preposition immediately follows 'folder'
    m3 = re.search(
        r'\b(?:create|make)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+'
        r'(?!(?:in|on|at|inside|under|for|a|the|my)\b)'
        r'["\']?([A-Za-z0-9][a-zA-Z0-9_\-\. ]{0,50}?)["\']?'
        r'(?=\s+(?:in|on|at|inside|under|for\s+me)|$)',
        text, re.IGNORECASE,
    )
    if m3:
        candidate = _strip_punct(m3.group(1))
        if candidate.lower() not in ("in", "on", "at", "inside", "under", "new", "a", "the"):
            return _clean_folder_name(candidate)
    return ""


def _extract_subfolder_params(text: str, last_action: dict | None) -> dict:
    """Extract parent, count, and names for subfolder creation."""
    m_count = re.search(r'(?:create|make|add)\s+(\d+)\s+sub', text, re.IGNORECASE)
    count = int(m_count.group(1)) if m_count else 0

    # "named X Y Z"  OR  "create subfolders X Y Z inside..."
    m_names = re.search(r'\b(?:named?|called)\s+(.+?)(?:\s+in\s+|\s+inside\s+|\s*$)', text, re.IGNORECASE)
    if not m_names:
        m_names = re.search(r'\bsub\s*folders?\s+([A-Za-z0-9][A-Za-z0-9 ,]+?)(?:\s+in\s+|\s+inside\s+|\s+on\s+|\s*$)', text, re.IGNORECASE)
    names: list[str] = []
    if m_names:
        raw = m_names.group(1).strip()
        names = [n.strip() for n in re.split(r'[,\s]+', raw) if n.strip() and len(n.strip()) > 1]

    parent = _extract_folder_location(text)
    if not parent and last_action and last_action.get("tool") in ("create_folder", "open_directory"):
        lp = last_action.get("params", {})
        parent = os.path.join(lp.get("path", ""), lp.get("name", "")).rstrip("\\") or lp.get("path", "")

    return {"parent": parent, "names": names, "count": count}


# ── Automation routing patterns ───────────────────────────────────────────────

# "type hello world", "write some text", "enter my password"
_DESKTOP_TYPE_RE = re.compile(
    r'^(?:type|write|enter|input)\s+(?:out\s+)?(?:the\s+(?:text|word|phrase|number)\s+)?["\']?(.+?)["\']?$',
    re.IGNORECASE,
)

# "press ctrl+c", "press enter", "press escape", "copy", "paste", "undo"
_HOTKEY_WORDS = frozenset([
    "ctrl+", "alt+", "shift+", "win+", "press enter", "press escape", "press tab",
    "press backspace", "press delete", "press f1", "press f5", "press f11",
    "copy that", "paste that", "paste it", "undo that", "redo that",
])
_DESKTOP_HOTKEY_RE = re.compile(
    r'^(?:press(?:\s+the)?\s+)?'
    r'(?:(?:ctrl|control|alt|shift|win|windows)\s*[+\-]\s*\w+|'
    r'enter|escape|esc|tab|backspace|delete|home|end|'
    r'f(?:1[0-2]|[1-9])|'
    r'copy|paste|cut|undo|redo|save|'
    r'(?:new|close)\s+(?:tab|window)|refresh|'
    r'select\s+all)'
    r'(?:\s+(?:that|it|now))?$',
    re.IGNORECASE,
)

# "go to youtube.com", "navigate to google.com", "open website X"
_BROWSER_GOTO_RE = re.compile(
    r'^(?:go\s+to|navigate\s+to|open\s+(?:website|webpage|page|site|url)\s+|'
    r'take\s+me\s+to|visit\s+)(.+)',
    re.IGNORECASE,
)

# "scroll down", "scroll up 5 times", "scroll the page"
_SCROLL_RE = re.compile(
    r'\bscroll\s+(up|down)(?:\s+(\d+)(?:\s+times?)?)?\b',
    re.IGNORECASE,
)

# "click the send button", "click submit", "click on X"
_BROWSER_CLICK_RE = re.compile(
    r'^click(?:\s+(?:on|the))?\s+(.+?)(?:\s+button|\s+link|\s+tab)?$',
    re.IGNORECASE,
)


def _is_workflow_command(text: str) -> bool:
    """Return True if text matches a workflow trigger (checked lazily)."""
    try:
        from ..services.automation_workflow_service import automation_workflow_service
        return automation_workflow_service.match_trigger(text) is not None
    except Exception:
        return False


def _extract_browser_url(text: str) -> str | None:
    m = _BROWSER_GOTO_RE.match(text.strip())
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".,!?;:")
    if not raw.startswith("http"):
        raw = "https://" + raw
    return raw


def _extract_hotkey(text: str) -> str:
    """Map voice phrase to hotkey string for desktop_hotkey tool."""
    lower = text.lower().strip()
    _PHRASE_MAP = {
        "copy": "ctrl+c", "copy that": "ctrl+c", "copy it": "ctrl+c",
        "paste": "ctrl+v", "paste that": "ctrl+v", "paste it": "ctrl+v",
        "cut": "ctrl+x", "undo": "ctrl+z", "undo that": "ctrl+z",
        "redo": "ctrl+y", "redo that": "ctrl+y",
        "save": "ctrl+s", "save that": "ctrl+s", "save the file": "ctrl+s",
        "select all": "ctrl+a",
        "new tab": "ctrl+t", "close tab": "ctrl+w",
        "new window": "ctrl+n", "refresh": "f5",
        "enter": "enter", "press enter": "enter",
        "escape": "escape", "esc": "escape", "press escape": "escape", "press esc": "escape",
        "tab": "tab", "press tab": "tab",
        "backspace": "backspace", "delete": "delete",
    }
    if lower in _PHRASE_MAP:
        return _PHRASE_MAP[lower]
    # "press ctrl+c" → "ctrl+c"
    m = re.match(r'^press\s+(.+)', lower)
    if m:
        return m.group(1).strip()
    return lower


@router.post("/remember")
async def remember_fact(request: Request):
    """Store an explicit user-stated fact in long-term memory.

    Body: {"fact": "I am a developer who works at night"}
    Returns: {"success": true, "spoken": "Got it, I'll remember that."}
    """
    body = await request.json()
    fact = (body.get("fact") or "").strip()
    if not fact:
        raise HTTPException(status_code=400, detail="'fact' is required")
    from ..services.memory_service import memory_service
    memory_service.remember_explicit(fact)
    return {"success": True, "spoken": "Got it, I'll remember that."}


@router.get("/memories")
async def get_memories():
    """Return all stored user facts as a natural spoken sentence.

    Returns: {"success": true, "spoken": "Here's what I remember about you: ..."}
    """
    from ..services.memory_service import memory_service
    spoken = memory_service.get_memories_spoken()
    return {"success": True, "spoken": spoken}


async def _suggest_follow_up(user_text: str, ai_response: str, openai_key: str) -> str:
    """Feature #3: Score whether a follow-up is natural after this exchange.
    Returns a short suggestion string or empty string if no follow-up needed."""
    # Quick keyword heuristics first — avoid GPT call for simple queries
    lower = user_text.lower()
    if any(kw in lower for kw in ["inbox", "emails", "mail"]):
        return "Want me to reply to any of these?"
    if any(kw in lower for kw in ["youtube", "video", "watch"]):
        return "Should I search for something specific?"
    if any(kw in lower for kw in ["calendar", "meetings", "schedule"]):
        return "Want me to create an event or set a reminder?"
    if any(kw in lower for kw in ["search", "look up", "find"]):
        return "Want me to open any of these results?"
    if any(kw in lower for kw in ["open", "launch", "start"]):
        return None   # no follow-up after opening apps
    # For everything else: GPT-scored only if response is substantial
    if len(ai_response) < 50:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=40,
            temperature=0,
            messages=[{
                "role": "system",
                "content": (
                    "You are a follow-up suggester. Given a user command and AI response, "
                    "decide if a brief follow-up question would be genuinely useful. "
                    "If yes, output ONLY the short question (max 10 words). "
                    "If no follow-up is natural, output NONE."
                ),
            }, {
                "role": "user",
                "content": f"Command: {user_text}\nResponse: {ai_response[:200]}",
            }],
        )
        suggestion = (resp.choices[0].message.content or "").strip()
        if suggestion.upper() == "NONE" or not suggestion:
            return None
        return suggestion
    except Exception:
        return None


@router.post("/respond-stream")
async def respond_stream(body: _RespondStreamBody):
    """Stream GPT-4o-mini response as Server-Sent Events, emitting sentence-level
    chunks for immediate TTS. Each event is ``data: <json>\\n\\n``.

    Event types::

        {"type": "chunk",  "turn_id": str, "index": int, "text": str}
        {"type": "done",   "turn_id": str, "full_text": str}
        {"type": "error",  "turn_id": str, "message": str}
    """
    _ensure_paths()
    turn_id = str(uuid.uuid4())

    # Resolve vague pronouns ("it", "that", "the file") → concrete entity
    # Must happen before _generate closure captures body, so body.text is clean.
    try:
        from ..services.context_resolver import resolve as _resolve_ctx
        _resolved_text = _resolve_ctx(body.text, body.session_id or turn_id)
        if _resolved_text != body.text:
            body = body.model_copy(update={"text": _resolved_text})
    except Exception:
        pass

    async def _generate():  # noqa: C901
        try:
            from ..config import settings
            if not (settings.openai_api_key and settings.openai_api_key.startswith("sk-")):
                fallback = "I need an OpenAI API key configured to respond."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': fallback})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': fallback})}\n\n"
                return

            from openai import AsyncOpenAI
            from ..services.memory_service import memory_service
            from ..services.episodic_memory import episodic_memory as _epi_mem
            from ..services.intent_router import intent_router as _intent_router

            client = AsyncOpenAI(api_key=settings.openai_api_key)

            # Inject personality mode + long-term memory into system prompt
            mem_context = memory_service.get_context_string()
            system_content = _VOICE_SYSTEM_PROMPT
            personality_addon = _PERSONALITY_ADDONS.get(body.personality_mode, "")
            if personality_addon:
                system_content += personality_addon
            if body.language == "ur":
                system_content += "\n\nLANGUAGE OVERRIDE: The user is speaking Urdu. You MUST reply entirely in Urdu script (e.g. آپ کیسے ہیں؟). Do not use English or transliteration."
            if mem_context:
                system_content += f"\n\n{mem_context}"

            # Inject usage habit context from episodic memory
            try:
                _activity = _epi_mem.summary_since(24)
                _top_now = _epi_mem.top_tools_now(3)
                if _activity and "No commands" not in _activity:
                    system_content += f"\n\nUSER HABITS (last 24h): {_activity}"
                if _top_now:
                    system_content += f"\nTools likely needed now: {', '.join(_top_now)}"
            except Exception:
                pass

            # Build message list — use last 20 turns (up from 6)
            msgs: list[dict] = [{"role": "system", "content": system_content}]
            for t in body.history[-20:]:
                if t.role in ("user", "assistant") and t.text.strip():
                    msgs.append({"role": t.role, "content": t.text})
            msgs.append({"role": "user", "content": body.text})

            # ── Episodic memory: save this user turn ──────────────────────────
            try:
                _epi_mem.save(body.session_id or turn_id, "user", body.text)
            except Exception:
                pass


            # ── LAYER -1: Pure conversation bypass ────────────────────────────
            # Casual inputs (jokes, "haha", etc.) — no tool routing, straight to GPT.
            if _is_pure_conversation(body.text.strip()):
                logger.info("[INTENT] conversation bypass → %r", body.text[:60])
                _c_msgs = [
                    {"role": "system", "content": _CONV_SYSTEM_PROMPT},
                    {"role": "user",   "content": body.text},
                ]
                try:
                    _c_stream = await client.chat.completions.create(
                        model="gpt-4o-mini", messages=_c_msgs,
                        max_tokens=80, temperature=0.8, stream=True,
                    )
                    _c_buf = ""; _c_full = ""; _c_idx = 0
                    async for _ck in _c_stream:
                        if _ck.choices[0].finish_reason: break
                        _cd = _ck.choices[0].delta.content or ""
                        if not _cd: continue
                        _c_buf += _cd; _c_full += _cd
                        _pts = _SENT_RE.split(_c_buf)
                        for _ss in _pts[:-1]:
                            _ss = _ss.strip()
                            if _ss:
                                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': _c_idx, 'text': _ss})}\n\n"
                                _c_idx += 1
                        _c_buf = _pts[-1] if _pts else ""
                    if _c_buf.strip():
                        yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': _c_idx, 'text': _c_buf.strip()})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'turn_id': turn_id, 'full_text': _c_full.strip()})}\n\n"
                    return
                except Exception as _cex:
                    logger.warning("Conversation bypass GPT failed: %s", _cex)

            # ── LAYER 0: Explicit "remember that X" command ───────────────────
            # Short-circuit before ANY tool or GPT call — just store + confirm.
            remember_m = _REMEMBER_RE.match(body.text.strip())
            if remember_m:
                fact = remember_m.group(1).strip()
                memory_service.remember_explicit(fact)
                confirm = "Got it — I'll remember that."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                return

            # ── LAYER 0b: "What do you remember about me?" ────────────────────
            if _QUERY_MEMORY_RE.search(body.text.strip()):
                spoken = memory_service.get_memories_spoken()
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken})}\n\n"
                return

            # ── LAYER 0c: Personality evolution ("be more casual") ────────────
            pers_m = _PERSONALITY_RE.search(body.text.strip())
            if pers_m:
                style = pers_m.group("style").lower()
                memory_service.set_personality_style(style)
                confirm = f"Got it — I'll be more {style} from now on."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                return

            # ── LAYER 0d: Screen context injection (Feature #1) ───────────────
            try:
                sc = _get_screen_ctx().get_context()
                if sc:
                    system_content += f"\n\n{sc}"
            except Exception:
                pass

            # ── LAYER 0e: Chill mode action trigger ───────────────────────────
            # Handled BEFORE profile switch so "chill mode" opens media + sets personality
            if _CHILL_TRIGGER_RE.search(body.text.strip()):
                memory_service.set_personality_style("chill")
                memory_service.set_last_action("chill_mode", {}, "opened_youtube_netflix")
                voice_name = _PROFILE_VOICES.get("chill", "shimmer")
                import random as _random
                _chill_responses = [
                    "I've opened YouTube and Netflix for you! On YouTube, what do you wanna watch — trending videos, music, lo-fi, something funny? And on Netflix, got any movie or series in mind, or want me to recommend something?",
                    "YouTube and Netflix are both open! Tell me what you're feeling on YouTube — trending music, rap, lo-fi, or anything else. And for Netflix, want a movie, a series, or should I suggest a few good ones?",
                    "Done! YouTube and Netflix are ready for you. On YouTube I can play trending songs, lo-fi, music videos, whatever you like. On Netflix, just say the genre or mood and I'll recommend something great.",
                    "I've got YouTube and Netflix open for you! Want me to play some trending music or videos on YouTube? And for Netflix — any genre in mind, or want me to pick a good movie or series for tonight?",
                ]
                confirm = _random.choice(_chill_responses)
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'profile_switch', 'profile': 'chill', 'voice': voice_name})}\n\n"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': 'https://www.youtube.com'}, 'action_url': 'https://www.youtube.com', 'spoken': 'Opening YouTube.'})}\n\n"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': 'https://www.netflix.com'}, 'action_url': 'https://www.netflix.com', 'spoken': 'Opening Netflix.'})}\n\n"
                try:
                    _get_history().log(body.text, confirm, "chill_mode", body.session_id)
                except Exception:
                    pass
                return

            # ── LAYER 0e2: Chill mode follow-up (recommendations) ─────────────
            if _CHILL_FOLLOWUP_RE.search(body.text.strip()):
                last = memory_service.get_last_action()
                if last and last.get("tool") == "chill_mode":
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _CHILL_RECOMMENDATIONS})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _CHILL_RECOMMENDATIONS})}\n\n"
                    return

            # ── LAYER 0e3: Profile switch (Feature #4) ────────────────────────
            profile_m = _PROFILE_SWITCH_RE.search(body.text.strip())
            if profile_m:
                profile = profile_m.group("profile").lower()
                memory_service.set_personality_style(profile)
                voice_name = _PROFILE_VOICES.get(profile, "nova")
                confirm = f"Switched to {profile} mode."
                yield f"data: {json.dumps({'type': 'chunk',  'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',   'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'profile_switch', 'profile': profile, 'voice': voice_name})}\n\n"
                try:
                    _get_history().log(body.text, confirm, "profile_switch", body.session_id)
                except Exception:
                    pass
                return

            # ── LAYER 0e4: Morning Mode ───────────────────────────────────────
            if _MORNING_RE.search(body.text.strip()):
                memory_service.set_last_action("morning_mode", {}, "morning_routine")

                # ── Fetch live weather from wttr.in (no API key needed) ───────
                _weather_line = ""
                _advice_line  = ""
                try:
                    import httpx as _wx
                    async with _wx.AsyncClient(timeout=5) as _wc:
                        _wr = await _wc.get("https://wttr.in/?format=j1")
                    if _wr.status_code == 200:
                        _wd  = _wr.json()
                        _cur = _wd["current_condition"][0]
                        _desc    = _cur["weatherDesc"][0]["value"]
                        _temp_c  = int(_cur["temp_C"])
                        _humidity = int(_cur["humidity"])
                        _feels   = int(_cur["FeelsLikeC"])
                        _wind    = int(_cur["windspeedKmph"])
                        _weather_line = (
                            f"It's {_desc.lower()}, {_temp_c}°C "
                            f"(feels like {_feels}°C), humidity {_humidity}%, wind {_wind} km/h."
                        )
                        # ── Contextual outdoor advice ─────────────────────────
                        _dl = _desc.lower()
                        if any(w in _dl for w in ("heavy rain", "thunder", "storm", "blizzard", "hail")):
                            _advice_line = "Definitely stay inside today — conditions are rough out there."
                        elif any(w in _dl for w in ("rain", "drizzle", "shower", "sleet")):
                            _advice_line = "Skip the outdoor walk today, it's raining."
                        elif any(w in _dl for w in ("snow", "ice", "frost")):
                            _advice_line = "It's snowy — bundle up if you have to go out."
                        elif any(w in _dl for w in ("fog", "mist", "haze")):
                            _advice_line = "A bit foggy out there — visibility is low, take it easy."
                        elif _wind > 40:
                            _advice_line = "Very windy today — maybe skip the run and do something indoors."
                        elif any(w in _dl for w in ("sunny", "clear", "bright", "fine")):
                            _advice_line = "Beautiful day — great time to go for a walk or jog!"
                        elif any(w in _dl for w in ("partly", "overcast", "cloudy")):
                            _advice_line = "Decent day to go outside. Clouds won't stop you."
                        else:
                            _advice_line = "Not bad outside — a short walk wouldn't hurt."
                except Exception:
                    pass   # weather unavailable — still run routine

                _weather_spoken = f"{_weather_line} {_advice_line}".strip() if _weather_line else ""
                if _weather_spoken:
                    confirm = (
                        f"Good morning! Here's your weather: {_weather_spoken} "
                        "I've also opened your calendar and queued some morning music. "
                        "Let me know what you need."
                    )
                else:
                    confirm = (
                        "Good morning! I couldn't fetch the weather right now, but I've opened your "
                        "calendar and some morning music to get you started. Have a great day!"
                    )

                # Send spoken response first so TTS starts immediately
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'mode_change', 'mode': 'morning'})}\n\n"
                # Open tabs — action_url is required for frontend window.open
                _wttr_url = "https://wttr.in"
                _cal_url  = "https://calendar.google.com"
                _music_url = "https://www.youtube.com/results?search_query=morning+playlist+energizing"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': _wttr_url},  'action_url': _wttr_url,  'spoken': 'Opening weather.'})}\n\n"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': _cal_url},   'action_url': _cal_url,   'spoken': 'Opening calendar.'})}\n\n"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': _music_url}, 'action_url': _music_url, 'spoken': 'Playing morning music.'})}\n\n"
                try:
                    _get_history().log(body.text, confirm, "morning_mode", body.session_id)
                except Exception:
                    pass
                return

            # ── LAYER 0e5: Jarvis / Home Mode ─────────────────────────────────
            if _JARVIS_HOME_RE.search(body.text.strip()):
                memory_service.set_last_action("jarvis_home", {}, "home_greeting")
                try:
                    from api.tools import registry as _reg_jv
                    _ctx_jv = {"openai_key": settings.openai_api_key}
                    _health = _reg_jv.execute("system_health", {}, _ctx_jv)
                    stats_text = _health.spoken if _health.success else "System is running normally."
                except Exception:
                    stats_text = "System is running normally."
                confirm = f"Welcome back. {stats_text} What would you like to do?"
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'mode_change', 'mode': 'jarvis'})}\n\n"
                try:
                    _get_history().log(body.text, confirm, "jarvis_home", body.session_id)
                except Exception:
                    pass
                return

            # ── LAYER 0e6: Entertainment Mode 2.0 ────────────────────────────
            if _ENTERTAIN_RE.search(body.text.strip()):
                memory_service.set_last_action("entertainment_mode", {}, "opened_content")
                _txt_l = body.text.lower()
                if _ENTERTAIN_FUNNY_RE.search(body.text):
                    _sq, _label = "funny videos compilation", "funny content"
                elif _ENTERTAIN_LOFI_RE.search(body.text):
                    if "jazz" in _txt_l:
                        _sq, _label = "relaxing jazz music", "jazz"
                    elif "acoustic" in _txt_l:
                        _sq, _label = "acoustic chill music playlist", "acoustic music"
                    else:
                        _sq, _label = "lofi hip hop beats to relax study", "lo-fi music"
                elif _ENTERTAIN_TRENDING_RE.search(body.text):
                    _sq, _label = "trending music top hits", "trending music"
                elif "documentary" in _txt_l:
                    _sq, _label = "best documentaries", "documentaries"
                else:
                    _sq, _label = "trending videos today", "popular content"
                _yt_url = f"https://www.youtube.com/results?search_query={_sq.replace(' ', '+')}"
                confirm = f"Opening YouTube for some {_label}. Playing the best result for you."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'mode_change', 'mode': 'entertainment'})}\n\n"
                yield f"data: {json.dumps({'type': 'action', 'tool': 'browser_navigate', 'params': {'url': _yt_url}, 'action_url': _yt_url, 'spoken': f'Opening {_label} on YouTube.'})}\n\n"
                try:
                    _get_history().log(body.text, confirm, "entertainment_mode", body.session_id)
                except Exception:
                    pass
                return

            # ── LAYER 0e7: Shutdown / Restart with confirmation ───────────────
            # Step 1: Detect command → ask confirmation, store pending state
            # Step 2: Detect "yes/confirm" when pending → execute
            if _SYS_CONFIRM_RE.search(body.text.strip()):
                _last_sc = memory_service.get_last_action()
                if _last_sc and _last_sc.get("tool") == "shutdown_pending":
                    from api.tools import registry as _ereg2
                    _ereg2.execute("shutdown_system", {"delay": 0}, {})
                    _spoken_sc = "Shutting down. Goodbye."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _spoken_sc})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _spoken_sc})}\n\n"
                    return
                if _last_sc and _last_sc.get("tool") == "restart_pending":
                    from api.tools import registry as _ereg2
                    _ereg2.execute("restart_system", {"delay": 0}, {})
                    _spoken_sc = "Restarting now. See you soon."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _spoken_sc})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _spoken_sc})}\n\n"
                    return
                if _last_sc and _last_sc.get("tool") == "delete_pending":
                    _del_path = _last_sc.get("params", {}).get("path", "")
                    if _del_path:
                        from api.tools import registry as _ereg2
                        _del_result = _ereg2.execute("delete_file", {"path": _del_path, "confirmed": True}, {})
                        _spoken_del = _del_result.spoken or f"Deleted {_del_path}."
                        yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _spoken_del})}\n\n"
                        yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _spoken_del})}\n\n"
                        return

            if _SCHED_SHUTDOWN_RE.search(body.text.strip()):
                _scm  = _SCHED_SHUTDOWN_RE.search(body.text.strip())
                _scn  = int(_scm.group("n"))
                _scu  = _scm.group("unit").lower()
                _scp  = {"hours": _scn, "minutes": 0} if _scu.startswith("h") else {"hours": 0, "minutes": _scn}
                from api.tools import registry as _screg
                _scr  = _screg.execute("schedule_shutdown", _scp, {})
                _scs  = _scr.spoken or f"Shutdown scheduled in {_scn} {'hour' if _scu.startswith('h') else 'minute'}s."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _scs})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _scs})}\n\n"
                return

            if _SHUTDOWN_RE.search(body.text.strip()):
                memory_service.set_last_action("shutdown_pending", {}, "awaiting_confirmation")
                confirm = "Are you sure you want to shut down the system? Say 'yes' to confirm."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'confirmation_required', 'action': 'shutdown'})}\n\n"
                return

            if _RESTART_RE.search(body.text.strip()):
                memory_service.set_last_action("restart_pending", {}, "awaiting_confirmation")
                confirm = "Are you sure you want to restart the system? Say 'yes' to confirm."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'confirmation_required', 'action': 'restart'})}\n\n"
                return

            if _DELETE_FILE_RE.search(body.text.strip()) and not _SYS_CONFIRM_RE.search(body.text.strip()):
                _del_target = _extract_delete_target(body.text.strip())
                if _del_target:
                    from api.tools import registry as _del_reg
                    _del_loc  = _extract_folder_location(body.text.strip())
                    if _del_loc:
                        _del_path = _del_loc.rstrip("\\") + "\\" + _del_target
                    else:
                        _del_path = _del_target
                    _del_res    = _del_reg.execute("delete_file", {"path": _del_path}, {})
                    _del_spoken = _del_res.spoken or f"Deleted {_del_target}."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _del_spoken})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _del_spoken})}\n\n"
                    return

            # ── LAYER 0e8: Sleep / Hibernate / Lock — execute server-side directly ──
            # These used to emit SSE action events (requiring frontend to call execute-tool).
            # Now they run here so they work regardless of which frontend is used.
            if _LOCK_RE.search(body.text.strip()):
                from api.tools import registry as _ereg
                _er = _ereg.execute("lock_system", {}, {})
                spoken_lk = _er.spoken or "Screen locked."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken_lk})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken_lk})}\n\n"
                return

            if _HIBERNATE_RE.search(body.text.strip()):
                from api.tools import registry as _ereg
                _er = _ereg.execute("hibernate_system", {}, {})
                spoken_hb = _er.spoken or "Hibernating now. See you when you're back."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken_hb})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken_hb})}\n\n"
                return

            if _SLEEP_RE.search(body.text.strip()):
                from api.tools import registry as _ereg
                _er = _ereg.execute("sleep_system", {}, {})
                spoken_sl = _er.spoken or "Going to sleep. Sweet dreams."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken_sl})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken_sl})}\n\n"
                return

            if _SCREENSHOT_RE.search(body.text.strip()):
                from api.tools import registry as _sreg
                _sr = _sreg.execute("take_screenshot", {}, {"openai_key": settings.openai_api_key})
                _ss = _sr.spoken or "Screenshot saved."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _ss})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _ss})}\n\n"
                return

            # ── LAYER 0f: Voice macros (Feature #5) ───────────────────────────
            try:
                macro = _get_macro().match(body.text)
                if macro:
                    _get_macro().increment_run(macro["id"])
                    from api.tools import registry as _reg
                    ctx = {"openai_key": settings.openai_api_key}
                    executed: list[str] = []
                    for step in macro.get("steps", [])[:6]:
                        t = step.get("tool", "")
                        p = step.get("params", {})
                        d = step.get("description", t)
                        if t and t in _reg:
                            r = _reg.execute(t, p, ctx)
                            sse_a = r.to_sse_action()
                            if sse_a:
                                yield f"data: {json.dumps({'type': 'action', 'turn_id': turn_id, **sse_a})}\n\n"
                            executed.append(f"{d} {'✓' if r.success else '✗'}")
                    summary = f"Macro '{macro['name']}': " + ", ".join(executed) + "."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': summary})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': summary})}\n\n"
                    try:
                        _get_history().log(body.text, summary, f"macro:{macro['name']}", body.session_id)
                    except Exception:
                        pass
                    return
            except Exception as macro_exc:
                logger.debug("Macro check skipped: %s", macro_exc)

            # ── LAYER 0g: Voice notes save (Feature #10) ──────────────────────
            note_m = _NOTE_SAVE_RE.match(body.text.strip())
            if note_m:
                note_text = note_m.group("note").strip()
                try:
                    _get_notes().add(note_text, settings.openai_api_key, body.session_id)
                except Exception:
                    pass
                confirm = f"Note saved: {note_text[:80]}"
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                return

            # ── LAYER 0h: Voice notes search (Feature #10) ────────────────────
            note_find = _NOTE_FIND_RE.search(body.text.strip())
            if note_find:
                topic = (note_find.group("topic") or note_find.group("topic2") or
                         note_find.group("topic3") or "").strip()
                if topic:
                    try:
                        results = _get_notes().semantic_search(topic, settings.openai_api_key)
                        spoken  = _get_notes().summarize_search_for_speech(results)
                    except Exception:
                        spoken = "I couldn't search your notes right now."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken})}\n\n"
                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken})}\n\n"
                    return

            # ── LAYER 0i: History query (Feature #2) ──────────────────────────
            hist_m = _HISTORY_RE.search(body.text.strip())
            if hist_m:
                from datetime import date, timedelta
                when = (hist_m.group("when") or "").lower()
                date_arg = hist_m.group("date") if hist_m.lastindex and hist_m.lastindex >= 2 else None
                if "yesterday" in when:
                    d = (date.today() - timedelta(days=1)).isoformat()
                else:
                    d = date_arg or date.today().isoformat()
                try:
                    spoken = _get_history().summarize_for_speech(d)
                except Exception:
                    spoken = "I couldn't retrieve your history right now."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken})}\n\n"
                return

            # ── LAYER 0j: Meeting assistant commands (Feature #6) ─────────────
            from ..services.meeting_service import meeting_service as _ms
            if _MEETING_START_RE.search(body.text):
                _ms.start_session()
                confirm = "Meeting recording started. I'm transcribing in the background."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                return
            elif _MEETING_STOP_RE.search(body.text):
                _ms.stop_session()
                confirm = f"Meeting recording stopped. Captured {_ms.word_count()} words."
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': confirm})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': confirm})}\n\n"
                return
            elif _MEETING_SUMMARY_RE.search(body.text):
                summary = await _ms.summarize(settings.openai_api_key)
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': summary})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': summary})}\n\n"
                return

            # Mark user as active (resets proactive break timer) — Feature #7
            try:
                _get_proactive().mark_active()
            except Exception:
                pass

            # ── Direct keyword routing (ZERO extra GPT calls) ─────────────────
            #
            # Tool selection is done entirely by keyword matching — GPT is never
            # asked to "pick a tool". This eliminates the double-GPT-call latency
            # and removes all tool-selection hallucination.
            #
            # Priority order (first match wins):
            #   1. system_health  — live CPU/RAM usage queries
            #   2. system_info    — hardware/OS specs queries
            #   3. continue-last  — short follow-up → replay last system tool
            #   4. open_app       — "open/launch/start <app>"
            #   5. search_web     — EXPLICIT "search/google" prefix only
            #   6. (none)         — pure GPT streaming, no tool
            #
            # search_web is NEVER selected for ambient factual questions.

            _tool_spoken = ""   # spoken fallback from last executed tool
            if body.use_tools:
                try:
                    from api.tools import registry
                    import json as _json

                    ctx       = {"openai_key": settings.openai_api_key}
                    tool_name: str | None = None
                    tool_params: dict     = {}
                    user_lower = body.text.lower().strip()

                    # ── MULTI-COMMAND: split compound requests and execute each ─
                    try:
                        from ..services.command_splitter import split as _cmd_split
                        _cmd_parts = _cmd_split(body.text)
                        if len(_cmd_parts) >= 2:
                            logger.info("[MULTI-CMD] split into %d parts: %s", len(_cmd_parts), _cmd_parts)
                            _spoken_parts: list[str] = []
                            for _part in _cmd_parts:
                                _sub_body = body.model_copy(update={"text": _part})
                                # Re-use intent router to find tool for each sub-command
                                _sub_ir = _intent_router.route(_part)
                                _sub_tool = _sub_ir.tool_name if _sub_ir.tier <= 3 else None
                                _sub_params = _sub_ir.params if _sub_tool else {}
                                if _sub_tool:
                                    _sub_result = registry.execute(_sub_tool, _sub_params, ctx)
                                    _sp = _sub_result.spoken or _sub_result.text or f"Done: {_part}"
                                    _spoken_parts.append(_sp)
                                    try:
                                        _epi_mem.save(body.session_id or turn_id, "user", _part)
                                        _epi_mem.save(body.session_id or turn_id, "assistant", _sp)
                                        _epi_mem.record_tool(_sub_tool, _sub_params, _sub_result.success)
                                    except Exception:
                                        pass
                                else:
                                    _spoken_parts.append(f"I wasn't sure how to handle: {_part}")
                            _combined = " ".join(_spoken_parts)
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _combined})}\n\n"
                            yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _combined})}\n\n"
                            return
                    except Exception as _mce:
                        logger.debug("Multi-command split skipped: %s", _mce)

                    # ── LAYER -1: System commands that must beat workflows ────
                    # WiFi system panel — beats google_maps workflow
                    if _WIFI_LIST_RE.search(body.text):
                        tool_name = "open_wifi_panel"
                        logger.info("[ROUTE] open_wifi_panel (pre-workflow)")
                    # "play X" without explicit "youtube/spotify/on" → local file
                    elif _PLAY_MEDIA_RE.search(body.text) and not re.search(
                        r'\b(?:youtube|spotify|netflix|on\s+youtube|on\s+spotify)\b', body.text, re.IGNORECASE
                    ):
                        _pm2 = _PLAY_MEDIA_RE.search(body.text)
                        _q2  = (_pm2.group(1) or "").strip().rstrip(".,!?")
                        if _q2:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q2, "type": "video"}
                            logger.info("[ROUTE] smart_open (video pre-workflow) → %r", _q2)
                    elif _OPEN_NAMED_FOLDER_RE.search(body.text):
                        _onf2 = _OPEN_NAMED_FOLDER_RE.search(body.text)
                        _q2   = (_onf2.group(1) or "").strip()
                        if _q2:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q2, "type": "folder"}
                            logger.info("[ROUTE] smart_open (folder pre-workflow) → %r", _q2)
                    elif _OPEN_NAMED_FILE_RE.search(body.text):
                        _of2  = _OPEN_NAMED_FILE_RE.search(body.text)
                        _q2   = (_of2.group(1) or "").strip()
                        _ft2  = _of2.group(0).split()[-1].lower()
                        _tp2  = "image" if _ft2 in ("picture","photo","image","pic") else \
                                "video" if _ft2 in ("video","movie","film","clip") else "file"
                        if _q2:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q2, "type": _tp2}
                            logger.info("[ROUTE] smart_open (%s pre-workflow) → %r", _tp2, _q2)

                    # ── IntentRouter Tier 1+2: cache + regex shortcut ─────────
                    # High-confidence matches skip the full elif chain below.
                    if not tool_name:
                        _ir = _intent_router.route(body.text)
                        if _ir.tier <= 2 and _ir.tool_name:
                            tool_name   = _ir.tool_name
                            tool_params = {**_ir.params, **tool_params}
                            logger.info("[ROUTE] intent-router tier=%d → %s", _ir.tier, tool_name)

                    # ── LAYER 0: Workflow trigger (multi-step automation) ──────
                    # Matched FIRST — workflows are explicit multi-step intents
                    # e.g. "play X on youtube", "send email to John"
                    _wf_match = None
                    try:
                        from ..services.automation_workflow_service import automation_workflow_service
                        _wf_match = automation_workflow_service.match_trigger(body.text)
                    except Exception:
                        pass

                    if _wf_match and not tool_name:
                        _wf, _wf_vars = _wf_match
                        tool_name   = "run_workflow"
                        tool_params = {"name": _wf["name"], "variables": _wf_vars}
                        logger.info("[ROUTE] workflow → %r vars=%r", _wf["name"], _wf_vars)

                    # ── LAYER 0.3: Desktop type ("type hello", "write X") ─────
                    elif _DESKTOP_TYPE_RE.match(body.text.strip()):
                        _type_m = _DESKTOP_TYPE_RE.match(body.text.strip())
                        _type_text = (_type_m.group(1) if _type_m else "").strip().rstrip(".,!?")
                        if _type_text:
                            tool_name   = "desktop_type"
                            tool_params = {"text": _type_text}
                            logger.info("[ROUTE] desktop_type → %r", _type_text)

                    # ── LAYER 0.4: Desktop hotkey ("press ctrl+c", "copy") ────
                    elif _DESKTOP_HOTKEY_RE.match(body.text.strip()):
                        _hk = _extract_hotkey(body.text.strip())
                        tool_name   = "desktop_hotkey"
                        tool_params = {"keys": _hk}
                        logger.info("[ROUTE] desktop_hotkey → %r", _hk)

                    # ── LAYER 0.45: Scroll ("scroll down", "scroll up 5 times") ─
                    elif _SCROLL_RE.search(body.text):
                        _sm = _SCROLL_RE.search(body.text)
                        _scroll_dir = _sm.group(1).lower() if _sm else "down"
                        _scroll_amt = int(_sm.group(2)) if (_sm and _sm.group(2)) else 3
                        tool_name   = "desktop_scroll"
                        tool_params = {"direction": _scroll_dir, "amount": _scroll_amt}
                        logger.info("[ROUTE] desktop_scroll dir=%r amt=%d", _scroll_dir, _scroll_amt)

                    # ── LAYER 0.5: Open last-created folder ("open this folder") ─
                    elif _OPEN_THIS_RE.search(body.text.strip()):
                        _last_for_open = memory_service.get_last_action()
                        if _last_for_open and _last_for_open.get("tool") in (
                                "create_folder", "open_directory", "create_subfolders"):
                            _lp = _last_for_open.get("params", {})
                            _folder_path = os.path.join(
                                _lp.get("path", ""), _lp.get("name", "")
                            ).rstrip("\\") or _lp.get("path", "")
                            if _folder_path:
                                tool_name   = "open_directory"
                                tool_params = {"path": _folder_path}
                                logger.info("[ROUTE] open_directory (last folder) → %r", _folder_path)

                    # ── LAYER 0.9: Specific system queries before open_command ─
                    elif _STARTUP_DISABLE_RE.search(body.text):
                        _sd0 = _STARTUP_DISABLE_RE.search(body.text)
                        _a0  = (_sd0.group("app") or "").strip()
                        if _a0:
                            tool_name   = "disable_startup_app"
                            tool_params = {"name": _a0}
                            logger.info("[ROUTE] disable_startup_app → %r", _a0)

                    elif _STARTUP_LIST_RE.search(body.text):
                        tool_name = "get_startup_apps"
                        logger.info("[ROUTE] get_startup_apps")

                    elif _DISK_CLEANUP_RE.search(body.text):
                        tool_name = "run_disk_cleanup"
                        logger.info("[ROUTE] run_disk_cleanup")

                    elif _LIST_PROCS_RE.search(body.text):
                        tool_name = "list_processes"
                        logger.info("[ROUTE] list_processes")

                    elif _KILL_PROC_RE.search(body.text):
                        _k0 = _KILL_PROC_RE.search(body.text)
                        _p0 = (_k0.group("proc") or "").strip().rstrip(".")
                        if _p0:
                            tool_name   = "kill_process"
                            tool_params = {"name": _p0}
                            logger.info("[ROUTE] kill_process → %r", _p0)

                    # ── LAYER 0.95: Play media / open named folder|file ───────
                    elif _PLAY_MEDIA_RE.search(body.text):
                        _pm = _PLAY_MEDIA_RE.search(body.text)
                        _q  = (_pm.group(1) or "").strip().rstrip(".,!?")
                        if _q:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q, "type": "video"}
                            logger.info("[ROUTE] smart_open (video) → %r", _q)

                    elif _OPEN_NAMED_FILE_RE.search(body.text):
                        _of = _OPEN_NAMED_FILE_RE.search(body.text)
                        _q  = (_of.group(1) or "").strip()
                        _ft = _of.group(0).split()[-1].lower()
                        _tp = "image" if _ft in ("picture","photo","image","pic") else \
                              "video" if _ft in ("video","movie","film","clip") else "file"
                        if _q:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q, "type": _tp}
                            logger.info("[ROUTE] smart_open (%s) → %r", _tp, _q)

                    elif _OPEN_NAMED_FOLDER_RE.search(body.text):
                        _onf = _OPEN_NAMED_FOLDER_RE.search(body.text)
                        _q   = (_onf.group(1) or "").strip()
                        if _q:
                            tool_name   = "smart_open"
                            tool_params = {"query": _q, "type": "folder"}
                            logger.info("[ROUTE] smart_open (folder) → %r", _q)

                    # ── LAYER 1: Open/launch/start <app or path> ──────────────
                    elif _is_open_command(body.text):
                        drive_path = _extract_drive_path(body.text)
                        if drive_path:
                            tool_name   = "open_directory"
                            tool_params = {"path": drive_path}
                            logger.info("[ROUTE] open_directory (drive) → %r", drive_path)
                        else:
                            # Named folder/file check before generic app launch
                            _m1_folder = _OPEN_NAMED_FOLDER_RE.search(body.text)
                            _m1_file   = _OPEN_NAMED_FILE_RE.search(body.text)
                            if _m1_folder:
                                _q = (_m1_folder.group(1) or "").strip()
                                if _q:
                                    tool_name   = "smart_open"
                                    tool_params = {"query": _q, "type": "folder"}
                                    logger.info("[ROUTE] smart_open (folder L1) → %r", _q)
                            elif _m1_file:
                                _q  = (_m1_file.group(1) or "").strip()
                                _ft = _m1_file.group(0).split()[-1].lower()
                                _tp = "image" if _ft in ("picture","photo","image","pic") else \
                                      "video" if _ft in ("video","movie","film","clip") else "file"
                                if _q:
                                    tool_name   = "smart_open"
                                    tool_params = {"query": _q, "type": _tp}
                                    logger.info("[ROUTE] smart_open (%s L1) → %r", _tp, _q)
                            else:
                                app = _extract_app_name(body.text)
                                if app:
                                    tool_name   = "open_application"
                                    tool_params = {"app_name": app}
                                    logger.info("[ROUTE] open_application → %r", app)

                    # ── LAYER 1b: Create folder ───────────────────────────────
                    elif _CREATE_FOLDER_RE.search(body.text) or _NAME_AND_CREATE_RE.search(body.text):
                        _floc   = _extract_folder_location(body.text)
                        _fname  = _extract_folder_name(body.text)
                        _parent = _extract_parent_folder(body.text)
                        # Subfolder: "create folder games inside projects in c drive"
                        # → path = C:\projects, name = games
                        if _parent and _floc:
                            _floc = _floc.rstrip("\\") + "\\" + _parent
                        elif _parent:
                            _floc = _parent
                        # Context: "now create in E drive" → reuse last folder name
                        if not _fname:
                            try:
                                _la = memory_service.get_last_action()
                                if _la and _la.get("tool") == "create_folder":
                                    _fname = _la.get("params", {}).get("name", "")
                                    if _fname:
                                        logger.info("[MEMORY] inherited folder name %r", _fname)
                            except Exception:
                                pass
                        tool_name   = "create_folder"
                        tool_params = {"path": _floc, "name": _fname}
                        logger.info("[ROUTE] create_folder path=%r name=%r parent=%r", _floc, _fname, _parent)

                    # ── LAYER 1c: Create subfolders ───────────────────────────
                    elif _SUBFOLDER_RE.search(body.text):
                        _last_for_sf = memory_service.get_last_action()
                        _sp = _extract_subfolder_params(body.text, _last_for_sf)
                        tool_name   = "create_subfolders"
                        tool_params = _sp
                        logger.info("[ROUTE] create_subfolders params=%r", _sp)

                    # ── LAYER 1d: Browser navigate ("go to X.com") ────────────
                    elif _BROWSER_GOTO_RE.match(body.text.strip()):
                        _nav_url = _extract_browser_url(body.text.strip())
                        if _nav_url:
                            tool_name   = "browser_navigate"
                            tool_params = {"url": _nav_url}
                            logger.info("[ROUTE] browser_navigate → %r", _nav_url)

                    # ── LAYER 1e: Browser click ("click the send button") ─────
                    elif _BROWSER_CLICK_RE.match(body.text.strip()):
                        _click_m = _BROWSER_CLICK_RE.match(body.text.strip())
                        _click_text = (_click_m.group(1) if _click_m else "").strip()
                        if _click_text:
                            tool_name   = "browser_click"
                            tool_params = {"text": _click_text}
                            logger.info("[ROUTE] browser_click → %r", _click_text)

                    # ── LAYER 1z: High-priority specific queries (before generic health/info) ─
                    elif _DISK_USAGE_RE.search(body.text):
                        tool_name = "get_disk_usage"
                        logger.info("[ROUTE] get_disk_usage")

                    elif _DATE_TIME_RE.search(body.text):
                        tool_name = "get_date_time"
                        logger.info("[ROUTE] get_date_time")

                    elif _BATTERY_RE.search(body.text):
                        tool_name = "get_battery_status"
                        logger.info("[ROUTE] get_battery_status")

                    elif _POWER_PLAN_RE.search(body.text):
                        _ppm2 = _POWER_PLAN_RE.search(body.text)
                        _plan2 = (_ppm2.group("plan") or _ppm2.group("plan2") or "balanced").strip().lower()
                        _plan2 = re.sub(r'\s+', ' ', _plan2)
                        tool_name   = "set_power_plan"
                        tool_params = {"plan": _plan2}
                        logger.info("[ROUTE] set_power_plan → %r", _plan2)

                    elif _STARTUP_DISABLE_RE.search(body.text):
                        _sdm2 = _STARTUP_DISABLE_RE.search(body.text)
                        _app2 = (_sdm2.group("app") or "").strip()
                        if _app2:
                            tool_name   = "disable_startup_app"
                            tool_params = {"name": _app2}
                            logger.info("[ROUTE] disable_startup_app → %r", _app2)

                    elif _STARTUP_LIST_RE.search(body.text):
                        tool_name = "get_startup_apps"
                        logger.info("[ROUTE] get_startup_apps")

                    elif _DISK_CLEANUP_RE.search(body.text):
                        tool_name = "run_disk_cleanup"
                        logger.info("[ROUTE] run_disk_cleanup (priority)")

                    elif _WIN_UPDATES_RE.search(body.text):
                        tool_name = "check_windows_updates"
                        logger.info("[ROUTE] check_windows_updates (priority)")

                    elif _UPTIME_RE.search(body.text):
                        tool_name = "get_uptime"
                        logger.info("[ROUTE] get_uptime (priority)")

                    elif _SPEED_TEST_RE.search(body.text):
                        tool_name = "network_speed_test"
                        logger.info("[ROUTE] network_speed_test (priority)")

                    elif _IP_INFO_RE.search(body.text):
                        tool_name = "get_ip_info"
                        logger.info("[ROUTE] get_ip_info (priority 1z)")

                    elif _GET_VOLUME_RE.search(body.text):
                        tool_name = "get_volume"
                        logger.info("[ROUTE] get_volume (priority 1z)")

                    # ── LAYER 2: System health (live usage) ───────────────────
                    elif _is_system_health_query(body.text):
                        tool_name = "system_health"
                        logger.info("[ROUTE] system_health")

                    # ── LAYER 3: System info (specs) ──────────────────────────
                    elif _is_system_info_query(body.text):
                        tool_name = "system_info"
                        logger.info("[ROUTE] system_info")

                    # ── LAYER 4: Short follow-up → replay last system tool ────
                    elif _is_continue_phrase(body.text):
                        last = memory_service.get_last_action()
                        if last and last.get("tool") in _SYSTEM_TOOL_NAMES:
                            tool_name   = last["tool"]
                            tool_params = last.get("params", {})
                            logger.info("[ROUTE] continue → %s", tool_name)

                    # ── LAYER 5: Explicit search ("search/google <query>") ────
                    elif _is_explicit_search(body.text):
                        q = _extract_search_query(body.text)
                        if q:
                            tool_name   = "search_web"
                            tool_params = {"query": q}
                            logger.info("[ROUTE] search_web → %r", q)

                    # ── LAYER 5a0: Volume / IP (must beat wiki) ───────────────
                    elif _GET_VOLUME_RE.search(body.text):
                        tool_name = "get_volume"
                        logger.info("[ROUTE] get_volume (priority)")

                    elif _IP_INFO_RE.search(body.text):
                        tool_name = "get_ip_info"
                        logger.info("[ROUTE] get_ip_info (priority)")

                    elif _SPEED_TEST_RE.search(body.text):
                        tool_name = "network_speed_test"
                        logger.info("[ROUTE] network_speed_test (priority)")

                    # ── LAYER 5a: Wikipedia quick-facts ──────────────────────
                    elif _extract_wiki_topic(body.text):
                        topic = _extract_wiki_topic(body.text)
                        tool_name   = "wiki_summary"
                        tool_params = {"topic": topic}
                        logger.info("[ROUTE] wiki_summary → %r", topic)

                    # ── LAYER 5b: Clipboard ───────────────────────────────────
                    elif _CLIPBOARD_READ_RE.search(body.text):
                        tool_name   = "read_clipboard"
                        tool_params = {}
                        logger.info("[ROUTE] read_clipboard")

                    elif _CLIPBOARD_WRITE_RE.search(body.text):
                        cm = _CLIPBOARD_WRITE_RE.search(body.text)
                        tool_name   = "write_clipboard"
                        tool_params = {"text": cm.group(1).strip() if cm else body.text}
                        logger.info("[ROUTE] write_clipboard")

                    # ── LAYER 5c: Screen reading (vision) ─────────────────────
                    elif _SCREEN_RE.search(body.text):
                        tool_name   = "read_screen"
                        tool_params = {"question": body.text}
                        logger.info("[ROUTE] read_screen")

                    # ── LAYER 5d: Typing ─────────────────────────────────────
                    elif _TYPE_RE.match(body.text.strip()):
                        tm = _TYPE_RE.match(body.text.strip())
                        tool_name   = "type_text"
                        tool_params = {"text": tm.group(1).strip() if tm else ""}
                        logger.info("[ROUTE] type_text")

                    # ── LAYER 5e: Window control ─────────────────────────────
                    elif _WIN_MINIMIZE_RE.search(body.text):
                        tool_name = "minimize_window"
                        logger.info("[ROUTE] minimize_window")
                    elif _WIN_MAXIMIZE_RE.search(body.text):
                        tool_name = "maximize_window"
                        logger.info("[ROUTE] maximize_window")
                    elif _WIN_CLOSE_RE.search(body.text):
                        tool_name = "close_window"
                        logger.info("[ROUTE] close_window")
                    elif _WIN_SWITCH_RE.search(body.text):
                        wm = _WIN_SWITCH_RE.search(body.text)
                        tool_name   = "switch_window"
                        tool_params = {"title": wm.group("app").strip() if wm else ""}
                        logger.info("[ROUTE] switch_window → %r", tool_params.get("title"))

                    # ── LAYER 5f: Reminder creation ───────────────────────────
                    elif _REMINDER_RE.search(body.text):
                        tool_name   = "_reminder_create"   # pseudo-tool → handled below
                        tool_params = {"text": body.text}
                        logger.info("[ROUTE] reminder_create via reminders API")

                    # ── LAYER 5g: Gmail read ─────────────────────────────────
                    elif _GMAIL_READ_RE.search(body.text):
                        tool_name   = "read_inbox"
                        tool_params = {"max_results": 5}
                        logger.info("[ROUTE] read_inbox")

                    # ── LAYER 5g2: Gmail send — GPT extracts to/subject/body ──
                    elif _GMAIL_SEND_RE.search(body.text):
                        logger.info("[ROUTE] send_email → GPT param extraction")
                        try:
                            import json as _json3
                            extract_resp = await client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=[
                                    {"role": "system", "content":
                                     "Extract email fields from the user's request. "
                                     "Return ONLY valid JSON: {\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}. "
                                     "If any field is unclear use empty string. No markdown."},
                                    {"role": "user", "content": body.text},
                                ],
                                max_tokens=150,
                                temperature=0,
                            )
                            extracted = _json3.loads(extract_resp.choices[0].message.content or "{}")
                            if extracted.get("to") and extracted.get("body"):
                                tool_name   = "send_email"
                                tool_params = extracted
                                logger.info("[ROUTE] send_email → %r", extracted.get("to"))
                        except Exception as gx:
                            logger.warning("send_email param extraction failed: %s", gx)

                    # ── LAYER 5h: Calendar read ───────────────────────────────
                    elif _CAL_READ_RE.search(body.text):
                        tool_name   = "list_events"
                        tool_params = {"days_ahead": 1}
                        logger.info("[ROUTE] list_events")

                    # ── LAYER 5h2: Calendar create — GPT extracts fields ──────
                    elif _CAL_CREATE_RE.search(body.text):
                        logger.info("[ROUTE] create_event → GPT param extraction")
                        try:
                            import json as _json4
                            from datetime import datetime, timezone
                            now_iso = datetime.now(timezone.utc).isoformat()
                            extract_resp2 = await client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=[
                                    {"role": "system", "content":
                                     f"Current UTC time: {now_iso}. "
                                     "Extract calendar event fields from the user's request. "
                                     "Return ONLY valid JSON: {\"summary\": \"...\", \"start_iso\": \"ISO8601\", \"duration_minutes\": 60, \"description\": \"\"}. "
                                     "start_iso must be a full ISO 8601 datetime with UTC offset. No markdown."},
                                    {"role": "user", "content": body.text},
                                ],
                                max_tokens=150,
                                temperature=0,
                            )
                            extracted2 = _json4.loads(extract_resp2.choices[0].message.content or "{}")
                            if extracted2.get("summary") and extracted2.get("start_iso"):
                                tool_name   = "create_event"
                                tool_params = extracted2
                                logger.info("[ROUTE] create_event → %r", extracted2.get("summary"))
                        except Exception as gx2:
                            logger.warning("create_event param extraction failed: %s", gx2)

                    # ── LAYER 5i: Volume control ──────────────────────────────
                    elif _SET_VOLUME_RE.search(body.text):
                        _m = _SET_VOLUME_RE.search(body.text)
                        _lvl = int(_m.group(1) or _m.group(2))
                        tool_name   = "volume_control"
                        tool_params = {"action": "set", "level": _lvl}
                        logger.info("[ROUTE] volume_control → set %d", _lvl)
                    elif _UNMUTE_RE.search(body.text):
                        tool_name   = "volume_control"
                        tool_params = {"action": "unmute"}
                        logger.info("[ROUTE] volume_control → unmute")
                    elif _MUTE_RE.search(body.text):
                        tool_name   = "volume_control"
                        tool_params = {"action": "mute"}
                        logger.info("[ROUTE] volume_control → mute")
                    elif _VOLUME_UP_RE.search(body.text):
                        tool_name   = "volume_control"
                        tool_params = {"action": "up", "steps": 5}
                        logger.info("[ROUTE] volume_control → up")
                    elif _VOLUME_DOWN_RE.search(body.text):
                        tool_name   = "volume_control"
                        tool_params = {"action": "down", "steps": 5}
                        logger.info("[ROUTE] volume_control → down")

                    # ── LAYER 5j: Brightness control ──────────────────────────
                    elif _SET_BRIGHTNESS_RE.search(body.text):
                        _m = _SET_BRIGHTNESS_RE.search(body.text)
                        _lvl = int(_m.group(1) or _m.group(2))
                        tool_name   = "brightness_control"
                        tool_params = {"action": "set", "level": _lvl}
                        logger.info("[ROUTE] brightness_control → set %d", _lvl)
                    elif _BRIGHTNESS_UP_RE.search(body.text):
                        tool_name   = "brightness_control"
                        tool_params = {"action": "up", "delta": 20}
                        logger.info("[ROUTE] brightness_control → up")
                    elif _BRIGHTNESS_DOWN_RE.search(body.text):
                        tool_name   = "brightness_control"
                        tool_params = {"action": "down", "delta": 20}
                        logger.info("[ROUTE] brightness_control → down")

                    # ── LAYER 5k: Process management ──────────────────────────
                    elif _LIST_PROCS_RE.search(body.text):
                        tool_name = "list_processes"
                        logger.info("[ROUTE] list_processes")

                    elif _KILL_PROC_RE.search(body.text):
                        _km = _KILL_PROC_RE.search(body.text)
                        _proc = (_km.group("proc") or "").strip().rstrip(".")
                        if _proc:
                            tool_name   = "kill_process"
                            tool_params = {"name": _proc}
                            logger.info("[ROUTE] kill_process → %r", _proc)

                    elif _STARTUP_DISABLE_RE.search(body.text):
                        _sdm = _STARTUP_DISABLE_RE.search(body.text)
                        _app = (_sdm.group("app") or "").strip()
                        if _app:
                            tool_name   = "disable_startup_app"
                            tool_params = {"name": _app}
                            logger.info("[ROUTE] disable_startup_app → %r", _app)

                    elif _STARTUP_LIST_RE.search(body.text):
                        tool_name = "get_startup_apps"
                        logger.info("[ROUTE] get_startup_apps")

                    # ── LAYER 5l: Display control ──────────────────────────────
                    elif _RESOLUTION_RE.search(body.text):
                        _rm = _RESOLUTION_RE.search(body.text)
                        if _rm.group("preset"):
                            _preset_map = {
                                "4k": (3840, 2160), "uhd": (3840, 2160),
                                "2k": (2560, 1440), "1440p": (2560, 1440),
                                "1080p": (1920, 1080), "fhd": (1920, 1080),
                                "720p": (1280, 720), "hd": (1280, 720),
                            }
                            _w, _h = _preset_map.get(_rm.group("preset").lower(), (1920, 1080))
                        else:
                            _w, _h = int(_rm.group("w")), int(_rm.group("h"))
                        tool_name   = "set_display_resolution"
                        tool_params = {"width": _w, "height": _h}
                        logger.info("[ROUTE] set_display_resolution → %dx%d", _w, _h)

                    elif _REFRESH_RATE_RE.search(body.text):
                        _rrm  = _REFRESH_RATE_RE.search(body.text)
                        _rate = int(_rrm.group("rate") or _rrm.group("rate2") or 60)
                        tool_name   = "set_refresh_rate"
                        tool_params = {"rate": _rate}
                        logger.info("[ROUTE] set_refresh_rate → %dHz", _rate)

                    elif _VDESK_CREATE_RE.search(body.text):
                        tool_name = "virtual_desktop_create"
                        logger.info("[ROUTE] virtual_desktop_create")

                    elif _VDESK_SWITCH_RE.search(body.text):
                        _vsm = _VDESK_SWITCH_RE.search(body.text)
                        _vdir_raw = (_vsm.group("dir") or _vsm.group("dir2") or "right").lower()
                        _vdir = "left" if _vdir_raw in ("previous", "prev", "left") else "right"
                        tool_name   = "virtual_desktop_switch"
                        tool_params = {"direction": _vdir}
                        logger.info("[ROUTE] virtual_desktop_switch → %s", _vdir)

                    elif _SCREENSHOT_RE.search(body.text):
                        tool_name = "take_screenshot"
                        logger.info("[ROUTE] take_screenshot")

                    # ── LAYER 5m: Network / WiFi ───────────────────────────────
                    elif _WIFI_LIST_RE.search(body.text):
                        tool_name = "open_wifi_panel"
                        logger.info("[ROUTE] open_wifi_panel")

                    elif _WIFI_CONNECT_RE.search(body.text):
                        _wcm  = _WIFI_CONNECT_RE.search(body.text)
                        _ssid = (_wcm.group("ssid") or "").strip()
                        if _ssid:
                            tool_name   = "wifi_connect"
                            tool_params = {"ssid": _ssid}
                            logger.info("[ROUTE] wifi_connect → %r", _ssid)

                    elif _WIFI_DISCONNECT_RE.search(body.text):
                        tool_name = "wifi_disconnect"
                        logger.info("[ROUTE] wifi_disconnect")

                    elif _SPEED_TEST_RE.search(body.text):
                        tool_name = "network_speed_test"
                        logger.info("[ROUTE] network_speed_test")

                    elif _IP_INFO_RE.search(body.text):
                        tool_name = "get_ip_info"
                        logger.info("[ROUTE] get_ip_info")

                    elif _FLUSH_DNS_RE.search(body.text):
                        tool_name = "flush_dns"
                        logger.info("[ROUTE] flush_dns")

                    # ── LAYER 5n: Date/time, battery & power plans ────────────
                    elif _DATE_TIME_RE.search(body.text):
                        tool_name = "get_date_time"
                        logger.info("[ROUTE] get_date_time")

                    elif _BATTERY_RE.search(body.text):
                        tool_name = "get_battery_status"
                        logger.info("[ROUTE] get_battery_status")

                    elif _POWER_PLAN_RE.search(body.text):
                        _ppm = _POWER_PLAN_RE.search(body.text)
                        _plan = (_ppm.group("plan") or _ppm.group("plan2") or "balanced").strip().lower()
                        _plan = re.sub(r'\s+', ' ', _plan)
                        tool_name   = "set_power_plan"
                        tool_params = {"plan": _plan}
                        logger.info("[ROUTE] set_power_plan → %r", _plan)

                    elif _SCHED_SHUTDOWN_RE.search(body.text):
                        _ssm  = _SCHED_SHUTDOWN_RE.search(body.text)
                        _n    = int(_ssm.group("n"))
                        _unit = _ssm.group("unit").lower()
                        if _unit.startswith("h"):
                            tool_name   = "schedule_shutdown"
                            tool_params = {"hours": _n, "minutes": 0}
                        else:
                            tool_name   = "schedule_shutdown"
                            tool_params = {"hours": 0, "minutes": _n}
                        logger.info("[ROUTE] schedule_shutdown → %r", tool_params)

                    # ── LAYER 5o: Storage / disk ───────────────────────────────
                    elif _DISK_USAGE_RE.search(body.text):
                        tool_name = "get_disk_usage"
                        logger.info("[ROUTE] get_disk_usage")

                    elif _RECYCLE_BIN_RE.search(body.text):
                        tool_name = "empty_recycle_bin"
                        logger.info("[ROUTE] empty_recycle_bin")

                    elif _TEMP_SIZE_RE.search(body.text):
                        tool_name = "get_temp_files_size"
                        logger.info("[ROUTE] get_temp_files_size")

                    elif _CLEAR_TEMP_RE.search(body.text):
                        tool_name = "clear_temp_files"
                        logger.info("[ROUTE] clear_temp_files")

                    # ── LAYER 5p: Audio ────────────────────────────────────────
                    elif _GET_VOLUME_RE.search(body.text):
                        tool_name = "get_volume"
                        logger.info("[ROUTE] get_volume")

                    elif _AUDIO_DEVICES_RE.search(body.text):
                        tool_name = "list_audio_devices"
                        logger.info("[ROUTE] list_audio_devices")

                    elif _SET_AUDIO_RE.search(body.text):
                        _sam = _SET_AUDIO_RE.search(body.text)
                        _dev = (_sam.group("device") or _sam.group("device2") or "").strip()
                        if _dev:
                            tool_name   = "set_default_audio"
                            tool_params = {"name": _dev}
                            logger.info("[ROUTE] set_default_audio → %r", _dev)

                    # ── LAYER 5q: System maintenance ───────────────────────────
                    elif _CLEAR_CLIPBOARD_RE.search(body.text):
                        tool_name = "clear_clipboard"
                        logger.info("[ROUTE] clear_clipboard")

                    elif _UPTIME_RE.search(body.text):
                        tool_name = "get_uptime"
                        logger.info("[ROUTE] get_uptime")

                    elif _DISK_CLEANUP_RE.search(body.text):
                        tool_name = "run_disk_cleanup"
                        logger.info("[ROUTE] run_disk_cleanup")

                    elif _WIN_UPDATES_RE.search(body.text):
                        tool_name = "check_windows_updates"
                        logger.info("[ROUTE] check_windows_updates")

                    # ── LAYER 5.5: Compound multi-step ("open X and then Y") ──
                    # Detected AFTER single-intent layers so simple commands
                    # never pay the planning overhead.
                    elif _COMPOUND_RE.search(body.text):
                        logger.info("[ROUTE] compound request → task planner")
                        try:
                            from ..services.task_service import plan_task_sync
                            import json as _json2
                            steps = plan_task_sync(body.text, None, settings.openai_api_key)
                            executed_descs: list[str] = []
                            for step_dict in steps[:4]:   # hard cap at 4 for voice
                                s_tool   = step_dict.get("tool", "")
                                s_params = step_dict.get("params", {})
                                s_desc   = step_dict.get("description", s_tool)
                                if s_tool and s_tool in registry:
                                    s_result = registry.execute(s_tool, s_params, ctx)
                                    s_action = s_result.to_sse_action()
                                    if s_action:
                                        yield f"data: {_json2.dumps({'type': 'action', 'turn_id': turn_id, **s_action})}\n\n"
                                    status = "done" if s_result.success else "failed"
                                    executed_descs.append(f"{s_desc} ({status})")
                                    logger.info("[COMPOUND] %s %s → %s", s_tool, s_params, status)
                            # Inject a summary instruction so GPT speaks concisely
                            if executed_descs:
                                summary = "; ".join(executed_descs)
                                msgs.append({
                                    "role": "user",
                                    "content": (
                                        f"I just completed these steps for you: {summary}. "
                                        "Briefly confirm what was done in 1-2 natural spoken sentences. "
                                        "Do not use markdown. Be warm and direct."
                                    ),
                                })
                                tool_name = None   # skip single-tool path; fall through to GPT
                        except Exception as compound_exc:
                            logger.warning("Compound routing failed: %s", compound_exc)

                    # ── IntentRouter Tier 3: semantic classifier ──────────────
                    # Handles novel phrasings that regex missed, avoids LLM calls.
                    if not tool_name and _intent_router.classifier_ready:
                        _ir3 = _intent_router.route(body.text)
                        if _ir3.tool_name and _ir3.confidence >= 0.65:
                            tool_name   = _ir3.tool_name
                            tool_params = _ir3.params
                            logger.info("[ROUTE] intent-router tier=3 conf=%.2f → %s",
                                        _ir3.confidence, tool_name)

                    # ── Clarification flow: ambiguous short commands ──────────────
                    # When no tool was resolved and the query looks like a system
                    # command (not a question/conversation), offer 2–3 candidates
                    # instead of falling through to pure GPT.
                    if not tool_name and len(body.text.split()) <= 6:
                        try:
                            _cl_candidates = _intent_router.top_candidates(body.text, n=3)
                            if _cl_candidates and _cl_candidates[0][1] >= 0.45:
                                _opts = [t for t, _ in _cl_candidates if _ >= 0.40][:3]
                                if len(_opts) >= 2:
                                    _opt_labels = [t.replace("_", " ") for t in _opts]
                                    _spoken_cl = (
                                        f"I'm not sure what you mean. Did you want to: "
                                        + ", or ".join(f"{i+1}) {l}" for i, l in enumerate(_opt_labels))
                                        + "? Say the number or rephrase."
                                    )
                                    logger.info("[CLARIFY] ambiguous → %s", _opts)
                                    yield f"data: {json.dumps({'type': 'clarify', 'turn_id': turn_id, 'options': _opts, 'text': _spoken_cl})}\n\n"
                                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _spoken_cl})}\n\n"
                                    yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _spoken_cl})}\n\n"
                                    return
                        except Exception:
                            pass

                    if not tool_name and not (msgs and msgs[-1].get("content", "").startswith("I just completed")):
                        logger.info("[ROUTE] no tool → pure GPT stream")

                    # ── Reminder pseudo-tool: call reminders API directly ─────
                    if tool_name == "_reminder_create":
                        try:
                            import httpx
                            r = httpx.post(
                                "http://localhost:8000/api/v1/reminders",
                                json={"text": tool_params.get("text", body.text)},
                                timeout=5,
                            )
                            rd = r.json()
                            spoken_r = rd.get("spoken") or "Reminder set!"
                        except Exception as rem_exc:
                            logger.warning("Reminder API call failed: %s", rem_exc)
                            spoken_r = "Got it — I'll remind you."
                        try:
                            _epi_mem.save(body.session_id or turn_id, "assistant", spoken_r)
                        except Exception:
                            pass
                        yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': spoken_r})}\n\n"
                        yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': spoken_r})}\n\n"
                        return

                    if tool_name and tool_name in registry:
                        t_tool_start = __import__("time").perf_counter()
                        logger.info("[TOOL] executing %s params=%r", tool_name, tool_params)

                        # Retry once on transient failure (network, subprocess timeout)
                        result = registry.execute(tool_name, tool_params, ctx)
                        if not result.success and _RETRYABLE_TOOLS.get(tool_name):
                            import time as _time
                            _time.sleep(0.4)
                            logger.info("[RETRY] %s attempt 2", tool_name)
                            result = registry.execute(tool_name, tool_params, ctx)

                        t_tool_end = __import__("time").perf_counter()
                        logger.info("[EXEC] %s completed in %.0fms — result len=%d",
                                    tool_name, (t_tool_end - t_tool_start) * 1000, len(result.text or ""))

                        # Execution validation: confirm real state change
                        if result.success:
                            try:
                                from ..services.exec_validator import validate as _validate_exec
                                from ..tools.registry import ToolResult as _ToolResult
                                _v_ok, _v_detail = _validate_exec(tool_name, tool_params, result.text or "")
                                if not _v_ok:
                                    logger.warning("[VALID] %s failed: %s", tool_name, _v_detail)
                                    _fb = _TOOL_FALLBACKS.get(tool_name)
                                    if _fb:
                                        _fb_tool, _fb_params_fn = _fb
                                        _fb_params = _fb_params_fn(tool_params)
                                        if _fb_tool in registry:
                                            logger.info("[FALLBACK] %s → %s", tool_name, _fb_tool)
                                            result = registry.execute(_fb_tool, _fb_params, ctx)
                                    else:
                                        # No fallback — report honestly rather than claim success
                                        _fail_msg = f"I tried but the action didn't take effect. {_v_detail}"
                                        result = _ToolResult(
                                            success=False,
                                            text=_fail_msg,
                                            spoken=_fail_msg,
                                        )
                            except Exception:
                                pass

                        # Backfill tool_name + success onto the user turn saved earlier
                        try:
                            _epi_mem.update_last_tool(
                                body.session_id or turn_id, tool_name, result.success
                            )
                        except Exception:
                            pass

                        # Store spoken text as fallback if GPT narration fails
                        _tool_spoken = result.spoken or result.text or ""

                        # If tool itself failed with a spoken message, return it directly
                        # (e.g. screen reading with no API key — avoid double GPT call)
                        if not result.success and _tool_spoken:
                            try:
                                _epi_mem.save(body.session_id or turn_id, "assistant", _tool_spoken)
                            except Exception:
                                pass
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _tool_spoken})}\n\n"
                            yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _tool_spoken})}\n\n"
                            return

                        # Tools with pre-built spoken output skip GPT narration entirely.
                        # This fixes "AI stream unavailable" for system_info, create_folder,
                        # open commands, etc. — they work even without an OpenAI API key.
                        if result.success and _tool_spoken and tool_name in _DIRECT_SPOKEN_TOOLS:
                            try:
                                _epi_mem.save(body.session_id or turn_id, "assistant", _tool_spoken)
                            except Exception:
                                pass
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': _tool_spoken})}\n\n"
                            yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': _tool_spoken})}\n\n"
                            return

                        # Signal frontend about side-effects (opens, URLs, etc.)
                        sse_action = result.to_sse_action()
                        if sse_action:
                            logger.info("[EXEC] SSE action: %r", sse_action)
                            yield f"data: {json.dumps({'type': 'action', 'turn_id': turn_id, **sse_action})}\n\n"

                        # Persist as last action for context continuity
                        if body.session_id:
                            try:
                                memory_service.set_last_action(tool_name, tool_params, result.text)
                            except Exception:
                                pass

                        # Inject tool result into message context so the single
                        # streaming GPT call produces a data-rich spoken answer.
                        msgs.append({"role": "assistant", "content": None,
                                     "tool_calls": [{"id": "direct-0", "type": "function",
                                                      "function": {"name": tool_name,
                                                                   "arguments": _json.dumps(tool_params)}}]})
                        msgs.append({"role": "tool", "content": result.text or "Done.",
                                     "tool_call_id": "direct-0"})
                        msgs.append({"role": "user", "content":
                                     "Using the exact data from the tool result above, speak the specific facts "
                                     "to the user in 1-2 natural sentences. Include real numbers, names, and values. "
                                     "Start immediately with the data — do not say 'I checked' or 'I found'. No markdown."})

                except Exception as tc_exc:
                    logger.debug("Direct routing skipped: %s", tc_exc)
                    _tool_spoken = ""

            buf = ""
            full = ""
            idx = 0
            first_flush_done = False   # tracks whether first TTS chunk has fired

            try:
                stream = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=msgs,
                    max_tokens=120,
                    temperature=0.4,
                    stream=True,
                )
            except Exception as gpt_exc:
                # GPT call failed (bad key, network, etc.) — fall back to tool spoken text
                fallback_text = _tool_spoken if _tool_spoken else "I ran into an issue connecting to the AI. Please check your API key and try again."
                logger.warning("GPT call failed, using fallback: %s", gpt_exc)
                try:
                    _epi_mem.save(body.session_id or turn_id, "assistant", fallback_text)
                except Exception:
                    pass
                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': fallback_text})}\n\n"
                yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': fallback_text})}\n\n"
                return

            try:
                async for chunk in stream:
                    if chunk.choices[0].finish_reason:
                        break
                    delta = chunk.choices[0].delta.content or ""
                    if not delta:
                        continue
                    buf  += delta
                    full += delta

                    # Flush complete sentences (all but the last split segment)
                    parts = _SENT_RE.split(buf)
                    for sentence in parts[:-1]:
                        sentence = sentence.strip()
                        if sentence:
                            cleaned = _clean_for_speech(sentence, max_chars=4000)
                            if cleaned:
                                yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': cleaned})}\n\n"
                                idx += 1
                                first_flush_done = True
                    buf = parts[-1] if parts else ""

                    # ── Fast first-chunk flush ────────────────────────────────
                    # Fire TTS as soon as ~10 chars accumulate (~2 words).
                    # This cuts perceived latency to near-zero — TTS starts
                    # while GPT is still generating the rest of the response.
                    if not first_flush_done and len(buf) >= 6:
                        cleaned = _clean_for_speech(buf.strip(), max_chars=4000)
                        if cleaned:
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': cleaned})}\n\n"
                            idx += 1
                            first_flush_done = True
                            buf = ""
                    # Subsequent: force-flush very long buffer
                    elif first_flush_done and len(buf) > 220:
                        cleaned = _clean_for_speech(buf.strip(), max_chars=4000)
                        if cleaned:
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': cleaned})}\n\n"
                            idx += 1
                        buf = ""

            except Exception as stream_exc:
                logger.warning("Stream interrupted: %s", stream_exc)
                # Flush whatever partial buffer we have
                if buf.strip():
                    cleaned = _clean_for_speech(buf.strip(), max_chars=4000)
                    if cleaned:
                        yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': cleaned})}\n\n"
                        idx += 1
                        buf = ""
                # If we got nothing at all, emit a recovery phrase
                if not full.strip():
                    fallback = "Sorry, my response was cut short. Please ask again."
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': fallback})}\n\n"
                    full = fallback

            # Flush remaining tail
            if buf.strip():
                cleaned = _clean_for_speech(buf.strip(), max_chars=4000)
                if cleaned:
                    yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': idx, 'text': cleaned})}\n\n"

            full_text = full.strip()
            yield f"data: {json.dumps({'type': 'done', 'turn_id': turn_id, 'full_text': full_text})}\n\n"

            # ── Store turn in memory ──────────────────────────────────────────
            if body.session_id and full_text:
                try:
                    from ..services.memory_service import memory_service
                    memory_service.add_turn(body.session_id, body.text, full_text)
                except Exception:
                    pass

            # ── Episodic memory: persist assistant turn + teach intent cache ──
            try:
                _resolved_tool = locals().get("tool_name")
                _epi_mem.save(
                    body.session_id or turn_id, "assistant", full_text,
                    tool_name=_resolved_tool,
                    success=True,
                )
                # If LLM resolved to a tool, teach Tier 1 cache so next identical
                # phrasing skips LLM entirely.
                if _resolved_tool:
                    _intent_router.confirm(body.text, _resolved_tool,
                                           locals().get("tool_params") or {})
            except Exception:
                pass

            # ── Feature #2: Log to history ────────────────────────────────────
            try:
                _get_history().log(
                    command=body.text,
                    result=full_text[:500],
                    tool_used="",
                    session_id=body.session_id,
                )
            except Exception:
                pass

            # ── Feature #3: Smart follow-up suggestion ────────────────────────
            try:
                _follow_up = await _suggest_follow_up(body.text, full_text, settings.openai_api_key)
                if _follow_up:
                    yield f"data: {json.dumps({'type': 'follow_up', 'turn_id': turn_id, 'suggestion': _follow_up})}\n\n"
            except Exception:
                pass

        except Exception as exc:
            # ── Feature #8: Ollama local LLM fallback ────────────────────────
            if "openai" in str(exc).lower() or "connection" in str(exc).lower() or "api" in str(exc).lower():
                try:
                    import httpx as _hx
                    payload = {
                        "model":  _OLLAMA_MODEL,
                        "prompt": body.text,
                        "stream": False,
                    }
                    r = _hx.post(_OLLAMA_URL, json=payload, timeout=30)
                    if r.status_code == 200:
                        ollama_text = r.json().get("response", "")
                        if ollama_text:
                            ollama_text = ollama_text.strip()
                            yield f"data: {json.dumps({'type': 'chunk', 'turn_id': turn_id, 'index': 0, 'text': ollama_text})}\n\n"
                            yield f"data: {json.dumps({'type': 'done',  'turn_id': turn_id, 'full_text': ollama_text, 'source': 'ollama'})}\n\n"
                            return
                except Exception as _ollama_exc:
                    logger.debug("Ollama fallback failed: %s", _ollama_exc)
            logger.exception("respond-stream error")
            yield f"data: {json.dumps({'type': 'error', 'turn_id': turn_id, 'message': str(exc)})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )
