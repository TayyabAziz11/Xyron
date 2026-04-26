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


def get_user_facts() -> str:
    """Scan SQLite episodic history for user-stated personal facts.

    Searches for turns where the user mentioned their name, role, or asked
    Xyron to remember something.  Returns a summary string for system-prompt
    injection so user facts persist even across fresh sessions.
    """
    keywords = ["remember", "naam", "name is", "i am", "main hun", "founder", "mera naam"]
    seen: set[str] = set()
    fact_lines: list[str] = []
    for kw in keywords:
        try:
            turns = episodic_memory.search(kw, limit=5)
            for t in turns:
                if t.role == "user" and t.text not in seen:
                    seen.add(t.text)
                    fact_lines.append(f"- User previously said: {t.text[:200]}")
        except Exception:
            pass
    if not fact_lines:
        return ""
    return "Previously stated user facts (from history):\n" + "\n".join(fact_lines[:10])


def get_full_context_for_prompt(session_id: str) -> str:
    """Combined memory facts + SQLite user facts + recent history for system-prompt injection."""
    facts = memory_service.get_context_string()
    user_facts = get_user_facts()
    recent = episodic_memory.recent(n=3)
    # recent() returns list[Turn] dataclass objects — use attributes, not .get()
    history_lines = [f"{t.role}: {t.text}" for t in recent]
    history_str = "\n".join(history_lines) if history_lines else "No recent history."
    parts = []
    if facts:
        parts.append(facts)
    if user_facts:
        parts.append(user_facts)
    parts.append(f"Recent conversation:\n{history_str}")
    return "\n\n".join(parts)
