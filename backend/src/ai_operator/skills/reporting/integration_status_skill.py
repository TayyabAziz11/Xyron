"""
Integration Status Skill — check which integrations are connected.

Public API:
    get_integration_status() -> str
        Returns a formatted markdown report of integration connectivity.
"""
from __future__ import annotations

from pathlib import Path


_INTEGRATIONS = [
    {
        "name": "Gmail",
        "files": ["gmail_token.json", "gmail_credentials.json"],
        "mcp": None,
    },
    {
        "name": "LinkedIn",
        "files": ["linkedin_credentials.json", "linkedin_token.json"],
        "mcp": None,
    },
    {
        "name": "WhatsApp",
        "files": ["whatsapp_credentials.json"],
        "mcp": "whatsapp_mcp",
    },
    {
        "name": "Odoo",
        "files": ["odoo_credentials.json"],
        "mcp": "odoo_mcp",
    },
    {
        "name": "Instagram",
        "files": ["instagram_credentials.json", "instagram_token.json"],
        "mcp": None,
    },
    {
        "name": "OpenAI",
        "files": ["ai_credentials.json"],
        "mcp": None,
    },
]


def get_integration_status() -> str:
    """
    Check each integration's credential files and return a status report.
    """
    secrets_dir = _find_secrets_dir()
    mcp_dir = _find_mcp_dir()

    lines = ["**Integration Status Report**\n"]
    connected = []
    disconnected = []

    for integration in _INTEGRATIONS:
        cred_ok = secrets_dir is not None and any(
            (secrets_dir / f).exists() for f in integration["files"]
        )
        mcp_name = integration.get("mcp")
        mcp_ok = (mcp_dir is None or mcp_name is None) or (mcp_dir / mcp_name).exists()

        if cred_ok and mcp_ok:
            connected.append(integration["name"])
            lines.append(f"  ✓ **{integration['name']}** — Connected")
        else:
            disconnected.append(integration["name"])
            reason = "credentials missing" if not cred_ok else "MCP server missing"
            lines.append(f"  ✗ **{integration['name']}** — Not configured ({reason})")

    lines.append(
        f"\n**{len(connected)}/{len(_INTEGRATIONS)} integrations online.** "
        f"See the Integrations panel for setup instructions."
    )
    return "\n".join(lines)


def _find_secrets_dir() -> Path | None:
    current = Path(__file__).parent
    while current != current.parent:
        candidate = current / ".secrets"
        if candidate.exists():
            return candidate
        current = current.parent
    return None


def _find_mcp_dir() -> Path | None:
    current = Path(__file__).parent
    while current != current.parent:
        candidate = current / "backend" / "mcp_servers"
        if candidate.exists():
            return candidate
        current = current.parent
    return None
