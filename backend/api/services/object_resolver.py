"""
object_resolver.py — Phase 3.5: canonical object-type resolution.

Determines WHAT the user is referring to (application / drive / folder /
file / document / project / workspace / repository / website / browser tab /
settings page / control panel item / currently-visible screen object) BEFORE
any tool is chosen — by reusing World State, explorer_context,
active_context, context_stack and fs_index, not by re-deriving any of them.

Why this exists: "open perfume folder" was being misrouted to
open_application because fuzzy app-name matching (entity_corrector) and an
overly-broad regex (tool_aware_corrector) both ran without ever checking the
one piece of structural evidence that settles it outright — the utterance
literally contains the noun "folder". This module makes that check the
FIRST, decisive signal, ahead of any fuzzy similarity scoring: an explicit
type noun always outranks how closely a name happens to sound like an
installed app name.

Not a duplicate of file_resolver.py: file_resolver finds WHICH file/folder
matches a query once the type is already known (its tiered cascade — current
workspace, current Explorer folder, recent/frequent, conversation, active
app, semantic, filename index — already IS the Part 3 search-scope order for
the folder/file case). object_resolver decides the type and rough scope
first, then hands off to file_resolver/smart_open/open_directory/open_drive/
open_application, whichever the resolved type maps to.

Logs: [OBJECT_RESOLUTION] [OBJECT_TYPE_SELECTED] [SEARCH_SCOPE_SELECTED]
      [OBJECT_CANDIDATES] [OBJECT_RESOLUTION_FINAL] [OBJECT_TOOL_INVARIANT_BLOCKED]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

OBJECT_TYPES = frozenset({
    "application", "drive", "folder", "file", "document", "project",
    "workspace", "repository", "website", "browser_tab", "settings_page",
    "control_panel_item", "selected_screen_object", "visible_page_entity",
    "unknown",
})

# Object types that a folder/file-shaped request must never be dispatched to
# open_application for (Part 4's invariant).
FOLDER_FILE_TYPES = frozenset({
    "folder", "file", "document", "project", "workspace", "repository",
})

# Explicit nouns are decisive structural evidence — checked before any fuzzy
# app-name matching. Longer/more specific phrases first so e.g. "control
# panel" wins over a bare "panel" fragment.
_TYPE_NOUN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\bcontrol\s+panel\b', re.I),                 "control_panel_item"),
    (re.compile(r'\b(?:settings?|preferences?)\b', re.I),      "settings_page"),
    (re.compile(r'\b(?:folder|directory|dir)\b', re.I),        "folder"),
    (re.compile(r'\b(?:file|document|doc)\b', re.I),           "file"),
    (re.compile(r'\b(?:drive|disk|partition)\b', re.I),        "drive"),
    (re.compile(r'\b(?:repo|repository)\b', re.I),             "repository"),
    (re.compile(r'\bworkspace\b', re.I),                       "workspace"),
    (re.compile(r'\bproject\b', re.I),                         "project"),
    (re.compile(r'\b(?:website|web\s*page|url)\b', re.I),      "website"),
    (re.compile(r'\b(?:browser\s+)?tab\b', re.I),              "browser_tab"),
    (re.compile(r'\b(?:app|application|program|software)\b', re.I), "application"),
]

_OPEN_IT_RE    = re.compile(r'^\s*(?:open|show|go\s+(?:to|inside|into))\s+it\s*[.!?]?\s*$', re.I)
_AGAIN_RE      = re.compile(r'\b(?:that|the)\s+folder\s+again\b|\bthe\s+folder\s+i\s+mentioned\b|\bsame\s+folder\b', re.I)

_STRIP_LEAD_RE = re.compile(
    r'^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|now\s+|also\s+|just\s+)+', re.I
)
_STRIP_DRIVE_CLAUSE_RE = re.compile(r'^(?:in|on|from)\s+[a-zA-Z]\s+(?:drive|disk)\s*,?\s*', re.I)
_STRIP_VERB_RE = re.compile(
    r'^(?:open|show|launch|start|run|find|locate|search(?:\s+for)?|browse|'
    r'navigate\s+to|go\s+(?:to|inside|into)|take\s+me\s+to)\s+',
    re.I,
)
_STRIP_DET_RE = re.compile(r'^(?:the|my|a|an|that|this)\s+', re.I)
_STRIP_NAMED_RE = re.compile(r'^(?:named|called)\s+', re.I)
_TYPE_WORD_ALT = (
    r'folder|directory|dir|file|document|doc|drive|disk|repo|repository|'
    r'workspace|project|website|site|page|tab|app|application|program|software'
)
# "file named perfume.txt" / "folder called My Projects" — type noun BEFORE
# the name, with named/called in between.
_STRIP_LEADING_TYPE_NAMED_RE = re.compile(
    r'^(?:' + _TYPE_WORD_ALT + r')\s+(?:named|called)\s+', re.I
)
_STRIP_TRAILING_TYPE_RE = re.compile(
    r'\s+(?:' + _TYPE_WORD_ALT + r')\s*$', re.I
)
_STRIP_TRAILING_DRIVE_RE = re.compile(r'\s+(?:in|on|from)\s+[a-zA-Z]\s+(?:drive|disk)\s*$', re.I)
# Drive mentioned only as a scope clause ("in E drive") — not the object
# itself — must not count as evidence that the OBJECT's type is "drive".
_DRIVE_SCOPE_CLAUSE_RE = re.compile(r'\b(?:in|on|from)\s+[a-zA-Z]\s+(?:drive|disk)\b', re.I)


@dataclass
class ObjectResolution:
    action:      str
    object_type: str
    name:        str
    scope:       dict = field(default_factory=dict)
    confidence:  float = 0.0
    evidence:    list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action": self.action, "object_type": self.object_type, "name": self.name,
            "scope": self.scope, "confidence": round(self.confidence, 3), "evidence": self.evidence,
        }


def _get_scope() -> dict:
    """Pull current drive/folder/workspace scope from the systems that
    already track it (World State, active_context, context_stack) — never
    re-queries a sensor directly."""
    scope: dict = {}
    try:
        from .world_state import world_state
        ctx = world_state.get_context(refresh=False)
        folder = ctx.get("current_explorer_folder")
        if folder:
            scope["current_folder"] = folder
            m = re.match(r'^/mnt/([a-zA-Z])/', folder)
            if m:
                scope["drive"] = m.group(1).upper()
        if ctx.get("current_project"):
            scope["current_project"] = ctx["current_project"]
        ws = ctx.get("current_workspace")
        if ws and ws.get("root"):
            scope["current_workspace"] = str(ws["root"])
    except Exception:
        pass

    if "drive" not in scope:
        try:
            from .active_context import active_context
            ac = active_context.get()
            if ac.get("current_platform") == "explorer" and ac.get("current_folder"):
                scope.setdefault("current_folder_name", ac["current_folder"])
        except Exception:
            pass

    if "drive" not in scope:
        try:
            from .context_stack import context_stack
            drive_ent = context_stack.get_last("drive")
            if drive_ent:
                scope["drive"] = drive_ent.value
        except Exception:
            pass

    return scope


def _explicit_type(text: str) -> tuple[Optional[str], list[str]]:
    # A drive mentioned only as a scope clause ("open perfume in E drive")
    # is not evidence that the OBJECT itself is a drive — strip those before
    # matching so "drive" only wins when it's the actual thing being opened
    # ("open E drive", "open the drive").
    stripped = _DRIVE_SCOPE_CLAUSE_RE.sub("", text)
    for pattern, otype in _TYPE_NOUN_PATTERNS:
        m = pattern.search(stripped)
        if m:
            return otype, [f"explicit noun: {m.group(0).lower()!r}"]
    return None, []


def _extract_name(text: str) -> str:
    """Strip filler/verb/determiner/type-noun scaffolding, leaving the bare
    object name — e.g. 'Now in E drive, can you also open perfume folder?'
    -> 'perfume'."""
    t = text.strip().rstrip(".,!?")
    t = _STRIP_LEAD_RE.sub("", t)
    t = _STRIP_DRIVE_CLAUSE_RE.sub("", t)
    t = _STRIP_LEAD_RE.sub("", t)
    t = _STRIP_VERB_RE.sub("", t)
    t = _STRIP_DET_RE.sub("", t)
    t = _STRIP_LEADING_TYPE_NAMED_RE.sub("", t)
    t = _STRIP_NAMED_RE.sub("", t)
    # Strip the full "in/on/from <letter> drive" scope clause as a unit
    # BEFORE the generic trailing-type-word strip below — otherwise that
    # strip eats the bare word "drive" first and leaves a dangling "in e".
    t = _STRIP_TRAILING_DRIVE_RE.sub("", t)
    t = _STRIP_TRAILING_TYPE_RE.sub("", t)
    t = _STRIP_NAMED_RE.sub("", t)
    return t.strip()


def _looks_like_known_app(name: str) -> bool:
    try:
        from .entity_corrector import _COMMON_APPS, _APP_ALIASES
        n = name.lower().strip()
        if n in _APP_ALIASES:
            return True
        return any(n == app.lower() for app in _COMMON_APPS)
    except Exception:
        return False


# Known websites — mirrors web_tools._URL_MAP (kept local to avoid importing
# the tool layer into this hot-path service). Real-mic Urdu test Issue 6:
# "open youtube" used to resolve as type=unknown and masquerade as an
# unknown application launch; classifying it as a website lets the shared
# routing layer pick the browser/site path deliberately.
_KNOWN_WEBSITES = frozenset({
    "youtube", "yt", "gmail", "google", "github", "twitter", "x",
    "linkedin", "netflix", "reddit", "amazon", "chatgpt", "facebook",
    "instagram", "wikipedia", "whatsapp",
})


def _looks_like_known_website(name: str) -> bool:
    n = name.lower().strip().rstrip(".")
    if n in _KNOWN_WEBSITES:
        return True
    return (
        n.startswith(("http://", "https://", "www."))
        or n.endswith((".com", ".org", ".net", ".io", ".pk"))
    )


_ENTITY_TO_OBJECT_TYPE = {
    "folder": "folder", "file": "file", "app": "application",
    "drive": "drive", "url": "website", "installed_app": "application",
    "store_app": "application",
}


def resolve(text: str) -> ObjectResolution:
    """
    Determine object_type + name + scope for an utterance, BEFORE any tool
    is picked. Pure/fast — regex plus already-cached World State/context
    reads, no LLM, no filesystem walk of its own (that's file_resolver's
    job once the type is known). Target: well under 50ms warm.
    """
    text = (text or "").strip()
    if not text:
        return ObjectResolution(action="open", object_type="unknown", name="", confidence=0.0)

    evidence: list[str] = []
    scope = _get_scope()
    if scope:
        evidence.append(f"scope: {scope}")

    # ── Pronoun / vague reference — reuse ContextStack, never re-implement it ──
    try:
        from .context_stack import context_stack
        resolved_entity = context_stack.resolve(text)
    except Exception:
        resolved_entity = None

    if resolved_entity is not None:
        otype = _ENTITY_TO_OBJECT_TYPE.get(resolved_entity.type, "unknown")
        if otype != "unknown":
            evidence.append(
                f"context stack: resolved pronoun/reference to previous "
                f"{resolved_entity.type} {resolved_entity.display!r}"
            )
            result = ObjectResolution(
                action="open", object_type=otype, name=resolved_entity.value,
                scope=scope, confidence=0.9, evidence=evidence,
            )
            _log(result, text)
            return result

    # ── Explicit type noun — decisive, overrides fuzzy app-name similarity ──
    explicit_type, noun_evidence = _explicit_type(text)
    name = _extract_name(text)

    if explicit_type:
        evidence += noun_evidence
        result = ObjectResolution(
            action="open", object_type=explicit_type, name=name or text,
            scope=scope, confidence=0.95, evidence=evidence,
        )
        _log(result, text)
        return result

    # ── No explicit noun — "open perfume", "take me to perfume" ──────────────
    # Known app/website name always wins over a fuzzy filesystem-scope guess.
    # Live bug (2026-08-24): "Microsoft Store kholo" right after a "...in C
    # drive" turn left scope.drive="C" set, so the filesystem probe below
    # ran FIRST, fuzzy-matched "microsoft store" against something under
    # C:\, and returned type=folder before the known-app check ever got a
    # turn — routing it to smart_open's filesystem search (which then
    # correctly failed to find a folder called "microsoft store", since
    # there isn't one) instead of open_application. A name that's
    # unambiguously a known app/website is decisive; only truly unknown
    # names should fall through to the fuzzy filesystem probe.
    candidate_name = name or text
    if candidate_name and _looks_like_known_app(candidate_name):
        evidence.append(f"matches known application name: {candidate_name!r}")
        result = ObjectResolution(
            action="open", object_type="application", name=candidate_name,
            scope=scope, confidence=0.75, evidence=evidence,
        )
        _log(result, text)
        return result

    # Known websites — checked AFTER installed apps so names that are both
    # (spotify, netflix) prefer the desktop app when one is known.
    if candidate_name and _looks_like_known_website(candidate_name):
        evidence.append(f"matches known website: {candidate_name!r}")
        result = ObjectResolution(
            action="open", object_type="website", name=candidate_name,
            scope=scope, confidence=0.85, evidence=evidence,
        )
        _log(result, text)
        return result

    # Fuzzy filesystem-scope probe — only for names that are NOT already a
    # known app/website (see reasoning above). Prefers filesystem scope
    # evidence over a bare guess when already in a filesystem context
    # (Explorer open, a drive/folder was just navigated).
    if candidate_name and (scope.get("current_folder") or scope.get("drive")):
        try:
            from . import file_resolver as _fr
            probe = _fr.resolve(candidate_name, open_type="folder", drive=scope.get("drive", ""))
            if probe.decision in ("open", "confirm") and probe.confidence >= 0.5:
                evidence.append(f"filesystem match in current scope: {probe.path}")
                result = ObjectResolution(
                    action="open", object_type="folder", name=candidate_name,
                    scope=scope, confidence=min(0.85, probe.confidence), evidence=evidence,
                )
                _log(result, text)
                return result
        except Exception:
            pass

    evidence.append("no explicit type noun, no scope filesystem match, not a known app name")
    result = ObjectResolution(
        action="open", object_type="unknown", name=candidate_name,
        scope=scope, confidence=0.3, evidence=evidence,
    )
    _log(result, text)
    return result


def _log(result: ObjectResolution, text: str) -> None:
    logger.info("[OBJECT_RESOLUTION] text=%r", text[:80])
    logger.info("[OBJECT_TYPE_SELECTED] type=%s name=%r confidence=%.2f",
                result.object_type, result.name[:40], result.confidence)
    if result.scope:
        logger.info("[SEARCH_SCOPE_SELECTED] scope=%s", result.scope)
    logger.info("[OBJECT_RESOLUTION_FINAL] %s", result.to_dict())


# ── Tool-selection invariant (Part 4) ────────────────────────────────────────

def forbids_open_application(object_type: str) -> bool:
    """True if this object type must never be dispatched to open_application."""
    return object_type in FOLDER_FILE_TYPES


def tool_for(object_type: str) -> str:
    """Map a resolved object type to the existing tool that should handle it."""
    return {
        "folder":             "smart_open",
        "file":               "smart_open",
        "document":           "smart_open",
        "drive":              "open_drive",
        "project":            "smart_open",
        "workspace":          "smart_open",
        "repository":         "smart_open",
        "application":        "open_application",
        "website":            "open_url",
        "browser_tab":        "open_url",
        "settings_page":      "open_system_settings",
        "control_panel_item": "open_application",
    }.get(object_type, "smart_open")
