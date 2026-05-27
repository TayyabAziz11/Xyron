"""
Xyron structured observability logger.

Writes append-only JSONL to logs/xyron.jsonl.
Every write is thread-safe and silently no-ops on failure — never disrupts
the voice pipeline.

Usage:
    from observability.xyron_logger import xlog

    xlog.brain_decision(route="tool", intent="open_app", confidence=0.95,
                        agent_id="system_agent", tier=1, latency_ms=1.2)
    xlog.memory_recall(query="dark mode", hits=2, latency_ms=18.4)
    xlog.tool(tool_name="open_application", intent="open_app",
              params={"name": "chrome"}, success=True, latency_ms=120.0)

Log format (COLLAB_PLAN §12):
    { "ts": "...", "event": "...", "agent": "...", "latency_ms": ...,
      "success": ..., "autonomy_level": ..., ... }
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_PATH = Path(__file__).parent.parent.parent / "logs" / "xyron.jsonl"
_lock     = threading.Lock()
_log      = logging.getLogger("observability.xyron_logger")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(event: str, data: dict[str, Any]) -> None:
    record = {"ts": _ts(), "event": event, **data}
    try:
        with _lock:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:
        _log.debug("xlog write failed: %s", exc)


def _autonomy() -> int:
    try:
        from brain.brain_state import brain_state
        return brain_state.autonomy_level
    except Exception:
        return 2


def _mood() -> str:
    try:
        from brain.brain_state import brain_state
        return brain_state.current_emotion or "neutral"
    except Exception:
        return "neutral"


class _XyronLogger:
    """Structured JSONL event logger. All methods are safe to call from any thread."""

    def brain_decision(
        self,
        *,
        route: str,
        intent: str,
        confidence: float,
        agent_id: str = "-",
        tier: int = 1,
        latency_ms: float = 0.0,
        requires_confirmation: bool = False,
    ) -> None:
        _write("brain_decision", {
            "route":                  route,
            "intent":                 intent,
            "confidence":             round(confidence, 4),
            "agent":                  agent_id,
            "semantic_tier":          tier,
            "latency_ms":             round(latency_ms, 2),
            "requires_confirmation":  requires_confirmation,
            "autonomy_level":         _autonomy(),
            "mood_state":             _mood(),
        })

    def semantic(
        self,
        *,
        text_preview: str,
        tier: int,
        route: str,
        intent: str,
        confidence: float,
        latency_ms: float = 0.0,
    ) -> None:
        _write("semantic_parse", {
            "text_preview":  text_preview[:60],
            "tier":          tier,
            "route":         route,
            "intent":        intent,
            "confidence":    round(confidence, 4),
            "latency_ms":    round(latency_ms, 2),
            "autonomy_level": _autonomy(),
        })

    def agent(
        self,
        *,
        agent_id: str,
        intent: str,
        success: bool,
        latency_ms: float,
        spoken_preview: str = "",
    ) -> None:
        _write("agent_executed", {
            "agent":          agent_id,
            "intent":         intent,
            "success":        success,
            "latency_ms":     round(latency_ms, 2),
            "spoken_preview": spoken_preview[:80],
            "autonomy_level": _autonomy(),
            "mood_state":     _mood(),
        })

    def tool(
        self,
        *,
        tool_name: str,
        intent: str = "",
        params: dict | None = None,
        success: bool,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        _write("tool_executed", {
            "tool":           tool_name,
            "intent":         intent,
            "params":         params or {},
            "success":        success,
            "latency_ms":     round(latency_ms, 2),
            "error":          error,
            "autonomy_level": _autonomy(),
            "mood_state":     _mood(),
        })

    def memory_write(
        self,
        *,
        mem_type: str,
        source: str,
        importance: float,
        text_preview: str = "",
    ) -> None:
        _write("memory_write", {
            "mem_type":    mem_type,
            "source":      source,
            "importance":  round(importance, 3),
            "text_preview": text_preview[:80],
            "autonomy_level": _autonomy(),
        })

    def memory_recall(
        self,
        *,
        query: str,
        hits: int,
        latency_ms: float = 0.0,
    ) -> None:
        _write("memory_recall", {
            "query":      query[:60],
            "hits":       hits,
            "latency_ms": round(latency_ms, 2),
        })

    def autonomy_step(
        self,
        *,
        goal: str,
        step_index: int,
        step_desc: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        _write("autonomy_step", {
            "goal":        goal[:80],
            "step_index":  step_index,
            "step_desc":   step_desc[:80],
            "success":     success,
            "latency_ms":  round(latency_ms, 2),
            "autonomy_level": _autonomy(),
        })

    def model_invoked(
        self,
        *,
        model: str,
        token_count: int = 0,
        latency_ms: float,
        purpose: str = "",
    ) -> None:
        _write("model_invoked", {
            "model":       model,
            "token_count": token_count,
            "latency_ms":  round(latency_ms, 2),
            "purpose":     purpose,
        })

    def tts(
        self,
        *,
        engine: str,
        char_count: int,
        latency_ms: float,
    ) -> None:
        _write("tts_synthesized", {
            "engine":     engine,
            "char_count": char_count,
            "latency_ms": round(latency_ms, 2),
        })

    def safety(
        self,
        *,
        tool: str,
        risk_level: str,
        approved: bool,
    ) -> None:
        _write("safety_confirmation", {
            "tool":        tool,
            "risk_level":  risk_level,
            "approved":    approved,
            "autonomy_level": _autonomy(),
        })

    def error(
        self,
        *,
        source: str,
        message: str,
        exc: Exception | None = None,
    ) -> None:
        _write("error", {
            "source":  source,
            "message": message[:200],
            "exc":     str(exc)[:200] if exc else None,
        })

    def latency(self, *, label: str, ms: float, **extra: Any) -> None:
        _write("latency", {"label": label, "ms": round(ms, 2), **extra})


xlog = _XyronLogger()
