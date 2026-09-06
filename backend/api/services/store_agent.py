"""
StoreAgent — canonical Microsoft Store install-intent detection and state tracking.

This module is the single source of truth for recognizing "install this app"
style commands and routing them to the store_tools.py pipeline instead of
open_application. It replaces two previously-duplicated regex sets that lived
independently in intent_router.py and voice_ws.py (Tier 0g bypass), which is
how "open Microsoft Store and install Instagram" slipped through both of them
and fell through to open_application's fuzzy app-name matcher.

Recognized phrasings (verb normalized to INSTALL — download/get/add/grab/
fetch/install/setup/set up all mean the same thing):
  - "install Instagram" / "download Spotify" / "get Telegram"      (bare)
  - "install Telegram from the store"                              (suffix)
  - "install the VS Code app" / "setup the Foo application"        (articled+app-word)
  - "open Microsoft Store and install Instagram"                   (compound)

State machine (tracked via session_state["store_install_state"], not a class
hierarchy — the actual work is still done by store_tools.py/active_context.py/
follow_up_resolver_v2.py; this enum documents and labels the transitions those
modules already drive):
  SEARCHING -> PAGE_OPENED -> WAITING_INSTALL -> INSTALLING -> INSTALLED
                                                             -> FAILED
  (any state) -> CANCELLED

Logs: [STORE_INTENT_DETECTED] [STORE_INTENT_PHRASING] [STORE_STATE_TRANSITION]
      [STORE_INSTALL_CANCELLED]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StoreInstallState(str, Enum):
    SEARCHING       = "searching"
    PAGE_OPENED     = "page_opened"
    WAITING_INSTALL = "waiting_install"
    INSTALLING      = "installing"
    INSTALLED       = "installed"
    FAILED          = "failed"
    CANCELLED       = "cancelled"


@dataclass
class StoreInstallIntent:
    product:  str   # cleaned product/app name, e.g. "instagram"
    phrasing: str   # "compound" | "bare" | "articled_app"
    raw_verb: str   # the verb that was matched, e.g. "install", "download"


def set_store_state(session_state: dict, state: StoreInstallState) -> None:
    """Record a store-install state transition on the session for logging/debugging."""
    prev = session_state.get("store_install_state")
    session_state["store_install_state"] = state.value
    logger.info("[STORE_STATE_TRANSITION] from=%s to=%s", prev, state.value)


# ── Intent detection ──────────────────────────────────────────────────────────

# Was a single optional greeting ("please install X"). Live-measured root
# cause of real install commands never routing: "Perfect. Now can you
# download Telegram" and "Yes. Install Telegram" both fell through every
# ^-anchored pattern below because natural speech chains several filler/
# affirmation words before the actual verb, not just one. Made repeatable
# (`*` instead of a single optional group) so any run of these gets
# consumed before `_VERB` has to match — "perfect." + "now " + "can you "
# all strip in sequence, leaving "download telegram" for the real pattern.
_GREETING = (
    r'(?:(?:please|can\s+you|can\s+u|could\s+you|hey|yo|'
    r'yes|yeah|yep|yup|sure|perfect|great|awesome|cool|nice|right|alright|'
    r'now|okay|ok|so|well|then)[\s,.!]+)*'
)
_VERB     = r'(?:download|install|get|add|grab|fetch|set\s*up|setup)'

# Words that must NOT immediately follow the verb — they indicate the phrase
# is not an app-install command ("install this", "get me a coffee", "install
# a plugin", pip/npm/system package managers, ordinal selection, etc.)
_EXCLUDE_NEXT = (
    r'(?:this\b|the\b|that\b|it\b|me\b|from\b|on\b|a\b|an\b|'
    r'first\b|second\b|third\b|'
    r'pip\b|npm\b|brew\b|apt\b|yum\b|conda\b|cargo\b|gem\b|'
    r'package\b|module\b|library\b|extension\b|plugin\b)'
)

_STORE_SUFFIX = (
    r'(?:\s+(?:from|on|via|through)\s+(?:the\s+)?'
    r'(?:microsoft\s+)?(?:store|windows\s+store|ms\s*store|app\s+store))?'
)

_PRODUCT = r'(?P<product>[\w][\w\s\-\'\.]{0,40}?)'

# Pattern A — compound: "open [the] [microsoft/windows] store and install X"
# This is the phrasing that previously fell through everything and hit the
# open_application fuzzy-match bug (see app_finder.py _search_index).
# Public (no leading underscore) so intent_router.py can reuse the exact same
# pattern source instead of hand-rolling a second copy.
COMPOUND_RE = re.compile(
    rf'^{_GREETING}open\s+(?:the\s+)?(?:microsoft\s+|windows\s+)?(?:app\s+)?store\s*,?\s+'
    rf'(?:app\s+)?and\s+(?:then\s+)?{_VERB}\s+(?:the\s+)?'
    rf'(?!{_EXCLUDE_NEXT}){_PRODUCT}'
    rf'\s*[.!?,]?\s*$',
    re.IGNORECASE,
)

# Pattern B — bare: "install X" / "download X from store", no leading article
BARE_RE = re.compile(
    rf'^{_GREETING}{_VERB}\s+'
    rf'(?!{_EXCLUDE_NEXT}){_PRODUCT}'
    rf'{_STORE_SUFFIX}\s*[.!?,]?\s*$',
    re.IGNORECASE,
)

# Pattern C — articled + explicit app/application word: "install the Foo app"
ARTICLED_APP_RE = re.compile(
    rf'^{_GREETING}{_VERB}\s+the\s+'
    rf'(?P<product>[A-Za-z][\w\s\-\'\.]{{0,40}}?)'
    rf'\s+(?:app|application)\b\s*[.!?,]?\s*$',
    re.IGNORECASE,
)

_TRAILING_APP_WORD_RE = re.compile(r'\s+(?:app|application)$', re.IGNORECASE)

# Words that mean "yes, proceed with the install" once a store product is
# pending. Shared by follow_up_resolver.py (v1) and follow_up_resolver_v2.py
# so both stay in sync instead of drifting like _INSTALL_IT_RE used to.
CONTINUE_INSTALL_WORDS = r'(?:yes|yeah|yep|sure|ok|okay|continue|proceed)'


def clean_product(raw: str) -> str:
    p = raw.strip().rstrip(".,!?").strip()
    p = _TRAILING_APP_WORD_RE.sub("", p).strip()
    return p


def detect_install_intent(text: str) -> Optional[StoreInstallIntent]:
    """
    Detect a Microsoft Store install command in `text` and extract a clean
    product name. Returns None if the text isn't an install-shaped command.

    Tries compound phrasing first (most specific), then bare, then the
    articled+app-word form.
    """
    t = text.strip()
    if not t:
        return None

    m = COMPOUND_RE.match(t)
    if m:
        product = clean_product(m.group("product"))
        if product:
            logger.info("[STORE_INTENT_DETECTED] phrasing=compound product=%r text=%r", product, t[:80])
            return StoreInstallIntent(product=product, phrasing="compound", raw_verb="install")

    m = BARE_RE.match(t)
    if m:
        product = clean_product(m.group("product"))
        if product:
            logger.info("[STORE_INTENT_DETECTED] phrasing=bare product=%r text=%r", product, t[:80])
            return StoreInstallIntent(product=product, phrasing="bare", raw_verb="install")

    m = ARTICLED_APP_RE.match(t)
    if m:
        product = clean_product(m.group("product"))
        if product:
            logger.info("[STORE_INTENT_DETECTED] phrasing=articled_app product=%r text=%r", product, t[:80])
            return StoreInstallIntent(product=product, phrasing="articled_app", raw_verb="install")

    return None


# ── Cancel handling ────────────────────────────────────────────────────────────

_CANCEL_RE = re.compile(
    r'\b(?:cancel(?:\s+(?:it|that|the\s+install|install))?|never\s*mind|nevermind|'
    r'stop(?:\s+(?:it|that|the\s+install))?|forget\s+it|'
    r'don\'?t\s+install(?:\s+it)?|no[,\s]+don\'?t)\b',
    re.IGNORECASE,
)


def is_cancel_phrase(text: str) -> bool:
    if _CANCEL_RE.search(text.strip()):
        return True
    # Roman Urdu / Urdu-script cancel words ("nahi", "rehne do", "نہیں") go
    # through the ONE shared approval/cancel classifier — see
    # api.services.approval_intent module docstring for why this isn't a
    # second, Urdu-only cancel regex living here instead.
    from api.services.approval_intent import parse_yes_no
    return parse_yes_no(text) == "no"


def store_context_active(active_ctx: dict[str, Any], session_state: dict) -> bool:
    """True if there is a live Microsoft Store install flow pending."""
    if active_ctx.get("current_platform") == "microsoft_store":
        return True
    if session_state.get("pending_store_candidates"):
        return True
    if session_state.get("pending_open_after_install"):
        return True
    return False


def cancel_install_context(active_context_service: Any, session_state: dict) -> None:
    """
    Clear all pending Microsoft Store install state: candidate disambiguation,
    the open-after-install offer, and (if it's the active platform) the
    active_context platform/goal tracking. Does not touch context_stack —
    its entries are historical log data, not a pending gate.
    """
    session_state["pending_store_candidates"]   = None
    session_state["pending_open_after_install"] = None
    try:
        if active_context_service.current_platform() == "microsoft_store":
            active_context_service.reset()
    except Exception as exc:
        logger.debug("[STORE_INSTALL_CANCELLED] active_context reset skipped: %s", exc)
    set_store_state(session_state, StoreInstallState.CANCELLED)
    logger.info("[STORE_INSTALL_CANCELLED] cleared pending store context")
