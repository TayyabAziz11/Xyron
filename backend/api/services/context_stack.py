"""
ContextStack — ordered entity history for pronoun/reference resolution.

Stores up to 50 recent context entities (apps opened, folders navigated,
files created, apps installed, search queries, etc.) and resolves vague
references like "it", "that", "the first one", "same folder".

Complements (does not duplicate) memory_service typed slots:
  - memory_service tracks last_app / last_file / last_folder (named slots)
  - ContextStack tracks ORDERED HISTORY of all entities, with type metadata

Used by FollowUpResolverV2 as resolution tier 1.

Thread-safe singleton. update_from_tool() mirrors active_context.update_from_tool()
pattern and is called in the same fire-and-forget asyncio.to_thread() call.

Log prefixes: [CTXSTACK_PUSH] [CTXSTACK_RESOLVE] [CTXSTACK_MISS] [CTXSTACK_SCREEN]
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

MAX_STACK = 50

# Entity types tracked
# app          — application opened (open_application)
# folder       — directory opened or created
# file         — file opened, created, or written
# url          — URL navigated to
# drive        — drive letter (e.g. "E")
# store_app    — app searched/viewed in Microsoft Store
# installed_app— app successfully installed
# search_query — web or YouTube search
# media        — local media file played
# web_video    — a specific YouTube (or other site) video actually playing,
#                as opposed to just a search query — see search_youtube's
#                autoplay path and play_youtube_video
# window       — active window from screen agent (no tool trigger)
# repository   — GitHub repo seen via screen/browser perception (Part 10)

ENTITY_TYPES = frozenset({
    "app", "folder", "file", "url", "drive",
    "store_app", "installed_app", "search_query", "media", "window",
    "repository", "web_video", "product",
})

# Maps tool names → entity type(s) they produce
_TOOL_ENTITY_MAP: dict[str, str] = {
    "open_application":       "app",
    "open_directory":         "folder",
    "create_folder":          "folder",
    "open_file":              "file",
    "write_file":             "file",
    "open_url":               "url",
    "search_web":             "search_query",
    "search_youtube":         "search_query",   # may be overridden to web_video — see _make_entity
    "play_youtube_video":     "web_video",
    "play_media_file":        "media",
    "install_store_app":      "store_app",
    "open_store_app_page":    "store_app",
    "install_store_app_exec": "installed_app",
    "open_drive":             "drive",
    "smart_open":             "folder",   # refined by params at runtime
}

# What verb → which entity type is relevant
_VERB_ENTITY_PREF: dict[str, list[str]] = {
    "open":     ["app", "folder", "file", "url", "installed_app", "store_app", "repository"],
    "close":    ["app", "window"],
    "kill":     ["app"],
    "install":  ["store_app", "installed_app"],
    "download": ["store_app", "installed_app"],
    "pin":      ["app", "installed_app"],
    "unpin":    ["app"],
    "delete":   ["file", "folder"],
    "move":     ["file", "folder"],
    "copy":     ["file", "folder"],
    "play":     ["media", "search_query", "web_video"],
    "search":   ["search_query"],
    "navigate": ["folder", "url"],
    "go":       ["folder", "url"],
    "review":   ["repository", "file", "folder"],
    "summarize": ["repository", "file"],
    "explain":  ["file", "repository"],
    "check":    ["repository"],
}

# Pronouns / vague references that trigger resolution
_PRONOUN_RE = re.compile(
    r'\b(it|this|that|them|these|those|'
    r'the\s+(?:app|file|folder|video|document|window|one|same)|'
    r'that\s+(?:app|file|folder|video|document|window|one)|'
    r'the\s+same\s+(?:one|app|folder|file)?|'
    r'same\s+(?:app|folder|file)?)\b',
    re.IGNORECASE,
)


@dataclass
class ContextEntity:
    type:       str           # from ENTITY_TYPES
    value:      str           # canonical value: path, name, URL, drive letter
    display:    str           # human-readable name for TTS
    source:     str           # tool name or "screen_agent"
    metadata:   dict = field(default_factory=dict)
    pushed_at:  float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return f"ContextEntity(type={self.type!r}, display={self.display!r})"


class ContextStack:
    """
    Thread-safe ordered entity stack.

    push() adds entities; older entries beyond MAX_STACK are dropped.
    resolve() picks the most relevant recent entity for a given command.
    update_from_tool() is the primary write path after each successful tool call.
    update_from_screen() is called by the screen_context_agent integration.
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._stack: deque[ContextEntity] = deque(maxlen=MAX_STACK)

    # ── Write ─────────────────────────────────────────────────────────────────

    def push(self, entity: ContextEntity) -> None:
        with self._lock:
            self._stack.append(entity)
        logger.info(
            "[CTXSTACK_PUSH] type=%s display=%r source=%s stack_size=%d",
            entity.type, entity.display[:40], entity.source, len(self._stack),
        )

    def update_from_tool(
        self,
        tool_name: str,
        params: dict,
        result_data: dict,
        success: bool,
    ) -> None:
        """
        Called after every tool execution (success or failure).
        Mirrors the active_context.update_from_tool() signature for symmetry.
        Only pushes entities on success.
        """
        if not success:
            return
        entity_type = _TOOL_ENTITY_MAP.get(tool_name)
        if not entity_type:
            return

        entity = self._make_entity(tool_name, entity_type, params, result_data)
        if entity:
            self.push(entity)

    def update_from_screen(self, snap: "ScreenSnapshot") -> None:  # type: ignore[name-defined]
        """
        Called by the screen_context_agent integration to record what the
        user is currently looking at (without a tool trigger).
        Only pushes if the app/context differs from the most recent stack entry.
        """
        try:
            if getattr(snap, "is_browser", False) and getattr(snap, "repository", None):
                repo = snap.repository
                entity = ContextEntity(
                    type="repository",
                    value=f"{repo.get('owner')}/{repo.get('name')}",
                    display=repo.get("name") or "repository",
                    source="screen_agent",
                    # "offer" records what describe() just offered out loud
                    # (Part 9/10) so a later bare "yes" can be resolved
                    # without a separate pending-offer state system.
                    metadata={"from_screen": True, "offer": ["review"], **repo},
                )
            elif getattr(snap, "is_browser", False) and getattr(snap, "product", None):
                product = snap.product
                entity = ContextEntity(
                    type="product",
                    value=product.get("name") or snap.browser_title or "product",
                    display=product.get("name") or snap.browser_title or "this product",
                    source="screen_agent",
                    metadata={"from_screen": True, "offer": ["cheaper", "compare"], **product},
                )
            elif snap.is_store and snap.store_page:
                entity = ContextEntity(
                    type="store_app",
                    value=snap.store_page,
                    display=snap.store_page,
                    source="screen_agent",
                    metadata={"from_screen": True},
                )
            elif snap.is_vscode and snap.project_name:
                entity = ContextEntity(
                    type="app",
                    value=snap.project_name,
                    display=snap.project_name,
                    source="screen_agent",
                    metadata={"app": "vscode", "project": snap.project_name, "from_screen": True},
                )
            elif snap.is_explorer and snap.explorer_path:
                folder = snap.explorer_path.rstrip("\\/").split("\\")[-1].split("/")[-1]
                entity = ContextEntity(
                    type="folder",
                    value=snap.explorer_path,
                    display=folder,
                    source="screen_agent",
                    metadata={"from_screen": True},
                )
            elif snap.active_app:
                entity = ContextEntity(
                    type="window",
                    value=snap.proc_name or snap.active_app,
                    display=snap.active_app,
                    source="screen_agent",
                    metadata={"from_screen": True, "title": snap.window_title},
                )
            else:
                return

            # Avoid pushing duplicate of most recent entry
            with self._lock:
                if self._stack:
                    last = self._stack[-1]
                    if (last.source == "screen_agent"
                            and last.type == entity.type
                            and last.value == entity.value):
                        return

            logger.info("[CTXSTACK_SCREEN] type=%s display=%r", entity.type, entity.display[:40])
            self.push(entity)
        except Exception as exc:
            logger.debug("[CTXSTACK_SCREEN] error: %s", exc)

    # ── Read ──────────────────────────────────────────────────────────────────

    def resolve(self, text: str) -> Optional[ContextEntity]:
        """
        Given a voice command, return the most relevant recent entity.

        Resolution strategy:
          1. Extract command verb (open/close/install/play/etc.)
          2. Use verb → preferred entity types list
          3. Walk stack newest-first, return first match
          4. Fall back to most recent entity of any type

        Returns None if stack is empty.
        """
        if not _PRONOUN_RE.search(text):
            return None  # no pronoun/vague reference — nothing to resolve

        verb = self._extract_verb(text)
        preferred = _VERB_ENTITY_PREF.get(verb, []) if verb else []

        with self._lock:
            stack_copy = list(self._stack)

        if not stack_copy:
            logger.debug("[CTXSTACK_MISS] reason=empty_stack text=%r", text[:40])
            return None

        # Walk newest-first
        # Pass 1: preferred type match
        if preferred:
            for entity in reversed(stack_copy):
                if entity.type in preferred:
                    logger.info(
                        "[CTXSTACK_RESOLVE] type=%s display=%r verb=%r source=%s",
                        entity.type, entity.display[:40], verb, entity.source,
                    )
                    return entity

        # Pass 2: any entity
        entity = stack_copy[-1]
        logger.info(
            "[CTXSTACK_RESOLVE] type=%s display=%r verb=%r source=%s (fallback)",
            entity.type, entity.display[:40], verb, entity.source,
        )
        return entity

    def get_last(self, entity_type: str) -> Optional[ContextEntity]:
        """Return the most recent entity of a given type, or None."""
        with self._lock:
            for entity in reversed(self._stack):
                if entity.type == entity_type:
                    return entity
        return None

    def get_all(self, entity_type: str) -> list[ContextEntity]:
        """Return all entities of a given type, newest first."""
        with self._lock:
            return [e for e in reversed(self._stack) if e.type == entity_type]

    def recent(self, n: int = 10) -> list[ContextEntity]:
        """Return the n most recent entities, newest first."""
        with self._lock:
            items = list(self._stack)
        return list(reversed(items))[:n]

    def peek(self) -> Optional[ContextEntity]:
        """Return the most recently pushed entity, or None."""
        with self._lock:
            return self._stack[-1] if self._stack else None

    def size(self) -> int:
        with self._lock:
            return len(self._stack)

    def clear(self) -> None:
        with self._lock:
            self._stack.clear()

    def to_summary(self) -> dict:
        """Return a compact summary dict for logging / injection into prompts."""
        recent = self.recent(5)
        return {
            "stack_size":    self.size(),
            "recent":        [{"type": e.type, "display": e.display} for e in recent],
            "last_app":      (self.get_last("app") or self.get_last("installed_app")),
            "last_folder":   self.get_last("folder"),
            "last_file":     self.get_last("file"),
            "last_store_app": self.get_last("store_app") or self.get_last("installed_app"),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_verb(self, text: str) -> Optional[str]:
        text_low = text.lower().strip()
        for verb in _VERB_ENTITY_PREF:
            if re.search(rf'\b{re.escape(verb)}\b', text_low):
                return verb
        return None

    def _make_entity(
        self,
        tool_name: str,
        entity_type: str,
        params: dict,
        result_data: dict,
    ) -> Optional[ContextEntity]:
        """Build a ContextEntity from tool execution data."""

        # ── app ──────────────────────────────────────────────────────────────
        if entity_type == "app":
            name = (params.get("app_name") or params.get("app") or
                    params.get("name") or "").strip()
            if not name:
                return None
            # Live-measured compounding bug: a garbled STT app_name (e.g.
            # "youtube. open") pushed here got fuzzy-matched by
            # entity_corrector on a LATER turn as the "best" candidate for
            # an unrelated garbled transcript, reinforcing the corruption
            # instead of ever recovering. Never let a leaked sentence
            # fragment become a future fuzzy-match candidate — same guard
            # system_tools.py's _launch_app uses to refuse launching one.
            try:
                from api.tools.system_tools import is_garbled_app_name
                if is_garbled_app_name(name):
                    logger.debug("[CTXSTACK_PUSH_SKIPPED] reason=garbled_app_name name=%r", name)
                    return None
            except Exception:
                pass
            return ContextEntity(
                type="app", value=name.lower(), display=name, source=tool_name,
                metadata={"pid": result_data.get("pid")},
            )

        # ── folder ────────────────────────────────────────────────────────────
        if entity_type == "folder":
            # For smart_open, check if result is a file or folder
            if tool_name == "smart_open":
                rtype = (result_data.get("type") or "").lower()
                if rtype == "file":
                    entity_type = "file"

            # Prefer actual resolved path from result over the query string.
            # result_data["action_path"] is what smart_open/open_directory returns
            # as the real Windows path; params["query"] is just the search term.
            path = (params.get("path") or params.get("folder_path") or
                    result_data.get("path") or result_data.get("action_path") or
                    params.get("query") or "").strip()
            if not path:
                return None
            norm  = path.rstrip("/\\").replace("\\", "/")
            fname = norm.split("/")[-1] if "/" in norm else norm
            return ContextEntity(
                type=entity_type, value=path, display=fname, source=tool_name,
                metadata={"full_path": path},
            )

        # ── file ──────────────────────────────────────────────────────────────
        if entity_type == "file":
            path = (params.get("path") or params.get("file_path") or
                    result_data.get("path") or "").strip()
            if not path:
                return None
            fname = path.replace("\\", "/").split("/")[-1]
            return ContextEntity(
                type="file", value=path, display=fname, source=tool_name,
                metadata={"full_path": path},
            )

        # ── url ───────────────────────────────────────────────────────────────
        if entity_type == "url":
            url = (params.get("url") or params.get("query") or
                   result_data.get("url") or "").strip()
            if not url:
                return None
            return ContextEntity(
                type="url", value=url, display=url[:60], source=tool_name,
            )

        # ── drive ─────────────────────────────────────────────────────────────
        if entity_type == "drive":
            drive = (params.get("drive") or params.get("letter") or
                     result_data.get("drive") or "").strip().upper()
            if not drive:
                # Try extracting from path
                path = params.get("path") or ""
                m = re.match(r'^([A-Z]):', path.upper())
                if m:
                    drive = m.group(1)
            if not drive:
                return None
            return ContextEntity(
                type="drive", value=drive, display=f"{drive} drive", source=tool_name,
            )

        # ── store_app ─────────────────────────────────────────────────────────
        if entity_type == "store_app":
            name = (result_data.get("app_name") or params.get("app_name") or "").strip()
            app_id = (result_data.get("app_id") or result_data.get("product_id") or
                      params.get("app_id") or "").strip()
            if not name:
                return None
            return ContextEntity(
                type="store_app", value=name.lower(), display=name, source=tool_name,
                metadata={"app_id": app_id},
            )

        # ── installed_app ─────────────────────────────────────────────────────
        if entity_type == "installed_app":
            name = (result_data.get("app_name") or params.get("app_name") or "").strip()
            if not name:
                return None
            return ContextEntity(
                type="installed_app", value=name.lower(), display=name, source=tool_name,
                metadata={"app_id": params.get("app_id") or ""},
            )

        # ── search_query ──────────────────────────────────────────────────────
        if entity_type == "search_query":
            query = (params.get("query") or params.get("q") or "").strip()
            if not query:
                return None
            # search_youtube may have autoplayed a specific video rather than
            # just leaving a search page open — if so, track the actual
            # video (web_video) instead of the search term, so "pause it" /
            # "play it again" resolve to what's really playing, not a re-search.
            played_url = (result_data.get("url") or "").strip()
            if tool_name == "search_youtube" and result_data.get("autoplayed") and played_url:
                title = (result_data.get("title") or query).strip()
                return ContextEntity(
                    type="web_video", value=played_url, display=title, source=tool_name,
                    metadata={"url": played_url, "source_query": query},
                )
            return ContextEntity(
                type="search_query", value=query.lower(), display=query, source=tool_name,
            )

        # ── web_video ─────────────────────────────────────────────────────────
        if entity_type == "web_video":
            url = (params.get("url") or result_data.get("url") or "").strip()
            if not url:
                return None
            title = (params.get("title") or result_data.get("title") or url).strip()
            return ContextEntity(
                type="web_video", value=url, display=title, source=tool_name,
                metadata={"url": url},
            )

        # ── media ─────────────────────────────────────────────────────────────
        if entity_type == "media":
            query = (params.get("query") or result_data.get("title") or "").strip()
            if not query:
                return None
            return ContextEntity(
                type="media", value=query.lower(), display=query, source=tool_name,
            )

        return None


# Module-level singleton
context_stack = ContextStack()
