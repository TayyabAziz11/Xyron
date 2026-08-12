"""
Tests for Phase 3 — Intelligent Action & Planning Engine.

This phase deliberately did NOT build a new parallel planner/orchestrator —
research confirmed a full Planner (agent_planner.py/DelegationPlanner) ->
Executor (agent_executor.py) -> Verifier (agent_verifier.py) -> Recovery
(agent_recovery.py) -> DAG-orchestrator (coordinator_agent.py/TaskGraph)
stack already existed and is live in production. The actual gap — zero
World State integration — is what these tests cover:

  - AgentStep/TaskNode gained success_condition/rollback fields (additive)
  - world_state_check.py: safe, declarative condition evaluation (no eval())
  - agent_executor.py: observes World State after a step, on top of the
    existing tool-specific verifier
  - agent_recovery.py: rollback as a new, opt-in, final recovery strategy
  - coordinator_agent.py: the same observation+rollback pattern at the
    TaskNode level
  - agent_runtime.launch(): the single enrichment point that gives both
    the direct-dispatch and coordinator paths World State context
  - Regression: existing plans/graphs that don't set the new fields are
    completely unaffected
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _run(coro):
    return asyncio.run(coro)


class TestWorldStateCheck(unittest.TestCase):

    def test_no_condition_always_passes(self):
        from api.agents.world_state_check import check_condition
        passed, _ = check_condition(None, {}, {})
        self.assertTrue(passed)

    def test_malformed_condition_passes_never_blocks(self):
        from api.agents.world_state_check import check_condition
        passed, _ = check_condition({"field": "x", "op": "not_a_real_op"}, {}, {})
        self.assertTrue(passed)
        passed2, _ = check_condition({"op": "changed"}, {}, {})  # missing field
        self.assertTrue(passed2)

    def test_changed_op(self):
        from api.agents.world_state_check import check_condition
        passed, _ = check_condition({"field": "x", "op": "changed"}, {"x": 1}, {"x": 2})
        self.assertTrue(passed)
        passed2, _ = check_condition({"field": "x", "op": "changed"}, {"x": 1}, {"x": 1})
        self.assertFalse(passed2)

    def test_not_none_op(self):
        from api.agents.world_state_check import check_condition
        passed, _ = check_condition({"field": "x", "op": "not_none"}, {}, {"x": "value"})
        self.assertTrue(passed)
        passed2, _ = check_condition({"field": "x", "op": "not_none"}, {}, {"x": None})
        self.assertFalse(passed2)

    def test_equals_op(self):
        from api.agents.world_state_check import check_condition
        cond = {"field": "x", "op": "equals", "value": "shopping"}
        self.assertTrue(check_condition(cond, {}, {"x": "shopping"})[0])
        self.assertFalse(check_condition(cond, {}, {"x": "other"})[0])

    def test_contains_op(self):
        from api.agents.world_state_check import check_condition
        cond = {"field": "x", "op": "contains", "value": "confirmation"}
        self.assertTrue(check_condition(cond, {}, {"x": "order confirmation page"})[0])
        self.assertFalse(check_condition(cond, {}, {"x": "cart page"})[0])

    def test_dotted_path(self):
        from api.agents.world_state_check import check_condition
        cond = {"field": "current_browser.page_type", "op": "equals", "value": "shopping"}
        after = {"current_browser": {"page_type": "shopping"}}
        self.assertTrue(check_condition(cond, {}, after)[0])

    def test_no_eval_no_arbitrary_code_execution(self):
        """A malicious/hallucinated condition must never execute code — just be treated as a literal,
        nonexistent field name (no exception, no side effect)."""
        from api.agents.world_state_check import check_condition
        cond = {"field": "__import__('os').system('echo pwned')", "op": "not_none"}
        passed, reason = check_condition(cond, {}, {})  # no exception raised is the real assertion
        self.assertFalse(passed)  # field doesn't exist anywhere -> after_val is None -> not_none fails
        self.assertIn("None", reason)


class TestAgentTypesExtension(unittest.TestCase):

    def test_agent_step_new_fields_default_none(self):
        from api.agents.agent_types import AgentStep
        step = AgentStep(index=0, description="x")
        self.assertIsNone(step.expected_output)
        self.assertIsNone(step.success_condition)
        self.assertIsNone(step.rollback_tool)
        self.assertEqual(step.rollback_args, {})

    def test_task_node_new_fields_default_none(self):
        from api.agents.coordinator.task_graph import TaskGraph
        graph = TaskGraph(workflow_id="w1", goal="test")
        node = graph.add_node("Step", "automation", "do something")
        self.assertIsNone(node.success_condition)
        self.assertIsNone(node.rollback_goal)

    def test_task_node_accepts_new_fields(self):
        from api.agents.coordinator.task_graph import TaskGraph
        graph = TaskGraph(workflow_id="w1", goal="test")
        node = graph.add_node(
            "Step", "automation", "do something",
            success_condition={"field": "current_file", "op": "changed"},
            rollback_goal="delete the partially created file",
        )
        self.assertEqual(node.success_condition["field"], "current_file")
        self.assertEqual(node.rollback_goal, "delete the partially created file")


class TestAgentExecutorObservation(unittest.TestCase):

    def tearDown(self):
        # world_state is a shared session-wide singleton — don't leak
        # test values into other test files' assumptions about defaults.
        from api.services.world_state import world_state
        world_state.publish("current_document", None, source="test_cleanup")

    def test_step_without_condition_unaffected(self):
        """Regression: a step with no success_condition behaves exactly as before."""
        from api.agents.agent_types import AgentStep, AgentTask, AgentType, StepResult
        from api.agents.agent_executor import agent_executor

        task = AgentTask(task_id="t1", agent_type=AgentType.GENERIC, goal="g")
        step = AgentStep(index=0, description="d", tool="noop")

        async def executor_fn(t, s):
            return StepResult(success=True, output="done")

        result = _run(agent_executor.execute_step(task, step, executor_fn))
        self.assertTrue(result.success)
        self.assertEqual(step.status.value, "completed")

    def test_condition_met_completes_step(self):
        from api.agents.agent_types import AgentStep, AgentTask, AgentType, StepResult
        from api.agents.agent_executor import agent_executor
        from api.services.world_state import world_state

        world_state.publish("current_document", None, source="test")
        task = AgentTask(task_id="t2", agent_type=AgentType.GENERIC, goal="g")
        step = AgentStep(index=0, description="open doc", tool="noop",
                          success_condition={"field": "current_document", "op": "changed"})

        async def executor_fn(t, s):
            world_state.publish("current_document", "report.docx", source="test")
            return StepResult(success=True, output="opened")

        result = _run(agent_executor.execute_step(task, step, executor_fn))
        self.assertTrue(result.success)
        self.assertEqual(step.status.value, "completed")

    def test_condition_not_met_triggers_retry(self):
        from api.agents.agent_types import AgentStep, AgentTask, AgentType, StepResult, RiskLevel
        from api.agents.agent_executor import agent_executor
        from api.services.world_state import world_state

        world_state.publish("current_document", "unchanged.txt", source="test")
        task = AgentTask(task_id="t3", agent_type=AgentType.GENERIC, goal="g")
        step = AgentStep(index=0, description="open doc", tool="noop", risk=RiskLevel.MEDIUM,
                          success_condition={"field": "current_document", "op": "changed"})

        async def executor_fn(t, s):
            return StepResult(success=True, output="claimed success but nothing changed")

        result = _run(agent_executor.execute_step(task, step, executor_fn))
        self.assertFalse(result.success)
        self.assertTrue(result.should_retry)
        self.assertEqual(step.status.value, "pending")  # reset for retry
        self.assertEqual(step.retries, 1)


class TestAgentRecoveryRollback(unittest.TestCase):

    def test_rollback_not_attempted_when_not_declared(self):
        from api.agents.agent_types import AgentStep, RiskLevel
        from api.agents.agent_recovery import attempt_recovery
        import api.tools.registry as reg_mod

        step = AgentStep(index=0, description="d", tool="some_tool_without_tweak",
                          risk=RiskLevel.MEDIUM)
        step.retries = 2

        class FakeTask:
            task_id = "t"

        original = reg_mod.execute
        reg_mod.execute = MagicMock(return_value=MagicMock(success=True))
        try:
            recovered = _run(attempt_recovery(FakeTask(), step, "fail"))
            self.assertFalse(recovered)
            reg_mod.execute.assert_not_called()
        finally:
            reg_mod.execute = original

    def test_rollback_invoked_when_declared_and_retries_exhausted(self):
        from api.agents.agent_types import AgentStep, RiskLevel
        from api.agents.agent_recovery import attempt_recovery
        import api.tools.registry as reg_mod

        step = AgentStep(index=0, description="d", tool="some_tool_without_tweak",
                          risk=RiskLevel.MEDIUM, rollback_tool="delete_file",
                          rollback_args={"path": "/tmp/partial.txt"})
        step.retries = 2

        class FakeTask:
            task_id = "t"

        original = reg_mod.execute
        reg_mod.execute = MagicMock(return_value=MagicMock(success=True))
        try:
            recovered = _run(attempt_recovery(FakeTask(), step, "fail"))
            self.assertFalse(recovered)  # rollback cleans up but task still fails
            reg_mod.execute.assert_called_once_with(
                "delete_file", {"path": "/tmp/partial.txt"}, {}
            )
        finally:
            reg_mod.execute = original

    def test_rollback_failure_does_not_crash_recovery(self):
        from api.agents.agent_types import AgentStep, RiskLevel
        from api.agents.agent_recovery import attempt_recovery
        import api.tools.registry as reg_mod

        step = AgentStep(index=0, description="d", tool="some_tool_without_tweak",
                          risk=RiskLevel.MEDIUM, rollback_tool="delete_file", rollback_args={})
        step.retries = 2

        class FakeTask:
            task_id = "t"

        original = reg_mod.execute
        reg_mod.execute = MagicMock(side_effect=RuntimeError("boom"))
        try:
            recovered = _run(attempt_recovery(FakeTask(), step, "fail"))
            self.assertFalse(recovered)  # never raises, still resolves to "gave up"
        finally:
            reg_mod.execute = original


class TestAgentRuntimeEnrichment(unittest.TestCase):

    def test_launch_enriches_context_with_world_state(self):
        from api.agents.agent_runtime import AgentRuntime
        from api.agents.agent_types import AgentType
        from api.services.world_state import world_state

        # world_state is a shared module-level singleton across the whole
        # test session — publishing here without cleanup leaks into other
        # test files' assertions about a "clean" current_product (see
        # test_world_state_engine.py's stub-fields test). Always restore.
        previous = world_state.get("current_product")
        world_state.publish("current_product", {"name": "Widget"}, source="test")
        try:
            runtime = AgentRuntime()
            # api/agents/__init__.py does `from .agent_runtime import agent_runtime`,
            # which shadows the submodule name in the package namespace — the
            # real module object is only reliably reachable via sys.modules.
            real_mod = sys.modules["api.agents.agent_runtime"]
            with patch.object(real_mod, "_load_agent", return_value=None):
                task = _run(runtime.launch(goal="test goal", agent_type=AgentType.GENERIC))

            self.assertIn("world_state", task.metadata["context"])
            self.assertEqual(task.metadata["context"]["world_state"]["current_product"], {"name": "Widget"})
        finally:
            world_state.publish("current_product", previous, source="test_cleanup")

    def test_launch_world_state_does_not_clobber_existing_context_keys(self):
        from api.agents.agent_runtime import AgentRuntime
        from api.agents.agent_types import AgentType

        runtime = AgentRuntime()
        real_mod = sys.modules["api.agents.agent_runtime"]
        with patch.object(real_mod, "_load_agent", return_value=None):
            task = _run(runtime.launch(
                goal="test goal", agent_type=AgentType.GENERIC,
                context={"project_path": "/some/path"},
            ))
        self.assertEqual(task.metadata["context"]["project_path"], "/some/path")
        self.assertIn("world_state", task.metadata["context"])


class TestCoordinatorObservationLoop(unittest.TestCase):

    def tearDown(self):
        from api.services.world_state import world_state
        world_state.publish("current_file", None, source="test_cleanup")

    def test_run_agent_node_condition_failure_triggers_retry_then_rollback(self):
        from api.agents.coordinator import coordinator_agent
        from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
        from api.services.world_state import world_state

        world_state.publish("current_file", "unchanged.txt", source="test")

        graph = TaskGraph(workflow_id="w", goal="g")
        node = graph.add_node(
            "Create report", "automation", "create the report",
            success_condition={"field": "current_file", "op": "changed"},
            rollback_goal="delete the partially created report",
            max_retries=0,  # exhausted immediately — go straight to rollback
        )

        fake_final_task = MagicMock()
        fake_final_task.status = MagicMock()
        from api.agents.agent_types import AgentStatus
        fake_final_task.status = AgentStatus.COMPLETED
        fake_final_task.result_summary = "claimed done"
        fake_final_task.metadata = {}

        fake_sub_task = MagicMock()
        fake_sub_task.task_id = "sub1"

        fake_runtime = MagicMock()
        fake_runtime.launch = AsyncMock(return_value=fake_sub_task)
        fake_runtime.get_task = MagicMock(return_value=fake_final_task)

        fake_coord_task = MagicMock()
        fake_coord_task.task_id = "coord1"
        fake_coord_task.ws_send_fn = None

        class FakeLock:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        fake_lock_mgr = MagicMock()
        fake_lock_mgr.lock = MagicMock(return_value=FakeLock())

        async def fake_send_progress(*a, **kw): pass
        async def fake_send(*a, **kw): pass

        with patch("api.agents.coordinator.coordinator_agent.asyncio.sleep", new=AsyncMock()):
            result = _run(coordinator_agent._run_agent_node(
                node, graph, fake_coord_task, fake_runtime, {},
                fake_send_progress, fake_send, fake_lock_mgr,
            ))

        self.assertEqual(node.status, NodeStatus.FAILED)
        # rollback goal dispatched via a fire-and-forget asyncio task —
        # give the event loop a tick to schedule it
        _run(asyncio.sleep(0.05))
        self.assertTrue(fake_runtime.launch.call_count >= 2)  # original + rollback

    def test_run_agent_node_without_condition_unaffected(self):
        """Regression: existing nodes without success_condition behave exactly as before."""
        from api.agents.coordinator import coordinator_agent
        from api.agents.coordinator.task_graph import TaskGraph, NodeStatus
        from api.agents.agent_types import AgentStatus

        graph = TaskGraph(workflow_id="w", goal="g")
        node = graph.add_node("Step", "automation", "do something")

        fake_final_task = MagicMock()
        fake_final_task.status = AgentStatus.COMPLETED
        fake_final_task.result_summary = "done"
        fake_final_task.metadata = {}

        fake_sub_task = MagicMock()
        fake_sub_task.task_id = "sub1"

        fake_runtime = MagicMock()
        fake_runtime.launch = AsyncMock(return_value=fake_sub_task)
        fake_runtime.get_task = MagicMock(return_value=fake_final_task)

        fake_coord_task = MagicMock()
        fake_coord_task.task_id = "coord1"
        fake_coord_task.ws_send_fn = None

        class FakeLock:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        fake_lock_mgr = MagicMock()
        fake_lock_mgr.lock = MagicMock(return_value=FakeLock())

        async def fake_send_progress(*a, **kw): pass
        async def fake_send(*a, **kw): pass

        result = _run(coordinator_agent._run_agent_node(
            node, graph, fake_coord_task, fake_runtime, {},
            fake_send_progress, fake_send, fake_lock_mgr,
        ))
        self.assertEqual(node.status, NodeStatus.DONE)
        self.assertEqual(result["result_summary"], "done")


class TestDelegationPlannerContextUnused_NowUsed(unittest.TestCase):

    def test_llm_prompt_includes_world_state_when_present(self):
        from api.agents.coordinator.delegation_planner import DelegationPlanner, GoalAnalysis

        planner = DelegationPlanner()
        analysis = GoalAnalysis("build a clothing website")
        context = {"world_state": {"current_product": {"name": "Blue Shirt"}, "current_url": None}}

        fake_generate = MagicMock(
            return_value='{"nodes": [{"title": "Step", "agent": "automation", "goal": "do it"}]}'
        )
        with patch("api.services.openai_client.openai_client.generate", fake_generate):
            graph = _run(planner._multi_agent_plan_llm("build a clothing website", analysis, context))

        self.assertGreaterEqual(len(graph.nodes), 1)
        prompt_user_msg = fake_generate.call_args[0][0][1]["content"]
        self.assertIn("Blue Shirt", prompt_user_msg)
        self.assertNotIn("current_url", prompt_user_msg)  # None values dropped as noise

    def test_malformed_llm_success_condition_ignored_gracefully(self):
        from api.agents.coordinator.delegation_planner import DelegationPlanner

        planner = DelegationPlanner()
        data = {"nodes": [{"title": "Step", "agent": "automation", "goal": "do it",
                            "success_condition": "not a dict", "rollback_goal": 123}]}
        graph = planner._build_graph_from_llm("goal", data)
        node = list(graph.nodes.values())[0]
        self.assertIsNone(node.success_condition)
        self.assertIsNone(node.rollback_goal)

    def test_well_formed_llm_fields_pass_through(self):
        from api.agents.coordinator.delegation_planner import DelegationPlanner

        planner = DelegationPlanner()
        data = {"nodes": [{
            "title": "Step", "agent": "automation", "goal": "do it",
            "success_condition": {"field": "current_file", "op": "changed"},
            "rollback_goal": "undo it",
        }]}
        graph = planner._build_graph_from_llm("goal", data)
        node = list(graph.nodes.values())[0]
        self.assertEqual(node.success_condition["field"], "current_file")
        self.assertEqual(node.rollback_goal, "undo it")


if __name__ == "__main__":
    unittest.main()
