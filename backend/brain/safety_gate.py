"""
Safety Gate — centralised risky-action confirmation system.

Risky actions require explicit voice or API confirmation before execution.
Timeout: 5 seconds. Expired confirmations are auto-rejected.

Risk levels:
  low    — execute immediately
  medium — confirm if autonomy_level < 3
  high   — always confirm, regardless of autonomy level
"""
from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── Risk classification ───────────────────────────────────────────────────────

_HIGH_RISK_KEYWORDS = frozenset([
    "delete", "remove", "erase", "destroy", "drop", "wipe",
    "overwrite", "truncate", "format", "rm", "rmdir",
    "send message", "post online", "publish", "submit",
    "run shell", "execute script", "sudo", "admin",
    "change system settings", "modify registry",
    "close apps", "kill process",
])

_MEDIUM_RISK_KEYWORDS = frozenset([
    "move many", "bulk", "all files", "entire folder",
    "shutdown", "restart", "reboot",
    "install", "uninstall",
])

_ALWAYS_CONFIRM_ROUTES = frozenset([
    "send_whatsapp", "send_email", "post_linkedin",
    "post_instagram", "post_twitter", "run_shell_command",
    "delete_file", "delete_folder",
])


def classify_risk(action: str, tool_name: str = "") -> str:
    """Return 'low' | 'medium' | 'high'."""
    action_lower = action.lower()
    tool_lower   = tool_name.lower()

    if tool_lower in _ALWAYS_CONFIRM_ROUTES:
        return "high"

    for kw in _HIGH_RISK_KEYWORDS:
        if kw in action_lower or kw in tool_lower:
            return "high"

    for kw in _MEDIUM_RISK_KEYWORDS:
        if kw in action_lower:
            return "medium"

    return "low"


# ── Pending confirmation store ─────────────────────────────────────────────────

@dataclass
class PendingConfirmation:
    id:          str
    action:      str
    risk:        str
    callback:    Optional[Callable] = None
    created_at:  float              = field(default_factory=time.time)
    timeout_s:   float              = 5.0
    resolved:    bool               = False
    approved:    Optional[bool]     = None


class SafetyGate:
    """
    Central safety check for all risky operations.

    Usage:
        gate = SafetyGate()
        risk = gate.check_risk("delete the folder", "delete_folder")

        if risk != "low":
            token = gate.request_confirmation("delete the folder", risk)
            # user says "yes" → gate.confirm(token, True)
            # timeout    → gate.confirm(token, False)  [auto-called]
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock    = threading.Lock()

    def check_risk(self, action: str, tool_name: str = "") -> str:
        return classify_risk(action, tool_name)

    def should_confirm(self, risk: str, autonomy_level: int) -> bool:
        """Whether this action requires confirmation given the autonomy level."""
        if risk == "high":
            return True
        if risk == "medium" and autonomy_level < 3:
            return True
        return False

    def request_confirmation(
        self,
        action:     str,
        risk:       str,
        callback:   Optional[Callable] = None,
        timeout_s:  float = 5.0,
        token_id:   Optional[str] = None,
    ) -> str:
        """
        Register a pending confirmation. Returns a token ID.
        Auto-rejects after timeout_s seconds.
        """
        import uuid
        tid = token_id or str(uuid.uuid4())[:8]
        pending = PendingConfirmation(
            id=tid, action=action, risk=risk,
            callback=callback, timeout_s=timeout_s,
        )
        with self._lock:
            self._pending[tid] = pending

        # Auto-reject after timeout
        def _timeout():
            time.sleep(timeout_s)
            self._auto_reject(tid)

        t = threading.Thread(target=_timeout, daemon=True)
        t.start()

        logger.info("[SAFETY_GATE] confirmation_requested id=%s risk=%s action=%r",
                    tid, risk, action[:60])
        return tid

    def confirm(self, token_id: str, approved: bool) -> bool:
        """
        Register a yes/no decision for the pending confirmation.
        Returns True if the token was found and not yet expired.
        """
        with self._lock:
            pending = self._pending.get(token_id)
            if not pending or pending.resolved:
                return False
            pending.resolved = True
            pending.approved = approved

        logger.info("[SAFETY_GATE] resolved id=%s approved=%s", token_id, approved)

        if pending.callback:
            try:
                pending.callback(approved)
            except Exception as exc:
                logger.warning("[SAFETY_GATE] callback error: %s", exc)
        return True

    def _auto_reject(self, token_id: str) -> None:
        with self._lock:
            pending = self._pending.get(token_id)
            if not pending or pending.resolved:
                return
            pending.resolved = True
            pending.approved = False

        logger.info("[SAFETY_GATE] timeout_rejected id=%s", token_id)
        if pending.callback:
            try:
                pending.callback(False)
            except Exception:
                pass

    def is_pending(self, token_id: str) -> bool:
        with self._lock:
            p = self._pending.get(token_id)
            return bool(p and not p.resolved)

    def confirmation_prompt(self, action: str, risk: str) -> str:
        """Return the voice prompt asking the user to confirm."""
        if risk == "high":
            return f"This is a high-risk action: {action}. Say yes to confirm, or I'll cancel in 5 seconds."
        return f"Just confirming: {action}. Say yes to proceed."


safety_gate = SafetyGate()
