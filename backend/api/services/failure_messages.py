"""
failure_messages — centralized, honest failure phrasing for the voice pipeline.

Individual tools already speak specific, well-crafted failure text where the
cause is known (e.g. "D drive doesn't exist or isn't mounted." in
system_tools.py) — that pattern is good and stays untouched. This module
exists only for the gaps that had no message at all, or a leaked raw
exception, or inconsistent hand-written strings scattered across several
files: the tool registry's generic crash handler, the OpenAI/Ollama
connectivity fallback chain, STT hard failures, and the browser agent.

Mirrors conversational_replies.py's anti-repeat pick() so these don't sound
robotic on repeated failures either.
"""
from __future__ import annotations

import random
from typing import Dict, List

_last: Dict[str, str] = {}


def _pick(slot: str, variants: List[str]) -> str:
    last = _last.get(slot)
    choices = [v for v in variants if v != last] or variants
    text = random.choice(choices)
    _last[slot] = text
    return text


# ── Connectivity / model-unreachable — used anywhere OpenAI AND the local
#    Ollama fallback are both unavailable ─────────────────────────────────
_OFFLINE_VARIANTS = [
    "I can't reach the network right now, so that needs a connection I don't have — local commands still work fine.",
    "Looks like there's no internet connection here — I'm running on local models only for now.",
    "I'm offline right now, so I can't do that one — but local commands still work.",
]


def offline_fallback() -> str:
    return _pick("offline", _OFFLINE_VARIANTS)


# ── STT hard failure — mic/model pipeline itself broke, not just low
#    confidence or a timeout (those already have their own good messages) ──
_STT_FAIL_VARIANTS = [
    "Sorry, something went wrong hearing that — please try again.",
    "I had trouble processing that audio — try again in a moment.",
    "That didn't come through right — please say it again.",
]


def stt_failure() -> str:
    return _pick("stt_failure", _STT_FAIL_VARIANTS)


# ── Generic exception categorization — for the tool registry's crash
#    handler and any other spot that only has a raw exception to work with ─
_CATEGORY_VARIANTS: Dict[str, List[str]] = {
    "network": [
        "I couldn't reach the network for that — check the connection and try again.",
        "That needs an internet connection I don't have right now.",
        "Network's unreachable on my end — try again once it's back.",
    ],
    "timeout": [
        "That took too long and timed out — want me to try again?",
        "It timed out on my end — give it another shot?",
    ],
    "permission": [
        "I don't have permission to do that.",
        "That was blocked — I don't have access for it.",
    ],
    "not_found": [
        "I couldn't find that.",
        "That doesn't seem to exist.",
    ],
    "generic": [
        "Something went wrong on that one — want me to try again?",
        "That didn't work — try again in a moment?",
        "Ran into a problem there — let's try that again.",
    ],
}

_NETWORK_MARKERS = (
    "connectionerror", "connection refused", "name resolution",
    "network is unreachable", "max retries exceeded", "newconnectionerror",
    "httpsconnectionpool", "httpconnectionpool", "getaddrinfo failed",
    "temporary failure in name resolution",
)
_TIMEOUT_MARKERS = ("timeout", "timed out")
_PERMISSION_MARKERS = ("permissionerror", "access is denied", "permission denied", "winerror 5")
_NOT_FOUND_MARKERS = ("filenotfounderror", "no such file", "not found", "does not exist")


def categorize_exception(exc: BaseException) -> str:
    """Best-effort mapping from an exception to a short failure category."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in _TIMEOUT_MARKERS):
        return "timeout"
    if any(m in text for m in _NETWORK_MARKERS):
        return "network"
    if any(m in text for m in _PERMISSION_MARKERS):
        return "permission"
    if any(m in text for m in _NOT_FOUND_MARKERS):
        return "not_found"
    return "generic"


def spoken_for_exception(exc: BaseException) -> str:
    """A clean, honest spoken message for an uncaught exception — never
    leaks raw exception text to the user (that stays in the logs)."""
    category = categorize_exception(exc)
    return _pick(f"cat:{category}", _CATEGORY_VARIANTS[category])
