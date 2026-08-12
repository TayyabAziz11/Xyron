"""
AgentRegistry — capability registry for all Phase 3/4 agents.

The Coordinator queries this registry to:
  1. Find which agent can handle a given task type
  2. Know which agents can run in parallel
  3. Enforce safety levels before delegation

Each agent declares its capabilities at registration time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("api.agents.coordinator.registry")


@dataclass
class AgentCapability:
    agent_id: str
    display_name: str
    agent_type: str                          # "browser" | "coding" | "automation" | "personality" | "verifier" | "coordinator"
    capabilities: list[str]                  # natural language capability descriptions
    required_permissions: list[str]          # e.g. ["internet", "filesystem", "subprocess"]
    safety_level: str                        # "low" | "medium" | "high"
    can_run_parallel: bool = False           # can run alongside other agents?
    supports_pause: bool = True
    supports_resume: bool = True
    supports_cancel: bool = True
    max_duration_s: int = 300               # hard timeout


class AgentRegistry:
    """
    Registry of all available agents.

    Usage:
        registry = AgentRegistry()
        agent = registry.find_for_task("research websites")
        # -> AgentCapability(agent_type="browser", ...)
    """

    def __init__(self):
        self._agents: dict[str, AgentCapability] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register all Phase 3 agents at startup."""
        self.register(AgentCapability(
            agent_id="browser",
            display_name="Browser Agent",
            agent_type="browser",
            capabilities=[
                "research topics on the web",
                "compare products and prices",
                "search for information",
                "book flights and hotels (with approval)",
                "fill web forms (with approval)",
                "download files from websites",
                "monitor websites",
                "apply for jobs (with approval)",
                "summarize web pages",
                "find references and examples",
            ],
            required_permissions=["internet", "browser"],
            safety_level="medium",
            can_run_parallel=True,   # can run alongside coding agent
            max_duration_s=300,
        ))
        self.register(AgentCapability(
            agent_id="coding",
            display_name="Coding Builder Agent",
            agent_type="coding",
            capabilities=[
                "create web applications and websites",
                "build React and Next.js projects",
                "build landing pages and portfolios",
                "create dashboards and admin panels",
                "write Python scripts",
                "install npm/pip dependencies",
                "start development servers",
                "open browser previews",
                "fix code errors automatically",
                "initialize git repositories",
            ],
            required_permissions=["filesystem", "subprocess", "internet"],
            safety_level="medium",
            can_run_parallel=False,  # writes files — don't parallelize
            max_duration_s=600,
        ))
        self.register(AgentCapability(
            agent_id="automation",
            display_name="Automation Agent",
            agent_type="automation",
            capabilities=[
                "clean temp files and system junk",
                "organize downloads folder",
                "find and remove duplicate files",
                "find large files",
                "optimize startup applications",
                "scan disk usage",
                "generate cleanup reports",
                "clear browser cache",
                "batch rename files",
            ],
            required_permissions=["filesystem"],
            safety_level="high",     # deletes files (safe trash)
            can_run_parallel=False,  # filesystem operations
            max_duration_s=300,
        ))
        self.register(AgentCapability(
            agent_id="personality",
            display_name="Personality Engine",
            agent_type="personality",
            capabilities=[
                "polish and improve response text",
                "apply personality modes (Jarvis, Professional, etc.)",
                "generate natural voice responses",
            ],
            required_permissions=[],
            safety_level="low",
            can_run_parallel=True,
            max_duration_s=5,
        ))
        self.register(AgentCapability(
            agent_id="verifier",
            display_name="Coordinator Verifier",
            agent_type="verifier",
            capabilities=[
                "verify website loaded correctly",
                "confirm project was created",
                "check file exists",
                "verify cleanup was successful",
            ],
            required_permissions=["filesystem", "internet"],
            safety_level="low",
            can_run_parallel=True,
            max_duration_s=30,
        ))
        logger.info("[AGENT_REGISTRY_READY] agents=%d", len(self._agents))

    def register(self, capability: AgentCapability) -> None:
        self._agents[capability.agent_id] = capability

    def find_for_task(self, task_description: str) -> Optional[AgentCapability]:
        """Find the best agent for a task description (simple keyword match)."""
        desc_lower = task_description.lower()
        # Score each agent by capability keyword overlap
        scores: dict[str, float] = {}
        for aid, cap in self._agents.items():
            score = 0.0
            for capability in cap.capabilities:
                cap_words = set(capability.lower().split())
                desc_words = set(desc_lower.split())
                overlap = len(cap_words & desc_words)
                score += overlap
            scores[aid] = score

        if not scores:
            return None

        best_id = max(scores, key=lambda k: scores[k])
        if scores[best_id] > 0:
            cap = self._agents[best_id]
            logger.info(
                "[AGENT_CAPABILITY_MATCH] task=%r agent=%s score=%.1f",
                task_description[:40],
                best_id,
                scores[best_id],
            )
            logger.info("[AGENT_SELECTED_FOR_TASK] agent=%s", best_id)
            return cap
        return None

    def get(self, agent_id: str) -> Optional[AgentCapability]:
        return self._agents.get(agent_id)

    def all(self) -> list[AgentCapability]:
        return list(self._agents.values())

    def capabilities_summary(self) -> str:
        """Human-readable summary for LLM prompts."""
        lines = []
        for cap in self._agents.values():
            lines.append(
                f"- {cap.display_name} ({cap.agent_type}): {', '.join(cap.capabilities[:3])}"
            )
        return "\n".join(lines)


agent_registry = AgentRegistry()
