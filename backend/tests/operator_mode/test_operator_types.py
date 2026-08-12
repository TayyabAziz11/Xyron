"""Tests for operator_mode types and state."""

import pytest
from operator_mode.operator_types import (
    OperatorAction, OperatorResult, VerifySpec, VerifyMethod
)
from operator_mode.operator_state import GoalState, GoalStatus


class TestOperatorTypes:

    def test_operator_action_defaults(self):
        a = OperatorAction(action_type="click", params={"x": 10, "y": 20})
        assert a.action_type == "click"
        assert a.params == {"x": 10, "y": 20}
        assert a.delay_after_ms == 300
        assert a.verify is None

    def test_operator_action_with_verify(self):
        spec = VerifySpec(VerifyMethod.WINDOW_EXISTS, expected="Chrome", timeout_ms=2000)
        a = OperatorAction(
            action_type="launch_app",
            params={"name": "chrome"},
            verify=spec,
        )
        assert a.verify is not None
        assert a.verify.method == VerifyMethod.WINDOW_EXISTS
        assert a.verify.expected == "Chrome"

    def test_operator_result_success(self):
        r = OperatorResult(success=True, response="Playing music.", trace_id="VX-ABC")
        assert r.success is True
        assert "music" in r.response

    def test_operator_result_failure(self):
        r = OperatorResult(success=False, response="Failed.", error="timeout")
        assert r.success is False
        assert r.error == "timeout"


class TestGoalState:

    def test_initial_state(self):
        g = GoalState(goal="play music", tool_name="search_youtube", params={"query": "lofi"})
        assert g.status == GoalStatus.PENDING
        assert g.retries == 0
        assert g.trace_id.startswith("VX-")

    def test_mark_running(self):
        g = GoalState(goal="test", tool_name="t", params={})
        g.mark_running()
        assert g.status == GoalStatus.RUNNING

    def test_mark_success(self):
        g = GoalState(goal="test", tool_name="t", params={})
        g.mark_success("Done.")
        assert g.status == GoalStatus.SUCCESS
        assert g.result == "Done."
        assert g.finished_at > 0

    def test_mark_failed(self):
        g = GoalState(goal="test", tool_name="t", params={})
        g.mark_failed("timeout")
        assert g.status == GoalStatus.FAILED
        assert g.error == "timeout"

    def test_retry_logic(self):
        g = GoalState(goal="test", tool_name="t", params={}, max_retries=3)
        assert g.can_retry() is True
        g.increment_retry()
        g.increment_retry()
        g.increment_retry()
        assert g.can_retry() is False

    def test_log_step(self):
        g = GoalState(goal="test", tool_name="t", params={})
        g.log_step("step 1 done")
        g.log_step("step 2 done")
        assert len(g.steps_log) == 2

    def test_trace_id_unique(self):
        g1 = GoalState(goal="a", tool_name="t", params={})
        g2 = GoalState(goal="b", tool_name="t", params={})
        assert g1.trace_id != g2.trace_id
