"""
Xyron — FastAPI application entry point.

Mounts all routers under /api/v1 with CORS configured for the web dashboard.
Agent registry is initialized at startup.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
    stream=sys.stdout,
)

import os as _os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

# Propagate ONNX_PROVIDER from settings into os.environ so kokoro_onnx + onnxruntime pick it up.
# Must happen before any model is loaded (kokoro_onnx reads os.getenv at InferenceSession creation).
if settings.onnx_provider and not _os.environ.get("ONNX_PROVIDER"):
    _os.environ["ONNX_PROVIDER"] = settings.onnx_provider
    logging.getLogger(__name__).info("[Config] ONNX_PROVIDER=%s", settings.onnx_provider)
from .routers import health, commands, approvals, activity, integrations, workflows, events, voice, voice_ws, drafts, system, tasks, reminders, history, macros, notes, meeting, proactive, automation, memory, dataset

logger = logging.getLogger(__name__)


def _init_agent_registry() -> None:
    """Register all agents with the command router at startup."""
    try:
        # Ensure the src/ package is importable
        src_path = settings.repo_root / "backend" / "src"
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))
        import ai_operator.agents.registry  # noqa: F401 — side-effect: registers agents
        logger.info("Agent registry initialized successfully")
    except Exception as exc:
        logger.warning("Agent registry init failed (non-fatal): %s", exc)


app = FastAPI(
    title="Xyron API",
    description=(
        "Backend API for the Xyron dashboard — "
        "command intake, approvals, activity, and integrations."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
async def startup() -> None:
    _init_agent_registry()
    # Pre-warm all models in background threads — eliminates cold-start lag on first request
    import threading as _threading

    def _warmup_all() -> None:
        import logging as _log, sys as _sys
        _l = _log.getLogger("startup.warmup")
        # Ensure backend/ is on sys.path so "voice.*" imports resolve
        _backend_path = str(settings.repo_root / "backend")
        if _backend_path not in _sys.path:
            _sys.path.insert(0, _backend_path)
        # 1. Whisper STT
        try:
            from .routers.voice import _get_local_whisper_model
            from voice.whisper_service import _detect_device as _wd
            _get_local_whisper_model()
            _dev, _ct = _wd()
            _l.info("[Warmup] Whisper ready — device=%s compute=%s", _dev, _ct)
        except Exception as exc:
            _l.warning("[Warmup] Whisper: %s", exc)
        # 2. Kokoro TTS
        try:
            import os as _os
            from .routers.voice import _get_kokoro, _kokoro_to_wav
            k = _get_kokoro()
            if k is not None:
                _kokoro_to_wav("Ready.", "nova", 1.0)
                _l.info("[Warmup] Kokoro ready — provider=%s",
                        _os.environ.get("ONNX_PROVIDER", "CPU"))
        except Exception as exc:
            _l.warning("[Warmup] Kokoro: %s", exc)
        # 3. Intent router (sentence-transformer)
        try:
            from .services.intent_router import intent_router as _ir  # noqa: F401
            _dev_st = str(getattr(getattr(_ir, "_model", None), "device", "unknown"))
            _l.info("[Warmup] IntentRouter ready — device=%s classifier=%s",
                    _dev_st, _ir.classifier_ready)
        except Exception as exc:
            _l.warning("[Warmup] IntentRouter: %s", exc)
        # 4. WakeWordService — loads OWW + tiny Whisper wake model
        try:
            from voice.wake_word_service import wake_word_service as _wws, preload_wake_model
            preload_wake_model()
            import time as _wt; _wt.sleep(1)  # allow OWW background thread to finish
            _l.info("[Warmup] WakeWordService ready — oww=%s wake_model=tiny", _wws._oww_ready)
        except Exception as exc:
            _l.warning("[Warmup] WakeWordService: %s", exc)
        # 5. Pre-generate TTS ack cache for instant playback (On it / Opening / Done)
        try:
            from .routers.voice import _kokoro_to_wav
            import pathlib as _pl
            _cache_dir = _pl.Path("/tmp/xyron-ack")
            _cache_dir.mkdir(parents=True, exist_ok=True)
            for _key, _text in [("on_it", "On it."), ("opening", "Opening."),
                                  ("done", "Done."), ("got_it", "Got it.")]:
                _out = _cache_dir / f"{_key}.wav"
                if not _out.exists():
                    _wav = _kokoro_to_wav(_text, "nova", 1.1)
                    if _wav:
                        _out.write_bytes(_wav)
            _l.info("[Warmup] TTS ack cache ready at %s", _cache_dir)
        except Exception as exc:
            _l.warning("[Warmup] TTS cache: %s", exc)

    _threading.Thread(target=_warmup_all, daemon=True, name="model-warmup").start()
    # Start background services
    try:
        from .config import settings as _s
        from .services.screen_context_service import screen_context_service
        from .services.proactive_service import proactive_service
        from .tools import browser_tools  # noqa: F401 — registers browser tools
        if _s.openai_api_key and _s.openai_api_key.startswith("sk-"):
            screen_context_service.start(_s.openai_api_key)
            proactive_service.start(_s.openai_api_key)
    except Exception as _exc:
        import logging
        logging.getLogger(__name__).warning("Background service startup failed: %s", _exc)


# CORS — allow the Next.js dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers under /api/v1
app.include_router(health.router)
app.include_router(commands.router)
app.include_router(approvals.router)
app.include_router(activity.router)
app.include_router(integrations.router)
app.include_router(workflows.router)
app.include_router(events.router)
app.include_router(voice.router)
app.include_router(drafts.router)
app.include_router(system.router)
app.include_router(tasks.router)
app.include_router(reminders.router)
app.include_router(history.router)
app.include_router(macros.router)
app.include_router(notes.router)
app.include_router(meeting.router)
app.include_router(proactive.router)
app.include_router(automation.router, prefix="/api/v1/automation", tags=["automation"])
app.include_router(memory.router)
app.include_router(voice_ws.router)
app.include_router(dataset.router)
