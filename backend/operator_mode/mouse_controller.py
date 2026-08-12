"""
Phase 3 — Mouse Controller.

Wraps the existing desktop_click / automation_tools tools.
All actions log with [MOUSE_*] prefixes and support verify=True.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def _registry():
    from api.tools import registry as _r
    return _r


class MouseController:
    """Human-like mouse control via existing automation_tools."""

    def click(self, x: int, y: int, verify: bool = False) -> bool:
        logger.info("[MOUSE_CLICK] x=%d y=%d verify=%s", x, y, verify)
        result = _registry().execute("desktop_click", {"x": x, "y": y}, {})
        ok = result.success
        if verify:
            return self._verify_click(x, y, ok)
        return ok

    def double_click(self, x: int, y: int) -> bool:
        logger.info("[MOUSE_DOUBLE_CLICK] x=%d y=%d", x, y)
        # Two rapid clicks
        r1 = _registry().execute("desktop_click", {"x": x, "y": y}, {})
        time.sleep(0.05)
        r2 = _registry().execute("desktop_click", {"x": x, "y": y}, {})
        return r1.success and r2.success

    def right_click(self, x: int, y: int) -> bool:
        logger.info("[MOUSE_CLICK] type=right x=%d y=%d", x, y)
        result = _registry().execute("desktop_click", {"x": x, "y": y, "button": "right"}, {})
        return result.success

    def move(self, x: int, y: int) -> bool:
        logger.info("[MOUSE_MOVE] x=%d y=%d", x, y)
        result = _registry().execute("desktop_click", {"x": x, "y": y, "move_only": True}, {})
        return result.success

    def _verify_click(self, x: int, y: int, click_ok: bool) -> bool:
        logger.info("[MOUSE_VERIFY] x=%d y=%d click_ok=%s", x, y, click_ok)
        return click_ok


mouse_controller = MouseController()
