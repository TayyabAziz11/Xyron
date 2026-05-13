"""
Brain Orchestrator — decision layer between STT transcript and action.

Priority order:
  1. STOP       — explicit end-of-session ("goodbye", "exit", etc.)
  2. CLARIFY    — user is confused or requesting a repeat
  3. MEMORY_REF — pronoun reference to previous action ("delete them", "open it")
  4. TOOL       — matched tool with confidence ≥ 0.55
  5. MULTI_STEP — compound command ("create folder and then open it")
  6. LLM        — general streaming response
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ActionType(Enum):
    MEMORY_REF = auto()
    TOOL       = auto()
    MULTI_STEP = auto()
    LLM        = auto()
    CLARIFY    = auto()
    STOP       = auto()
    INTERRUPT  = auto()  # soft cancel: stop current TTS/action, keep session alive


_STOP_RE = re.compile(
    r'\b(goodbye|bye|exit|quit|close session|stop session|'
    r'that(?:\'s|\s+is)\s+all|done for now|shut\s?down|no\s+more)\b',
    re.IGNORECASE,
)

_CLARIFY_RE = re.compile(
    r'^\s*(what(?:\s*\?)? |huh|sorry|pardon|say that again|repeat that|'
    r'i\s+don\'?t\s+understand|what\s+did\s+you\s+say)\s*[?.!]?\s*$',
    re.IGNORECASE,
)

_INTERRUPT_RE = re.compile(
    r'^\s*(stop|wait|no|cancel|never\s+mind|forget\s+it|hold\s+on|'
    r'change\s+that|actually\s+no|don\'?t|not\s+that)\s*[.!]?\s*$',
    re.IGNORECASE,
)

_MULTISTEP_RE = re.compile(
    r'\band\s+then\b|\bafter\s+that\b|\bthen\s+(?:open|create|delete|move|copy|send|run)\b'
    r'|\band\s+also\b|\bfirst\b.+\bthen\b',
    re.IGNORECASE,
)


@dataclass
class OrchestratorDecision:
    action:      ActionType
    tool_name:   Optional[str]  = None
    tool_params: dict[str, Any] = field(default_factory=dict)
    confidence:  float          = 1.0
    tier:        int            = 0
    context:     dict[str, Any] = field(default_factory=dict)
    reason:      str            = ""


class Orchestrator:
    """
    Single async entry point: decide() → OrchestratorDecision.

    Stateless — session memory is passed in via `history`.
    """

    async def decide(
        self,
        transcript: str,
        history:    list[dict] | None = None,
    ) -> OrchestratorDecision:
        t = transcript.strip()

        # Read cognitive state — inform routing and mark as processing
        try:
            from cognition.cognitive_state import cognitive_state as _cs
            from cognition.state_transitions import transition_to_processing
            _active_goal = _cs.active_goal
            _mood        = _cs.mood_bias
            transition_to_processing()
            logger.debug("[ORCHESTRATOR] state — attention=PROCESSING goal=%r mood=%s",
                         _active_goal, _mood)
        except Exception:
            _active_goal = None
            _mood        = None

        # 1. Stop
        if _STOP_RE.search(t):
            logger.info("[ORCHESTRATOR] STOP transcript=%r", t[:60])
            return OrchestratorDecision(action=ActionType.STOP, reason="stop_intent")

        # 2. Interrupt — soft cancel, keep session alive (standalone: "stop", "wait", "no", etc.)
        if _INTERRUPT_RE.match(t):
            logger.info("[ORCHESTRATOR] INTERRUPT transcript=%r", t[:60])
            return OrchestratorDecision(action=ActionType.INTERRUPT, reason="interrupt_word")

        # 4. Clarify
        if _CLARIFY_RE.match(t):
            logger.info("[ORCHESTRATOR] CLARIFY transcript=%r", t[:60])
            return OrchestratorDecision(action=ActionType.CLARIFY, reason="clarify_intent")

        # 5. Context memory pronoun refs
        mem = await self._check_memory_refs(t)
        if mem:
            return mem

        # 6. Intent routing → tool
        tool_dec = await self._route_intent(t)
        if tool_dec and tool_dec.action == ActionType.TOOL:
            return tool_dec

        # 7. Multi-step compound command
        if _MULTISTEP_RE.search(t):
            logger.info("[ORCHESTRATOR] MULTI_STEP transcript=%r", t[:60])
            return OrchestratorDecision(
                action=ActionType.MULTI_STEP,
                context={"transcript": t},
                reason="multistep_marker",
            )

        # 8. LLM fallback
        model_hint = (tool_dec.context.get("model") if tool_dec else None) or "gpt-4o-mini"
        return OrchestratorDecision(
            action=ActionType.LLM,
            context={"model": model_hint},
            reason=tool_dec.reason if tool_dec else "llm_fallback",
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _check_memory_refs(self, transcript: str) -> Optional[OrchestratorDecision]:
        try:
            from api.services.context_memory import context_memory as _cm
            refs = await asyncio.to_thread(_cm.resolve_references, transcript)

            if refs.get("is_delete_ref") and refs.get("resolved_paths"):
                return OrchestratorDecision(
                    action=ActionType.MEMORY_REF,
                    tool_name="delete_file",
                    tool_params={"paths": refs["resolved_paths"], "confirmed": True},
                    confidence=1.0,
                    context={"refs": refs},
                    reason="memory_delete_ref",
                )
            if refs.get("is_open_ref") and refs.get("resolved_paths"):
                return OrchestratorDecision(
                    action=ActionType.MEMORY_REF,
                    tool_name="open_directory",
                    tool_params={"path": refs["resolved_paths"][0]},
                    confidence=1.0,
                    context={"refs": refs},
                    reason="memory_open_ref",
                )
            if refs.get("is_repeat") and refs.get("last_action"):
                last = refs["last_action"]
                tool = last.get("type")
                params = {k: v for k, v in last.items()
                          if k not in ("type", "entities", "paths", "timestamp")}
                if last.get("paths"):
                    params["path"] = last["paths"][0]
                return OrchestratorDecision(
                    action=ActionType.MEMORY_REF,
                    tool_name=tool,
                    tool_params=params,
                    confidence=1.0,
                    context={"refs": refs},
                    reason="memory_repeat",
                )
        except Exception as exc:
            logger.debug("[ORCHESTRATOR] memory ref check error: %s", exc)
        return None

    async def _route_intent(self, transcript: str) -> Optional[OrchestratorDecision]:
        try:
            from api.services.intent_router import intent_router as _ir
            from api.tools import registry as _registry
            from api.services.model_router import select_model

            route = await asyncio.to_thread(_ir.route, transcript)
            model = select_model(transcript)

            if route.tool_name and route.tool_name in _registry and route.confidence >= 0.55:
                logger.info("[ORCHESTRATOR] TOOL tier=%d tool=%s conf=%.2f",
                            route.tier, route.tool_name, route.confidence)
                return OrchestratorDecision(
                    action=ActionType.TOOL,
                    tool_name=route.tool_name,
                    tool_params=route.params,
                    confidence=route.confidence,
                    tier=route.tier,
                    context={"model": model},
                    reason="intent_router",
                )

            return OrchestratorDecision(
                action=ActionType.LLM,
                context={"model": model, "low_conf_tool": route.tool_name},
                reason="low_confidence",
            )
        except Exception as exc:
            logger.warning("[ORCHESTRATOR] intent routing error: %s", exc)
            return None


# Module-level singleton — import and reuse across sessions
orchestrator = Orchestrator()
