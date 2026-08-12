"""
ProgressReporter — format coordinator workflow progress for voice output.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.coordinator.task_graph import TaskGraph, NodeStatus

logger = logging.getLogger("api.agents.coordinator.progress")


class ProgressReporter:
    """Formats workflow progress into concise voice-friendly strings."""

    def format_progress(self, graph: "TaskGraph") -> str:
        """Return a spoken progress update."""
        from api.agents.coordinator.task_graph import NodeStatus

        total = len(graph.nodes)
        done = sum(1 for n in graph.nodes.values() if n.status == NodeStatus.DONE)
        running_nodes = [n for n in graph.nodes.values() if n.status == NodeStatus.RUNNING]

        if total == 0:
            return "Planning the workflow now."

        if done == total:
            return f"All {total} steps completed."

        pct = int((done / total) * 100)

        if running_nodes:
            current = running_nodes[0]
            return f"Step {done + 1} of {total}: {current.title}. {pct}% done."

        return f"Completed {done} of {total} steps. {pct}% done."

    def format_cancellation(self, graph: "TaskGraph") -> str:
        from api.agents.coordinator.task_graph import NodeStatus

        done = sum(1 for n in graph.nodes.values() if n.status == NodeStatus.DONE)
        return f"Cancelled. Completed {done} of {len(graph.nodes)} steps before stopping."

    def format_completion(
        self, graph: "TaskGraph", output: dict, duration_s: float
    ) -> str:
        from api.agents.coordinator.task_graph import NodeStatus

        done = sum(1 for n in graph.nodes.values() if n.status == NodeStatus.DONE)

        if output.get("project_path"):
            return f"Done. Project created in {duration_s:.0f} seconds."
        if output.get("research_summary"):
            return f"Research complete in {duration_s:.0f} seconds."
        return f"Done. Completed all {done} steps in {duration_s:.0f} seconds."

    def format_pause(self) -> str:
        return "Workflow paused. Say resume when you're ready."

    def format_resume(self) -> str:
        return "Resuming the workflow now."

    def format_node_start(self, node_title: str, agent_type: str) -> str:
        agent_names = {
            "browser": "Browser",
            "coding": "Builder",
            "automation": "System",
            "personality": "Response",
            "verifier": "Verifier",
        }
        name = agent_names.get(agent_type, agent_type.title())
        return f"{name} Agent starting: {node_title}."


progress_reporter = ProgressReporter()
