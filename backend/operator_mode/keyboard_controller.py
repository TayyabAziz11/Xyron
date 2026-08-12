"""
Phase 4 — Keyboard Controller.

Wraps existing desktop_type / desktop_hotkey automation_tools.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _registry():
    from api.tools import registry as _r
    return _r


class KeyboardController:

    def type_text(self, text: str) -> bool:
        logger.info("[KEYBOARD_TYPE] text=%r", text[:60])
        result = _registry().execute("desktop_type", {"text": text}, {})
        return result.success

    def press_key(self, key: str) -> bool:
        logger.info("[KEYBOARD_PRESS] key=%r", key)
        result = _registry().execute("desktop_hotkey", {"keys": key}, {})
        return result.success

    def hotkey(self, *keys: str) -> bool:
        combo = "+".join(keys)
        logger.info("[KEYBOARD_HOTKEY] keys=%r", combo)
        result = _registry().execute("desktop_hotkey", {"keys": combo}, {})
        return result.success


keyboard_controller = KeyboardController()
