"""
High-level operator action primitives.

Each action wraps the lower-level controllers and logs with [OPERATOR_*].
These are called by skills and the agent loop.
"""
from __future__ import annotations

import asyncio
import logging
import time

from operator_mode.operator_types import OperatorAction, VerifySpec, VerifyMethod

logger = logging.getLogger(__name__)


async def execute_action(action: OperatorAction, trace_id: str = "?") -> bool:
    """
    Execute a single OperatorAction. Returns True on success.
    Runs the verify spec if provided.
    """
    logger.info("[TRACE %s] [AGENT_ACT] type=%s desc=%r",
                trace_id, action.action_type, action.description[:60])
    ok = await asyncio.to_thread(_sync_execute, action, trace_id)
    if action.delay_after_ms > 0:
        await asyncio.sleep(action.delay_after_ms / 1000.0)
    if action.verify:
        from operator_mode.operator_verifier import operator_verifier
        ok = operator_verifier.verify(action.verify, trace_id)
    return ok


def _sync_execute(action: OperatorAction, trace_id: str) -> bool:
    """Dispatch to the right controller based on action_type."""
    a = action.action_type
    p = action.params

    if a == "click":
        from operator_mode.mouse_controller import mouse_controller
        return mouse_controller.click(p.get("x", 0), p.get("y", 0),
                                      verify=p.get("verify", False))

    if a == "double_click":
        from operator_mode.mouse_controller import mouse_controller
        return mouse_controller.double_click(p.get("x", 0), p.get("y", 0))

    if a == "right_click":
        from operator_mode.mouse_controller import mouse_controller
        return mouse_controller.right_click(p.get("x", 0), p.get("y", 0))

    if a == "move":
        from operator_mode.mouse_controller import mouse_controller
        return mouse_controller.move(p.get("x", 0), p.get("y", 0))

    if a == "type":
        from operator_mode.keyboard_controller import keyboard_controller
        return keyboard_controller.type_text(p.get("text", ""))

    if a == "press":
        from operator_mode.keyboard_controller import keyboard_controller
        return keyboard_controller.press_key(p.get("key", ""))

    if a == "hotkey":
        from operator_mode.keyboard_controller import keyboard_controller
        return keyboard_controller.hotkey(*p.get("keys", []))

    if a == "focus_window":
        from operator_mode.window_controller import window_controller
        return window_controller.focus(p.get("app_name", ""))

    if a == "maximize_window":
        from operator_mode.window_controller import window_controller
        return window_controller.maximize(p.get("app_name", ""))

    if a == "bring_to_front":
        from operator_mode.window_controller import window_controller
        return window_controller.bring_to_front(p.get("app_name", ""))

    if a == "wait_for_window":
        from operator_mode.window_controller import window_controller
        return window_controller.wait_for_window(
            p.get("app_name", ""), p.get("timeout_s", 5.0)
        )

    if a == "powershell":
        import subprocess
        ps_exe = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        script = p.get("script", "")
        timeout = p.get("timeout", 6)
        logger.info("[TRACE %s] [PS_RUN] script=%r", trace_id, script[:80])
        try:
            r = subprocess.run(
                [ps_exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=timeout,
            )
            if r.returncode != 0:
                logger.warning("[TRACE %s] [PS_FAIL] rc=%d stderr=%r",
                               trace_id, r.returncode, r.stderr[:120])
                return False
            logger.info("[TRACE %s] [PS_OK] stdout=%r", trace_id, r.stdout[:80])
            return True
        except Exception as exc:
            logger.warning("[TRACE %s] [PS_ERROR] %s", trace_id, exc)
            return False

    if a == "navigate_url":
        # Uses existing registry browser_navigate or runs URL via explorer
        from api.tools import registry as _r
        result = _r.execute("browser_navigate", {"url": p.get("url", "")}, {})
        return result.success

    if a == "launch_app":
        from api.tools import registry as _r
        result = _r.execute("launch_application",
                            {"name": p.get("name", "")}, {})
        return result.success

    if a == "tool":
        # Raw tool registry call
        from api.tools import registry as _r
        result = _r.execute(p.get("tool_name", ""), p.get("params", {}), {})
        return result.success

    logger.warning("[TRACE %s] [OPERATOR_ERROR] unknown action_type=%r", trace_id, a)
    return False


# ── Convenience constructors ──────────────────────────────────────────────────

def click(x: int, y: int, description: str = "", wait_ms: int = 300) -> OperatorAction:
    return OperatorAction(
        action_type="click", params={"x": x, "y": y},
        description=description, delay_after_ms=wait_ms,
    )

def type_text(text: str, description: str = "", wait_ms: int = 200) -> OperatorAction:
    return OperatorAction(
        action_type="type", params={"text": text},
        description=description, delay_after_ms=wait_ms,
    )

def press(key: str, description: str = "", wait_ms: int = 200) -> OperatorAction:
    return OperatorAction(
        action_type="press", params={"key": key},
        description=description, delay_after_ms=wait_ms,
    )

def hotkey(description: str, *keys: str, wait_ms: int = 300) -> OperatorAction:
    return OperatorAction(
        action_type="hotkey", params={"keys": list(keys)},
        description=description, delay_after_ms=wait_ms,
    )

def focus_window(app_name: str, wait_ms: int = 500) -> OperatorAction:
    return OperatorAction(
        action_type="focus_window", params={"app_name": app_name},
        description=f"Focus {app_name}", delay_after_ms=wait_ms,
        verify=VerifySpec(VerifyMethod.WINDOW_EXISTS, expected=app_name, timeout_ms=3000),
    )

def wait_for_window(app_name: str, timeout_s: float = 8.0) -> OperatorAction:
    return OperatorAction(
        action_type="wait_for_window",
        params={"app_name": app_name, "timeout_s": timeout_s},
        description=f"Wait for {app_name} to appear",
        delay_after_ms=200,
        verify=VerifySpec(VerifyMethod.WINDOW_EXISTS, expected=app_name,
                          timeout_ms=int(timeout_s * 1000)),
    )

def launch_app(name: str, wait_ms: int = 2000) -> OperatorAction:
    return OperatorAction(
        action_type="launch_app", params={"name": name},
        description=f"Launch {name}", delay_after_ms=wait_ms,
    )

def powershell_cmd(description: str, script: str, wait_ms: int = 1500, timeout: int = 6) -> OperatorAction:
    return OperatorAction(
        action_type="powershell",
        params={"script": script, "timeout": timeout},
        description=description,
        delay_after_ms=wait_ms,
    )
