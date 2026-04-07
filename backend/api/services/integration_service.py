"""
Integration Service — checks .secrets/ and MCP server directories
to determine which integrations are connected.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..config import settings
from ..schemas.integration import Integration, IntegrationStatus


# Registry of all known integrations with their credential file markers
_INTEGRATION_REGISTRY: List[Dict] = [
    {
        "id": "gmail",
        "name": "Gmail",
        "description": "Send, receive, and manage email via Gmail API",
        "icon": "mail",
        "credential_files": ["gmail_token.json", "gmail_credentials.json"],
        "mcp_server": None,
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "description": "Post and engage on LinkedIn via OAuth API",
        "icon": "linkedin",
        "credential_files": ["linkedin_credentials.json", "linkedin_token.json"],
        "mcp_server": None,
    },
    {
        "id": "whatsapp",
        "name": "WhatsApp",
        "description": "Read and send WhatsApp messages via MCP server",
        "icon": "message-circle",
        "credential_files": ["whatsapp_credentials.json"],
        "mcp_server": "whatsapp_mcp",
    },
    {
        "id": "odoo",
        "name": "Odoo",
        "description": "ERP integration — invoices, customers, accounting",
        "icon": "database",
        "credential_files": ["odoo_credentials.json"],
        "mcp_server": "odoo_mcp",
    },
    {
        "id": "instagram",
        "name": "Instagram",
        "description": "Post and monitor Instagram via Graph API",
        "icon": "instagram",
        "credential_files": ["instagram_credentials.json", "instagram_token.json"],
        "mcp_server": None,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "GPT-4o and DALL-E 3 for content generation",
        "icon": "zap",
        "credential_files": ["ai_credentials.json"],
        "mcp_server": None,
    },
]


def _check_status(registry_entry: Dict) -> IntegrationStatus:
    """Determine integration status by checking credential files and MCP server."""
    secrets_dir = settings.secrets_dir

    # Check any credential file exists
    cred_files = registry_entry.get("credential_files", [])
    cred_found = any((secrets_dir / f).exists() for f in cred_files)

    # Check MCP server directory exists
    mcp_server = registry_entry.get("mcp_server")
    if mcp_server:
        mcp_exists = (settings.mcp_servers_dir / mcp_server).exists()
    else:
        mcp_exists = True  # N/A — not MCP backed

    if cred_found and mcp_exists:
        return IntegrationStatus.connected
    elif cred_found and not mcp_exists:
        return IntegrationStatus.error
    else:
        return IntegrationStatus.not_configured


class IntegrationService:
    """Checks filesystem for credential files to determine integration status."""

    def list_integrations(self) -> List[Integration]:
        integrations = []
        for entry in _INTEGRATION_REGISTRY:
            status = _check_status(entry)
            integrations.append(
                Integration(
                    id=entry["id"],
                    name=entry["name"],
                    description=entry["description"],
                    status=status,
                    icon=entry.get("icon"),
                    credential_file=next(
                        (f for f in entry.get("credential_files", [])
                         if (settings.secrets_dir / f).exists()),
                        None,
                    ),
                    mcp_server=entry.get("mcp_server"),
                )
            )
        return integrations

    def get(self, integration_id: str) -> Optional[Integration]:
        for integration in self.list_integrations():
            if integration.id == integration_id:
                return integration
        return None


# Module-level singleton
integration_service = IntegrationService()
