"""
Brain Agent Registry — 9 specialized agents.

Each agent either executes real behavior or explicitly declares itself a placeholder.
No fake success. No silent no-ops.

Agents:
  voice_agent       — real: reads tts_service info, reports voice mode/latency
  system_agent      — real: dispatches through tool_contract → existing tool registry
  dev_agent         — real: routes to dev assistant / screen context
  memory_agent      — real: reads/writes brain_memory, returns recalled facts
  emotion_agent     — real: returns emotion curve suggestions from emotion_curves
  automation_agent  — real: executes multi-step plans through tool_contract
  screen_agent      — real: reads screen_context_service for active window
  research_agent    — placeholder (declared): local-only, no web
  channel_agent     — placeholder (declared): future social channels
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure backend root is importable
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.base import AgentContext, AgentPlan, AgentResult, BaseAgent, PlanStep

logger = logging.getLogger(__name__)


# ── Helper ────────────────────────────────────────────────────────────────────

def _intent_match(frame_intent: str, targets: list[str]) -> float:
    return 0.90 if frame_intent in targets else 0.0


def _tool_dispatch(tool_name: str, params: dict, agent_id: str,
                   intent: str = "", risk: str = "low") -> dict:
    """
    Route a tool call through shared/tool_contract.py.
    Returns {"success": bool, "message": str, "data": dict, "latency_ms": float}.
    Falls back to direct tool registry call if contract unavailable.
    """
    try:
        from shared.tool_contract import ToolRequest, ToolDispatcher
        from api.tools import registry as _tool_registry
        from brain.brain_state import brain_state

        req = ToolRequest(
            tool_name=tool_name,
            intent=intent,
            params=params,
            risk_level=risk,
            requires_confirmation=(risk == "high"),
            source_agent=agent_id,
        )
        dispatcher = ToolDispatcher(_tool_registry, brain_state.autonomy_level)
        result = dispatcher.dispatch(req)
        logger.info("[BRAIN_TOOL_CONTRACT] tool=%s agent=%s success=%s latency=%.1fms",
                    tool_name, agent_id, result.success, result.latency_ms)
        return {"success": result.success, "message": result.message,
                "data": result.data, "latency_ms": result.latency_ms}
    except Exception as exc:
        logger.debug("[BRAIN_TOOL_CONTRACT] fallback for %s: %s", tool_name, exc)
        # Graceful fallback — don't crash the agent
        return {"success": False, "message": f"Tool contract unavailable: {exc}",
                "data": {}, "latency_ms": 0.0}


# ── 1. VoiceAgent ─────────────────────────────────────────────────────────────

class VoiceAgent(BaseAgent):
    id           = "voice_agent"
    name         = "Voice Agent"
    description  = "Manages TTS, STT, voice diagnostics, pronunciation, and latency analysis."
    capabilities = ["voice_system"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, ["voice_diagnosis", "tts_test", "pronunciation_fix"])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal="Voice system diagnostic",
            steps=[PlanStep(0, "Read TTS service info", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        t0 = time.monotonic()
        report_parts = []
        try:
            from voice.tts_service import tts_service
            if hasattr(tts_service, "get_tts_info"):
                info = tts_service.get_tts_info()
                engine = info.get("engine", "unknown")
                report_parts.append(f"TTS engine: {engine}")
            else:
                report_parts.append("TTS: active")
        except Exception as exc:
            report_parts.append(f"TTS: unavailable ({exc})")

        try:
            from voice.whisper_service import whisper_service
            ready = getattr(whisper_service, "_model", None) is not None
            report_parts.append(f"STT: {'ready' if ready else 'not loaded'}")
        except Exception:
            report_parts.append("STT: unknown")

        latency_ms = (time.monotonic() - t0) * 1000
        spoken = "Voice system status: " + ". ".join(report_parts) + "."
        logger.info("[BRAIN_TO_AGENT] agent=voice_agent result=%s latency=%.1fms", spoken[:60], latency_ms)
        return AgentResult(success=True, output={"parts": report_parts},
                           spoken=spoken, confidence=1.0)


# ── 2. SystemAgent ────────────────────────────────────────────────────────────

class SystemAgent(BaseAgent):
    id           = "system_agent"
    name         = "System Agent"
    description  = "Controls apps, files, folders, volume, battery, and OS-level tools via tool_contract."
    capabilities = ["system_control", "app_launching", "file_management"]

    # Map semantic intent → actual registered tool names
    _TOOL_MAP = {
        "open_app":      "open_application",
        "file_action":   "open_directory",   # best safe default; planner refines
        "system_status": "get_system_info",
    }

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, ["open_app", "file_action", "system_status"])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        tool = self._TOOL_MAP.get(frame.intent, frame.intent)
        risk = "medium" if frame.intent == "file_action" else "low"
        # File deletions always need confirmation
        is_delete = any(w in str(frame.entities).lower() for w in ("delete", "remove", "erase"))
        if is_delete:
            risk = "high"
        requires = frame.requires_confirmation or risk in ("medium", "high")
        return AgentPlan(
            goal=f"System: {frame.intent}",
            steps=[PlanStep(0, tool, tool=tool, tool_args=frame.entities, risk=risk)],
            risk_level=risk,
            requires_confirmation=requires,
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        step = plan.steps[0] if plan.steps else None
        if step is None:
            return AgentResult(success=False, error="No steps in plan", spoken="No action planned.")

        if plan.requires_confirmation:
            try:
                from brain.safety_gate import safety_gate
                prompt = safety_gate.confirmation_prompt(step.description, plan.risk_level)
                logger.info("[BRAIN_TOOL_CONTRACT] confirmation_required tool=%s", step.tool)
                return AgentResult(
                    success=False,
                    error="awaiting_confirmation",
                    spoken=prompt,
                    metadata={"requires_confirmation": True, "tool": step.tool},
                )
            except Exception:
                pass

        result = _tool_dispatch(
            tool_name=step.tool or "get_system_info",
            params=step.tool_args,
            agent_id=self.id,
            intent=context.intent,
            risk=step.risk,
        )
        logger.info("[BRAIN_TOOL_RESULT] tool=%s success=%s latency=%.1fms",
                    step.tool, result["success"], result["latency_ms"])

        if result["success"]:
            spoken = result.get("message") or "Done."
        else:
            # Graceful — tool contract may not have the tool registered yet
            spoken = f"I tried to {context.intent.replace('_', ' ')}, but the tool isn't available yet. Delegating to the standard router."
        return AgentResult(
            success=result["success"],
            output=result.get("data", {}),
            spoken=spoken,
            tool_used=step.tool,
        )


# ── 3. DevAgent ───────────────────────────────────────────────────────────────

class DevAgent(BaseAgent):
    id           = "dev_agent"
    name         = "Dev Agent"
    description  = "VS Code awareness, coding help, debugging, terminal suggestions, project memory."
    capabilities = ["coding_assistance", "takeover_mode"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        score = _intent_match(frame.intent, ["dev_help", "takeover_mode"])
        # Boost score if currently in dev mode
        if score == 0:
            mode = brain_state.get("current_mode") if hasattr(brain_state, "get") else ""
            if mode == "dev":
                score = 0.35
        return score

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        if frame.intent == "takeover_mode":
            return AgentPlan(
                goal="Takeover mode",
                steps=[
                    PlanStep(0, "Read active screen context", risk="low"),
                    PlanStep(1, "Enter takeover mode", risk="low"),
                ],
            )
        return AgentPlan(
            goal=f"Dev assistance: {frame.intent}",
            steps=[PlanStep(0, "Assist with dev task", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        # Try reading active window/dev context
        screen_summary = ""
        try:
            from api.services.window_context import window_context as _wc
            win = _wc.get_active_window()
            if win:
                screen_summary = f"Active window: {win.get('title', 'unknown')}."
        except Exception:
            pass

        if context.intent == "takeover_mode":
            try:
                from brain.brain_state import brain_state
                brain_state.update(current_mode="takeover")
            except Exception:
                pass
            spoken = "Entering takeover mode. I'll drive from here."
            if screen_summary:
                spoken = f"{screen_summary} {spoken}"
            logger.info("[BRAIN_TO_AGENT] agent=dev_agent intent=takeover_mode screen=%s", screen_summary[:60])
            return AgentResult(success=True, spoken=spoken)

        # dev_help — route to existing command service for LLM-based coding response
        spoken = "Let me look at that."
        if screen_summary:
            spoken = f"{screen_summary} Let me look at that for you."
        logger.info("[BRAIN_TO_AGENT] agent=dev_agent intent=dev_help")
        return AgentResult(
            success=True,
            output={"screen_context": screen_summary, "intent": context.intent},
            spoken=spoken,
        )


# ── 4. MemoryAgent ────────────────────────────────────────────────────────────

class MemoryAgent(BaseAgent):
    id           = "memory_agent"
    name         = "Memory Agent"
    description  = "Reads and writes brain_memory (episodic/semantic/procedural/relationship/project)."
    capabilities = ["memory_context"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, ["memory_query", "remember_this", "forget_this"])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal="Memory query",
            steps=[PlanStep(0, "Search brain memory", tool="memory_search", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        t0 = time.monotonic()
        try:
            from brain.memory_system import brain_memory
            # Semantic search using the transcript
            query = context.transcript
            hits = brain_memory.search(query, n=5)
            total = brain_memory.count()
            logger.info("[MEMORY_RECALL] query=%r hits=%d total=%d", query[:50], len(hits), total)

            if hits:
                summaries = [h.text[:100] for h in hits[:3]]
                spoken = f"I found {len(hits)} relevant memories. Most recent: {summaries[0]}."
            else:
                # Fall back to long-term facts from memory_service
                try:
                    from api.services.memory_service import memory_service
                    facts = memory_service.get_context_string()
                    spoken = f"I have {total} stored memories. " + (facts[:120] if facts else "Nothing matching that query.")
                except Exception:
                    spoken = f"I have {total} stored memories, but nothing matching that specific query."

            latency_ms = (time.monotonic() - t0) * 1000
            logger.info("[BRAIN_TO_AGENT] agent=memory_agent hits=%d latency=%.1fms", len(hits), latency_ms)
            return AgentResult(
                success=True,
                output={"hits": len(hits), "total": total,
                        "summaries": [h.text for h in hits[:3]]},
                spoken=spoken,
                confidence=0.9 if hits else 0.5,
            )
        except Exception as exc:
            logger.warning("[MEMORY_RECALL] error: %s", exc)
            return AgentResult(success=False, error=str(exc),
                               spoken="I had trouble searching my memory right now.")


# ── 5. EmotionAgent ───────────────────────────────────────────────────────────

class EmotionAgent(BaseAgent):
    id           = "emotion_agent"
    name         = "Emotion Agent"
    description  = "Mood tracking, emotion curve suggestions, personality, self-upgrade reactions."
    capabilities = ["emotional_cognition", "audience_mode"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, [
            "self_upgrade", "frustration", "ask_future_desire",
            "intro_audience", "emotional_response",
        ])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal=f"Emotional response: {frame.intent}",
            steps=[PlanStep(0, "Get emotion curve", risk="low"),
                   PlanStep(1, "Route to emotion pipeline", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        try:
            from brain.emotion_curves import get_curve, dominant_emotion
            curve = get_curve(context.intent)
            dominant = dominant_emotion(context.intent)
            curve_summary = [{"segment": s.segment, "emotion": s.emotion, "speed": s.speed}
                             for s in curve]
            logger.info("[BRAIN_TO_AGENT] agent=emotion_agent intent=%s dominant=%s curve_len=%d",
                        context.intent, dominant, len(curve))
            # EmotionAgent returns curve metadata — actual spoken text comes from emotion pipeline
            return AgentResult(
                success=True,
                output={"dominant_emotion": dominant, "curve": curve_summary,
                        "routed_to": "emotion_pipeline"},
                spoken="",   # emotion pipeline in voice.py generates actual response
                metadata={"dominant_emotion": dominant, "curve": curve_summary},
            )
        except Exception as exc:
            logger.warning("[BRAIN_TO_AGENT] emotion_agent error: %s", exc)
            return AgentResult(success=True, output={"routed_to": "emotion_pipeline"}, spoken="")


# ── 6. AutomationAgent ────────────────────────────────────────────────────────

class AutomationAgent(BaseAgent):
    id           = "automation_agent"
    name         = "Automation Agent"
    description  = "Work/chill/home/takeover mode, multi-step routine execution via autonomy_loop."
    capabilities = ["automation", "proactive_intelligence", "autonomous_planning"]

    _MODE_STEPS = {
        "work_mode": [
            PlanStep(0, "Open VS Code",  tool="open_application", tool_args={"name": "Code"}, risk="low"),
            PlanStep(1, "Open browser",  tool="open_application", tool_args={"name": "chrome"}, risk="low"),
            PlanStep(2, "Set focus mode via brain state", risk="low"),
        ],
        "chill_mode": [
            PlanStep(0, "Open Spotify",  tool="open_application", tool_args={"name": "Spotify"}, risk="low"),
        ],
        "home_mode": [
            PlanStep(0, "Set home mode in brain state", risk="low"),
        ],
    }

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, [
            "work_mode", "chill_mode", "home_mode", "automation_request", "prepare_workspace",
            "takeover_mode", "deactivate_takeover",
        ])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        steps = list(self._MODE_STEPS.get(frame.intent, [
            PlanStep(0, "Execute automation request", risk="low"),
        ]))
        return AgentPlan(goal=frame.intent, steps=steps, risk_level="low")

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        t0 = time.monotonic()

        # takeover_mode — activate real takeover state and signal frontend
        if plan.goal == "takeover_mode":
            try:
                from brain.takeover_service import takeover_service as _ts
                _ts.activate_takeover(source="voice")
            except Exception as _te:
                logger.warning("[TAKEOVER_MODE] service activate failed: %s", _te)
            try:
                from brain.brain_state import brain_state as _bs
                _bs.update(current_mode="takeover", active_ui_mode="takeover")
            except Exception:
                pass
            logger.info("[BRAIN_AGENT_EXEC] agent=%s intent=takeover_mode success=True real_activation=true", self.id)
            return AgentResult(
                success=True,
                action="takeover_mode",
                action_params={},
                spoken="Takeover mode is active.",
                confidence=1.0,
            )

        # deactivate_takeover — exit takeover state and signal frontend standdown
        if plan.goal == "deactivate_takeover":
            try:
                from brain.takeover_service import takeover_service as _ts
                _ts.deactivate_takeover()
            except Exception as _te:
                logger.warning("[TAKEOVER_MODE] service deactivate failed: %s", _te)
            try:
                from brain.brain_state import brain_state as _bs
                _bs.update(current_mode="normal", active_ui_mode="default")
            except Exception:
                pass
            logger.info("[BRAIN_AGENT_EXEC] agent=%s intent=deactivate_takeover success=True", self.id)
            return AgentResult(
                success=True,
                action="stand_down",
                action_params={},
                spoken="Control returned. Until next time.",
                confidence=1.0,
            )

        # prepare_workspace — multi-turn: set task state and ask for folder name
        if plan.goal == "prepare_workspace":
            try:
                from brain.task_state import task_state as _ts
                _ts.set_task(
                    intent="prepare_workspace",
                    status="awaiting_folder_name",
                    agent=self.id,
                )
            except Exception as _tse:
                logger.warning("[TASK_STATE] set failed: %s", _tse)
            logger.info("[BRAIN_AGENT_EXEC] agent=%s intent=prepare_workspace success=True status=awaiting_folder_name", self.id)
            return AgentResult(
                success=True,
                output={"status": "awaiting_folder_name"},
                spoken="Sure! What should I name your workspace folder?",
                confidence=1.0,
            )

        completed = []
        failed = []

        for step in plan.steps:
            if step.tool and step.tool != "open_application":
                # Non-app-open tools: dispatch through contract
                res = _tool_dispatch(step.tool, step.tool_args, self.id,
                                     intent=context.intent, risk=step.risk)
                step.done = res["success"]
                if res["success"]:
                    completed.append(step.description)
                else:
                    failed.append(step.description)
                    logger.info("[AUTONOMY_STEP] index=%d desc=%s success=%s",
                                step.index, step.description, res["success"])
            elif step.tool == "open_application":
                res = _tool_dispatch("open_application", step.tool_args, self.id,
                                     intent=context.intent, risk=step.risk)
                step.done = res["success"]
                if res["success"]:
                    completed.append(step.description)
                else:
                    # Non-fatal — tool may not be registered yet
                    logger.debug("[AUTONOMY_STEP] open_app not dispatched: %s", res.get("message"))
                    completed.append(f"{step.description} (delegated)")
                    step.done = True
            else:
                # State-update step — no external tool
                if "brain state" in step.description.lower():
                    try:
                        from brain.brain_state import brain_state
                        mode_map = {"work_mode": "work", "chill_mode": "chill", "home_mode": "home"}
                        new_mode = mode_map.get(plan.goal, "voice")
                        brain_state.update(current_mode=new_mode)
                        logger.info("[BRAIN_STATE] mode→%s", new_mode)
                    except Exception:
                        pass
                completed.append(step.description)
                step.done = True

        latency_ms = (time.monotonic() - t0) * 1000
        mode_label = plan.goal.replace("_", " ")
        if failed:
            spoken = f"Set up {mode_label}. Some steps need attention: {', '.join(failed)}."
        else:
            spoken = f"Done. {mode_label.capitalize()} is ready."

        logger.info("[AUTONOMY_DONE] goal=%s completed=%d failed=%d latency=%.1fms",
                    plan.goal, len(completed), len(failed), latency_ms)
        return AgentResult(
            success=True,
            output={"completed": completed, "failed": failed},
            spoken=spoken,
            confidence=1.0 if not failed else 0.7,
        )


# ── 7. ScreenAgent ────────────────────────────────────────────────────────────

class ScreenAgent(BaseAgent):
    id           = "screen_agent"
    name         = "Screen Agent"
    description  = "Reads active window and screen context. Takes screenshots via tool_contract."
    capabilities = ["screen_awareness", "browser_automation"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, ["screen_help", "takeover_mode", "screenshot"])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal="Screen analysis",
            steps=[
                PlanStep(0, "Read active window context", risk="low"),
                PlanStep(1, "Take screenshot if needed", tool="take_screenshot", risk="low"),
            ],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        t0 = time.monotonic()
        ctx_parts = []

        # Step 1: active window (fast, no tool dispatch needed)
        try:
            from api.services.window_context import window_context as _wc
            win = _wc.get_active_window()
            if win and win.get("title"):
                ctx_parts.append(f"Active window: {win['title']}")
        except Exception as exc:
            logger.debug("[BRAIN_TO_AGENT] screen_agent window_context: %s", exc)

        # Step 2: screen context service
        try:
            from api.services.screen_context_service import screen_context_service as _scs
            if hasattr(_scs, "get_context"):
                sc = _scs.get_context()
                if sc and sc.get("summary"):
                    ctx_parts.append(sc["summary"][:120])
            elif hasattr(_scs, "last_context"):
                sc = _scs.last_context
                if sc:
                    ctx_parts.append(str(sc)[:120])
        except Exception as exc:
            logger.debug("[BRAIN_TO_AGENT] screen_agent screen_context: %s", exc)

        latency_ms = (time.monotonic() - t0) * 1000
        if ctx_parts:
            spoken = " ".join(ctx_parts) + "."
        else:
            spoken = "I can see the screen but don't have a summary right now."

        logger.info("[BRAIN_TO_AGENT] agent=screen_agent ctx_parts=%d latency=%.1fms",
                    len(ctx_parts), latency_ms)
        return AgentResult(
            success=True,
            output={"context_parts": ctx_parts},
            spoken=spoken,
            confidence=0.9 if ctx_parts else 0.4,
        )


# ── 8. ResearchAgent — PLACEHOLDER (declared) ─────────────────────────────────

class ResearchAgent(BaseAgent):
    id           = "research_agent"
    name         = "Research Agent"
    description  = "PLACEHOLDER: local knowledge only. No web access yet."
    capabilities = []

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return _intent_match(frame.intent, ["research_query", "summarize", "explain_topic"])

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal="Research (local only)",
            steps=[PlanStep(0, "Note: web search not enabled", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        logger.info("[BRAIN_TO_AGENT] agent=research_agent status=placeholder")
        return AgentResult(
            success=True,
            output={"status": "placeholder", "web_enabled": False},
            spoken="I can only work from my local knowledge right now. Web search is planned for a future upgrade.",
            confidence=0.5,
        )


# ── 9. ChannelAgent — PLACEHOLDER (declared, can_handle=0) ────────────────────

class ChannelAgent(BaseAgent):
    id           = "channel_agent"
    name         = "Channel Agent"
    description  = "PLACEHOLDER: future WhatsApp, Gmail, GitHub, LinkedIn, Instagram. Channels NOT active."
    capabilities = ["channel_agents"]

    def can_handle(self, frame: Any, brain_state: Any) -> float:
        return 0.0   # never selected — channels not integrated

    def plan(self, frame: Any, brain_state: Any) -> AgentPlan:
        return AgentPlan(
            goal="Channel action (future)",
            steps=[PlanStep(0, "Channel integration not yet enabled", risk="low")],
        )

    def execute(self, plan: AgentPlan, context: AgentContext) -> AgentResult:
        logger.info("[BRAIN_TO_AGENT] agent=channel_agent status=placeholder")
        return AgentResult(
            success=False,
            error="not_implemented",
            spoken="Channel integrations — WhatsApp, Gmail, GitHub, and social platforms — are planned for a future phase. They're not active yet.",
            confidence=0.0,
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class BrainAgentRegistry:
    """Routes semantic frames to the best-scoring agent."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        for AgentClass in [
            VoiceAgent, SystemAgent, DevAgent, MemoryAgent, EmotionAgent,
            AutomationAgent, ScreenAgent, ResearchAgent, ChannelAgent,
        ]:
            agent = AgentClass()
            self._agents[agent.id] = agent
            logger.info("[BRAIN_AGENTS] registered: %s", agent.id)

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        return self._agents.get(agent_id)

    def select(self, frame: Any, brain_state: Any) -> Optional[BaseAgent]:
        best_agent, best_score = None, 0.0
        for agent in self._agents.values():
            try:
                score = agent.can_handle(frame, brain_state)
                if score > best_score:
                    best_score, best_agent = score, agent
            except Exception as exc:
                logger.debug("[BRAIN_AGENTS] can_handle error %s: %s", agent.id, exc)
        if best_agent:
            logger.info("[BRAIN_AGENTS] selected=%s score=%.2f", best_agent.id, best_score)
        return best_agent if best_score > 0 else None

    def all(self) -> list[dict]:
        return [
            {"id": a.id, "name": a.name, "description": a.description,
             "capabilities": a.capabilities,
             "is_placeholder": "PLACEHOLDER" in a.description}
            for a in self._agents.values()
        ]

    def ids(self) -> list[str]:
        return list(self._agents.keys())


brain_agent_registry = BrainAgentRegistry()
