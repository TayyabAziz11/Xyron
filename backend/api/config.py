"""
API Configuration — pydantic-settings based config for AI Operator.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (contains README.md + backend/)."""
    current = Path(__file__).parent
    while current != current.parent:
        if (current / "README.md").exists() and (current / "backend").exists():
            return current
        current = current.parent
    # Fallback: go up 3 levels from backend/api/config.py → backend/ → repo root
    return Path(__file__).parent.parent.parent


# Absolute path to backend/.env — works regardless of launch directory
_BACKEND_ENV = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ENV),   # absolute path so CWD doesn't matter
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,        # empty env vars don't override .env values
    )

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    debug: bool = True

    # CORS — Next.js web dashboard + Electron desktop app dev server
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5173",  # electron-vite dev renderer
        "http://localhost:4173",  # electron-vite preview
        "http://localhost:5174",  # fallback if 5173 is taken
    ]

    # Paths (auto-detected, can be overridden via env)
    repo_root: Path = _find_repo_root()

    # OpenAI
    openai_api_key: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: object) -> List[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v  # type: ignore[return-value]

    # Computed path helpers (not env vars)
    @property
    def logs_dir(self) -> Path:
        return self.repo_root / "Logs"

    @property
    def pending_approval_dir(self) -> Path:
        return self.repo_root / "backend" / "src" / "ai_operator" / "skills" / "gold" / "Pending_Approval"

    @property
    def approved_dir(self) -> Path:
        return self.repo_root / "Approved"

    @property
    def rejected_dir(self) -> Path:
        return self.repo_root / "Rejected"

    @property
    def secrets_dir(self) -> Path:
        return self.repo_root / ".secrets"

    @property
    def mcp_servers_dir(self) -> Path:
        return self.repo_root / "backend" / "mcp_servers"


settings = Settings()
