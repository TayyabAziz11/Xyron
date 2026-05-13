"""Routes transcribed voice commands to the AI Operator command API."""
from __future__ import annotations

import logging
import os
from typing import Optional
import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = os.getenv("XYRON_API_BASE", "http://localhost:8000")

# Keywords that belong to code domain — only intercepted when code_mode is active
_CODE_KEYWORDS = [
    "explain", "what does", "how does", "debug", "fix this", "refactor",
    "write", "generate", "implement", "test", "optimize", "architect",
    "why is", "code", "function", "class", "method", "module", "import",
    "error", "exception", "traceback", "bug", "unit test",
]

# Keywords that must NEVER be hijacked by code_mode (system commands)
_SYSTEM_KEYWORDS = [
    "volume", "mute", "music", "play", "pause", "stop", "open", "close",
    "browser", "chrome", "firefox", "screenshot", "wifi", "battery",
    "shutdown", "restart", "sleep", "lock",
]


def _is_code_related(text: str) -> bool:
    lower = text.lower()
    if any(kw in lower for kw in _SYSTEM_KEYWORDS):
        return False
    return any(kw in lower for kw in _CODE_KEYWORDS)


class VoiceCommandRouter:
    """Sends transcribed text to the command API and returns the result."""

    def __init__(self, api_base: str = DEFAULT_API_BASE, source: str = "voice"):
        self.api_base = api_base.rstrip("/")
        self.source = source

    def submit(self, text: str) -> dict:
        """Submit transcribed command text, routing to DevAgent first when in code_mode."""
        if not text or not text.strip():
            return {"error": "empty_transcription", "text": text}

        text = text.strip()
        logger.info("Routing voice command: %r", text)

        # Code-mode fast path — send directly to dev endpoint
        try:
            from api.services.cognitive_state import cognitive_state
            if cognitive_state.code_mode and _is_code_related(text):
                logger.info("[VoiceRouter] code_mode active — routing to dev_agent")
                resp = requests.post(
                    f"{self.api_base}/api/v1/dev/query",
                    json={
                        "text": text,
                        "source": self.source,
                        "active_project": cognitive_state.active_project,
                        "active_file": cognitive_state.active_file,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("[VoiceRouter] dev_agent fast path failed, falling back: %s", exc)

        try:
            resp = requests.post(
                f"{self.api_base}/api/v1/commands",
                json={"text": text, "source": self.source},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error("Failed to submit voice command: %s", exc)
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
