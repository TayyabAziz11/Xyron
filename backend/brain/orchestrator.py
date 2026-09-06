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
import time
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
        detected_language: str = "en",
        trace_id: str = "",
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

        # 6. Compound-command detection (Urdu/Roman-Urdu) — deterministic,
        # MUST run before step 7's single-shot intent routing. A garbled
        # compound transcript's single-shot intent_router match (e.g. the
        # whole remaining string mis-swallowed whole as an
        # open_application app_name, at confidence 1.0) wins and returns
        # immediately at step 7 below — step 8's English-only
        # _MULTISTEP_RE would never even get a chance to fire, since it's
        # only checked AFTER step 7 already failed to find a tool. Live
        # bug this closes (2026-09-04 real backend log): "YouTube کو کھولو
        # اور کوئی گانا چلا دو" ("open YouTube and play some song")
        # collapsed to a single "open YouTube" action — the second half
        # was silently discarded. See
        # mixed_language_engine.split_compound()'s docstring for the full
        # mechanism. English is completely untouched: split_compound() is
        # gated on detected_language != "en" and returns None immediately
        # for English input, so this step is a no-op there.
        if detected_language not in ("en",):
            try:
                from api.services.mixed_language_engine import split_compound as _mle_split_compound
                _canonical_steps = _mle_split_compound(t, detected_language, trace_id)
                if _canonical_steps:
                    logger.info(
                        "[ORCHESTRATOR] MULTI_STEP via deterministic compound split steps=%s",
                        _canonical_steps,
                    )
                    return OrchestratorDecision(
                        action=ActionType.MULTI_STEP,
                        context={"canonical_steps": _canonical_steps, "transcript": t,
                                 "compound_source": "deterministic"},
                        reason="urdu_compound_split",
                    )
            except Exception as _split_exc:
                logger.debug("[ORCHESTRATOR] compound split error: %s", _split_exc)

        # 7. Intent routing → tool
        tool_dec = await self._route_intent(t, detected_language, trace_id)
        if tool_dec and tool_dec.action == ActionType.TOOL:
            return tool_dec
        if tool_dec and tool_dec.action == ActionType.MULTI_STEP:
            # Tier 4 (comprehend_multi(), inside _route_intent below)
            # resolved >=2 confident intents where the deterministic
            # splitter above could not. Same MULTI_STEP shape either way —
            # the caller (voice_ws.py) doesn't need to know which tier
            # produced canonical_steps.
            return tool_dec

        # 8. Multi-step compound command (English-only connector marker —
        # unchanged, still the fallback for "create X and then open it"
        # style English compounds that never go through split_compound()
        # above since detected_language == "en" short-circuits it).
        if _MULTISTEP_RE.search(t):
            logger.info("[ORCHESTRATOR] MULTI_STEP transcript=%r", t[:60])
            return OrchestratorDecision(
                action=ActionType.MULTI_STEP,
                context={"transcript": t},
                reason="multistep_marker",
            )

        # 8. LLM fallback
        model_hint = (tool_dec.context.get("model") if tool_dec else None) or "gpt-4o-mini"
        # _route_intent's context (local_qwen_used/local_qwen_ms) would
        # otherwise be silently discarded here — only tool_dec.action ==
        # ActionType.TOOL is ever returned directly above, so an LLM-bound
        # tool_dec (Qwen understood the intent but it had no safe tool
        # mapping — e.g. compare_records) loses that telemetry unless it's
        # carried forward explicitly, same as model_hint already is.
        _llm_context = {"model": model_hint}
        if tool_dec:
            _llm_context["local_qwen_used"] = tool_dec.context.get("local_qwen_used", False)
            if "local_qwen_ms" in tool_dec.context:
                _llm_context["local_qwen_ms"] = tool_dec.context["local_qwen_ms"]
        return OrchestratorDecision(
            action=ActionType.LLM,
            context=_llm_context,
            reason=tool_dec.reason if tool_dec else "llm_fallback",
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _check_memory_refs(self, transcript: str) -> Optional[OrchestratorDecision]:
        try:
            from api.services.context_memory import context_memory as _cm
            refs = await asyncio.to_thread(_cm.resolve_references, transcript)

            if refs.get("is_delete_ref") and refs.get("resolved_paths"):
                # NOTE: voice_ws.py's MEMORY_REF handler gates this behind a
                # spoken confirmation before calling delete_file — never
                # execute a pronoun-resolved delete straight from here.
                return OrchestratorDecision(
                    action=ActionType.MEMORY_REF,
                    tool_name="delete_file",
                    tool_params={"paths": refs["resolved_paths"]},
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

    async def _route_intent(
        self, transcript: str, detected_language: str = "en", trace_id: str = "",
    ) -> Optional[OrchestratorDecision]:
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
                    context={"model": model, "local_qwen_used": False},
                    reason="intent_router",
                )

            # Deterministic routing found nothing confident enough. Only for
            # non-English/mixed input (the case that actually motivated it,
            # and where a keyword-only router is weakest) try the local
            # Qwen/OpenAI fallback before giving up to the general LLM path
            # — it NEVER executes a tool itself, only proposes structured
            # intent(s) that get validated against the real tool registry.
            #
            # comprehend_multi() ALWAYS asks for the compound-aware
            # "intents" schema (local_comprehension._SYSTEM_PROMPT_COMPOUND)
            # — a single-action utterance still comes back as a 1-element
            # list, handled below with EXACTLY the same TOOL/LLM decision
            # shape this returned before compound support existed (see
            # TestOrchestratorLocalQwenCanonicalizationWiring in
            # test_multilingual_pipeline.py for the contract this
            # preserves). A >=2-element list is the compound path.
            _qwen_invoked = False  # True as soon as comprehend_multi() actually
                                    # runs, regardless of whether it ends up
                                    # mapped to a tool — this is what the
                                    # manual-validation [ML_TURN] log means by
                                    # "was Qwen used", not "did its output
                                    # execute a tool".
            _qwen_ms: float = 0.0
            if detected_language not in ("en",):
                try:
                    from api.services.local_comprehension import comprehend_multi, validate_and_map
                    _lc_t0 = time.monotonic()
                    lc_results = await asyncio.to_thread(comprehend_multi, transcript, detected_language, trace_id)
                    _qwen_ms = (time.monotonic() - _lc_t0) * 1000
                    if lc_results:
                        _qwen_invoked = True
                        mapped_results = []
                        for lc_result in lc_results:
                            lc_result = validate_and_map(lc_result, _registry)
                            if lc_result.mapped:
                                mapped_results.append(lc_result)

                        if len(lc_results) == 1:
                            if mapped_results:
                                lc_result = mapped_results[0]
                                logger.info(
                                    "[ORCHESTRATOR] TOOL via local_qwen_canonicalization canonical=%r tool=%s "
                                    "route_conf=%.2f ms=%.0f",
                                    lc_result.canonical_text, lc_result.tool_name,
                                    lc_result.route_confidence, _qwen_ms,
                                )
                                return OrchestratorDecision(
                                    action=ActionType.TOOL,
                                    tool_name=lc_result.tool_name,
                                    tool_params=lc_result.tool_params,
                                    confidence=lc_result.route_confidence,
                                    tier=4,
                                    context={"model": model, "local_qwen_used": True, "local_qwen_ms": _qwen_ms},
                                    reason="local_qwen_canonicalization",
                                )
                            logger.info(
                                "[ORCHESTRATOR] local_qwen understood but unmapped action=%s — falling to LLM",
                                lc_results[0].action,
                            )
                        else:
                            # Compound path — only proceed if EVERY proposed
                            # intent mapped to a confident tool. Running a
                            # partial subset (some clauses mapped, others
                            # didn't) would silently drop part of what the
                            # user asked for. Never execute only the
                            # clauses that happened to parse — fail through
                            # to the general LLM path instead, exactly like
                            # an unmapped single intent already does.
                            if len(mapped_results) == len(lc_results):
                                _canonical_steps = [r.canonical_text for r in mapped_results]
                                logger.info(
                                    "[ORCHESTRATOR] MULTI_STEP via local_qwen_compound_canonicalization "
                                    "steps=%s ms=%.0f",
                                    _canonical_steps, _qwen_ms,
                                )
                                return OrchestratorDecision(
                                    action=ActionType.MULTI_STEP,
                                    context={
                                        "canonical_steps": _canonical_steps, "transcript": transcript,
                                        "compound_source": "qwen_or_openai", "local_qwen_used": True,
                                        "local_qwen_ms": _qwen_ms,
                                    },
                                    reason="local_qwen_compound_canonicalization",
                                )
                            logger.info(
                                "[ORCHESTRATOR] local_qwen compound partially unmapped (%d/%d) — "
                                "falling to LLM rather than dropping a clause",
                                len(mapped_results), len(lc_results),
                            )
                except Exception as _lc_exc:
                    logger.debug("[ORCHESTRATOR] local_qwen comprehension error: %s", _lc_exc)

            return OrchestratorDecision(
                action=ActionType.LLM,
                context={
                    "model": model, "low_conf_tool": route.tool_name,
                    "local_qwen_used": _qwen_invoked, "local_qwen_ms": _qwen_ms,
                },
                reason="low_confidence",
            )
        except Exception as exc:
            logger.warning("[ORCHESTRATOR] intent routing error: %s", exc)
            return None


# Module-level singleton — import and reuse across sessions
orchestrator = Orchestrator()
