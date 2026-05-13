"""
Voice Command Router — Phase 9 upgrade: context-aware routing in code mode.

In code_mode, resolves pronouns and short phrases against active workspace context:
  "explain this"       → uses active file
  "what went wrong"    → uses latest terminal error
  "fix it" / "fix the error" → uses terminal error + active file
  "continue"           → uses last DevAgent response (session memory)
  "summarize this project"   → uses project memory

System commands (volume, music, browser, etc.) are NEVER hijacked by code_mode.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = os.getenv("XYRON_API_BASE", "http://localhost:8000")

# Keywords intercepted only when code_mode is active
_CODE_KEYWORDS = [
    "explain", "what does", "how does", "debug", "fix this", "fix it",
    "refactor", "write", "generate", "implement", "test", "optimize",
    "architect", "why is", "code", "function", "class", "method", "module",
    "import", "error", "exception", "traceback", "bug", "unit test",
    # Phase 9 contextual triggers
    "explain this", "what went wrong", "fix the error", "continue",
    "summarize this project", "same pattern", "use the same",
    "from yesterday", "what were we working on",
]

# Commands that must NEVER be routed to dev even if code_mode is active
_SYSTEM_KEYWORDS = [
    "volume", "mute", "music", "play", "pause", "stop", "open app", "close",
    "browser", "chrome", "firefox", "screenshot", "wifi", "battery",
    "shutdown", "restart", "sleep", "lock", "brightness",
    "whatsapp", "email", "calendar", "reminder", "alarm",
]

# Contextual phrase expansions — resolved before sending to DevAgent
_CONTEXTUAL_MAP = {
    "explain this":             lambda f, _p, _t: f"Explain this file: {f}" if f else "Explain the active file.",
    "what went wrong":          lambda _f, _p, t: f"What went wrong? Latest error: {t}" if t else "What went wrong?",
    "fix it":                   lambda f, _p, t: f"Fix this error: {t}" if t else f"Fix the issues in {f}" if f else "Fix it.",
    "fix the error":            lambda _f, _p, t: f"Fix this error: {t}" if t else "Fix the error.",
    "summarize this project":   lambda _f, p, _t: f"Summarize the project at {p}." if p else "Summarize this project.",
}


def _is_system_command(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _SYSTEM_KEYWORDS)


def _is_code_related(text: str) -> bool:
    if _is_system_command(text):
        return False
    lower = text.lower()
    return any(kw in lower for kw in _CODE_KEYWORDS)


def _resolve_contextual(text: str, active_file: Optional[str],
                        active_project: Optional[str],
                        latest_error_summary: Optional[str]) -> str:
    """Expand short contextual phrases to include workspace context."""
    lower = text.lower().strip()
    for phrase, expander in _CONTEXTUAL_MAP.items():
        if phrase in lower:
            return expander(active_file, active_project, latest_error_summary)
    return text


class VoiceCommandRouter:
    """Sends transcribed text to the command API, routing context-aware in code_mode."""

    def __init__(self, api_base: str = DEFAULT_API_BASE, source: str = "voice"):
        self.api_base = api_base.rstrip("/")
        self.source   = source

    def submit(self, text: str) -> dict:
        """Submit a transcribed command, applying code_mode context routing when active."""
        if not text or not text.strip():
            return {"error": "empty_transcription", "text": text}
        text = text.strip()
        logger.info("[VoiceRouter] routing: %r", text)

        # Code-mode fast path
        try:
            from api.services.cognitive_state import cognitive_state
            if cognitive_state.code_mode and _is_code_related(text):
                active_file    = cognitive_state.active_file
                active_project = cognitive_state.active_project

                # Resolve contextual phrase → enriched prompt
                latest_error: Optional[str] = None
                try:
                    from dev.terminal_intelligence import get_latest_error
                    err = get_latest_error(active_project or "")
                    if err and err.get("detected"):
                        latest_error = err.get("summary", "")
                except Exception:
                    pass

                resolved = _resolve_contextual(text, active_file, active_project, latest_error)
                if resolved != text:
                    logger.info("[VoiceRouter] contextual expansion: %r → %r", text, resolved)

                logger.info("[VoiceRouter] code_mode=true routing to dev_agent: %r", resolved[:80])
                resp = requests.post(
                    f"{self.api_base}/api/v1/dev/query",
                    json={
                        "text":           resolved,
                        "source":         self.source,
                        "active_project": active_project,
                        "active_file":    active_file,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("[VoiceRouter] dev_agent fast path failed, falling back: %s", exc)

        # Standard command API
        try:
            resp = requests.post(
                f"{self.api_base}/api/v1/commands",
                json={"text": text, "source": self.source},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("[VoiceRouter] command submit failed: %s", exc)
            return {"error": str(exc), "text": text}

    def get_status(self, command_id: str) -> dict:
        """Poll command status."""
        try:
            resp = requests.get(
                f"{self.api_base}/api/v1/commands/{command_id}",
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            return {"error": str(exc)}
