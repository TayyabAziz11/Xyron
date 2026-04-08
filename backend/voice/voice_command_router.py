"""Routes transcribed voice commands to the AI Operator command API."""
from __future__ import annotations

import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "http://localhost:8000"


class VoiceCommandRouter:
    """Sends transcribed text to the command API and returns the result."""

    def __init__(self, api_base: str = DEFAULT_API_BASE, source: str = "voice"):
        self.api_base = api_base.rstrip("/")
        self.source = source

    def submit(self, text: str) -> dict:
        """Submit transcribed command text to POST /api/v1/commands.

        Returns the command response dict with id, status, intent.
        """
        if not text or not text.strip():
            return {"error": "empty_transcription", "text": text}

        text = text.strip()
        logger.info("Routing voice command: %r", text)

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
