"""
SQLite memory bridge — wires episodic_memory.save() into the voice command lifecycle.
Contribution: feat/natural-voice-layer

Does NOT modify episodic_memory.py. Wraps it with helpers called from
response_generator.py so that every voice turn is persisted to SQLite.
"""
from __future__ import annotations

from typing import Optional

from .episodic_memory import episodic_memory
from .memory_service import memory_service


def record_turn(
    session_id: str,
    user_text: str,
    assistant_text: str,
    tool_name: Optional[str] = None,
    success: bool = True,
) -> None:
    """Save one full conversation turn (user + assistant) to SQLite."""
    episodic_memory.save(
        session_id=session_id,
        role="user",
        text=user_text,
        tool_name=None,
        success=None,
    )
    episodic_memory.save(
        session_id=session_id,
        role="assistant",
        text=assistant_text,
        tool_name=tool_name,
        success=success,
    )


def get_recent_context(session_id: str, n: int = 5) -> list[dict]:
    """Get last n turns as OpenAI-ready {role, content} dicts."""
    return episodic_memory.conversation_context(session_id, n=n)


def get_full_context_for_prompt(session_id: str) -> str:
    """Combined memory facts + recent history as a string for system prompt injection."""
    facts = memory_service.get_context_string()
    recent = episodic_memory.recent(n=3)
    # recent() returns list[Turn] dataclass objects — use attributes, not .get()
    history_lines = [f"{t.role}: {t.text}" for t in recent]
    history_str = "\n".join(history_lines) if history_lines else "No recent history."
    parts = []
    if facts:
        parts.append(facts)
    parts.append(f"Recent conversation:\n{history_str}")
    return "\n\n".join(parts)
