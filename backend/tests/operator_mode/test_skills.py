"""Tests for operator skills — build_actions() output."""

import pytest


class TestYouTubeSkill:

    @pytest.mark.asyncio
    async def test_build_actions_returns_nonempty(self):
        from operator_mode.skills.youtube_skill import YouTubeSkill
        skill   = YouTubeSkill()
        actions = await skill.build_actions({"query": "lofi music"}, "VX-TEST")
        assert len(actions) > 0

    @pytest.mark.asyncio
    async def test_launch_action_uses_windows_chrome_powershell(self):
        from operator_mode.skills.youtube_skill import YouTubeSkill
        skill   = YouTubeSkill()
        actions = await skill.build_actions({"query": "lofi music"}, "VX-TEST")
        ps_actions = [a for a in actions if a.action_type == "powershell"]
        assert len(ps_actions) >= 1
        launch_script = ps_actions[0].params.get("script", "")
        assert "youtube.com" in launch_script
        assert "Start-Process" in launch_script
        assert "Program Files" in launch_script or "chrome" in launch_script.lower()

    @pytest.mark.asyncio
    async def test_query_encoded_in_launch_script(self):
        from operator_mode.skills.youtube_skill import YouTubeSkill
        skill   = YouTubeSkill()
        actions = await skill.build_actions({"query": "suzume soundtrack"}, "VX-TEST")
        ps_actions = [a for a in actions if a.action_type == "powershell"]
        launch_script = ps_actions[0].params.get("script", "")
        assert "suzume" in launch_script

    @pytest.mark.asyncio
    async def test_success_response_includes_query(self):
        from operator_mode.skills.youtube_skill import YouTubeSkill
        skill = YouTubeSkill()
        resp  = await skill.get_success_response({"query": "lofi music"})
        assert "YouTube" in resp

    @pytest.mark.asyncio
    async def test_failure_response(self):
        from operator_mode.skills.youtube_skill import YouTubeSkill
        skill = YouTubeSkill()
        resp  = await skill.get_failure_response({"query": "lofi"})
        assert "YouTube" in resp or "couldn't" in resp


class TestExplorerSkill:

    @pytest.mark.asyncio
    async def test_build_actions_with_drive_and_query(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill   = ExplorerSkill()
        actions = await skill.build_actions({"query": "Python", "drive": "E"}, "VX-TEST")
        assert len(actions) > 0

    @pytest.mark.asyncio
    async def test_first_action_opens_explorer(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill   = ExplorerSkill()
        actions = await skill.build_actions({"query": "Python", "drive": "E"}, "VX-TEST")
        # First action is a direct PowerShell launch (Win+E SendKeys is unreliable in WSL2)
        first = actions[0]
        assert first.action_type == "powershell"
        assert "E:\\" in first.params.get("script", "") or "E:/" in first.params.get("script", "")

    @pytest.mark.asyncio
    async def test_drive_path_in_ps_script(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill   = ExplorerSkill()
        actions = await skill.build_actions({"query": "Python", "drive": "E"}, "VX-TEST")
        ps_actions = [a for a in actions if a.action_type == "powershell"
                      and "E:\\" in a.params.get("script", "")]
        assert len(ps_actions) >= 1

    @pytest.mark.asyncio
    async def test_no_drive_still_works(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill   = ExplorerSkill()
        actions = await skill.build_actions({"query": "Python"}, "VX-TEST")
        assert len(actions) > 0

    @pytest.mark.asyncio
    async def test_success_response_with_drive(self):
        from operator_mode.skills.explorer_skill import ExplorerSkill
        skill = ExplorerSkill()
        resp  = await skill.get_success_response({"query": "Python", "drive": "E"})
        assert "Python" in resp and "E" in resp


class TestChromeSkill:

    @pytest.mark.asyncio
    async def test_build_actions_navigates_to_url(self):
        from operator_mode.skills.chrome_skill import ChromeSkill
        skill   = ChromeSkill()
        actions = await skill.build_actions({"url": "https://google.com"}, "VX-TEST")
        nav = [a for a in actions if a.action_type == "navigate_url"]
        assert len(nav) >= 1
        assert "google.com" in nav[0].params.get("url", "")


class TestVSCodeSkill:

    @pytest.mark.asyncio
    async def test_build_actions_no_path(self):
        from operator_mode.skills.vscode_skill import VSCodeSkill
        skill   = VSCodeSkill()
        actions = await skill.build_actions({}, "VX-TEST")
        assert len(actions) >= 1

    @pytest.mark.asyncio
    async def test_build_actions_with_path(self):
        from operator_mode.skills.vscode_skill import VSCodeSkill
        skill   = VSCodeSkill()
        actions = await skill.build_actions({"path": "E:\\Projects"}, "VX-TEST")
        type_actions = [a for a in actions if a.action_type == "type"
                        and "Projects" in a.params.get("text", "")]
        assert len(type_actions) >= 1
