"""
ReflectionEngine — post-workflow self-reflection and learning.

After every coordinator workflow:
1. Analyze what worked and what failed
2. Estimate which steps were slowest
3. Save learnings to collaboration_memory
4. Optionally improve next run
"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.coordinator.task_graph import TaskGraph

logger = logging.getLogger("api.agents.coordinator.reflection")


class ReflectionEngine:
    """Analyzes completed workflows and saves learnings."""

    async def reflect(
        self,
        graph: "TaskGraph",
        output: dict[str, Any],
        duration_s: float,
        verified: bool,
    ) -> dict[str, Any]:
        """
        Reflect on a completed workflow. Returns reflection dict.
        Also saves to collaboration_memory.
        """
        logger.info(
            "[REFLECTION_START] workflow=%s duration=%.1fs verified=%s",
            graph.workflow_id,
            duration_s,
            verified,
        )

        # Analyze nodes
        from api.agents.coordinator.task_graph import NodeStatus

        done_nodes = [n for n in graph.nodes.values() if n.status == NodeStatus.DONE]
        failed_nodes = [n for n in graph.nodes.values() if n.status == NodeStatus.FAILED]
        skipped_nodes = [n for n in graph.nodes.values() if n.status == NodeStatus.SKIPPED]

        # Find slowest node
        slowest = None
        slowest_time = 0.0
        for node in done_nodes:
            elapsed = node.elapsed_s()
            if elapsed > slowest_time:
                slowest_time = elapsed
                slowest = node

        reflection = {
            "workflow_id": graph.workflow_id,
            "goal": graph.goal,
            "total_nodes": len(graph.nodes),
            "done_nodes": len(done_nodes),
            "failed_nodes": len(failed_nodes),
            "skipped_nodes": len(skipped_nodes),
            "duration_s": duration_s,
            "verified": verified,
            "slowest_node": slowest.title if slowest else None,
            "slowest_time_s": round(slowest_time, 1),
            "what_worked": [n.title for n in done_nodes],
            "what_failed": [n.title for n in failed_nodes],
            "overall_success": len(failed_nodes) == 0 and verified,
        }

        # Generate notes for learning
        notes_parts = []
        if verified:
            notes_parts.append("Workflow completed successfully.")
        if failed_nodes:
            notes_parts.append(f"Failed steps: {[n.title for n in failed_nodes]}.")
        if slowest and slowest_time > 30:
            notes_parts.append(
                f"Slowest step was '{slowest.title}' at {slowest_time:.0f}s."
            )

        notes = " ".join(notes_parts)

        logger.info(
            "[REFLECTION_SUMMARY] done=%d failed=%d duration=%.1fs verified=%s",
            len(done_nodes),
            len(failed_nodes),
            duration_s,
            verified,
        )

        # Save to collaboration memory
        try:
            from api.agents.coordinator.collaboration_memory import collaboration_memory

            collaboration_memory.save_learning(
                goal_pattern=graph.goal,
                delegation_type=f"nodes_{len(graph.nodes)}",
                duration_s=duration_s,
                verified=verified,
                notes=notes,
            )
            collaboration_memory.write(
                key=f"reflection_{graph.workflow_id}",
                value=reflection,
                persist=True,
            )
        except Exception as e:
            logger.debug("[REFLECTION] memory save failed: %s", e)

        return reflection

    def get_recovery_strategy(self, error: str, attempt: int) -> str:
        """Decide recovery strategy based on error and attempt number."""
        logger.info(
            "[RECOVERY_STRATEGY_SELECTED] error=%r attempt=%d", error[:40], attempt
        )
        if attempt == 0:
            return "retry"
        if attempt == 1:
            return "skip"
        return "fail"


reflection_engine = ReflectionEngine()
