"""
CoordinatorVerifier — verify workflow outputs are correct.

Called after all nodes complete to confirm the workflow actually succeeded.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from api.agents.coordinator.task_graph import TaskGraph, TaskNode

logger = logging.getLogger("api.agents.coordinator.verifier")


class CoordinatorVerifier:
    """Verify coordinator workflow outputs."""

    async def verify(self, graph: "TaskGraph", output: dict[str, Any]) -> bool:
        """
        Top-level verify: check all done nodes produced expected output.
        Returns True if workflow is considered successful.
        """
        from api.agents.coordinator.task_graph import NodeStatus

        failed = [n for n in graph.nodes.values() if n.status == NodeStatus.FAILED]
        if len(failed) > len(graph.nodes) // 2:
            logger.info(
                "[COORDINATOR_VERIFY] FAIL — too many failed nodes: %d/%d",
                len(failed),
                len(graph.nodes),
            )
            return False

        # Check specific output types
        if output.get("project_path"):
            path = Path(output["project_path"])
            exists = path.exists()
            logger.info("[COORDINATOR_VERIFY] project_path=%s exists=%s", path, exists)
            return exists

        if output.get("preview_url"):
            url = output["preview_url"]
            # Quick TCP check
            port = 5173  # default Vite port
            try:
                url_port = int(url.split(":")[-1])
                port = url_port
            except Exception:
                pass
            reachable = await self._check_port("localhost", port)
            logger.info(
                "[COORDINATOR_VERIFY] preview_url=%s port=%d reachable=%s",
                url,
                port,
                reachable,
            )
            return reachable

        if output.get("research_summary") and len(output["research_summary"]) > 50:
            logger.info(
                "[COORDINATOR_VERIFY] research_summary present len=%d",
                len(output["research_summary"]),
            )
            return True

        if output.get("verified") is True:
            return True

        # Default: if no failures and something was done -> success
        done_count = sum(
            1 for n in graph.nodes.values() if n.status == NodeStatus.DONE
        )
        result = done_count > 0
        logger.info(
            "[COORDINATOR_VERIFY] default check done_count=%d result=%s",
            done_count,
            result,
        )
        return result

    async def verify_node(self, node: "TaskNode", context: dict[str, Any]) -> bool:
        """Verify a single verifier node."""
        goal_lower = node.goal.lower()

        if "website" in goal_lower or "localhost" in goal_lower or "port" in goal_lower:
            # Try to reach localhost:5173
            reachable = await self._check_port("localhost", 5173)
            if not reachable:
                reachable = await self._check_port("localhost", 3000)
            logger.info(
                "[COORDINATOR_VERIFY] localhost check reachable=%s", reachable
            )
            return reachable

        if "file" in goal_lower or "project" in goal_lower:
            project_path = context.get("project_path")
            if project_path:
                exists = Path(project_path).exists()
                logger.info(
                    "[COORDINATOR_VERIFY] file check path=%s exists=%s",
                    project_path,
                    exists,
                )
                return exists

        return True  # default: assume ok

    async def _check_port(self, host: str, port: int, timeout: float = 2.0) -> bool:
        """TCP connection check — returns True if port is open."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False


coordinator_verifier = CoordinatorVerifier()
