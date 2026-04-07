"""
LinkedIn Draft Skill — compose a LinkedIn post using OpenAI.

Public API:
    draft_linkedin_post(command: str, tone: str = "Professional") -> str
        Returns a formatted LinkedIn post draft.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def draft_linkedin_post(command: str, tone: str = "Professional") -> str:
    """
    Generate a LinkedIn post from a natural language command.

    Args:
        command: Instruction such as "Write a LinkedIn post about our product update"
        tone:    Post tone — Professional | Conversational | Thought-Leadership

    Returns:
        Formatted post draft as a markdown string.
    """
    api_key = _get_openai_key()
    if not api_key:
        return _fallback_draft(command)

    system_prompt = (
        "You are an expert LinkedIn content strategist. "
        "Write engaging, authentic posts that perform well on LinkedIn. "
        "Use short paragraphs and line breaks. "
        "Hashtags go at the end only, max 5. "
        "Never use generic filler phrases like 'Excited to share...'."
    )
    user_prompt = (
        f"Write a {tone.lower()} LinkedIn post based on this request:\n{command}\n\n"
        "Return ONLY the post text — no preamble, no explanation."
    )

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 400,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    draft = data["choices"][0]["message"]["content"].strip()
    return (
        f"**LinkedIn Post Draft:**\n\n{draft}\n\n"
        "---\n"
        "*Review before publishing. Use the Approvals panel to approve and schedule.*"
    )


def _get_openai_key() -> str:
    env_key = os.environ.get("OPENAI_API_KEY", "")
    if env_key:
        return env_key
    root = Path(__file__).parent
    while root != root.parent:
        cred = root / ".secrets" / "ai_credentials.json"
        if cred.exists():
            try:
                with open(cred) as f:
                    return json.load(f).get("openai_api_key", "")
            except Exception:
                pass
        root = root.parent
    return ""


def _fallback_draft(command: str) -> str:
    topic = command.replace("draft", "").replace("linkedin post", "").replace("write", "").strip()
    return (
        f"**LinkedIn Post Draft (template — OpenAI not configured):**\n\n"
        f"[Your post about {topic or 'this topic'} here.]\n\n"
        "#AIOperator #Innovation\n\n"
        "---\n*Configure OPENAI_API_KEY in .env to enable AI-powered drafting.*"
    )
