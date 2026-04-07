"""
Base integration adapter — defines the interface all adapters must satisfy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import json


class IntegrationError(Exception):
    """Raised when an integration call fails."""


class BaseIntegration(ABC):
    """Abstract base for all integration adapters."""

    #: Integration identifier, e.g. "gmail", "linkedin"
    integration_id: str = "base"

    def _load_secret(self, filename: str) -> dict:
        """Load a JSON credentials file from .secrets/."""
        secrets_dir = self._find_secrets_dir()
        if not secrets_dir:
            raise IntegrationError(
                f"Credentials directory .secrets/ not found. "
                f"Cannot load {filename}."
            )
        path = secrets_dir / filename
        if not path.exists():
            raise IntegrationError(
                f"Credential file not found: .secrets/{filename}. "
                f"Run the setup script for {self.integration_id}."
            )
        with open(path) as f:
            return json.load(f)

    def _find_secrets_dir(self) -> Path | None:
        current = Path(__file__).parent
        while current != current.parent:
            candidate = current / ".secrets"
            if candidate.exists():
                return candidate
            current = current.parent
        return None

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials are present and the adapter can be used."""
