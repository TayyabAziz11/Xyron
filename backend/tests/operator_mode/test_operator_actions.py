"""Tests for operator_actions convenience constructors."""

import pytest
from operator_mode.operator_actions import click, type_text, press, hotkey, focus_window, wait_for_window, launch_app
from operator_mode.operator_types import VerifyMethod


class TestOperatorActionConstructors:

    def test_click_action(self):
        a = click(100, 200, description="test click")
        assert a.action_type == "click"
        assert a.params["x"] == 100
        assert a.params["y"] == 200

    def test_type_text_action(self):
        a = type_text("hello world")
        assert a.action_type == "type"
        assert a.params["text"] == "hello world"

    def test_press_action(self):
        a = press("Return")
        assert a.action_type == "press"
        assert a.params["key"] == "Return"

    def test_hotkey_action(self):
        a = hotkey("Win+E shortcut", "Win", "e")
        assert a.action_type == "hotkey"
        assert "Win" in a.params["keys"]
        assert "e" in a.params["keys"]

    def test_focus_window_has_verify(self):
        a = focus_window("Chrome")
        assert a.verify is not None
        assert a.verify.method == VerifyMethod.WINDOW_EXISTS
        assert "Chrome" in a.verify.expected

    def test_wait_for_window_action(self):
        a = wait_for_window("YouTube", timeout_s=5.0)
        assert a.action_type == "wait_for_window"
        assert a.params["timeout_s"] == 5.0

    def test_launch_app_action(self):
        a = launch_app("chrome")
        assert a.action_type == "launch_app"
        assert a.params["name"] == "chrome"
        assert a.delay_after_ms == 2000
