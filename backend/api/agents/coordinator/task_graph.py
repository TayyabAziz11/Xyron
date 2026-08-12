"""
TaskGraph — directed dependency graph for multi-agent workflows.

Each node represents one agent sub-task. Edges represent dependencies:
a node can only run after all its dependencies are DONE.
Supports sequential, parallel, and approval-gated execution.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("api.agents.coordinator.task_graph")


class NodeStatus(str, Enum):
    PENDING = "pending"
    WAITING = "waiting"          # waiting for dependencies
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    APPROVAL_REQUIRED = "approval_required"


@dataclass
class TaskNode:
    node_id: str
    title: str
    agent_type: str                      # "browser" | "coding" | "automation" | "personality" | "verifier"
    goal: str                            # what this agent should do
    status: NodeStatus = NodeStatus.PENDING
    dependencies: list[str] = field(default_factory=list)   # list of node_ids
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    agent_task_id: Optional[str] = None  # set when launched
    retries: int = 0
    max_retries: int = 1
    can_run_parallel: bool = False
    requires_approval: bool = False
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    # Phase 3 (Planning Engine) — declarative World-State-based success
    # checking + rollback, mirroring AgentStep's equivalent fields. All
    # optional/default-None: every existing graph (LLM or rule-based) is
    # unaffected until a planner explicitly sets one.
    success_condition: Optional[dict[str, Any]] = None
    rollback_goal: Optional[str] = None

    def elapsed_s(self) -> float:
        if self.started_at:
            return (self.completed_at or time.time()) - self.started_at
        return 0.0


class TaskGraph:
    """
    Manages a collection of TaskNodes with dependency tracking.

    Usage:
        graph = TaskGraph(workflow_id="abc", goal="Build clothing website")
        n1 = graph.add_node("Research", "browser", "research fashion websites")
        n2 = graph.add_node("Create project", "coding", "create vite react app", deps=[n1.node_id])
        n3 = graph.add_node("Verify", "verifier", "check site loads", deps=[n2.node_id])

        ready = graph.get_ready_nodes()
        graph.mark_started(n1.node_id, task_id="abc123")
        graph.mark_done(n1.node_id, output={"summary": "..."})
    """

    def __init__(self, workflow_id: str, goal: str) -> None:
        self.workflow_id = workflow_id
        self.goal = goal
        self.nodes: dict[str, TaskNode] = {}
        self.created_at = time.time()

    # ── Node management ────────────────────────────────────────────────────────

    def add_node(
        self,
        title: str,
        agent_type: str,
        goal: str,
        deps: list[str] = None,
        can_run_parallel: bool = False,
        requires_approval: bool = False,
        max_retries: int = 1,
        success_condition: Optional[dict[str, Any]] = None,
        rollback_goal: Optional[str] = None,
    ) -> TaskNode:
        """Add a task node. Returns the created TaskNode."""
        node_id = str(uuid.uuid4())[:8]
        node = TaskNode(
            node_id=node_id,
            title=title,
            agent_type=agent_type,
            goal=goal,
            dependencies=list(deps) if deps else [],
            can_run_parallel=can_run_parallel,
            requires_approval=requires_approval,
            max_retries=max_retries,
            success_condition=success_condition,
            rollback_goal=rollback_goal,
        )
        self.nodes[node_id] = node
        logger.info(
            "[TASK_GRAPH_NODE_CREATED] node_id=%s title=%r agent=%s deps=%s",
            node_id, title, agent_type, node.dependencies,
        )
        return node

    def get_ready_nodes(self) -> list[TaskNode]:
        """Return nodes whose all dependencies are DONE (or SKIPPED) and status is PENDING."""
        ready: list[TaskNode] = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            # All dependency nodes must be in a terminal-success state
            unfinished = [
                dep_id for dep_id in node.dependencies
                if dep_id in self.nodes
                and self.nodes[dep_id].status not in (NodeStatus.DONE, NodeStatus.SKIPPED)
            ]
            if unfinished:
                logger.debug(
                    "[TASK_GRAPH_DEPENDENCY_WAIT] node=%s waiting_on=%s",
                    node.node_id, unfinished,
                )
            else:
                ready.append(node)
        return ready

    def mark_started(self, node_id: str, task_id: str) -> None:
        """Mark node as RUNNING, record agent_task_id and started_at."""
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[TASK_GRAPH] mark_started: unknown node_id=%s", node_id)
            return
        node.status = NodeStatus.RUNNING
        node.agent_task_id = task_id
        node.started_at = time.time()
        logger.info(
            "[TASK_GRAPH_NODE_STARTED] node=%s task_id=%s title=%r",
            node_id, task_id, node.title,
        )

    def mark_done(self, node_id: str, output: dict = None) -> None:
        """Mark node as DONE with output data."""
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[TASK_GRAPH] mark_done: unknown node_id=%s", node_id)
            return
        node.status = NodeStatus.DONE
        node.output_data = output or {}
        node.completed_at = time.time()
        logger.info(
            "[TASK_GRAPH_NODE_DONE] node=%s title=%r elapsed_s=%.1f output_keys=%s",
            node_id, node.title, node.elapsed_s(), list(node.output_data.keys()),
        )

    def mark_failed(self, node_id: str, error: str) -> None:
        """Mark node as FAILED."""
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[TASK_GRAPH] mark_failed: unknown node_id=%s", node_id)
            return
        node.status = NodeStatus.FAILED
        node.error = error
        node.completed_at = time.time()
        logger.warning(
            "[TASK_GRAPH_NODE_FAILED] node=%s title=%r error=%r elapsed_s=%.1f",
            node_id, node.title, error, node.elapsed_s(),
        )

    def mark_skipped(self, node_id: str) -> None:
        """Mark node as SKIPPED (e.g. when a dependency failed and skip is acceptable)."""
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[TASK_GRAPH] mark_skipped: unknown node_id=%s", node_id)
            return
        node.status = NodeStatus.SKIPPED
        node.completed_at = time.time()
        logger.info("[TASK_GRAPH_NODE_SKIPPED] node=%s title=%r", node_id, node.title)

    # ── Graph analysis ─────────────────────────────────────────────────────────

    def get_parallel_groups(self) -> list[list[TaskNode]]:
        """
        Return groups of nodes that can run in parallel.

        A group = nodes at the same depth in the dependency tree (longest
        path from any root). Only nodes with can_run_parallel=True within
        a depth group are combined. Emits [TASK_GRAPH_PARALLEL_START] when
        a group has more than one member.
        """
        depths: dict[str, int] = {}

        def _depth(node_id: str, visiting: set[str]) -> int:
            if node_id in depths:
                return depths[node_id]
            if node_id in visiting:
                return 0   # cycle guard
            visiting = visiting | {node_id}
            node = self.nodes.get(node_id)
            if node is None or not node.dependencies:
                depths[node_id] = 0
                return 0
            valid_deps = [d for d in node.dependencies if d in self.nodes]
            if not valid_deps:
                depths[node_id] = 0
                return 0
            max_dep = max(_depth(d, visiting) for d in valid_deps)
            depths[node_id] = max_dep + 1
            return depths[node_id]

        for nid in self.nodes:
            _depth(nid, set())

        # Group by depth
        by_depth: dict[int, list[TaskNode]] = {}
        for nid, d in depths.items():
            by_depth.setdefault(d, []).append(self.nodes[nid])

        groups: list[list[TaskNode]] = []
        for depth in sorted(by_depth.keys()):
            parallel = [n for n in by_depth[depth] if n.can_run_parallel]
            if len(parallel) > 1:
                logger.info(
                    "[TASK_GRAPH_PARALLEL_START] depth=%d nodes=%d titles=%s",
                    depth, len(parallel), [n.title for n in parallel],
                )
            if parallel:
                groups.append(parallel)
        return groups

    def is_complete(self) -> bool:
        """All nodes are DONE or SKIPPED."""
        return all(
            n.status in (NodeStatus.DONE, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )

    def is_failed(self) -> bool:
        """Any node is FAILED and has exhausted its retry budget."""
        return any(
            n.status == NodeStatus.FAILED and n.retries >= n.max_retries
            for n in self.nodes.values()
        )

    def get_progress_pct(self) -> int:
        """0–100 progress based on done/total nodes."""
        total = len(self.nodes)
        if total == 0:
            return 100
        done = sum(
            1 for n in self.nodes.values()
            if n.status in (NodeStatus.DONE, NodeStatus.SKIPPED)
        )
        return int(done / total * 100)

    def summary(self) -> str:
        """Human-readable workflow status."""
        total = len(self.nodes)
        done = sum(1 for n in self.nodes.values() if n.status == NodeStatus.DONE)
        running = sum(1 for n in self.nodes.values() if n.status == NodeStatus.RUNNING)
        failed = sum(1 for n in self.nodes.values() if n.status == NodeStatus.FAILED)
        skipped = sum(1 for n in self.nodes.values() if n.status == NodeStatus.SKIPPED)
        pct = self.get_progress_pct()
        return (
            f"Workflow '{self.goal[:40]}': {pct}% — "
            f"{done} done / {running} running / {failed} failed / {skipped} skipped / {total} total"
        )

    # ── Output helpers ─────────────────────────────────────────────────────────

    def get_output_for_node(self, node_id: str) -> dict:
        """Get output data of a specific node."""
        node = self.nodes.get(node_id)
        return node.output_data if node else {}

    def get_all_outputs(self) -> dict[str, Any]:
        """Merge all node output_data into one dict for context passing."""
        merged: dict[str, Any] = {}
        for node in self.nodes.values():
            merged.update(node.output_data)
        return merged

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize to dict (for WebSocket progress messages)."""
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "progress_pct": self.get_progress_pct(),
            "is_complete": self.is_complete(),
            "is_failed": self.is_failed(),
            "summary": self.summary(),
            "nodes": [
                {
                    "node_id": n.node_id,
                    "title": n.title,
                    "agent_type": n.agent_type,
                    "goal": n.goal[:120],
                    "status": n.status.value,
                    "dependencies": n.dependencies,
                    "retries": n.retries,
                    "max_retries": n.max_retries,
                    "can_run_parallel": n.can_run_parallel,
                    "requires_approval": n.requires_approval,
                    "started_at": n.started_at,
                    "completed_at": n.completed_at,
                    "elapsed_s": round(n.elapsed_s(), 2),
                    "error": n.error,
                    "output_keys": list(n.output_data.keys()),
                }
                for n in self.nodes.values()
            ],
        }
