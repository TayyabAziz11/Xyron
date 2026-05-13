"""
Voice identity API — mode switching, profile management, FX control.
Endpoints under /api/v1/voice/identity/
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voice/identity", tags=["voice-identity"])

_PROFILES_PATH = Path(__file__).parent.parent.parent / "data" / "voice_profiles.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_profiles() -> dict:
    try:
        return json.loads(_PROFILES_PATH.read_text())
    except Exception:
        return {"active_profile": "echo", "fx_enabled": True, "profiles": {}}


def _save_profiles(data: dict) -> None:
    _PROFILES_PATH.write_text(json.dumps(data, indent=2))


# ── Models ────────────────────────────────────────────────────────────────────

class SetModeRequest(BaseModel):
    mode: str


class SetProfileRequest(BaseModel):
    profile: str


class SetFxRequest(BaseModel):
    enabled: bool


class PreviewRequest(BaseModel):
    text: str = "Neural systems online. All parameters nominal."
    mode: str | None = None
    profile: str | None = None


# ── Voice mode endpoints ───────────────────────────────────────────────────────

@router.get("/mode")
def get_voice_mode():
    from voice.voice_personality import get_mode, list_modes
    return {"mode": get_mode(), "modes": list_modes()}


@router.post("/mode")
def set_voice_mode(req: SetModeRequest):
    from voice.voice_personality import set_mode, list_modes
    if not set_mode(req.mode):
        raise HTTPException(status_code=400, detail=f"Unknown mode '{req.mode}'. Valid: DEFAULT, WORK, TAKEOVER, CHILL, HOME")
    return {"mode": req.mode.upper(), "modes": list_modes()}


@router.post("/interrupt")
def interrupt_voice():
    """Cancel any in-progress TTS generation."""
    from voice.voice_personality import interrupt_speech
    interrupt_speech()
    return {"interrupted": True}


# ── FX control ────────────────────────────────────────────────────────────────

@router.get("/fx")
def get_fx_state():
    from voice.audio_fx import is_fx_enabled
    return {"enabled": is_fx_enabled()}


@router.post("/fx")
def set_fx_state(req: SetFxRequest):
    from voice.audio_fx import set_fx_enabled
    set_fx_enabled(req.enabled)
    # Persist to profiles file
    try:
        data = _load_profiles()
        data["fx_enabled"] = req.enabled
        _save_profiles(data)
    except Exception:
        pass
    return {"enabled": req.enabled}


# ── Profile endpoints ──────────────────────────────────────────────────────────

@router.get("/profiles")
def list_profiles():
    data = _load_profiles()
    return {
        "active_profile": data.get("active_profile", "echo"),
        "fx_enabled": data.get("fx_enabled", True),
        "profiles": [
            {
                "id": pid,
                "display_name": p.get("display_name", pid),
                "description": p.get("description", ""),
                "speed": p.get("speed", 0.92),
                "fx_preset": p.get("fx_preset", "subtle"),
                "personality_style": p.get("personality_style", ""),
                "active": pid == data.get("active_profile"),
            }
            for pid, p in data.get("profiles", {}).items()
        ],
    }


@router.post("/profile")
def set_profile(req: SetProfileRequest):
    data = _load_profiles()
    profiles = data.get("profiles", {})
    if req.profile not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown profile '{req.profile}'")
    data["active_profile"] = req.profile
    _save_profiles(data)

    # Apply the profile's speed + fx_preset to voice_personality globals
    p = profiles[req.profile]
    try:
        from voice.voice_personality import MODES, get_mode
        from voice.audio_fx import set_fx_enabled
        set_fx_enabled(data.get("fx_enabled", True))
    except Exception:
        pass

    return {"active_profile": req.profile, "profile": profiles[req.profile]}


# ── Preview endpoint ───────────────────────────────────────────────────────────

@router.post("/preview")
async def preview_voice(req: PreviewRequest):
    """
    Synthesize a sample phrase with the specified mode/profile and return WAV audio.
    Used by the settings page preview button.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    data       = _load_profiles()
    profile_id = req.profile or data.get("active_profile", "echo")
    profile    = data.get("profiles", {}).get(profile_id, {})
    mode       = (req.mode or "DEFAULT").upper()

    kokoro_voice = profile.get("kokoro_voice", "af_heart")
    speed        = profile.get("speed", 0.92)
    fx_preset    = profile.get("fx_preset", "subtle")

    text = req.text.strip()[:300] or "Neural systems online."

    def _generate() -> bytes | None:
        try:
            from voice.voice_personality import shape_text, set_mode, get_mode as _gm
            from voice.audio_fx import apply_fx
            import soundfile as sf
            import io as _io

            prev_mode = _gm()
            set_mode(mode)
            shaped = shape_text(text, mode)
            set_mode(prev_mode)

            # Try the router's Kokoro instance first (warm, cached)
            try:
                from api.routers.voice import _kokoro_to_wav as _ktw
                wav = _ktw(shaped, kokoro_voice, speed)
                if wav:
                    return wav
            except Exception:
                pass

            # Fall back to tts_service
            from voice.tts_service import synthesize_kokoro
            samples, sr = None, None
            try:
                import soundfile as sf
                import io as _io2
                raw = synthesize_kokoro(shaped, voice=kokoro_voice, speed=speed)
                if raw:
                    return raw
            except Exception:
                pass
            return None
        except Exception as exc:
            logger.warning("[VoiceIdentity] preview generation failed: %s", exc)
            return None

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as ex:
        wav_bytes = await loop.run_in_executor(ex, _generate)

    if not wav_bytes:
        raise HTTPException(status_code=503, detail="TTS unavailable")

    return Response(content=wav_bytes, media_type="audio/wav")
