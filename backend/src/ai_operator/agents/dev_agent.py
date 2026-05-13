"""
Dev Agent — handles all code-related interactions for Xyron Code Assistant Mode.

Supported intents: explain, write, test, debug, refactor, architect, optimize
Uses phi3:mini for classification and mistral:7b for reasoning responses.
All LLM calls go through local Ollama — no cloud dependencies.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Generator

import requests

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_OLLAMA_BASE = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
_CLASSIFY_MODEL = "phi3:mini"
_REASON_MODEL = "mistral:7b"

_DEV_KEYWORDS = [
    # explain
    "explain", "what does", "how does", "what is", "describe", "walk me through",
    # write
    "write", "create", "generate", "implement", "add function", "add method",
    "make a", "build a", "code for",
    # test
    "test", "unit test", "write tests", "add tests", "pytest", "jest",
    # debug
    "debug", "fix", "error", "bug", "broken", "not working", "issue", "exception",
    "traceback", "why is",
    # refactor
    "refactor", "clean up", "improve", "optimize", "simplify", "restructure",
    # architect
    "architect", "architecture", "design", "how should i structure", "best way to structure",
    "system design", "design this", "design a", "how to design",
    # optimize
    "performance", "faster", "speed up", "bottleneck", "latency", "memory usage",
]

_INTENT_MAP = {
    "explain":   ["explain", "what does", "how does", "what is", "describe", "walk me through"],
    "write":     ["write", "create", "generate", "implement", "add function", "add method", "make a", "build a", "code for"],
    "test":      ["test", "unit test", "write tests", "add tests", "pytest", "jest"],
    "debug":     ["debug", "fix", "error", "bug", "broken", "not working", "issue", "exception", "traceback", "why is"],
    "refactor":  ["refactor", "clean up", "restructure", "simplify"],
    "architect": ["architect", "architecture", "design", "how should i structure", "best way to structure", "system design"],
    "optimize":  ["optimize", "improve", "performance", "faster", "speed up", "bottleneck", "latency", "memory usage"],
}

_SYSTEM_PROMPT = """You are Xyron, an embedded AI developer. You reason like a senior engineer.
Rules:
- Be concise and direct. No fluff, no motivation.
- Explain your reasoning briefly before code.
- Use production-quality patterns.
- Prefer stdlib/existing deps over new ones.
- Format code in markdown code blocks with language tags.
- Max response: ~400 tokens unless the task demands more."""


class DevAgent(BaseAgent):
    """Agent for all code-related interactions in Xyron Code Assistant Mode."""

    name = "dev_agent"
    keywords = _DEV_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        intent = self._classify_intent(command)
        system = self._build_system_prompt(intent, kwargs)
        response = self._ollama_generate(system, command)
        return self._result(
            True,
            response,
            command,
            metadata={"intent": intent, "model": _REASON_MODEL, "source": "local"},
        )

    def stream(self, command: str, **kwargs: Any) -> Generator[str, None, None]:
        """Yield response tokens progressively for streaming endpoints."""
        intent = self._classify_intent(command)
        system = self._build_system_prompt(intent, kwargs)
        yield from self._ollama_stream(system, command)

    # ── Private ───────────────────────────────────────────────────────────────

    def _classify_intent(self, command: str) -> str:
        lower = command.lower()
        for intent, keywords in _INTENT_MAP.items():
            if any(kw in lower for kw in keywords):
                return intent
        return "explain"

    def _build_system_prompt(self, intent: str, ctx: dict) -> str:
        project = ctx.get("active_project") or "unknown project"
        file_ = ctx.get("active_file") or "unknown file"
        extra = f"\nActive context: project={project}, file={file_}"
        intent_hints = {
            "explain":   "Explain the code or concept clearly. State the key insight first.",
            "write":     "Write clean, production-ready code. Add minimal comments for non-obvious logic only.",
            "test":      "Write thorough tests. Cover happy path and edge cases. Use the project's existing test framework.",
            "debug":     "Diagnose root cause first. Then provide the fix. Don't paper over the real issue.",
            "refactor":  "Improve structure without changing behavior. Call out each change and why.",
            "architect": "Propose a clear architecture. State trade-offs. Be opinionated — don't hedge.",
            "optimize":  "Profile before optimizing. State what the bottleneck is, then show the fix with expected improvement.",
        }
        hint = intent_hints.get(intent, "")
        return f"{_SYSTEM_PROMPT}{extra}\n\nTask type: {intent}. {hint}"

    def _ollama_generate(self, system: str, prompt: str) -> str:
        try:
            resp = requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                json={
                    "model": _REASON_MODEL,
                    "system": system,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 512},
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException as exc:
            logger.error("DevAgent Ollama call failed: %s", exc)
            return f"[DevAgent] LLM unavailable: {exc}"

    def _ollama_stream(self, system: str, prompt: str) -> Generator[str, None, None]:
        try:
            with requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                json={
                    "model": _REASON_MODEL,
                    "system": system,
                    "prompt": prompt,
                    "stream": True,
                    "options": {"temperature": 0.2, "num_predict": 512},
                },
                stream=True,
                timeout=60,
            ) as resp:
                resp.raise_for_status()
                import json as _json
                for line in resp.iter_lines():
                    if line:
                        chunk = _json.loads(line)
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
        except Exception as exc:
            logger.error("DevAgent stream failed: %s", exc)
            yield f"[DevAgent] stream error: {exc}"
