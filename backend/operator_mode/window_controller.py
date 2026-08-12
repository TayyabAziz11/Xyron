"""
Phase 5 — Window Controller.

Wraps existing switch_window / maximize_window / minimize_window / close_window.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def _registry():
    from api.tools import registry as _r
    return _r


class WindowController:

    def focus(self, app_name: str) -> bool:
        logger.info("[WINDOW_FOCUS] app=%r", app_name)
        result = _registry().execute("switch_window", {"title": app_name}, {})
        return result.success

    def maximize(self, app_name: str) -> bool:
        logger.info("[WINDOW_MAXIMIZE] app=%r", app_name)
        result = _registry().execute("maximize_window", {"title": app_name}, {})
        return result.success

    def minimize(self, app_name: str) -> bool:
        result = _registry().execute("minimize_window", {"title": app_name}, {})
        logger.info("[WINDOW_FOCUS] action=minimize app=%r ok=%s", app_name, result.success)
        return result.success

    def restore(self, app_name: str) -> bool:
        logger.info("[WINDOW_RESTORE] app=%r", app_name)
        # focus + maximize is equivalent to restore-to-normal on Windows
        return self.focus(app_name)

    def close(self, app_name: str) -> bool:
        result = _registry().execute("close_window", {"title": app_name}, {})
        logger.info("[WINDOW_FOCUS] action=close app=%r ok=%s", app_name, result.success)
        return result.success

    def bring_to_front(self, app_name: str) -> bool:
        """Focus and maximize a window."""
        logger.info("[WINDOW_FOCUS] action=bring_to_front app=%r", app_name)
        ok = self.focus(app_name)
        if ok:
            time.sleep(0.2)
            self.maximize(app_name)
        return ok

    def wait_for_window(self, app_name: str, timeout_s: float = 5.0) -> bool:
        """Poll until a window matching app_name appears or timeout."""
        from operator_mode.screen_observer import screen_observer
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if screen_observer.is_app_open(app_name):
                logger.info("[WINDOW_FOCUS] wait_for_window found app=%r", app_name)
                return True
            time.sleep(0.5)
        logger.warning("[WINDOW_FOCUS] wait_for_window timeout app=%r", app_name)
        return False


window_controller = WindowController()
