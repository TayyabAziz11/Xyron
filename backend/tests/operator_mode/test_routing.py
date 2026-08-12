"""
Tests verifying operator mode intercept routing:
  - OPERATOR_MODE=true  → open_drive, smart_open, search_youtube go to operator
  - OPERATOR_MODE=false → can_handle() always False
  - Blocklisted tools never handled
  - Fallback logs reason when skill returns empty
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# Helper: patch the module-level _operator_mode_enabled function
_ENABLED  = "operator_mode.operator_engine._operator_mode_enabled"


class TestOperatorModeRouting:

    def test_open_drive_handled_when_enabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("open_drive") is True

    def test_smart_open_handled_when_enabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("smart_open") is True

    def test_open_directory_handled_when_enabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("open_directory") is True

    def test_search_youtube_handled_when_enabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("search_youtube") is True

    def test_open_drive_NOT_handled_when_disabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=False):
            assert engine.can_handle("open_drive") is False

    def test_youtube_NOT_handled_when_disabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=False):
            assert engine.can_handle("search_youtube") is False

    def test_delete_file_always_blocked(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("delete_file") is False

    def test_shutdown_always_blocked(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("shutdown_system") is False

    def test_launch_application_not_intercepted(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=True):
            assert engine.can_handle("launch_application") is False

    @pytest.mark.asyncio
    async def test_execute_returns_empty_when_disabled(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        with patch(_ENABLED, return_value=False):
            result = await engine.execute("open_drive", {"drive": "E"})
        assert result == ""

    @pytest.mark.asyncio
    async def test_execute_open_drive_calls_explorer_skill(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.response = "Opened E drive in File Explorer."
        mock_result.trace_id = "VX-TEST"
        with patch(_ENABLED, return_value=True), \
             patch("operator_mode.agent_loop.AgentLoop.run", new_callable=AsyncMock,
                   return_value=mock_result):
            result = await engine.execute("open_drive", {"drive": "E"}, goal="open E drive")
        assert result == "Opened E drive in File Explorer."

    @pytest.mark.asyncio
    async def test_fallback_returns_empty_when_skill_fails(self):
        import operator_mode.operator_engine as _oe
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.response = ""
        mock_result.trace_id = "VX-FAIL"
        mock_result.error = "test_failure"
        with patch(_ENABLED, return_value=True), \
             patch("operator_mode.agent_loop.AgentLoop.run", new_callable=AsyncMock,
                   return_value=mock_result):
            result = await engine.execute("open_drive", {"drive": "E"})
        assert result == ""
        assert _oe._last_fallback_reason != ""

    @pytest.mark.asyncio
    async def test_execute_youtube_routes_to_youtube_skill(self):
        from operator_mode.operator_engine import OperatorEngine
        engine = OperatorEngine()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.response = "Playing lofi music on YouTube."
        mock_result.trace_id = "VX-YT"
        with patch(_ENABLED, return_value=True), \
             patch("operator_mode.agent_loop.AgentLoop.run", new_callable=AsyncMock,
                   return_value=mock_result):
            result = await engine.execute("search_youtube", {"query": "lofi music"})
        assert "YouTube" in result


class TestSettingsIntegration:

    def test_settings_has_operator_mode_field(self):
        from api.config import Settings
        s = Settings()
        assert hasattr(s, "operator_mode")
        assert isinstance(s.operator_mode, bool)

    def test_env_file_contains_operator_mode_true(self):
        """Verify OPERATOR_MODE=true is present in backend/.env."""
        from api.config import _BACKEND_ENV
        content = _BACKEND_ENV.read_text()
        assert "OPERATOR_MODE=true" in content, (
            f"OPERATOR_MODE=true not found in {_BACKEND_ENV}"
        )

    def test_fresh_settings_reads_operator_mode(self):
        """Fresh Settings() reads OPERATOR_MODE from .env correctly."""
        from api.config import Settings
        s = Settings()
        assert s.operator_mode is True


class TestAgentLoopSkillMap:

    def test_all_explorer_tools_in_skill_map(self):
        from operator_mode.agent_loop import _SKILL_MAP
        for tool in ("open_drive", "smart_open", "open_directory", "open_folder"):
            assert tool in _SKILL_MAP, f"{tool!r} missing from _SKILL_MAP"

    def test_youtube_in_skill_map(self):
        from operator_mode.agent_loop import _SKILL_MAP
        assert "search_youtube" in _SKILL_MAP

    def test_blocklist_prevents_handling(self):
        from operator_mode.agent_loop import agent_loop
        for tool in ("delete_file", "shutdown_system", "format_drive"):
            assert agent_loop.can_handle(tool) is False
