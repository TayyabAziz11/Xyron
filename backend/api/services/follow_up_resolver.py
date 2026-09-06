"""
Follow-Up Resolver — transforms short/ambiguous commands into concrete ones
using active_context.

Called between text normalisation and Tier-0 routing in the voice pipeline.
Target: <50ms (pure Python regex, no I/O).

Logs: [FOLLOWUP_INPUT] [FOLLOWUP_CONTEXT_USED] [FOLLOWUP_RESOLVED]
      [FOLLOWUP_NEEDS_CLARIFICATION] [FOLLOWUP_EXPIRED] [FOLLOWUP_RESOLVER_MS]
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Compiled patterns ────────────────────────────────────────────────────────

# "download whatsapp" / "install spotify" / "get telegram"
# [,\s]+ (not \s+) after the verb — Whisper commonly inserts a comma right
# after an imperative verb from the natural speech pause ("Play, some call
# believer."), which a plain \s+ requirement rejects outright, silently
# falling through to an unrelated tier (live bug: misrouted to media_control
# play/pause instead of searching YouTube).
_INSTALL_RE = re.compile(
    r'^(?:download|install|get|add)[,\s]+(.+)$',
    re.IGNORECASE,
)

# "install the first one" / "download second one" / "get the 1st" — an
# ordinal reference into voice_ws.py's pending_store_candidates list, not a
# literal app name. _INSTALL_RE above has no exclusion for this (unlike
# store_agent.py's BARE_RE, which already excludes first/second/third via
# _EXCLUDE_NEXT), so it was capturing "the first one" as app_name and firing
# a second, doomed winget search instead of falling through to the ordinal
# candidate-selection tier (live bug).
_ORDINAL_SELECTION_RE = re.compile(
    r'^(?:the\s+)?(?:first|second|third|1st|2nd|3rd|number\s+(?:one|two|three))'
    r'(?:\s+one)?\s*[.!?]?\s*$',
    re.IGNORECASE,
)

# "play believer" / "watch video" — but NOT a bare pronoun follow-up like
# "play it" / "watch this" / "now play it." Those must fall through to the
# context-aware ContextStack tier (follow_up_resolver_v2's _tier_context_stack),
# which resolves against the actual last-played entity instead of literally
# searching for the word "it". Live bug: "now play it." after "open youtube"
# → "play a song called believer" was matching here with group(1)=="it",
# producing search_youtube(query="it") instead of replaying believer.
# Same guard for activity-memory recall phrasings ("play the same songs you
# played yesterday") — those must reach voice_ws Tier 0m, not become a literal
# search for the whole sentence.
_PLAY_RE = re.compile(
    r'^(?:play|watch|listen\s+to)[,\s]+'
    r'(?!(?:it|this|that|these|those|them)\s*[.!]?\s*$)'
    r'(?!the\s+same\b)'
    r'(?!.*\byou\s+(?:played|were\s+playing)\b)'
    r'(.+)$',
    re.IGNORECASE,
)

# "search for believer" (without "youtube" — in YouTube context)
_SEARCH_RE = re.compile(
    r'^(?:search(?:\s+for)?|find)[,\s]+(?!youtube|yt\b)(.+)$',
    re.IGNORECASE,
)

# "open it" (alone)
_OPEN_IT_RE = re.compile(
    r'^open\s+it\s*(?:now)?\s*[.!]?\s*$',
    re.IGNORECASE,
)

# "open it in vs code" / "open it in notepad"
_OPEN_IT_IN_RE = re.compile(
    r'^open\s+it\s+in\s+(.+)$',
    re.IGNORECASE,
)

# "open latest file" / "open last document" / "open newest pdf"
_LATEST_FILE_RE = re.compile(
    r'^open\s+(?:the\s+)?(?:latest|last|newest|most\s+recent)\s+'
    r'(?:file|document|pdf|video|photo|image|spreadsheet)\s*[.!]?\s*$',
    re.IGNORECASE,
)

# "download this app" / "install the app" / "get this app"
_THIS_APP_RE = re.compile(
    r'^(?:download|install|get)\s+(?:this|the|that)\s+app\s*[.!]?\s*$',
    re.IGNORECASE,
)

# "install it" / "download it now" / "yes install" / "go ahead" / "get it" / "install this"
# CONTINUE_INSTALL_WORDS is shared with follow_up_resolver_v2.py and store_agent.py
# so "yes"/"continue"/"proceed" stay in sync across all three.
from api.services.store_agent import CONTINUE_INSTALL_WORDS as _CONTINUE_WORDS  # noqa: E402

_INSTALL_IT_RE = re.compile(
    rf'^(?:'
    rf'(?:yes[,.\s!]*)?(?:can\s+(?:you|u)\s+|could\s+you\s+)?(?:please\s+)?(?:install|download|get)\s+(?:it|this|that)(?:\s+now)?'
    rf'|yes[,.\s!]+install'
    rf'|go\s+ahead(?:\s+(?:and\s+)?install)?'
    rf'|install(?:\s+it)?\s+now'
    rf')\b',
    re.IGNORECASE,
)
# Live bug: "Perfect. Now can you please install it? It's not installed yet."
# — even with leading-filler stripping fixing the prefix, the trailing "It's
# not installed yet" broke the old \s*[.!]?\s*$ end anchor, which required
# the WHOLE utterance to be nothing but the confirmation phrase. _INSTALL_IT_RE
# above now only anchors the START (drops the end anchor) since it always
# contains an unambiguous verb (install/download/get) — trailing chatter is
# harmless to tolerate. Bare affirmations ("yes"/"ok"/"continue") are kept in
# a SEPARATE, still strictly whole-string-anchored pattern below: unlike
# "install it", a bare "yes" is short/ambiguous outside this exact narrow
# pending-install context, so trailing content after it should NOT confirm.
_BARE_CONTINUE_RE = re.compile(
    rf'^(?:{_CONTINUE_WORDS})\s*[.!]?\s*$',
    re.IGNORECASE,
)

# Strip source context from app name: "instagram from microsoft store" → "instagram"
_STRIP_SOURCE_RE = re.compile(
    r'\s+(?:from|in|on|via|through|at)\s+(?:the\s+)?(?:microsoft\s+)?'
    r'(?:store|windows\s+store|ms\s*store|app\s+store|microsoft\s+app\s+store)'
    r'\s*[.!]?\s*$',
    re.IGNORECASE,
)

# Phonetic corrections for MS Store app names (STT mishearings)
_STORE_PHONETIC_MAP: dict[str, str] = {
    "histogram":    "instagram",
    "insta gram":   "instagram",
    "instagrams":   "instagram",
    "in stagram":   "instagram",
    "this diagram": "instagram",  # live-measured: tiny.en mis-heard "download instagram"
    "tic toc":      "tiktok",
    "tick tock":    "tiktok",
    "tik tok":      "tiktok",
    "u tube":       "youtube",
    "you tube":     "youtube",
    "whats app":    "whatsapp",
    "what's app":   "whatsapp",
    "what sapp":    "whatsapp",
    "snap chat":    "snapchat",
    "tele gram":    "telegram",
    "face book":    "facebook",
}

# "first one" / "play first one" / "open second one"
_ORDINAL_ACTION_RE = re.compile(
    r'^(?:play|open|watch)?\s*(?:the\s+)?(?:first|second|third|1st|2nd|3rd)\s+(?:one|result|option)\s*$',
    re.IGNORECASE,
)

# "pause it" / "stop music" / "resume it"
_MEDIA_CONTROL_RE = re.compile(
    r'^(?:pause|resume|stop|unpause)\s+(?:it|music|video|that)?\s*[.!]?\s*$',
    re.IGNORECASE,
)

# ── Generic web-interaction verbs ──────────────────────────────────────────
# Unambiguous DOM-manipulation commands only — deliberately NOT "check X"
# (collides with informational asks like "check baggage allowance", which
# belongs to the existing travel/flight conversation flow, not a checkbox
# click) and NOT "compare X" (an existing multi-step research flow, not a
# single click). These are new: no prior tier routed voice text to
# browser_click/browser_fill at all before this.
_CLICK_RE = re.compile(
    r'^(?:click|press|tap)\s+(?:on\s+)?(?:the\s+)?(.+?)'
    r'(?:\s+button|\s+link)?\s*[.!]?\s*$',
    re.IGNORECASE,
)
_SELECT_RE = re.compile(
    r'^(?:select|choose|pick)\s+(?:the\s+)?(.+?)\s*[.!]?\s*$',
    re.IGNORECASE,
)
_FILL_RE = re.compile(
    r'^(?:fill\s+(?:in\s+)?(?:the\s+)?(?P<field1>.+?)\s+with\s+(?P<value1>.+?)'
    r'|type\s+(?P<value2>.+?)\s+(?:in|into)\s+(?:the\s+)?(?P<field2>.+?)'
    r'|enter\s+(?P<value3>.+?)\s+(?:in|into)\s+(?:the\s+)?(?P<field3>.+?))'
    r'\s*[.!]?\s*$',
    re.IGNORECASE,
)
_SUBMIT_RE = re.compile(
    r'^submit(?:\s+(?:the\s+)?form|\s+it)?\s*[.!]?\s*$',
    re.IGNORECASE,
)

# ── Leading conversational filler ────────────────────────────────────────────
# Live-measured root cause of a real follow-up failure: "now download
# instagram" (Microsoft Store context already active) never matched
# _INSTALL_RE ("^(?:download|install|get|add)\s+(.+)$") because the
# anchored pattern requires the string to START with the verb — "now"
# breaks every single `^`-anchored pattern in this file, not just
# _INSTALL_RE. Fixed once, centrally, rather than loosening every regex
# individually (which would also have to be redone for every regex added
# later). Only strips genuine filler words a user might naturally prefix a
# command with — never strips content words that could be part of the
# actual command.
#
# Widened again — live bug: "Yes. Install it." and "Perfect. Now can you
# please install it?" never matched _INSTALL_IT_RE below because neither
# "yes"/"perfect" (affirmations) nor "can you"/"could you" (two-word
# preamble, not just single filler words) were in this list, so nothing
# stripped and the anchored install-confirmation regex saw the whole
# unstripped sentence. Loop bound raised from 3 to 6 since a real sentence
# can chain more of these than before ("perfect." + "now" + "can you" + "please").
_LEADING_FILLER_RE = re.compile(
    r'^(?:now|okay|ok|so|then|well|please|alright|and|also|'
    r'yes|yeah|yep|yup|sure|perfect|great|awesome|cool|nice|right|'
    r'can\s+you|can\s+u|could\s+you|hey)[\s,.!]+',
    re.IGNORECASE,
)


def _strip_leading_filler(text: str) -> str:
    """Strip one or more chained leading filler words ("now, so download
    instagram" -> "download instagram"). Bounded to a few iterations so a
    pathological input can't loop; returns the original text unchanged if
    stripping would leave nothing."""
    stripped = text
    for _ in range(6):
        new = _LEADING_FILLER_RE.sub("", stripped)
        if new == stripped:
            break
        stripped = new
    stripped = stripped.strip()
    return stripped if stripped else text


def _clean_store_query(raw: str) -> str:
    """Strip source phrases and apply phonetic correction to a store app name."""
    # Strip "from microsoft store" / "in store" / etc.
    cleaned = _STRIP_SOURCE_RE.sub("", raw).strip().rstrip(".!?,")
    if cleaned != raw:
        logger.info("[STORE_APP_QUERY_CLEANED] %r → %r", raw, cleaned)

    # Phonetic correction
    lower = cleaned.lower()
    corrected = _STORE_PHONETIC_MAP.get(lower)
    if corrected:
        logger.info("[STORE_APP_PHONETIC_CORRECTED] %r → %r", cleaned, corrected)
        return corrected
    return cleaned


@dataclass
class FollowUpResult:
    resolved:             str
    was_resolved:         bool  = False
    context_used:         dict  = field(default_factory=dict)
    needs_clarification:  bool  = False
    clarification_prompt: str   = ""
    # When set, voice_ws.py should call this tool directly (skips orchestrator)
    tool_name:   str  = ""
    tool_params: dict = field(default_factory=dict)


def resolve(text: str, active_ctx: dict[str, Any]) -> FollowUpResult:
    """
    Transform a short/ambiguous command into a concrete action using active_ctx.
    Returns FollowUpResult; was_resolved=True or tool_name set means action taken.
    """
    t0 = time.monotonic()
    text_stripped = text.strip()
    platform = (active_ctx.get("current_platform") or "").lower()
    folder   = active_ctx.get("current_folder") or ""
    entity   = active_ctx.get("current_entity") or ""

    logger.debug("[FOLLOWUP_INPUT] text=%r platform=%s folder=%s", text_stripped[:60], platform, folder)

    result = _do_resolve(text_stripped, platform, folder, entity, active_ctx)

    ms = (time.monotonic() - t0) * 1000
    logger.info(
        "[FOLLOWUP_RESOLVER_MS] ms=%.1f was_resolved=%s tool_override=%s",
        ms, result.was_resolved, result.tool_name or "none",
    )
    if result.tool_name:
        logger.info(
            "[FOLLOWUP_RESOLVED] %r → direct_tool=%s params=%s ctx=platform:%s folder:%s",
            text_stripped[:50], result.tool_name, result.tool_params, platform, folder,
        )
    elif result.was_resolved:
        logger.info(
            "[FOLLOWUP_RESOLVED] %r → %r (platform=%s folder=%s)",
            text_stripped[:50], result.resolved[:50], platform, folder,
        )
    elif result.needs_clarification:
        logger.info(
            "[FOLLOWUP_NEEDS_CLARIFICATION] text=%r prompt=%r",
            text_stripped[:50], result.clarification_prompt,
        )
    return result


def _do_resolve(
    text: str,
    platform: str,
    folder: str,
    entity: str,
    ctx: dict,
) -> FollowUpResult:
    _original_text = text
    text = _strip_leading_filler(text)
    if text != _original_text:
        logger.info("[FOLLOWUP_FILLER_STRIPPED] %r → %r", _original_text[:60], text[:60])

    # ── Microsoft Store context ───────────────────────────────────────────────
    if platform == "microsoft_store":
        # "install it / download it now / get this / yes" → exec stored product_id directly
        if entity and (_INSTALL_IT_RE.match(text) or _BARE_CONTINUE_RE.match(text) or _THIS_APP_RE.match(text)):
            _app = ctx.get("current_app") or "the app"
            logger.info(
                "[PRONOUN_RESOLVED_TO_STORE_APP] text=%r app=%r entity=%r",
                text, _app, entity,
            )
            logger.info(
                "[STORE_INSTALL_FOLLOWUP_EXEC] app=%r app_id=%r source=msstore",
                _app, entity,
            )
            return FollowUpResult(
                resolved=text,
                was_resolved=True,
                tool_name="install_store_app_exec",
                tool_params={"app_name": _app, "app_id": entity, "source": "msstore"},
                context_used={"platform": platform, "app": _app, "product_id": entity},
            )

        # "download this app" with no entity — need clarification
        if _THIS_APP_RE.match(text):
            return FollowUpResult(
                resolved=text,
                needs_clarification=True,
                clarification_prompt="Which app should I install from the Microsoft Store?",
            )

        # "download instagram from microsoft store" → strip source + phonetic correct
        m = _INSTALL_RE.match(text)
        if m:
            raw_app = m.group(1).strip().rstrip(".!?,")
            if not _ORDINAL_SELECTION_RE.match(raw_app):
                app_name = _clean_store_query(raw_app)
                logger.info("[FOLLOWUP_CONTEXT_USED] platform=microsoft_store app=%r", app_name)
                return FollowUpResult(
                    resolved=text,
                    was_resolved=True,
                    tool_name="install_store_app",
                    tool_params={"app_name": app_name},
                    context_used={"platform": platform, "app": app_name},
                )
            logger.info("[FOLLOWUP_ORDINAL_SKIPPED] raw_app=%r — falling through to store-ordinal tier", raw_app)

    # ── YouTube context ───────────────────────────────────────────────────────
    if platform == "youtube":
        m = _PLAY_RE.match(text)
        if m:
            query = m.group(1).strip().rstrip(".!?,")
            logger.info("[FOLLOWUP_CONTEXT_USED] platform=youtube play=%r", query)
            return FollowUpResult(
                resolved=text,
                was_resolved=True,
                tool_name="search_youtube",
                tool_params={"query": query, "intent": "play"},
                context_used={"platform": platform, "query": query},
            )
        m = _SEARCH_RE.match(text)
        if m:
            query = m.group(1).strip().rstrip(".!?,")
            return FollowUpResult(
                resolved=text,
                was_resolved=True,
                tool_name="search_youtube",
                tool_params={"query": query, "intent": "search"},
                context_used={"platform": platform, "query": query},
            )
        # Media control in YouTube context — let normal routing handle
        if _MEDIA_CONTROL_RE.match(text):
            return FollowUpResult(resolved=text)

    # ── Generic web-interaction context (click/select/fill/submit) ───────────
    # New: no prior tier routed voice text to browser_click/browser_fill at
    # all — "click X"/"fill in X" previously fell through to intent_router,
    # which has no concept of "the page the user is currently looking at"
    # and would either mis-route or dead-end. Applies on "youtube" or "web"
    # platform (any site opened via open_application's web-shortcut branch
    # or open_url — see active_context.py). The actual browser_click/fill
    # execution goes through voice_ws.py's web-interaction confirmation gate
    # (browser_workspace needs a real, CDP-controlled page — see that gate's
    # comment for why this can't just silently reuse the user's native tab).
    if platform in ("youtube", "web"):
        m = _CLICK_RE.match(text)
        if m:
            target = m.group(1).strip().rstrip(".!?,")
            logger.info("[FOLLOWUP_CONTEXT_USED] platform=%s click=%r", platform, target)
            return FollowUpResult(
                resolved=text, was_resolved=True,
                tool_name="browser_click", tool_params={"text": target},
                context_used={"platform": platform, "target": target},
            )
        m = _SELECT_RE.match(text)
        if m:
            target = m.group(1).strip().rstrip(".!?,")
            logger.info("[FOLLOWUP_CONTEXT_USED] platform=%s select=%r", platform, target)
            return FollowUpResult(
                resolved=text, was_resolved=True,
                tool_name="browser_click", tool_params={"text": target},
                context_used={"platform": platform, "target": target},
            )
        m = _FILL_RE.match(text)
        if m:
            field = (m.group("field1") or m.group("field2") or m.group("field3") or "").strip().rstrip(".!?,")
            value = (m.group("value1") or m.group("value2") or m.group("value3") or "").strip().rstrip(".!?,")
            if field and value:
                logger.info("[FOLLOWUP_CONTEXT_USED] platform=%s fill field=%r value=%r", platform, field, value)
                return FollowUpResult(
                    resolved=text, was_resolved=True,
                    tool_name="browser_fill", tool_params={"label": field, "value": value},
                    context_used={"platform": platform, "field": field, "value": value},
                )
        if _SUBMIT_RE.match(text):
            logger.info("[FOLLOWUP_CONTEXT_USED] platform=%s submit", platform)
            return FollowUpResult(
                resolved=text, was_resolved=True,
                tool_name="browser_click", tool_params={"selector": "button[type=submit], input[type=submit]"},
                context_used={"platform": platform, "action": "submit"},
            )

    # ── File explorer / folder context ────────────────────────────────────────
    if folder or platform == "explorer":
        if _LATEST_FILE_RE.match(text) and folder:
            logger.info("[FOLLOWUP_CONTEXT_USED] folder=%r open latest file", folder)
            return FollowUpResult(
                resolved=f"open latest file in {folder} folder",
                was_resolved=True,
                context_used={"folder": folder},
            )
        m = _OPEN_IT_IN_RE.match(text)
        if m and folder:
            app = m.group(1).strip().rstrip(".!?,")
            logger.info("[FOLLOWUP_CONTEXT_USED] folder=%r open in app=%r", folder, app)
            return FollowUpResult(
                resolved=f"open {folder} folder in {app}",
                was_resolved=True,
                context_used={"folder": folder, "app": app},
            )
        if _OPEN_IT_RE.match(text) and folder:
            logger.info("[FOLLOWUP_CONTEXT_USED] folder=%r open it", folder)
            return FollowUpResult(
                resolved=f"open {folder} folder",
                was_resolved=True,
                context_used={"folder": folder},
            )

    # ── Generic install without explicit context ──────────────────────────────
    # "download whatsapp" / "install spotify" with no prior Store context
    # still routes to install_store_app — winget will search and ask for confirmation.
    m = _INSTALL_RE.match(text)
    if m:
        raw_app = m.group(1).strip().rstrip(".!?,")
        app_name = _clean_store_query(raw_app)
        # Skip if it looks like a dev/pip install (contains "package", "-", "pip", etc.)
        # or is an ordinal candidate reference ("install the first one") meant
        # for voice_ws.py's pending_store_candidates tier, not a literal app name.
        _app_low = app_name.lower()
        if (not any(s in _app_low for s in ("pip", "npm", "apt", "brew", "package", "--"))
                and not _ORDINAL_SELECTION_RE.match(raw_app)):
            logger.info("[FOLLOWUP_RESOLVED] bare_install app=%r → install_store_app", app_name)
            return FollowUpResult(
                resolved=text,
                was_resolved=True,
                tool_name="install_store_app",
                tool_params={"app_name": app_name},
                context_used={"reason": "bare_install_intent"},
            )

    return FollowUpResult(resolved=text, was_resolved=False)
