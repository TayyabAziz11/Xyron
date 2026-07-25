"""
Xyron — FastAPI application entry point.

Mounts all routers under /api/v1 with CORS configured for the web dashboard.
Agent registry is initialized at startup.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s:%(name)s:%(message)s",
    datefmt="%H:%M:%S",
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
from .routers import health, commands, approvals, activity, integrations, workflows, events, voice, voice_ws, drafts, system, tasks, reminders, history, macros, notes, meeting, proactive, automation, memory, dataset, environment, cognition, voice_identity, dev, brain, takeover, dashboard
from .routers import auth as auth_router
from .routers import monitor as monitor_router
from .routers import agents as agents_router
from .routers import browser as browser_router

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
    import threading as _threading

    # ai_operator's registry (LinkedIn/Gmail/Odoo/etc. business-automation
    # agents) is not on the voice/text critical path — nothing in it is
    # required for the first interaction. Register it on a background
    # thread instead of blocking the very first line of startup.
    _threading.Thread(target=_init_agent_registry, daemon=True, name="ai-operator-registry-init").start()
    from .services.readiness_service import readiness_service as _rs
    from .services.background_scheduler import scheduler as _sched

    # ── Phase 4.14: critical-path boot ──────────────────────────────────────
    # Previously `set_core_ready()` fired almost immediately at boot — long
    # before Whisper/Kokoro/intent routing had actually finished loading in
    # their background threads — so a session could connect and speak while
    # models were still cold (measured: ~1 minute first-STT, warmup finishing
    # after the user's first command). The gate now only opens once every
    # service in Part 1's critical list has actually been attempted:
    # Whisper fast, Whisper accurate, Kokoro, intent routing, browser
    # runtime, conversation engine.
    _critical_models_done = _threading.Event()

    def _critical_model_warmup() -> None:
        """Kokoro + both Whisper models + intent router. Pure CPU/GPU work —
        no event loop needed, so this runs in a plain background thread."""
        import time as _t
        _l = logging.getLogger("startup.critical_boot")

        def _run(name: str, fn) -> None:
            _t0 = _t.monotonic()
            try:
                fn()
                _rs.mark_service(name, True)
                _l.info("[CRITICAL_BOOT_DONE] service=%s ms=%.0f", name, (_t.monotonic() - _t0) * 1000)
            except Exception as exc:
                _rs.mark_service(name, False)
                _l.warning("[CRITICAL_BOOT_FAIL] service=%s error=%s", name, exc)

        def _kokoro() -> None:
            from .routers.voice import _get_kokoro, _kokoro_to_wav
            if _get_kokoro() is None:
                raise RuntimeError("kokoro model unavailable")
            _kokoro_to_wav("Ready.", "nova", 1.0)

        def _whisper_fast() -> None:
            import numpy as _np
            from voice.whisper_service import transcribe_fast as _tr_fast
            _tr_fast(_np.zeros(16000, dtype=_np.float32))

        def _whisper_accurate() -> None:
            import numpy as _np
            from voice.whisper_service import transcribe_audio as _tr
            _tr(_np.zeros(16000, dtype=_np.float32), 16000, "en", True)

        def _intent() -> None:
            # Best-effort: Tier 3 semantic classifier is known-broken on some
            # environments (sentence-transformers/sklearn ABI mismatch) and
            # degrades gracefully to keyword/LLM tiers — still attempt the
            # warm-up so any failure surfaces now, not on the user's first turn.
            from .services.intent_router import intent_router as _ir
            if not _ir.classifier_ready:
                raise RuntimeError("semantic classifier unavailable (degraded — keyword/LLM tiers still active)")

        _threads = [
            _threading.Thread(target=_run, args=(n, f), daemon=True, name=f"boot-{n}")
            for n, f in (
                ("tts", _kokoro),
                ("whisper_fast", _whisper_fast),
                ("whisper_accurate", _whisper_accurate),
                ("intent", _intent),
            )
        ]
        for _th in _threads:
            _th.start()
        for _th in _threads:
            _th.join(timeout=45.0)
        _critical_models_done.set()

    _threading.Thread(target=_critical_model_warmup, daemon=True, name="critical-model-warmup").start()

    async def _browser_warmup() -> None:
        """Playwright's async objects are bound to the event loop that
        created them — this MUST run as a task on the main loop (not a
        background thread with its own loop), otherwise agent tasks running
        later on this same loop would be unable to reuse the pre-warmed
        page at all."""
        try:
            from .agents.browser_agent.browser_workspace import browser_workspace as _bw
            await _bw.get_or_create_page()
            _rs.mark_service("browser", True)
            logger.info("[CRITICAL_BOOT_DONE] service=browser")
        except Exception as exc:
            _rs.mark_service("browser", False)
            logger.warning("[CRITICAL_BOOT_FAIL] service=browser error=%s", exc)

    def _conversation_check() -> None:
        try:
            from .agents.personality.personality_engine import personality_engine  # noqa: F401
            from .agents.agent_runtime import agent_runtime  # noqa: F401
            from .agents.browser_agent.conversation_layer import narrate  # noqa: F401
            _rs.mark_service("conversation", True)
        except Exception as exc:
            _rs.mark_service("conversation", False)
            logger.warning("[CRITICAL_BOOT_FAIL] service=conversation error=%s", exc)

    async def _critical_boot_supervisor() -> None:
        import time as _t
        _t0 = _t.monotonic()
        _conversation_check()
        # Browser/CDP warmup is NOT awaited here — a cold or hung Chrome
        # launch has no bound of its own at this level (get_or_create_page's
        # internal retry chain can run well past 45s), and voice/text
        # sessions do not need the browser to be ready. It runs fully
        # independently and reports into readiness_service via mark_service
        # when (if) it finishes; CORE_READY no longer waits on it.
        asyncio.create_task(_browser_warmup())
        # Bounded wait — Whisper/Kokoro/intent classifier only. 45s covers
        # a cold model load with margin; anything still running past that
        # is reported as a blocker but the gate opens anyway.
        await asyncio.to_thread(_critical_models_done.wait, 45.0)
        _rs.set_core_ready()
        logger.info("[CRITICAL_BOOT_TOTAL_MS] %.0f", (_t.monotonic() - _t0) * 1000)

        # One bounded, synchronous "Sure." pre-cache — Kokoro is guaranteed
        # warm by this point, closing the race that used to cost ~2.1s on
        # the single first-ever request.
        try:
            from .services.tts_cache_service import tts_cache as _tcc_sure
            _tcc_sure.synthesize_or_cached("Sure.", "nova", 1.0)
        except Exception:
            pass

        _threading.Thread(target=_secondary_boot, daemon=True, name="secondary-boot").start()

    def _secondary_boot() -> None:
        """Everything NOT required for the first command — Ollama,
        wake-word, full TTS ack-phrase cache. Starts only after the
        critical gate opens, so it never competes with Whisper/Kokoro/
        browser warm-up for CPU/GPU during the boot window that matters."""
        _l = logging.getLogger("startup.secondary_boot")

        try:
            import os as _os_w, time as _owt
            _voice_model = _os_w.getenv("OLLAMA_VOICE_MODEL", "qwen2.5:1.5b")
            _os_w.environ.setdefault("OLLAMA_MODEL", _voice_model)
            try:
                import ollama as _olc
                _olc_client = _olc.Client(host=_os_w.getenv("OLLAMA_API_URL", "http://localhost:11434"))
                _olc_models = [m.model for m in _olc_client.list().models]
                _model_ready = any(_voice_model in m for m in _olc_models)
            except Exception:
                _model_ready = False
            if _model_ready:
                _wt0 = _owt.monotonic()
                from .services.openai_client import offline_generate as _og
                _og("hi", system="You are Xyron.")
                _wms = (_owt.monotonic() - _wt0) * 1000
                _l.info("[Warmup] Ollama preloaded model=%s keep_alive=30m warmup_ms=%.0f", _voice_model, _wms)
                _rs.mark_service("ollama", True)
            else:
                _l.warning("[Warmup] Ollama model %r not found — run: ollama pull %s", _voice_model, _voice_model)
                _rs.mark_service("ollama", False)
        except Exception as exc:
            _l.warning("[Warmup] Ollama: %s", exc)
            _rs.mark_service("ollama", False)

        try:
            from voice.wake_word_service import wake_word_service as _wws
            import time as _wt; _wt.sleep(1)
            _l.info("[Warmup] WakeWordService ready — oww=%s", _wws._oww_ready)
            _rs.mark_service("wakeword", _wws._oww_ready)
        except Exception as exc:
            _l.warning("[Warmup] WakeWordService: %s", exc)
            _rs.mark_service("wakeword", False)

        try:
            from .services.tts_cache_service import tts_cache as _tcc
            _ACK_PHRASES = [
                "On it.", "Opening.", "Done.", "Got it.", "Sure.",
                "Opening Chrome.", "Opening VS Code.", "Opening Notepad.",
                "Opening Calculator.", "Opening Explorer.",
                "Opening File Explorer.", "Opening Spotify.", "Opening Discord.",
                "Opening Microsoft Edge.", "Opening Firefox.", "Opening Terminal.",
                "Opening Task Manager.", "Opening Microsoft Store.",
                "Opening E Drive.", "Opening C Drive.", "Opening D Drive.",
                "Opening F Drive.",
            ]
            _synth_ok = 0
            for _phrase in _ACK_PHRASES:
                try:
                    _wav = _tcc.synthesize_or_cached(_phrase, "nova", 1.0)
                    if _wav:
                        _synth_ok += 1
                except Exception:
                    pass
            _l.info("[Warmup] TTS ACK cache pre-built: %d/%d phrases ready",
                    _synth_ok, len(_ACK_PHRASES))
        except Exception as exc:
            _l.warning("[Warmup] TTS ACK cache: %s", exc)

        # Phase 4.15: the travel Conversation Layer's static phrase pools
        # (ack/delay/opening-browser/cancelled/comparison-started) were never
        # pre-cached, so every one paid a full, uncached Kokoro synthesis
        # pass on first use — live-measured as the reason narration audio
        # started *after* the (now pre-warmed, near-instant) browser action
        # it was describing had already completed. Same fix as the ACK
        # cache above, applied to the conversation-layer's fixed lines.
        try:
            from .agents.browser_agent.conversation_layer import (
                _ACK_PHRASES as _CL_ACK, _DELAY_PHRASES as _CL_DELAY,
                _OPENING_BROWSER_PHRASES as _CL_OPENING,
            )
            _CL_STATIC_PHRASES = [
                *_CL_ACK, *_CL_DELAY, *_CL_OPENING,
                "Done — I've cancelled that.", "Okay, cancelled.", "No problem, I've stopped there.",
                "Let me see which one gives you the best balance between price and travel time.",
                "Let me weigh these against each other for you.",
                "I'm looking at price, duration, and stops together — not just the cheapest number.",
                "Still the same set of options here.", "Same options as before.",
            ]
            _cl_synth_ok = 0
            for _phrase in _CL_STATIC_PHRASES:
                try:
                    _wav = _tcc.synthesize_or_cached(_phrase, "nova", 1.0)
                    if _wav:
                        _cl_synth_ok += 1
                except Exception:
                    pass
            _l.info("[Warmup] Conversation-layer phrase cache pre-built: %d/%d phrases ready",
                    _cl_synth_ok, len(_CL_STATIC_PHRASES))
        except Exception as exc:
            _l.warning("[Warmup] Conversation-layer phrase cache: %s", exc)

        _rs.set_ready()
        _sched.on_ready()

    asyncio.create_task(_critical_boot_supervisor())

    # Optional: pre-warm XTTS-v2 in background (only if MULTILINGUAL_TTS_PRELOAD=true).
    # Disabled by default — XTTS-v2 needs ~2GB VRAM; enable only on machines with headroom.
    # With XTTS not preloaded, multilingual responses are spoken by Kokoro (localized text).
    try:
        from voice.xtts_service import preload_background as _xtts_preload
        _xtts_preload()
    except Exception:
        pass

    # Start background services
    try:
        from .config import settings as _s
        from .services.screen_context_service import screen_context_service
        from .services.proactive_service import proactive_service
        from .tools import browser_tools  # noqa: F401 — registers browser tools
        from .services.background_scheduler import scheduler as _sched1, JobPriority as _JP1
        _sched1.register("proactive", _JP1.BACKGROUND_IDLE_ONLY)
        _sched1.register("screen_context", _JP1.BACKGROUND_IDLE_ONLY)
        if _s.openai_api_key and _s.openai_api_key.startswith("sk-"):
            screen_context_service.start(_s.openai_api_key)
            proactive_service.start(_s.openai_api_key)
    except Exception as _exc:
        logger.warning("Background service startup failed: %s", _exc)

    # Phase 9 — start Dev Observer background loop
    try:
        _backend_path2 = str(settings.repo_root / "backend")
        if _backend_path2 not in sys.path:
            sys.path.insert(0, _backend_path2)
        from dev.dev_observer import observer_loop
        from .services.background_scheduler import scheduler as _sched2, JobPriority as _JP2
        _sched2.register("dev_observer", _JP2.BACKGROUND_IDLE_ONLY)
        asyncio.create_task(observer_loop())
        logger.info("[DEV_OBSERVER] background task started")
    except Exception as _exc2:
        logger.warning("[DEV_OBSERVER] startup skipped: %s", _exc2)

    # Phase 12 — start Self-Reflection Engine background loop
    try:
        from cognition.reflection import reflection_engine
        from .services.background_scheduler import scheduler as _sched3, JobPriority as _JP3
        _sched3.register("reflection", _JP3.BACKGROUND_IDLE_ONLY)
        asyncio.create_task(reflection_engine.start_loop(interval_minutes=30))
        logger.info("[REFLECTION] background loop started (30-min interval)")
    except Exception as _exc3:
        logger.warning("[REFLECTION] startup skipped: %s", _exc3)

    # Core tools — pre-warm app index and drive cache in background
    try:
        from .tools.core.app_finder import build_app_index
        from .tools.core.drives import get_drives as _get_drives
        asyncio.create_task(build_app_index())
        asyncio.create_task(_get_drives())
        logger.info("[CORE_TOOLS] app_finder index and drive cache warming started")
    except Exception as _exc4:
        logger.warning("[CORE_TOOLS] warmup skipped: %s", _exc4)

    # System monitor — start background sampler
    try:
        from .services.system_monitor_service import system_monitor as _sysmon
        _sysmon.start(asyncio.get_event_loop())
    except Exception as _exc5:
        logger.warning("[SystemMonitor] startup skipped: %s", _exc5)

    # Filesystem index — trigger singleton import so background scan starts immediately
    try:
        from .services.fs_index import fs_index as _fsidx  # noqa: F401 — side-effect: starts bg scan
        from .services.background_scheduler import scheduler as _sched4, JobPriority as _JP4
        _sched4.register("fs_index", _JP4.BACKGROUND_IDLE_ONLY)
        logger.info("[FS_INDEX] background scan started (ready=%s)", _fsidx.is_ready)
    except Exception as _exc6:
        logger.warning("[FS_INDEX] startup skipped: %s", _exc6)

    # Note: the "Sure." TTS pre-cache, readiness gate (set_core_ready), STT
    # warm-up (Whisper fast + accurate), and conversation-engine checks
    # (personality engine + agent runtime) all now live in
    # `_critical_boot_supervisor`/`_critical_model_warmup` above — see the
    # Phase 4.14 block at the top of this function. They used to run here,
    # unordered relative to the readiness gate, which is exactly what let
    # sessions connect before models were actually warm.


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
app.include_router(environment.router)
app.include_router(voice_identity.router)
app.include_router(cognition.router)
app.include_router(dev.router)
app.include_router(brain.router)
app.include_router(takeover.router)
app.include_router(dashboard.router)
app.include_router(auth_router.router, prefix="/api/v1")
app.include_router(monitor_router.router, prefix="/api/v1")
app.include_router(agents_router.router)
app.include_router(browser_router.router)
