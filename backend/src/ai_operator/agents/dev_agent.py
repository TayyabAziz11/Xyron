"""
Dev Agent — Phase 9 upgrade: full workspace context injection.

Before responding, injects:
  • active file intelligence (language, framework, symbols, issues)
  • project memory summary (stack, recent errors, past fixes)
  • latest terminal error
  • session history hint

[DEV_AGENT_CONTEXT] log prefix for context-enriched calls.
Uses phi3:mini for intent classification, mistral:7b for reasoning.
All LLM calls go through local Ollama — no cloud dependencies.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Generator

import requests

from .base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

_OLLAMA_BASE    = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
_CLASSIFY_MODEL = "phi3:mini"
_REASON_MODEL   = "mistral:7b"

_DEV_KEYWORDS = [
    "explain", "what does", "how does", "what is", "describe", "walk me through",
    "write", "create", "generate", "implement", "add function", "add method",
    "make a", "build a", "code for",
    "test", "unit test", "write tests", "add tests", "pytest", "jest",
    "debug", "fix", "error", "bug", "broken", "not working", "issue", "exception",
    "traceback", "why is",
    "refactor", "clean up", "improve", "optimize", "simplify", "restructure",
    "architect", "architecture", "design", "how should i structure",
    "best way to structure", "system design",
    "performance", "faster", "speed up", "bottleneck", "latency", "memory usage",
    # Phase 9 contextual triggers
    "explain this", "what went wrong", "fix it", "fix the error", "continue",
    "summarize this project", "same pattern", "use the same",
]

_INTENT_MAP: dict[str, list[str]] = {
    "explain":   ["explain", "what does", "how does", "what is", "describe", "walk me through", "explain this"],
    "write":     ["write", "create", "generate", "implement", "add function", "add method", "make a", "build a", "code for"],
    "test":      ["test", "unit test", "write tests", "add tests", "pytest", "jest"],
    "debug":     ["debug", "fix", "error", "bug", "broken", "not working", "issue", "exception", "traceback", "why is", "what went wrong", "fix it", "fix the error"],
    "refactor":  ["refactor", "clean up", "restructure", "simplify"],
    "architect": ["architect", "architecture", "design", "how should i structure", "best way to structure", "system design"],
    "optimize":  ["optimize", "improve", "performance", "faster", "speed up", "bottleneck", "latency", "memory usage"],
    "recall":    ["continue", "summarize this project", "same pattern", "use the same", "from yesterday", "what were we"],
}

_SYSTEM_BASE = """You are Xyron, an embedded AI developer. You reason like a senior engineer.
Rules:
- Be concise and direct. No fluff, no motivation.
- Explain your reasoning briefly before code.
- Use production-quality patterns.
- Prefer stdlib/existing deps over new ones.
- Format code in markdown code blocks with language tags.
- Never hallucinate file contents — only reference what is provided in context.
- If active file content is unavailable, say so explicitly.
- Max response: ~400 tokens unless the task demands more."""

_INTENT_HINTS: dict[str, str] = {
    "explain":   "Explain the code or concept clearly. State the key insight first.",
    "write":     "Write clean, production-ready code. Add minimal comments for non-obvious logic only.",
    "test":      "Write thorough tests. Cover happy path and edge cases. Use the project's existing test framework.",
    "debug":     "Diagnose root cause first. Then provide the fix. Don't paper over the real issue.",
    "refactor":  "Improve structure without changing behavior. Call out each change and why.",
    "architect": "Propose a clear architecture. State trade-offs. Be opinionated — don't hedge.",
    "optimize":  "Profile before optimizing. State what the bottleneck is, then show the fix.",
    "recall":    "Use the provided project memory and history to answer. Be specific.",
}


def _gather_context(ctx: dict) -> dict[str, Any]:
    """Gather file intelligence, project memory, and terminal error. Never raises."""
    result: dict[str, Any] = {
        "file_ctx":     None,
        "project_mem":  None,
        "terminal_err": None,
    }
    project = ctx.get("active_project")
    file_   = ctx.get("active_file")

    if file_:
        try:
            from dev.file_intelligence import analyze_file
            result["file_ctx"] = analyze_file(file_, project)
        except Exception as exc:
            logger.debug("[DEV_AGENT_CONTEXT] file_intelligence unavailable: %s", exc)

    if project:
        try:
            from dev.project_memory import get_memory
            result["project_mem"] = get_memory(project)
        except Exception as exc:
            logger.debug("[DEV_AGENT_CONTEXT] project_memory unavailable: %s", exc)

        try:
            from dev.terminal_intelligence import get_latest_error
            result["terminal_err"] = get_latest_error(project)
        except Exception as exc:
            logger.debug("[DEV_AGENT_CONTEXT] terminal_intelligence unavailable: %s", exc)

    return result


def _build_context_block(gathered: dict[str, Any]) -> str:
    """Render gathered context into a concise text block for the system prompt."""
    lines: list[str] = []

    fc = gathered.get("file_ctx")
    if fc and fc.get("language") != "unknown":
        lines.append(f"\n── Active File ──")
        lines.append(f"File:      {fc.get('file', 'unknown')}")
        lines.append(f"Language:  {fc.get('language')} / {fc.get('framework', 'unknown')}")
        if fc.get("summary"):
            lines.append(f"Purpose:   {fc['summary']}")
        if fc.get("symbols"):
            lines.append(f"Symbols:   {', '.join(fc['symbols'][:10])}")
        if fc.get("imports"):
            lines.append(f"Imports:   {', '.join(fc['imports'][:8])}")
        if fc.get("todos"):
            lines.append(f"TODOs:     {' | '.join(fc['todos'][:3])}")
        if fc.get("issues"):
            lines.append(f"Issues:    {' | '.join(fc['issues'][:3])}")
    else:
        lines.append("\n── Active File ── (unavailable or not set)")

    pm = gathered.get("project_mem")
    if pm:
        lines.append(f"\n── Project Memory ──")
        lines.append(f"Project:   {pm.get('project_name', 'unknown')}")
        stack = pm.get("detected_stack", [])
        if stack:
            lines.append(f"Stack:     {', '.join(stack[:5])}")
        if pm.get("package_manager", "unknown") != "unknown":
            lines.append(f"Pkg mgr:   {pm['package_manager']}")
        if pm.get("git_branch", "unknown") != "unknown":
            lines.append(f"Branch:    {pm['git_branch']}")
        rec = pm.get("recurring_errors", [])
        if rec:
            lines.append(f"Recurring errors: {rec[0].get('type','?')} ×{rec[0].get('count',1)}")
        fixes = pm.get("previous_fixes", [])
        if fixes:
            lines.append(f"Last fix:  {fixes[0].get('problem','')[:80]}")
        summaries = pm.get("session_summaries", [])
        if summaries:
            lines.append(f"Last session: {summaries[0].get('text','')[:120]}")

    te = gathered.get("terminal_err")
    if te and te.get("detected"):
        lines.append(f"\n── Latest Terminal Error ──")
        lines.append(f"Type:     {te.get('error_type', 'unknown')}")
        lines.append(f"Severity: {te.get('severity', '?')}")
        lines.append(f"Summary:  {te.get('summary', '')}")
        lines.append(f"Cause:    {te.get('likely_cause', '')}")
        if te.get("command"):
            lines.append(f"Command:  {te['command'][:80]}")

    return "\n".join(lines)


class DevAgent(BaseAgent):
    """Phase 9 DevAgent — answers with full workspace context injected."""

    name     = "dev_agent"
    keywords = _DEV_KEYWORDS

    def can_handle(self, command: str) -> bool:
        lower = command.lower()
        return any(kw in lower for kw in self.keywords)

    def run(self, command: str, **kwargs: Any) -> AgentResult:
        t0 = time.monotonic()
        intent  = self._classify_intent(command)
        gathered = _gather_context(kwargs)
        system  = self._build_system_prompt(intent, kwargs, gathered)
        response = self._ollama_generate(system, command)
        latency = int((time.monotonic() - t0) * 1000)
        logger.info("[DEV_AGENT_CONTEXT] intent=%s latency_ms=%d project=%s file=%s action=run",
                    intent, latency, kwargs.get("active_project"), kwargs.get("active_file"))
        return self._result(
            True, response, command,
            metadata={"intent": intent, "model": _REASON_MODEL, "source": "local",
                      "latency_ms": latency},
        )

    def stream(self, command: str, **kwargs: Any) -> Generator[str, None, None]:
        """Yield response tokens with full context injected."""
        intent   = self._classify_intent(command)
        gathered = _gather_context(kwargs)
        system   = self._build_system_prompt(intent, kwargs, gathered)
        logger.info("[DEV_AGENT_CONTEXT] intent=%s action=stream project=%s file=%s",
                    intent, kwargs.get("active_project"), kwargs.get("active_file"))
        yield from self._ollama_stream(system, command)

    # ── Private ───────────────────────────────────────────────────────────────

    def _classify_intent(self, command: str) -> str:
        lower = command.lower()
        for intent, keywords in _INTENT_MAP.items():
            if any(kw in lower for kw in keywords):
                return intent
        return "explain"

    def _build_system_prompt(self, intent: str, ctx: dict, gathered: dict) -> str:
        project = ctx.get("active_project") or "unknown project"
        file_   = ctx.get("active_file")    or "not set"
        context_block = _build_context_block(gathered)
        hint = _INTENT_HINTS.get(intent, "")
        return (
            f"{_SYSTEM_BASE}\n"
            f"\nWorkspace: project={project}, file={file_}"
            f"{context_block}"
            f"\n\nTask type: {intent}. {hint}"
        )

    def _ollama_generate(self, system: str, prompt: str) -> str:
        try:
            resp = requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                json={
                    "model":   _REASON_MODEL,
                    "system":  system,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"temperature": 0.2, "num_predict": 512},
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException as exc:
            logger.error("[DEV_AGENT_CONTEXT] Ollama call failed: %s", exc)
            return f"[DevAgent] LLM unavailable: {exc}"

    def _ollama_stream(self, system: str, prompt: str) -> Generator[str, None, None]:
        try:
            import json as _json
            with requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                json={
                    "model":   _REASON_MODEL,
                    "system":  system,
                    "prompt":  prompt,
                    "stream":  True,
                    "options": {"temperature": 0.2, "num_predict": 512},
                },
                stream=True,
                timeout=60,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        chunk = _json.loads(line)
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
        except Exception as exc:
            logger.error("[DEV_AGENT_CONTEXT] stream failed: %s", exc)
            yield f"[DevAgent] stream error: {exc}"
