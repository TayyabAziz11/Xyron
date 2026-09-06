"""
Tool Registry — central catalogue of all AI Operator tools.

Each tool has:
  • An OpenAI function definition (used for tool calling)
  • A Python executor function
  • A risk level (low / medium / high)
  • A category (system / web / content)

Usage:
    from api.tools import registry

    definitions = registry.get_definitions()           # → list[dict] for OpenAI
    result      = registry.execute("open_directory",   # → ToolResult
                                   {"path": "E:\\"},
                                   context={"openai_key": "sk-..."})
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    success:     bool
    text:        str                          # human-readable detail
    spoken:      str = ""                    # shorter spoken confirmation
    data:        Dict[str, Any] = field(default_factory=dict)
    action_url:  Optional[str] = None        # browser URL to open
    action_app:  Optional[str] = None        # app name to launch
    action_path: Optional[str] = None        # folder/file path to open in explorer
    risk:        str = "low"
    error:       Optional[str] = None

    def to_task_result(self) -> str:
        """Serialize for task step result field (strips prefixes added by execute)."""
        return self.text

    def to_sse_action(self) -> dict | None:
        """Return SSE action dict if any side-effect needs to be signalled to frontend."""
        if self.error == "confirm_required":
            return {
                "confirm_required": True,
                "confirm_tool":     self.data.get("tool", ""),
                "confirm_params":   self.data.get("params", {}),
                "confirm_prompt":   self.data.get("prompt", "Are you sure? Say yes to confirm or no to cancel."),
            }
        if self.action_url or self.action_app or self.action_path:
            return {
                "action_url":  self.action_url,
                "action_app":  self.action_app,
                "action_path": self.action_path,
            }
        return None


# ── Tool registration ─────────────────────────────────────────────────────────

class _Tool:
    __slots__ = ("name", "definition", "executor", "risk", "category")

    def __init__(
        self,
        name:       str,
        definition: dict,
        executor:   Callable[..., ToolResult],
        risk:       str = "low",
        category:   str = "general",
    ) -> None:
        self.name       = name
        self.definition = definition
        self.executor   = executor
        self.risk       = risk
        self.category   = category


class ToolRegistry:
    """Thread-safe central tool registry."""

    def __init__(self) -> None:
        self._tools: Dict[str, _Tool] = {}

    def register(
        self,
        name:       str,
        definition: dict,
        executor:   Callable[..., ToolResult],
        risk:       str = "low",
        category:   str = "general",
    ) -> None:
        self._tools[name] = _Tool(name, definition, executor, risk, category)
        logger.debug("Tool registered: %s (risk=%s, cat=%s)", name, risk, category)

    def get_definitions(self, categories: List[str] | None = None) -> List[dict]:
        """Return OpenAI-format function definitions, optionally filtered by category."""
        tools = self._tools.values()
        if categories:
            tools = [t for t in tools if t.category in categories]  # type: ignore[assignment]
        return [t.definition for t in tools]

    def execute(
        self,
        name:    str,
        params:  Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool by name. Returns ToolResult (never raises)."""
        ctx = context or {}
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                text=f"Unknown tool: {name}",
                spoken=f"I don't know how to perform that action.",
                error=f"Tool '{name}' not registered",
            )
        try:
            result = tool.executor(params, ctx)
            result.risk = tool.risk
            return result
        except Exception as exc:
            logger.exception("Tool %s crashed", name)
            from api.services.failure_messages import spoken_for_exception
            return ToolResult(
                success=False,
                text=f"Tool error: {exc}",
                spoken=spoken_for_exception(exc),
                error=str(exc),
                risk=tool.risk,
            )

    def get_risk(self, name: str) -> str:
        t = self._tools.get(name)
        return t.risk if t else "medium"

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Module-level singleton
registry = ToolRegistry()
