"""
Memory Agent — answers "what did I tell you about X?" queries.

Searches both semantic ChromaDB and long-term fact store.
Handles commands like:
  "what do you know about my project?"
  "recall what I said about the API"
  "what did I tell you about Qasim?"
  "show my memories"
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_MEMORY_KW = [
    "what did i", "what did you", "what do you know",
    "recall", "remember when", "told you about",
    "what have i", "show my memories", "my memories",
    "what did i say", "do you remember", "you know about",
]


class MemoryAgent(BaseAgent):
    """Answers memory recall queries from ChromaDB and fact store."""

    name = "memory_agent"
    keywords = _MEMORY_KW

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        lower = command.lower()

        if any(w in lower for w in ("show", "list", "all memories", "everything")):
            return self._dump_facts(command)
        return self._semantic_recall(command)

    def _semantic_recall(self, command: str) -> AgentResult:
        """Query ChromaDB for semantically similar stored memories."""
        results: list[dict] = []
        try:
            from cognition.memory.memory_bridge import memory_bridge
            results = memory_bridge.recall(command, n=5)
        except Exception as exc:
            logger.debug("[MemoryAgent] semantic recall error: %s", exc)

        # Also include matching long-term facts
        facts_text = ""
        try:
            from api.services.memory_service import memory_service
            facts = memory_service.get_facts()
            if facts:
                facts_text = "; ".join(f"{k}: {v}" for k, v in list(facts.items())[:5])
        except Exception:
            pass

        if not results and not facts_text:
            return self._result(
                success=True,
                output="I don't have any stored memories matching that query.",
                command=command,
                metadata={"hits": 0},
            )

        parts: list[str] = []
        for r in results[:3]:
            parts.append(r.get("text", ""))
        if facts_text:
            parts.append(f"Known facts: {facts_text}")

        output = " | ".join(p for p in parts if p)
        return self._result(
            success=True,
            output=output[:400],
            command=command,
            metadata={"semantic_hits": len(results), "has_facts": bool(facts_text)},
        )

    def _dump_facts(self, command: str) -> AgentResult:
        """Return all stored long-term facts."""
        try:
            from api.services.memory_service import memory_service
            spoken = memory_service.get_memories_spoken()
            return self._result(success=True, output=spoken, command=command,
                                metadata={"action": "dump_facts"})
        except Exception as exc:
            return self._result(success=False, output="Could not retrieve memories.",
                                command=command, error=str(exc))
