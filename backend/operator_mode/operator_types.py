"""
Shared types for the Operator Layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class VerifyMethod(Enum):
    WINDOW_EXISTS  = auto()   # verify app window appeared
    WINDOW_TITLE   = auto()   # window title contains expected text
    ACTIVE_WINDOW  = auto()   # active foreground window changed
    SCREENSHOT_OCR = auto()   # text visible on screen
    NONE           = auto()   # skip verification


@dataclass
class OperatorAction:
    action_type: str              # "click", "type", "hotkey", "navigate", "focus_window"
    params:      dict[str, Any]   # action-specific params
    description: str = ""        # human-readable step description
    verify:      "VerifySpec | None" = None
    delay_after_ms: int = 300    # wait after action before next


@dataclass
class VerifySpec:
    method:       VerifyMethod
    expected:     str = ""       # window name / title / OCR text to find
    timeout_ms:   int = 2000
    retry_on_fail: bool = True


@dataclass
class OperatorResult:
    success:  bool
    response: str                # spoken response text
    trace_id: str = ""
    steps_executed: int = 0
    steps_failed:   int = 0
    error:    str = ""
