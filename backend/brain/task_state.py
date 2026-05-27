"""
Task state machine — tracks multi-turn pending tasks across voice turns.

When an agent needs a follow-up value from the user (e.g. folder name),
it sets an active task here. The next voice turn checks this BEFORE running
the brain orchestrator, resolves the pending value, and executes the action.

Logs:
  [TASK_STATE]        active=<intent> status=<status>
  [TASK_CONTINUATION] resolved=true value=<value>
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    intent:  str                        # e.g. "prepare_workspace"
    status:  str                        # e.g. "awaiting_folder_name"
    agent:   str                        # e.g. "automation_agent"
    context: dict[str, Any] = field(default_factory=dict)


class TaskState:
    """Session-scoped multi-turn task tracker. Thread-safe singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task: Optional[ActiveTask] = None

    def set_task(self, intent: str, status: str, agent: str, **ctx: Any) -> None:
        with self._lock:
            self._task = ActiveTask(intent=intent, status=status, agent=agent, context=ctx)
        logger.info("[TASK_STATE] active=%s status=%s agent=%s", intent, status, agent)

    def get_active_task(self) -> Optional[ActiveTask]:
        with self._lock:
            return self._task

    def clear_task(self) -> None:
        with self._lock:
            if self._task:
                logger.info("[TASK_STATE] cleared intent=%s", self._task.intent)
            self._task = None

    def resolve_continuation(self, text: str) -> Optional[dict[str, Any]]:
        """
        If an active task is waiting for user input, try to extract the value.

        Returns {"resolved": True, "value": str, "task": ActiveTask} or None.
        """
        with self._lock:
            task = self._task
        if not task:
            return None

        if task.status == "awaiting_folder_name":
            value = _extract_folder_name(text)
            if value:
                logger.info("[TASK_CONTINUATION] resolved=true value=%r task=%s", value, task.intent)
                return {"resolved": True, "value": value, "task": task}
            logger.debug("[TASK_CONTINUATION] no folder name found in %r", text[:60])
            return None

        return None


_FOLDER_PATTERNS = [
    # "folder name GTA" / "I want the folder name GTA"
    re.compile(r'\bfolder\s+name\s+["\']?(\w[\w\s\-]{0,40})["\']?', re.IGNORECASE),
    # "call it MyProject" / "name it Xyron_Dev"
    re.compile(r'\b(?:call|name)\s+it\s+["\']?(\w[\w\s\-]{0,40})["\']?', re.IGNORECASE),
    # "folder named ProjectX" / "folder called Beta"
    re.compile(r'\bfolder\s+(?:named?|called)\s+["\']?(\w[\w\s\-]{0,40})["\']?', re.IGNORECASE),
    # "the folder GTA" / "folder GTA"
    re.compile(r'\b(?:the\s+)?folder\s+["\']?([A-Z][\w\s\-]{0,40})["\']?', re.IGNORECASE),
    # "I want GTA" / "I want folder Alpha"
    re.compile(r'\bi\s+want\s+(?:(?:the\s+)?folder\s+)?["\']?([A-Z][\w\-]{1,40})["\']?', re.IGNORECASE),
]

_COMMON_WORDS = frozenset(
    "i the a an is it my me we you he she they this that these those"
    " want need please thanks ok okay yeah yep sure who what when where why how".split()
)


def _extract_folder_name(text: str) -> Optional[str]:
    t = text.strip().rstrip(".,?!")
    # Try explicit patterns first — order matters (most specific first)
    for pat in _FOLDER_PATTERNS:
        m = pat.search(t)
        if m:
            val = m.group(1).strip().rstrip(".,?! ")
            val = re.sub(r'\s+(please|thanks|ok|okay|yeah|yep|sure)$', '', val, flags=re.IGNORECASE)
            if val and len(val) <= 50 and val.lower() not in _COMMON_WORDS:
                return val
    # Fallback: 1-2 word answer with no common filler words
    words = t.split()
    if 1 <= len(words) <= 2 and all(
        re.fullmatch(r'[\w\-]+', w) and w.lower() not in _COMMON_WORDS for w in words
    ):
        return "_".join(words)
    return None


# Module singleton — imported in voice.py and agents
task_state = TaskState()
