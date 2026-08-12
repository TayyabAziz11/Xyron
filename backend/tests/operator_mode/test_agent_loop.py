"""Tests for agent_loop — OPERATOR_BLOCKLIST and can_handle()."""
import pytest
from unittest.mock import patch

from operator_mode.agent_loop import AgentLoop, OPERATOR_BLOCKLIST

_ENABLED = "operator_mode.operator_engine._operator_mode_enabled"


class TestAgentLoopSafety:

    def setup_method(self):
        self.loop = AgentLoop()

    def test_blocklist_tools_not_handled(self):
        for tool in OPERATOR_BLOCKLIST:
            assert self.loop.can_handle(tool) is False

    def test_youtube_skill_handled(self):
        assert self.loop.can_handle("search_youtube") is True

    def test_open_drive_skill_handled(self):
        assert self.loop.can_handle("open_drive") is True

    def test_smart_open_skill_handled(self):
        assert self.loop.can_handle("smart_open") is True

    def test_unknown_tool_not_handled(self):
        assert self.loop.can_handle("nonexistent_tool_xyz") is False

    @pytest.mark.asyncio
    async def test_blocklisted_tool_returns_error(self):
        result = await self.loop.run("delete_file", {"path": "/tmp/test"})
        assert result.success is False
        assert "not permitted" in result.response

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_failure(self):
        result = await self.loop.run("unknown_xyz_tool", {})
        assert result.success is False
        # response may be empty (falls back to direct tool) — error field confirms no_skill
        assert result.error == "no_skill"


class TestOperatorEngineGating:
    """OperatorEngine.can_handle() returns False when OPERATOR_MODE is off."""

    def test_disabled_returns_false(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=False):
            assert engine.can_handle("search_youtube") is False

    def test_enabled_returns_true_for_known_tool(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("search_youtube") is True

    def test_blocklisted_even_when_enabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("delete_file") is False
