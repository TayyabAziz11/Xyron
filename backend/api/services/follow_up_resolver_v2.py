"""
FollowUpResolverV2 — 5-tier context-aware command expansion.

Resolution order (stops at first match):
  Tier 1 — ContextStack  (entity history: last app/folder/file/store_app/etc.)
  Tier 2 — ScreenContext (live screen state — cached only, <50ms)
  Tier 3 — SessionState  (pending_confirmation, pending_open_after_install)
  Tier 4 — MemoryService typed slots (last_app / last_file / last_folder)
  Tier 5 — V1 fallback   (active_context platform-pattern matching)

Performance: <50ms total.
  Tiers 1–4 are pure Python (<1ms each).
  Tier 2 uses cached screen snapshot only — never triggers a fresh PS query.
  Tier 5 delegates to follow_up_resolver.resolve() (<5ms).

Log prefixes:
  [FOLLOWUP_INPUT]    — raw transcript + active_ctx summary
  [FOLLOWUP_RESOLVED] — what the resolver returned
  [FOLLOWUP_SOURCE]   — which tier resolved it (ctxstack/screen/session/memory/v1/none)
  [FOLLOWUP_ENTITY]   — the entity that was resolved
  [FOLLOWUP_FINAL]    — final resolved command or tool dispatched
  [FOLLOWUP_RESOLVER_V2_MS] — total latency
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

# Re-export FollowUpResult so callers can import from either module
from api.services.follow_up_resolver import FollowUpResult, _clean_store_query  # noqa: F401

logger = logging.getLogger(__name__)

# ── Pronoun patterns ─────────────────────────────────────────────────────────

_PRONOUN_RE = re.compile(
    r'\b(it|this|that|them|these|those|'
    r'the\s+(?:app|file|folder|video|document|window|one|same)|'
    r'that\s+(?:app|file|folder|video|document|window|one)|'
    r'the\s+same\s+(?:one|app|folder|file)?|'
    r'same\s+(?:app|folder|file)?)\b',
    re.IGNORECASE,
)

# "open it" / "launch it" / "run it" / "start it"
_OPEN_IT_RE   = re.compile(r'^(?:open|launch|run|start)\s+it\s*(?:now)?\s*[.!]?\s*$', re.IGNORECASE)
# "close it" / "kill it" / "quit it"
_CLOSE_IT_RE  = re.compile(r'^(?:close|kill|quit|stop|exit)\s+it\s*[.!]?\s*$', re.IGNORECASE)
# "install it" / "download it" / "continue" / "proceed"
# _INSTALL_IT_RE and _BARE_CONTINUE_RE imported from follow_up_resolver.py
# (v1) rather than redefined here — this file used to keep its own copy,
# which drifted from v1's and independently missed the same "Yes. Install
# it." / trailing-chatter fixes v1 got. Single source of truth now, same
# fix applies everywhere it's used.
from api.services.follow_up_resolver import (  # noqa: E402
    _INSTALL_IT_RE,
    _BARE_CONTINUE_RE,
)
# "pin it" / "unpin it"
_PIN_IT_RE    = re.compile(r'^(?:pin|unpin)\s+(?:it|this|that|the\s+app)\s*[.!]?\s*$', re.IGNORECASE)
# "play it" / "play that"
_PLAY_IT_RE   = re.compile(r'^(?:play|watch)\s+(?:it|this|that)\s*[.!]?\s*$', re.IGNORECASE)
# "open it again" / "open same folder"
_REOPEN_RE    = re.compile(r'^(?:open|go\s+to)\s+(?:it|that|the\s+same|same)\s+(?:again|folder|directory)?\s*[.!]?\s*$', re.IGNORECASE)


def resolve_v2(
    text: str,
    active_ctx: dict[str, Any],
    context_stack: Optional[Any] = None,   # ContextStack instance
    session_state: Optional[dict] = None,
) -> FollowUpResult:
    """
    Top-level resolver. Returns a FollowUpResult.

    Signature matches follow_up_resolver.resolve() plus two optional args,
    so voice_ws.py can upgrade with minimal code change.
    """
    t0   = time.monotonic()
    text_stripped = text.strip()
    # Live bug: "now, play it." — v1's resolve() strips this leading filler
    # internally before matching its own patterns, but that stripped copy
    # never left v1, so v2's OWN pronoun tiers (anchored patterns like
    # _PLAY_IT_RE) still saw the unstripped "now, play it." and never
    # matched either — the pronoun follow-up fell through everywhere.
    # Apply the same strip once, here, so every v2 tier benefits.
    try:
        from api.services.follow_up_resolver import _strip_leading_filler
        text_stripped = _strip_leading_filler(text_stripped)
    except Exception:
        pass

    logger.debug(
        "[FOLLOWUP_INPUT] text=%r platform=%s folder=%s",
        text_stripped[:60],
        active_ctx.get("current_platform", ""),
        active_ctx.get("current_folder", ""),
    )

    result = _resolve(text_stripped, active_ctx, context_stack, session_state)

    ms = (time.monotonic() - t0) * 1000
    if result.tool_name:
        logger.info(
            "[FOLLOWUP_FINAL] text=%r tool=%s params=%s source=%s ms=%.1f",
            text_stripped[:50], result.tool_name, result.tool_params,
            result.context_used.get("_source", "?"), ms,
        )
    elif result.was_resolved:
        logger.info(
            "[FOLLOWUP_FINAL] text=%r → %r source=%s ms=%.1f",
            text_stripped[:50], result.resolved[:50],
            result.context_used.get("_source", "?"), ms,
        )
    logger.info("[FOLLOWUP_RESOLVER_V2_MS] ms=%.1f was_resolved=%s tool=%s",
                ms, result.was_resolved, result.tool_name or "none")
    return result


def _resolve(
    text: str,
    active_ctx: dict,
    context_stack: Optional[Any],
    session_state: Optional[dict],
) -> FollowUpResult:

    # ── Fast path: V1 platform-context patterns (store/youtube/explorer/web) ─
    # These are the most common flows and must stay fast. "web" covers any
    # website opened via open_application's web-shortcut branch or open_url
    # that ISN'T youtube specifically (github, gmail, etc.) — added for the
    # generic click/select/fill/submit interaction tier in follow_up_resolver.py.
    platform = (active_ctx.get("current_platform") or "").lower()
    if platform in ("microsoft_store", "youtube", "explorer", "web"):
        from api.services.follow_up_resolver import resolve as _v1
        v1 = _v1(text, active_ctx)
        if v1.was_resolved or v1.tool_name or v1.needs_clarification:
            v1.context_used["_source"] = "v1_platform"
            logger.info("[FOLLOWUP_SOURCE] tier=v1_platform platform=%s", platform)
            return v1

    # Only proceed with V2 tiers if there's a pronoun/vague reference
    has_pronoun = bool(_PRONOUN_RE.search(text))

    # ── Tier 1: ContextStack ─────────────────────────────────────────────────
    if has_pronoun and context_stack is not None:
        r = _tier_context_stack(text, context_stack)
        if r:
            r.context_used["_source"] = "ctxstack"
            logger.info("[FOLLOWUP_SOURCE] tier=ctxstack entity=%r", r.context_used.get("entity"))
            return r

    # ── Tier 2: ScreenContext (cached only) ──────────────────────────────────
    if has_pronoun:
        r = _tier_screen_context(text)
        if r:
            r.context_used["_source"] = "screen_context"
            logger.info("[FOLLOWUP_SOURCE] tier=screen_context")
            return r

    # ── Tier 3: SessionState ─────────────────────────────────────────────────
    if has_pronoun and session_state:
        r = _tier_session_state(text, session_state)
        if r:
            r.context_used["_source"] = "session_state"
            logger.info("[FOLLOWUP_SOURCE] tier=session_state")
            return r

    # ── Tier 4: MemoryService typed slots ────────────────────────────────────
    if has_pronoun:
        r = _tier_memory_slots(text)
        if r:
            r.context_used["_source"] = "memory_slots"
            logger.info("[FOLLOWUP_SOURCE] tier=memory_slots")
            return r

    # ── Tier 5: V1 full fallback ─────────────────────────────────────────────
    from api.services.follow_up_resolver import resolve as _v1
    v1 = _v1(text, active_ctx)
    v1.context_used["_source"] = "v1_fallback"
    if v1.was_resolved or v1.tool_name:
        logger.info("[FOLLOWUP_SOURCE] tier=v1_fallback")
    return v1


# ── Tier 1 implementation ─────────────────────────────────────────────────────

def _tier_context_stack(text: str, stack: Any) -> Optional[FollowUpResult]:
    """
    Resolve from the ContextStack entity history.

    Handles:
      "open it"     → last app or folder
      "close it"    → last app
      "install it"  → last store_app
      "play it"     → last media or search_query
      "open it again" → last folder
    """
    try:
        entity = stack.resolve(text)
        if not entity:
            logger.debug("[CTXSTACK_MISS] text=%r", text[:40])
            return None

        logger.info("[FOLLOWUP_ENTITY] ctxstack type=%s display=%r", entity.type, entity.display[:40])

        # "install it" / "download it" — last store_app
        if _INSTALL_IT_RE.match(text) or _BARE_CONTINUE_RE.match(text):
            # For store_app, try to get the app_id from metadata for direct exec
            if entity.type in ("store_app", "installed_app"):
                app_id = entity.metadata.get("app_id") or ""
                if app_id:
                    return FollowUpResult(
                        resolved=text, was_resolved=True,
                        tool_name="install_store_app_exec",
                        tool_params={"app_name": entity.display, "app_id": app_id, "source": "msstore"},
                        context_used={"entity": entity.display, "app_id": app_id},
                    )
                return FollowUpResult(
                    resolved=text, was_resolved=True,
                    tool_name="install_store_app",
                    tool_params={"app_name": entity.display},
                    context_used={"entity": entity.display},
                )

        # "open it" / "launch it" / "open it again"
        if _OPEN_IT_RE.match(text) or _REOPEN_RE.match(text):
            logger.info("[FOLLOWUP_ENTITY_TYPE] entity_type=%s display=%r", entity.type, entity.display[:40])

            if entity.type == "app":
                return FollowUpResult(
                    resolved=f"open {entity.display}",
                    was_resolved=True,
                    tool_name="open_application",
                    tool_params={"app_name": entity.display},
                    context_used={"entity": entity.display},
                )

            if entity.type in ("folder", "drive", "window"):
                # Folders always go to smart_open (searches by name) or open_directory
                # (if we have an exact path). Never open_application.
                full_path = entity.metadata.get("full_path") or ""
                if full_path:
                    logger.info("[FOLLOWUP_FOLDER_RESOLVED] path=%r tool=open_directory", full_path)
                    return FollowUpResult(
                        resolved=f"open {entity.display} folder",
                        was_resolved=True,
                        tool_name="open_directory",
                        tool_params={"path": full_path},
                        context_used={"entity": entity.display, "path": full_path},
                    )
                else:
                    logger.info("[FOLLOWUP_FOLDER_RESOLVED] query=%r tool=smart_open", entity.display)
                    return FollowUpResult(
                        resolved=f"open {entity.display} folder",
                        was_resolved=True,
                        tool_name="smart_open",
                        tool_params={"query": entity.display, "type": "folder"},
                        context_used={"entity": entity.display},
                    )

            if entity.type in ("installed_app", "store_app"):
                return FollowUpResult(
                    resolved=f"open {entity.display}",
                    was_resolved=True,
                    tool_name="open_application",
                    tool_params={"app_name": entity.display},
                    context_used={"entity": entity.display},
                )
            if entity.type == "url":
                return FollowUpResult(
                    resolved=f"open {entity.value}",
                    was_resolved=True,
                    tool_name="open_url",
                    tool_params={"url": entity.value},
                    context_used={"entity": entity.display},
                )

        # "close it" / "kill it"
        if _CLOSE_IT_RE.match(text):
            if entity.type in ("app", "installed_app", "window"):
                return FollowUpResult(
                    resolved=f"close {entity.display}",
                    was_resolved=True,
                    tool_name="kill_app",
                    tool_params={"app_name": entity.display},
                    context_used={"entity": entity.display},
                )

        # "play it"
        if _PLAY_IT_RE.match(text):
            if entity.type == "web_video":
                # A specific video already autoplayed — "play it again" /
                # "now play it" should replay THAT video, not re-search the
                # original query (the "now play it" garbage-query bug).
                return FollowUpResult(
                    resolved=f"play {entity.display}",
                    was_resolved=True,
                    tool_name="play_youtube_video",
                    tool_params={"url": entity.metadata.get("url", entity.value), "title": entity.display},
                    context_used={"entity": entity.display},
                )
            if entity.type == "media":
                return FollowUpResult(
                    resolved=f"play {entity.display}",
                    was_resolved=True,
                    tool_name="play_media_file",
                    tool_params={"query": entity.display, "media_type": "video"},
                    context_used={"entity": entity.display},
                )
            if entity.type == "search_query":
                return FollowUpResult(
                    resolved=f"play {entity.display} on youtube",
                    was_resolved=True,
                    tool_name="search_youtube",
                    tool_params={"query": entity.display, "intent": "play"},
                    context_used={"entity": entity.display},
                )

    except Exception as exc:
        logger.debug("[TIER1_CTXSTACK] error: %s", exc)
    return None


# ── Tier 2 implementation ─────────────────────────────────────────────────────

def _tier_screen_context(text: str) -> Optional[FollowUpResult]:
    """
    Use cached screen snapshot to resolve pronoun.
    ONLY uses hot cache — never triggers a fresh PS query.
    """
    try:
        from api.services.screen_context_agent import screen_context_agent as _sca
        with _sca._lock:
            snap = _sca._last_snap
            last_at = _sca._last_at
        if snap is None or (time.monotonic() - last_at) > 30.0:
            return None  # no fresh snapshot

        logger.debug("[TIER2_SCREEN] app=%r store=%s vscode=%s", snap.active_app, snap.is_store, snap.is_vscode)

        # "install it" when store is showing a product page
        if (_INSTALL_IT_RE.match(text) or _BARE_CONTINUE_RE.match(text)) and snap.is_store and snap.store_page:
            return FollowUpResult(
                resolved=text, was_resolved=True,
                tool_name="install_store_app",
                tool_params={"app_name": snap.store_page},
                context_used={"entity": snap.store_page, "from_screen": True},
            )

        # "open it" when VS Code project is visible
        if _OPEN_IT_RE.match(text) and snap.is_vscode and snap.project_name:
            return FollowUpResult(
                resolved=f"open {snap.project_name} in vs code",
                was_resolved=True,
                context_used={"entity": snap.project_name, "from_screen": True},
            )

        # "open it" when Explorer is visible with a path
        if (_OPEN_IT_RE.match(text) or _REOPEN_RE.match(text)) and snap.is_explorer and snap.explorer_path:
            folder = snap.explorer_path.rstrip("\\/").split("\\")[-1].split("/")[-1]
            return FollowUpResult(
                resolved=f"open {folder} folder",
                was_resolved=True,
                tool_name="open_directory",
                tool_params={"path": snap.explorer_path},
                context_used={"entity": folder, "from_screen": True},
            )

    except Exception as exc:
        logger.debug("[TIER2_SCREEN] error: %s", exc)
    return None


# ── Tier 3 implementation ─────────────────────────────────────────────────────

def _tier_session_state(text: str, session_state: dict) -> Optional[FollowUpResult]:
    """
    Resolve from active session state (pending open-after-install, etc.).
    Mostly covers the "open it" case right after an install completes.
    """
    try:
        pending_oai = session_state.get("pending_open_after_install")
        if pending_oai:
            app_name = pending_oai.get("app_name", "")
            if app_name and _OPEN_IT_RE.match(text):
                return FollowUpResult(
                    resolved=f"open {app_name}",
                    was_resolved=True,
                    tool_name="open_application",
                    tool_params={"app_name": app_name},
                    context_used={"entity": app_name, "from_session": True},
                )
    except Exception as exc:
        logger.debug("[TIER3_SESSION] error: %s", exc)
    return None


# ── Tier 4 implementation ─────────────────────────────────────────────────────

def _tier_memory_slots(text: str) -> Optional[FollowUpResult]:
    """
    Resolve from memory_service typed slots (last_app / last_file / last_folder).
    These are the traditional pronoun resolution paths already available in context_resolver.
    FollowUpResolverV2 taps them for TOOL DISPATCH (not just text rewrite).
    """
    try:
        from api.services.memory_service import memory_service as _ms
        ctx = _ms.get_context()

        # "close it" / "kill it" → last_app
        if _CLOSE_IT_RE.match(text):
            slot = ctx.get("last_app")
            if slot and slot.get("name"):
                app_name = slot["name"]
                logger.info("[FOLLOWUP_ENTITY] memory_slots last_app=%r", app_name)
                return FollowUpResult(
                    resolved=f"close {app_name}",
                    was_resolved=True,
                    tool_name="kill_app",
                    tool_params={"app_name": app_name},
                    context_used={"entity": app_name},
                )

        # "open it" → last_app (prefer app over folder here since user said "open")
        if _OPEN_IT_RE.match(text):
            slot = ctx.get("last_app")
            if slot and slot.get("name"):
                app_name = slot["name"]
                logger.info("[FOLLOWUP_ENTITY] memory_slots last_app=%r", app_name)
                return FollowUpResult(
                    resolved=f"open {app_name}",
                    was_resolved=True,
                    tool_name="open_application",
                    tool_params={"app_name": app_name},
                    context_used={"entity": app_name},
                )
            slot = ctx.get("last_folder")
            if slot and slot.get("path"):
                path    = slot["path"]
                fname   = path.replace("\\", "/").split("/")[-1] or path
                logger.info("[FOLLOWUP_ENTITY] memory_slots last_folder=%r", path)
                return FollowUpResult(
                    resolved=f"open {fname} folder",
                    was_resolved=True,
                    tool_name="open_directory",
                    tool_params={"path": path},
                    context_used={"entity": fname, "path": path},
                )

    except Exception as exc:
        logger.debug("[TIER4_MEMORY] error: %s", exc)
    return None
