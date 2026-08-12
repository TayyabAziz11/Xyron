"""
LLM-based plan generator for the Phase 3 agent runtime.

Uses gpt-4o-mini via the existing openai_client singleton to produce a
structured, JSON-encoded plan from a natural-language goal.

If the OpenAI client is unavailable (no key, rate-capped, network error)
a 1-step fallback plan is returned so the runtime always gets a usable plan.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from api.agents.agent_types import AgentPlan, AgentStep, AgentType, RiskLevel

logger = logging.getLogger("api.agents.planner")

# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a planning agent for Xyron AI assistant. Given a user goal, produce a
JSON plan that Xyron will execute step by step.

Respond ONLY with valid JSON matching this exact schema — no prose, no markdown
fences:
{
  "goal": "<restatement of goal>",
  "steps": [
    {"index": 0, "description": "<action description>", "risk": "low|medium|high"},
    ...
  ],
  "risk_level": "low|medium|high",
  "requires_confirmation": false,
  "estimated_duration_s": 30
}

Rules:
- steps must be ordered and individually actionable
- risk for any destructive or irreversible action is "high"
- requires_confirmation must be true when risk_level is "high"
- estimated_duration_s is a realistic wall-clock estimate in seconds
- maximum 10 steps; combine trivial sub-actions into one step
- keep descriptions concise (under 80 chars each)
"""

# ── JSON extraction helper ─────────────────────────────────────────────────────


def _extract_json(raw: str) -> dict[str, Any]:
    """
    Extract a JSON object from a raw LLM response.

    Handles:
      - Bare JSON
      - JSON wrapped in ```json ... ``` fences
    Raises json.JSONDecodeError if no valid JSON found.
    """
    # Strip markdown fences if present
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    # Find the first {...} block
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))
    raise json.JSONDecodeError("No JSON object found in LLM response", raw, 0)


# ── Plan builder ───────────────────────────────────────────────────────────────


def _build_plan_from_dict(data: dict[str, Any], goal: str) -> AgentPlan:
    """Construct an AgentPlan from a parsed LLM response dict."""
    raw_steps: list[dict[str, Any]] = data.get("steps", [])
    steps: list[AgentStep] = []
    for raw in raw_steps:
        risk_str = str(raw.get("risk", "low")).lower()
        try:
            risk = RiskLevel(risk_str)
        except ValueError:
            risk = RiskLevel.LOW
        steps.append(
            AgentStep(
                index=int(raw.get("index", len(steps))),
                description=str(raw.get("description", "")).strip(),
                risk=risk,
            )
        )

    risk_level_str = str(data.get("risk_level", "low")).lower()
    try:
        risk_level = RiskLevel(risk_level_str)
    except ValueError:
        risk_level = RiskLevel.LOW

    requires_confirmation = bool(data.get("requires_confirmation", False))
    # Safety: always require confirmation on high-risk plans
    if risk_level == RiskLevel.HIGH:
        requires_confirmation = True

    estimated_duration_s = int(data.get("estimated_duration_s", 30))

    return AgentPlan(
        goal=str(data.get("goal", goal)),
        steps=steps,
        risk_level=risk_level,
        requires_confirmation=requires_confirmation,
        estimated_duration_s=estimated_duration_s,
    )


def _fallback_plan(goal: str) -> AgentPlan:
    """1-step fallback plan used when the LLM is unavailable."""
    logger.warning("[PLANNER] LLM unavailable — returning 1-step fallback plan")
    return AgentPlan(
        goal=goal,
        steps=[
            AgentStep(
                index=0,
                description=f"Complete goal: {goal[:100]}",
                risk=RiskLevel.LOW,
            )
        ],
        risk_level=RiskLevel.LOW,
        requires_confirmation=False,
        estimated_duration_s=30,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


async def generate_plan(
    goal: str,
    agent_type: AgentType,
    context: dict[str, Any] | None = None,
) -> AgentPlan:
    """
    Use gpt-4o-mini to generate a structured AgentPlan for the given goal.

    Falls back to a 1-step plan if:
      - The OpenAI client is not configured or rate-capped
      - The LLM returns malformed JSON
      - Any network or unexpected error occurs

    This function is always safe to await — it never raises.
    """
    if context is None:
        context = {}

    # Build user message — include agent_type and any extra context
    context_str = ""
    if context:
        try:
            context_str = "\n\nContext:\n" + json.dumps(context, indent=2)[:500]
        except (TypeError, ValueError):
            context_str = ""

    user_message = (
        f"Agent type: {agent_type.value}\n"
        f"Goal: {goal}"
        f"{context_str}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        # Import lazily to avoid circular imports at module load time
        from api.services.openai_client import openai_client  # noqa: PLC0415

        # generate() is synchronous — run it in a thread pool to avoid
        # blocking the asyncio event loop
        raw: str | None = await asyncio.to_thread(
            openai_client.generate,
            messages,
            "gpt-4o-mini",
            512,    # max_tokens — plans are verbose
            0.3,    # low temperature for deterministic structure
        )

        if raw is None:
            return _fallback_plan(goal)

        data = _extract_json(raw)
        plan = _build_plan_from_dict(data, goal)

        logger.info(
            "[PLANNER] Generated plan: goal=%r steps=%d risk=%s",
            goal[:60],
            len(plan.steps),
            plan.risk_level.value,
        )
        return plan

    except json.JSONDecodeError as exc:
        logger.warning("[PLANNER] JSON parse error: %s — using fallback", exc)
        return _fallback_plan(goal)

    except ImportError:
        logger.error("[PLANNER] Could not import openai_client — using fallback")
        return _fallback_plan(goal)

    except Exception as exc:  # noqa: BLE001
        logger.exception("[PLANNER] Unexpected error generating plan: %s", exc)
        return _fallback_plan(goal)
