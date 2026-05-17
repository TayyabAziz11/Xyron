"""
Intro context builder — gathers live system state for self-introduction responses.

Does NOT import voice.py (circular import risk). Checks model file existence via
pathlib to report voice stack status safely.

Used by self_intro_engine.py to fill template variables at generation time.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Xyron project root — used to locate models and config ────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class IntroContext:
    operator_name:    str
    mood_state:       str
    tool_count:       int
    active_agents:    list[str]
    enabled_modules:  list[str]
    voice_status:     str        # "Kokoro TTS (local)" | "edge-tts" | "pyttsx3"
    memory_status:    str        # "session-only" | "JSON facts" | "vector store"
    local_ai_models:  list[str]  # e.g. ["Kokoro", "Whisper (faster-whisper)", "SentenceTransformer"]
    mcp_servers:      list[str]
    capability_summary: str      # one-liner from capability_registry


def build() -> IntroContext:
    """Gather live system state. Always returns a complete IntroContext — never raises."""

    operator_name   = _get_operator_name()
    mood_state      = _get_mood()
    tool_count      = _get_tool_count()
    active_agents   = _get_active_agents()
    enabled_modules = _get_enabled_modules()
    voice_status    = _get_voice_status()
    memory_status   = _get_memory_status()
    local_ai_models = _get_local_ai_models()
    mcp_servers     = _get_mcp_servers()
    cap_summary     = _get_capability_summary()

    ctx = IntroContext(
        operator_name=operator_name,
        mood_state=mood_state,
        tool_count=tool_count,
        active_agents=active_agents,
        enabled_modules=enabled_modules,
        voice_status=voice_status,
        memory_status=memory_status,
        local_ai_models=local_ai_models,
        mcp_servers=mcp_servers,
        capability_summary=cap_summary,
    )
    logger.info(
        "[INTRO_CONTEXT] operator=%r mood=%s tools=%d agents=%d modules=%d models=%d",
        operator_name, mood_state, tool_count, len(active_agents),
        len(enabled_modules), len(local_ai_models),
    )
    return ctx


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_operator_name() -> str:
    try:
        from api.services.memory_service import memory_service
        facts = memory_service.get_long_term_facts()
        name = facts.get("name") or facts.get("user_name") or ""
        return str(name).strip() or "Tayyab"
    except Exception:
        return "Tayyab"


def _get_mood() -> str:
    try:
        from cognition.mood_state_machine import mood_machine
        return mood_machine.state.value
    except Exception:
        return "NEUTRAL"


def _get_tool_count() -> int:
    try:
        from api.tools.registry import tool_registry
        return len(tool_registry.get_definitions())
    except Exception:
        return 10


def _get_active_agents() -> list[str]:
    try:
        from src.ai_operator.agents.registry import agent_registry
        return list(agent_registry.list_agents())[:6]
    except Exception:
        return ["operator", "memory", "tools", "voice", "cognition"]


def _get_enabled_modules() -> list[str]:
    modules: list[str] = []
    # Voice: check Kokoro model file
    if _get_voice_status().startswith("Kokoro"):
        modules.append("Kokoro TTS")
    # Whisper
    try:
        from voice.whisper_service import whisper_service
        if whisper_service.is_loaded():
            modules.append("Whisper STT")
    except Exception:
        pass
    # Emotion pipeline
    try:
        from cognition.emotion_engine import emotion_engine  # noqa: F401
        modules.append("Emotion Engine")
    except Exception:
        pass
    # Mood machine
    try:
        from cognition.mood_state_machine import mood_machine  # noqa: F401
        modules.append("Mood State Machine")
    except Exception:
        pass
    # Memory
    try:
        from api.services.memory_service import memory_service  # noqa: F401
        modules.append("Memory Service")
    except Exception:
        pass
    # Proactive
    try:
        from api.services.proactive_service import proactive_service  # noqa: F401
        modules.append("Proactive Intelligence")
    except Exception:
        pass
    return modules or ["Voice", "Memory", "Emotion Engine", "Tool Automation"]


def _get_voice_status() -> str:
    # Check for Kokoro model files without importing voice.py
    kokoro_paths = [
        _REPO_ROOT / "voice" / "kokoro" / "kokoro-v1_0.onnx",
        _REPO_ROOT / "voice" / "kokoro-v1_0.onnx",
        Path(os.getenv("KOKORO_MODEL_PATH", "")) if os.getenv("KOKORO_MODEL_PATH") else Path("/nonexistent"),
    ]
    for p in kokoro_paths:
        if p.exists():
            return "Kokoro TTS (local)"
    return "edge-tts (online fallback)"


def _get_memory_status() -> str:
    try:
        from api.services.episodic_memory import episodic_memory
        _ = episodic_memory  # noqa: F841
        return "episodic SQLite + JSON facts"
    except Exception:
        return "session-only"


def _get_local_ai_models() -> list[str]:
    models: list[str] = []
    if _get_voice_status().startswith("Kokoro"):
        models.append("Kokoro TTS")
    # Whisper
    whisper_paths = [
        _REPO_ROOT / "voice" / "whisper" / "model.bin",
        Path(os.getenv("WHISPER_MODEL_PATH", "")) if os.getenv("WHISPER_MODEL_PATH") else Path("/nonexistent"),
    ]
    for p in whisper_paths:
        if p.exists():
            models.append("Whisper (faster-whisper)")
            break
    else:
        # Check if whisper service is loaded in memory
        try:
            from voice.whisper_service import whisper_service
            if whisper_service.is_loaded():
                models.append("Whisper (faster-whisper)")
        except Exception:
            pass
    # SentenceTransformer
    try:
        from api.services.intent_router import intent_router
        if intent_router.is_loaded():
            models.append("SentenceTransformer (intent)")
    except Exception:
        pass
    # Wake word
    wake_paths = [
        _REPO_ROOT / "voice" / "wake_word" / "model.onnx",
        Path(os.getenv("WAKE_MODEL_PATH", "")) if os.getenv("WAKE_MODEL_PATH") else Path("/nonexistent"),
    ]
    for p in wake_paths:
        if p.exists():
            models.append("ONNX Wake Detector")
            break
    return models or ["Kokoro TTS", "Whisper (faster-whisper)"]


def _get_mcp_servers() -> list[str]:
    servers: list[str] = []
    mcp_dir = _REPO_ROOT / "mcp_servers"
    if mcp_dir.exists():
        for entry in mcp_dir.iterdir():
            if entry.is_dir() and (entry / "server.py").exists():
                servers.append(entry.name.replace("_mcp", "").replace("_", " ").title())
    return servers or ["WhatsApp", "Odoo"]


def _get_capability_summary() -> str:
    try:
        from cognition.capability_registry import capability_registry
        strong = capability_registry.get_strong()
        names  = [n.replace("_", " ").title() for n, _ in strong[:3]]
        return ", ".join(names) if names else "Tool Automation, Emotional Detection"
    except Exception:
        return "Tool Automation, Emotional Detection"
