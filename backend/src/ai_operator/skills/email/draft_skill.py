"""
Email Draft Skill — compose an email draft using OpenAI.

Public API:
    draft_email(command: str) -> str
        Returns a formatted markdown email draft based on the command.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def draft_email(command: str) -> str:
    """
    Generate an email draft from a natural language command using OpenAI.

    Args:
        command: Natural language instruction, e.g. "Draft a follow-up to John about the meeting"

    Returns:
        Formatted email draft as a markdown string.
    """
    api_key = _get_openai_key()
    if not api_key:
        return _fallback_draft(command)

    system_prompt = (
        "You are an expert professional email writer. "
        "When given an instruction, produce a complete email with Subject, To (placeholder), and Body. "
        "Format: Subject: ...\n\nBody:\n...\n\nSign off appropriately."
    )

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": command},
        ],
        "max_tokens": 600,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    draft = data["choices"][0]["message"]["content"].strip()
    return f"**Email Draft:**\n\n{draft}\n\n---\n*Review carefully before sending.*"


def _get_openai_key() -> str:
    """Resolve OpenAI key from env or .secrets/ai_credentials.json."""
    env_key = os.environ.get("OPENAI_API_KEY", "")
    if env_key:
        return env_key

    repo_root = Path(__file__).parent
    while repo_root != repo_root.parent:
        cred_file = repo_root / ".secrets" / "ai_credentials.json"
        if cred_file.exists():
            try:
                with open(cred_file) as f:
                    return json.load(f).get("openai_api_key", "")
            except Exception:
                pass
        repo_root = repo_root.parent
    return ""


def _fallback_draft(command: str) -> str:
    """Return a template draft when OpenAI is unavailable."""
    return (
        "**Email Draft (template — OpenAI not configured):**\n\n"
        f"Subject: [Re: {command[:50]}]\n\n"
        "Hi [Name],\n\n"
        "[Your message here based on the request above.]\n\n"
        "Best regards,\n[Your Name]\n\n"
        "---\n*Configure OPENAI_API_KEY in .env to enable AI-powered drafting.*"
    )
