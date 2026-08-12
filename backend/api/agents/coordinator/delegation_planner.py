"""
DelegationPlanner — analyze a goal and produce a TaskGraph.

Two modes:
  1. LLM-based (gpt-4o-mini) — rich plan with specific steps
  2. Rule-based fallback    — keyword pattern matching, offline-safe

The planner does NOT execute anything — it just produces a TaskGraph.
The CoordinatorAgent executes it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from api.agents.coordinator.task_graph import TaskGraph
from api.services.agent_intent_detector import classify_domains

logger = logging.getLogger("api.agents.coordinator.planner")


# ── Goal analysis ──────────────────────────────────────────────────────────────


class GoalAnalysis:
    """Result of analyzing a goal before planning."""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        # Canonical classification — same predicate agent_intent_detector
        # used upstream to decide DIRECT_AGENT vs COORDINATED_WORKFLOW. By
        # the time a goal reaches DelegationPlanner from the live voice
        # pipeline it's already been classified as multi-domain; this is
        # kept for other callers (tests, direct DelegationPlanner use).
        self.needs_browser, self.needs_coding, self.needs_automation = classify_domains(goal)
        self.needs_personality = True   # always polish final response

        # Booking/high-risk browser goals require approval — route via multi-agent path
        self.needs_booking = bool(re.search(
            r"\b(book|flight|flights|ticket|tickets|fly|cheapest\s+flight|cheap\s+flight)\b",
            goal, re.I,
        ))

        # Phase 4.11: booking goals used to force the multi-agent LLM
        # planning path (up to a 15s timeout, worse under quota
        # exhaustion) before the browser agent — and therefore Chrome —
        # ever started. That routing existed to gate booking behind
        # approval, but the approval gate now lives inside
        # flight_search_agent.request_decision() itself (Phase 4.7+), so
        # this override was pure latency with no remaining safety
        # purpose. needs_booking is still computed/available for
        # narration/context, it just no longer forces the slow path.
        self.is_multi_agent = (
            sum([self.needs_browser, self.needs_coding, self.needs_automation]) > 1
        )

        # Special combined case: coding-heavy goals benefit from browser research first
        if self.needs_coding and not self.needs_browser:
            domain_match = bool(re.search(
                r"\b(clothing|fashion|ecommerce|e-commerce|portfolio|landing|"
                r"dashboard|admin|store|shop|blog)\b",
                goal, re.I,
            ))
            if domain_match:
                self.needs_browser = True
                self.is_multi_agent = True


# ── LLM prompt ────────────────────────────────────────────────────────────────

_PLAN_SYSTEM = """\
You are a multi-agent workflow planner for Xyron AI. Given a high-level user goal, \
produce a structured JSON task graph that specialist agents will execute.

You may be given a "World State" block describing what the user is currently
looking at (current app, browser page/product, open document, selection).
Use it to resolve vague references in the goal ("this", "it", "that page")
— e.g. if the goal is "review this product" and World State shows a
current_product, the node goal should reference that specific product by
name rather than staying vague.

Return ONLY valid JSON (no prose, no markdown fences) matching:
{
  "nodes": [
    {
      "title": "<short action title>",
      "agent": "browser|coding|automation|personality|verifier",
      "goal": "<detailed instruction for the agent>",
      "depends_on": [],
      "parallel": false,
      "approval": false,
      "success_condition": {"field": "<world state field, e.g. current_document>", "op": "changed|not_none|equals|contains", "value": "<only for equals/contains>"},
      "rollback_goal": "<optional: what to do if this step fails and can't be retried — omit if nothing needs undoing>"
    }
  ]
}

Rules:
- Use "browser" for research, web search, booking, form filling
- Use "coding" to build projects, write files, run dev servers
- Use "automation" for PC cleanup, file organization, system tasks
- Use "verifier" to confirm results (check URL loads, file exists, etc.)
- Use "personality" as the final node to produce a polished user-facing response
- depends_on: list of 0-based indices of prerequisite nodes
- parallel: true only if this node can run concurrently with siblings
- approval: true for irreversible actions (purchase, deletion, form submit)
- success_condition and rollback_goal are both OPTIONAL — omit entirely for
  steps where "the sub-agent reported success" is already good enough
  (most steps). Only add success_condition when there's a concrete World
  State field the step should visibly change. Only add rollback_goal for
  steps with a real side effect worth undoing (e.g. a created file/folder)
- Max 7 nodes; keep goals concrete and actionable
"""


def _extract_json(raw: str) -> dict[str, Any]:
    """Strip markdown fences and extract first JSON object."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace:
        return json.loads(brace.group(0))
    raise json.JSONDecodeError("No JSON found", raw, 0)


# ── Planner ────────────────────────────────────────────────────────────────────


class DelegationPlanner:
    """
    Plans multi-agent workflows.

    Strategy:
    1. Quick rule-based analysis to decide single vs multi-agent
    2. If single-agent → simple 1-node graph, no LLM needed
    3. If multi-agent → try LLM for rich task graph, fall back to rule-based
    """

    async def plan(self, goal: str, context: dict = {}) -> tuple[TaskGraph, str]:
        """
        Analyze goal and create a TaskGraph.

        Returns: (TaskGraph, delegation_type)
        delegation_type: "single" | "multi"
        """
        analysis = GoalAnalysis(goal)

        if not analysis.is_multi_agent:
            logger.info("[DELEGATION_SINGLE_AGENT] goal=%r", goal[:60])
            logger.info("[DELEGATION_REASON] Only one agent type required")
            graph = await self._single_agent_plan(goal, analysis)
            return graph, "single"

        logger.info("[DELEGATION_MULTI_AGENT] goal=%r", goal[:60])
        logger.info(
            "[DELEGATION_REASON] browser=%s coding=%s automation=%s",
            analysis.needs_browser, analysis.needs_coding, analysis.needs_automation,
        )

        # Try LLM first, fall back to rule-based
        graph = None
        try:
            graph = await asyncio.wait_for(
                self._multi_agent_plan_llm(goal, analysis, context), timeout=15.0
            )
            logger.info("[DELEGATION_MULTI_AGENT] LLM plan created nodes=%d", len(graph.nodes))
        except Exception as exc:
            logger.warning(
                "[DELEGATION_MULTI_AGENT] LLM plan failed (%s), using rule-based fallback", exc
            )

        if graph is None or len(graph.nodes) == 0:
            graph = self._multi_agent_plan_rules(goal, analysis)
            logger.info(
                "[DELEGATION_MULTI_AGENT] rule-based plan nodes=%d", len(graph.nodes)
            )

        return graph, "multi"

    # ── Single-agent plan ──────────────────────────────────────────────────────

    async def _single_agent_plan(self, goal: str, analysis: GoalAnalysis) -> TaskGraph:
        """Create a simple single-node graph for goals that only need one agent."""
        wid = str(uuid.uuid4())[:8]
        graph = TaskGraph(workflow_id=wid, goal=goal)

        if analysis.needs_automation:
            graph.add_node("Automate", "automation", goal)
        elif analysis.needs_browser:
            graph.add_node("Research", "browser", goal)
        elif analysis.needs_coding:
            graph.add_node("Build", "coding", goal)
        else:
            # Generic fallback
            graph.add_node("Execute", "automation", goal)

        return graph

    # ── LLM plan ──────────────────────────────────────────────────────────────

    async def _multi_agent_plan_llm(
        self, goal: str, analysis: GoalAnalysis, context: dict | None = None,
    ) -> TaskGraph:
        """
        Use gpt-4o-mini to create a rich multi-node task graph.

        LLM response JSON:
        {
          "nodes": [
            {"title": "...", "agent": "browser|coding|automation|personality|verifier",
             "goal": "...", "depends_on": [], "parallel": false, "approval": false,
             "success_condition": {...}, "rollback_goal": "..."},
            ...
          ]
        }
        """
        from api.services.openai_client import openai_client  # noqa: PLC0415

        user_content = f"Goal: {goal}"
        ws_ctx = (context or {}).get("world_state")
        if ws_ctx:
            try:
                relevant = {k: v for k, v in ws_ctx.items() if v}  # drop empty/None noise
                if relevant:
                    user_content += "\n\nWorld State:\n" + json.dumps(relevant, indent=2, default=str)[:600]
            except (TypeError, ValueError):
                pass

        messages = [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user",   "content": user_content},
        ]
        raw = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: openai_client.generate(messages, model="gpt-4o-mini", max_tokens=600),
        )
        if not raw:
            raise ValueError("OpenAI returned empty response")
        data = _extract_json(raw)
        return self._build_graph_from_llm(goal, data)

    def _build_graph_from_llm(self, goal: str, data: dict[str, Any]) -> TaskGraph:
        """Convert LLM response dict to a TaskGraph."""
        wid = str(uuid.uuid4())[:8]
        graph = TaskGraph(workflow_id=wid, goal=goal)

        raw_nodes: list[dict] = data.get("nodes", [])
        # First pass: create all nodes, collect node_ids in order
        node_ids: list[str] = []
        for raw in raw_nodes:
            success_condition = raw.get("success_condition")
            if not isinstance(success_condition, dict) or not success_condition.get("field"):
                success_condition = None  # tolerate a malformed/hallucinated shape — never crash the plan
            rollback_goal = raw.get("rollback_goal")
            if not isinstance(rollback_goal, str) or not rollback_goal.strip():
                rollback_goal = None

            node = graph.add_node(
                title=str(raw.get("title", "Step")),
                agent_type=self._agent_type_from_str(str(raw.get("agent", "automation"))),
                goal=str(raw.get("goal", goal)),
                deps=[],   # wire deps in second pass
                can_run_parallel=bool(raw.get("parallel", False)),
                requires_approval=bool(raw.get("approval", False)),
                max_retries=1,
                success_condition=success_condition,
                rollback_goal=rollback_goal,
            )
            node_ids.append(node.node_id)

        # Second pass: wire dependencies
        for idx, raw in enumerate(raw_nodes):
            dep_indices: list[int] = raw.get("depends_on", [])
            nid = node_ids[idx]
            dep_node_ids = [
                node_ids[i] for i in dep_indices
                if isinstance(i, int) and 0 <= i < len(node_ids)
            ]
            graph.nodes[nid].dependencies = dep_node_ids

        return graph

    # ── Rule-based plan ────────────────────────────────────────────────────────

    def _multi_agent_plan_rules(self, goal: str, analysis: GoalAnalysis) -> TaskGraph:
        """
        Rule-based multi-agent plan (offline fallback).

        Known workflow patterns:
          clothing/fashion/ecommerce/shop website
            → browser(research) + coding(create) + coding(serve) + verifier + personality
          portfolio / landing / dashboard / blog website
            → coding(create) + verifier + personality
          build any website (generic)
            → browser(research) + coding(build) + verifier + personality
          research / summarize
            → browser(research) + personality
          book flight / find cheapest
            → browser(book, approval=True) + personality
          clean PC / temp / junk / cache / organize
            → automation(clean) + personality
        """
        wid = str(uuid.uuid4())[:8]
        goal_lower = goal.lower()

        # ── Pattern 1: Clothing / fashion / ecommerce ──────────────────────
        if re.search(
            r"\b(clothing|fashion|ecommerce|e-commerce|clothes|apparel|shop|store)\b",
            goal_lower,
        ) and re.search(r"\b(website|site|web|build|create|make)\b", goal_lower):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Research designs",
                "browser",
                "Research modern clothing website designs and collect 3 reference URLs",
            )
            n2 = graph.add_node(
                "Create project",
                "coding",
                "Create a Vite React Tailwind clothing website project",
                deps=[n1.node_id],
            )
            n3 = graph.add_node(
                "Start dev server",
                "coding",
                "Start the dev server and open browser preview",
                deps=[n2.node_id],
            )
            n4 = graph.add_node(
                "Verify website",
                "verifier",
                "Verify the website loads at localhost:5173",
                deps=[n3.node_id],
            )
            graph.add_node(
                "Final response",
                "personality",
                "Produce a polished completion message",
                deps=[n4.node_id],
            )
            return graph

        # ── Pattern 2: Portfolio / landing / dashboard ──────────────────────
        if re.search(
            r"\b(portfolio|landing\s+page|dashboard|admin\s+panel|blog)\b",
            goal_lower,
        ) and re.search(r"\b(website|site|web|build|create|make)\b", goal_lower):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Build project",
                "coding",
                f"Create and set up a complete {goal}",
            )
            n2 = graph.add_node(
                "Verify build",
                "verifier",
                "Verify the project built successfully and dev server is running",
                deps=[n1.node_id],
            )
            graph.add_node(
                "Final response",
                "personality",
                "Produce a polished completion message",
                deps=[n2.node_id],
            )
            return graph

        # ── Pattern 3: Generic website build ───────────────────────────────
        if (
            re.search(r"\b(build|create|make|develop)\b", goal_lower)
            and re.search(r"\b(website|web\s+app|site|app)\b", goal_lower)
        ):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Research references",
                "browser",
                f"Search for design references and best practices for: {goal}",
            )
            n2 = graph.add_node(
                "Build project",
                "coding",
                f"Build the project: {goal}",
                deps=[n1.node_id],
            )
            n3 = graph.add_node(
                "Verify result",
                "verifier",
                "Verify the build is successful and preview is available",
                deps=[n2.node_id],
            )
            graph.add_node(
                "Final response",
                "personality",
                "Produce a polished completion message",
                deps=[n3.node_id],
            )
            return graph

        # ── Pattern 4: Flight booking / cheapest price ──────────────────────
        # Searching and comparing flights is read-only and must NOT be gated —
        # only an actual booking/payment step would require approval, and no
        # such step exists in this graph (BrowserAgent only searches/presents
        # options; see browser_purchase_guard.py). Approval-before-booking is
        # surfaced as a non-blocking notice once results are found (see
        # coordinator_agent._run_agent_node).
        if re.search(
            r"\b(flight|flights|cheapest|cheap\s+flight|book\s+flight|ticket|tickets|fly\b)\b",
            goal_lower,
        ):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Find flight",
                "browser",
                f"Search for the cheapest available option: {goal}",
            )
            graph.add_node(
                "Final response",
                "personality",
                "Summarise the flight options found",
                deps=[n1.node_id],
            )
            return graph

        # ── Pattern 5: Research / AI / summarize ───────────────────────────
        if re.search(
            r"\b(research|find\s+out|what\s+is|explain|summarize|compare|"
            r"look\s+up|search\s+for|information\s+about|ai\s+agent)\b",
            goal_lower,
        ):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Research",
                "browser",
                f"Research and summarize: {goal}",
            )
            graph.add_node(
                "Final response",
                "personality",
                "Produce a concise summary of the research findings",
                deps=[n1.node_id],
            )
            return graph

        # ── Pattern 6: PC cleanup / automation ─────────────────────────────
        if re.search(
            r"\b(clean|organize|junk|temp|cache|startup|duplicate|"
            r"large\s+files?|disk\s+space|free\s+up)\b",
            goal_lower,
        ):
            graph = TaskGraph(workflow_id=wid, goal=goal)

            n1 = graph.add_node(
                "Clean system",
                "automation",
                goal,
            )
            graph.add_node(
                "Final response",
                "personality",
                "Summarize what was cleaned and how much space was freed",
                deps=[n1.node_id],
            )
            return graph

        # ── Default: browser + coding + verify ────────────────────────────
        graph = TaskGraph(workflow_id=wid, goal=goal)
        n1 = graph.add_node("Research", "browser", f"Research: {goal}")
        n2 = graph.add_node("Execute", "coding", goal, deps=[n1.node_id])
        n3 = graph.add_node("Verify", "verifier", "Verify task completed", deps=[n2.node_id])
        graph.add_node(
            "Final response",
            "personality",
            "Produce a polished completion message",
            deps=[n3.node_id],
        )
        return graph

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _agent_type_from_str(self, s: str) -> str:
        """Canonicalize agent type strings."""
        s = s.strip().lower()
        _map = {
            "browser": "browser",
            "web": "browser",
            "research": "browser",
            "coding": "coding",
            "code": "coding",
            "coder": "coding",
            "build": "coding",
            "automation": "automation",
            "auto": "automation",
            "cleaner": "automation",
            "system": "automation",
            "personality": "personality",
            "polish": "personality",
            "verifier": "verifier",
            "verify": "verifier",
            "checker": "verifier",
        }
        return _map.get(s, "automation")


# ── Module-level singleton ────────────────────────────────────────────────────

delegation_planner = DelegationPlanner()
