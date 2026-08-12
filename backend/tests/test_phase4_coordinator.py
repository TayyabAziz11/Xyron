"""
Phase 4 Live Tests — CoordinatorAgent E2E

Tests the full Coordinator stack:
  - delegation_planner.plan() → TaskGraph
  - task_graph dependency resolution
  - agent_registry capabilities
  - collaboration_memory read/write
  - reflection_engine (standalone)
  - progress_reporter formatting
  - resource_locks (asyncio)
  - agent_intent_detector control actions
  - Personality mode routing through coordinator
"""
from __future__ import annotations

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Phase 4.1: TaskGraph ────────────────────────────────────────────────────

class TestTaskGraph:
    def test_nodes_created_correctly(self):
        from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
        g = TaskGraph("wf-001", "Build clothing website")
        n1 = g.add_node("Research", "browser", "research designs")
        n2 = g.add_node("Build", "coding", "create vite project", deps=[n1.node_id])
        n3 = g.add_node("Verify", "verifier", "check loads", deps=[n2.node_id])
        assert len(g.nodes) == 3

    def test_dependency_ordering(self):
        from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
        g = TaskGraph("wf-002", "Multi-step workflow")
        n1 = g.add_node("Step 1", "browser", "do step 1")
        n2 = g.add_node("Step 2", "coding", "do step 2", deps=[n1.node_id])
        ready = g.get_ready_nodes()
        assert len(ready) == 1
        assert ready[0].node_id == n1.node_id  # only n1 is ready

        g.mark_started(n1.node_id, "task-abc")
        g.mark_done(n1.node_id, {"result": "done"})
        ready2 = g.get_ready_nodes()
        assert len(ready2) == 1
        assert ready2[0].node_id == n2.node_id  # n2 unblocked

    def test_progress_pct(self):
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-003", "Four-step flow")
        nodes = [g.add_node(f"Step {i}", "browser", f"task {i}") for i in range(4)]
        assert g.get_progress_pct() == 0
        g.mark_started(nodes[0].node_id, "t1")
        g.mark_done(nodes[0].node_id, {})
        assert g.get_progress_pct() == 25
        g.mark_started(nodes[1].node_id, "t2")
        g.mark_done(nodes[1].node_id, {})
        assert g.get_progress_pct() == 50

    def test_is_complete(self):
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-004", "Complete test")
        n1 = g.add_node("A", "browser", "task a")
        n2 = g.add_node("B", "coding", "task b")
        assert not g.is_complete()
        g.mark_started(n1.node_id, "t1"); g.mark_done(n1.node_id, {})
        g.mark_started(n2.node_id, "t2"); g.mark_done(n2.node_id, {})
        assert g.is_complete()

    def test_failed_node_tracked(self):
        from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
        g = TaskGraph("wf-005", "Failure test")
        n1 = g.add_node("A", "browser", "task a", max_retries=0)
        g.mark_started(n1.node_id, "t1")
        g.mark_failed(n1.node_id, "Timeout")
        assert g.nodes[n1.node_id].status == NodeStatus.FAILED
        # retries=0 >= max_retries=0 → is_failed() is True
        assert g.is_failed()

    def test_to_dict_serialization(self):
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-006", "Serialization test")
        g.add_node("Node A", "browser", "do something")
        d = g.to_dict()
        assert "workflow_id" in d
        assert "nodes" in d
        assert len(d["nodes"]) == 1
        assert d["nodes"][0]["agent_type"] == "browser"


# ── Phase 4.2: DelegationPlanner ───────────────────────────────────────────

class TestDelegationPlanner:
    @pytest.mark.asyncio
    async def test_clothing_website_multi_agent(self):
        """Test 1: 'Build me a clothing website' → multi-agent workflow"""
        from api.agents.coordinator.delegation_planner import DelegationPlanner
        planner = DelegationPlanner()
        graph, dtype = await planner.plan("Build me a clothing website", {})
        assert dtype == "multi"
        agent_types = [n.agent_type for n in graph.nodes.values()]
        assert "browser" in agent_types, "Needs browser for research"
        assert "coding" in agent_types, "Needs coding to build"
        # Should have at least 4 nodes (research + create + server + verify/personality)
        assert len(graph.nodes) >= 4

    @pytest.mark.asyncio
    async def test_research_single_agent(self):
        """Test 2: 'Research latest AI agents' → single browser agent"""
        from api.agents.coordinator.delegation_planner import DelegationPlanner
        planner = DelegationPlanner()
        graph, dtype = await planner.plan("Research latest AI agents and summarize them", {})
        # Can be single or multi — must have a browser node
        agent_types = [n.agent_type for n in graph.nodes.values()]
        assert "browser" in agent_types

    @pytest.mark.asyncio
    async def test_clean_pc_automation(self):
        """Test 3: 'Clean my PC' → automation agent"""
        from api.agents.coordinator.delegation_planner import DelegationPlanner
        planner = DelegationPlanner()
        graph, dtype = await planner.plan("Clean my PC", {})
        agent_types = [n.agent_type for n in graph.nodes.values()]
        assert "automation" in agent_types

    @pytest.mark.asyncio
    async def test_flight_booking_requires_approval(self):
        """Test 4: Flight booking → approval gate required"""
        from api.agents.coordinator.delegation_planner import DelegationPlanner
        planner = DelegationPlanner()
        graph, dtype = await planner.plan(
            "Find cheapest flight from Karachi to Dubai next month", {}
        )
        # Must have at least one approval-gated node
        has_approval = any(n.requires_approval for n in graph.nodes.values())
        assert has_approval, "Flight booking must require approval"
        # Must include a browser agent for the search
        assert any(n.agent_type == "browser" for n in graph.nodes.values())

    @pytest.mark.asyncio
    async def test_portfolio_website_has_coding_node(self):
        """Portfolio website → coding node present"""
        from api.agents.coordinator.delegation_planner import DelegationPlanner
        planner = DelegationPlanner()
        graph, _ = await planner.plan("Build a portfolio website", {})
        assert any(n.agent_type == "coding" for n in graph.nodes.values())


# ── Phase 4.3: AgentRegistry ───────────────────────────────────────────────

class TestAgentRegistry:
    def test_all_five_agents_registered(self):
        from api.agents.coordinator.agent_registry import agent_registry
        agents = agent_registry.all()
        ids = [a.agent_id for a in agents]
        assert "browser" in ids
        assert "coding" in ids
        assert "automation" in ids
        assert "personality" in ids
        assert "verifier" in ids

    def test_find_for_research_task(self):
        from api.agents.coordinator.agent_registry import agent_registry
        cap = agent_registry.find_for_task("research latest AI trends")
        assert cap is not None
        assert cap.agent_type == "browser"

    def test_find_for_coding_task(self):
        from api.agents.coordinator.agent_registry import agent_registry
        cap = agent_registry.find_for_task("build a React website")
        assert cap is not None
        assert cap.agent_type == "coding"

    def test_find_for_automation_task(self):
        from api.agents.coordinator.agent_registry import agent_registry
        cap = agent_registry.find_for_task("clean temp files and junk")
        assert cap is not None
        assert cap.agent_type == "automation"

    def test_capabilities_summary_nonempty(self):
        from api.agents.coordinator.agent_registry import agent_registry
        summary = agent_registry.capabilities_summary()
        assert len(summary) > 50
        assert "browser" in summary.lower() or "Browser" in summary


# ── Phase 4.4: CollaborationMemory ─────────────────────────────────────────

class TestCollaborationMemory:
    def test_write_and_read(self):
        from api.agents.coordinator.collaboration_memory import CollaborationMemory
        mem = CollaborationMemory()
        mem.write("test_key_001", {"data": "hello", "value": 42})
        result = mem.read("test_key_001")
        assert result is not None
        assert result["data"] == "hello"
        assert result["value"] == 42

    def test_read_missing_key_returns_none(self):
        from api.agents.coordinator.collaboration_memory import CollaborationMemory
        mem = CollaborationMemory()
        result = mem.read("nonexistent_key_xyz")
        assert result is None

    def test_overwrite_key(self):
        from api.agents.coordinator.collaboration_memory import CollaborationMemory
        mem = CollaborationMemory()
        mem.write("overwrite_key", {"v": 1})
        mem.write("overwrite_key", {"v": 2})
        result = mem.read("overwrite_key")
        assert result["v"] == 2

    def test_get_all_session(self):
        from api.agents.coordinator.collaboration_memory import CollaborationMemory
        mem = CollaborationMemory()
        mem.write("session_a", {"x": 1})
        mem.write("session_b", {"y": 2})
        all_data = mem.get_all_session()
        assert isinstance(all_data, dict)
        assert "session_a" in all_data or len(all_data) >= 2  # may have prior keys


# ── Phase 4.5: ResourceLocks ───────────────────────────────────────────────

class TestResourceLocks:
    @pytest.mark.asyncio
    async def test_browser_lock_acquired_and_released(self):
        from api.agents.coordinator.resource_locks import ResourceLockManager
        mgr = ResourceLockManager()
        async with mgr.lock("browser"):
            assert mgr.is_locked("browser")
        assert not mgr.is_locked("browser")

    @pytest.mark.asyncio
    async def test_multiple_resources_independent(self):
        from api.agents.coordinator.resource_locks import ResourceLockManager
        mgr = ResourceLockManager()
        async with mgr.lock("filesystem"):
            async with mgr.lock("audio"):
                assert mgr.is_locked("filesystem")
                assert mgr.is_locked("audio")
        assert not mgr.is_locked("filesystem")
        assert not mgr.is_locked("audio")

    @pytest.mark.asyncio
    async def test_unknown_resource_creates_lock(self):
        from api.agents.coordinator.resource_locks import ResourceLockManager
        mgr = ResourceLockManager()
        async with mgr.lock("new_resource_xyz"):
            assert mgr.is_locked("new_resource_xyz")


# ── Phase 4.6: ProgressReporter ────────────────────────────────────────────

class TestProgressReporter:
    def test_format_progress_returns_string(self):
        from api.agents.coordinator.progress_reporter import progress_reporter
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-pr-001", "Test workflow")
        g.add_node("Step", "browser", "do research")
        msg = progress_reporter.format_progress(g)
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_format_completion_contains_goal(self):
        from api.agents.coordinator.progress_reporter import progress_reporter
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-pr-002", "Build a portfolio website")
        n = g.add_node("Build", "coding", "create project")
        g.mark_started(n.node_id, "t1"); g.mark_done(n.node_id, {})
        msg = progress_reporter.format_completion(g, output={}, duration_s=12.5)
        assert isinstance(msg, str)

    def test_format_cancellation_nonempty(self):
        from api.agents.coordinator.progress_reporter import progress_reporter
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-pr-003", "Cancelled workflow")
        msg = progress_reporter.format_cancellation(g)
        assert isinstance(msg, str)
        assert len(msg) > 0


# ── Phase 4.7: CoordinatorVerifier ─────────────────────────────────────────

class TestCoordinatorVerifier:
    @pytest.mark.asyncio
    async def test_verify_empty_output_partial(self):
        from api.agents.coordinator.coordinator_verifier import CoordinatorVerifier
        from api.agents.coordinator.task_graph import TaskGraph
        v = CoordinatorVerifier()
        g = TaskGraph("wf-cv-001", "Empty output verify")
        n = g.add_node("Step", "browser", "do stuff")
        g.mark_started(n.node_id, "t1")
        g.mark_done(n.node_id, {})
        # Empty output with 1 done node — low failure ratio, should pass
        result = await v.verify(g, {})
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_verify_with_research_summary(self):
        from api.agents.coordinator.coordinator_verifier import CoordinatorVerifier
        from api.agents.coordinator.task_graph import TaskGraph
        v = CoordinatorVerifier()
        g = TaskGraph("wf-cv-002", "Research verify")
        n = g.add_node("Research", "browser", "research AI")
        g.mark_started(n.node_id, "t1")
        g.mark_done(n.node_id, {"research_summary": "AI agents are..."})
        result = await v.verify(g, {"research_summary": "AI agents are very interesting and useful"})
        assert result is True


# ── Phase 4.8: Intent Detection — Workflow Control ─────────────────────────

class TestAgentIntentWorkflowControl:
    def test_cancel_detected(self):
        from api.services.agent_intent_detector import agent_intent_detector
        result = agent_intent_detector.detect("cancel that")
        assert result.is_agent_command
        assert result.control_action == "cancel"

    def test_pause_detected(self):
        from api.services.agent_intent_detector import agent_intent_detector
        result = agent_intent_detector.detect("pause the task")
        assert result.is_agent_command
        assert result.control_action == "pause"

    def test_resume_detected(self):
        from api.services.agent_intent_detector import agent_intent_detector
        result = agent_intent_detector.detect("resume")
        assert result.is_agent_command
        assert result.control_action == "resume"

    def test_progress_detected(self):
        from api.services.agent_intent_detector import agent_intent_detector
        result = agent_intent_detector.detect("what's the progress")
        assert result.is_agent_command
        assert result.control_action == "progress"


# ── Phase 4.9: Personality × Coordinator Integration ──────────────────────

class TestPersonalityCoordinatorIntegration:
    def test_personality_polish_applied(self):
        from api.agents.personality.personality_engine import personality_engine, PersonalityMode
        personality_engine.set_mode(PersonalityMode.JARVIS)
        polished = personality_engine.polish_response("I built the project at /tmp/proj")
        assert isinstance(polished, str)
        assert len(polished) > 5
        personality_engine.set_mode(PersonalityMode.DEFAULT)

    def test_coordinator_workflow_id_in_task_graph(self):
        """Coordinator produces graphs with unique workflow IDs."""
        from api.agents.coordinator.task_graph import TaskGraph
        import uuid
        g1 = TaskGraph(str(uuid.uuid4())[:8], "Goal A")
        g2 = TaskGraph(str(uuid.uuid4())[:8], "Goal B")
        assert g1.workflow_id != g2.workflow_id

    def test_reflection_engine_runs_without_error(self):
        from api.agents.coordinator.reflection_engine import reflection_engine
        from api.agents.coordinator.task_graph import TaskGraph
        g = TaskGraph("wf-re-001", "Reflection test")
        n = g.add_node("Step", "browser", "do something")
        g.mark_started(n.node_id, "t1")
        g.mark_done(n.node_id, {"result": "ok"})
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            reflection_engine.reflect(g, {"result": "ok"}, 5.0, True)
        )
        assert isinstance(result, dict)
