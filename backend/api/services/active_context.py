"""
Active Context Manager — session-scoped platform and goal tracking.

Tracks what Xyron is currently working with so follow-up commands resolve:
  open microsoft store  →  current_platform="microsoft_store"
  download whatsapp     →  resolved to install_store_app tool directly
  open downloads folder →  current_folder="Downloads"
  open it in vs code    →  opens current_folder in VS Code

Context expires after CONTEXT_TTL seconds of inactivity.
Logs: [ACTIVE_CONTEXT_UPDATE] [ACTIVE_CONTEXT_CURRENT] [ACTIVE_CONTEXT_EXPIRED]
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONTEXT_TTL = 900.0  # 15 minutes

# Map known app names/substrings to platform identifiers
_PLATFORM_FROM_APP: dict[str, str] = {
    "microsoft store":    "microsoft_store",
    "windowsstore":       "microsoft_store",
    "winstore":           "microsoft_store",
    "youtube":            "youtube",
    "chrome":             "browser",
    "firefox":            "browser",
    "edge":               "browser",
    "microsoftedge":      "browser",
    "spotify":            "spotify",
    "vs code":            "vscode",
    "vscode":             "vscode",
    "code":               "vscode",
    "terminal":           "terminal",
    "explorer":           "explorer",
    "file explorer":      "explorer",
}

_GOAL_FROM_TOOL: dict[str, str] = {
    "open_application":       "app_launch",
    "smart_open":             "file_management",
    "open_directory":         "file_management",
    "open_drive":             "filesystem_navigation",
    "create_folder":          "file_management",
    "open_file":              "file_management",
    "search_youtube":         "media_search",
    "open_url":               "web_browsing",
    "search_web":             "web_search",
    "install_store_app":      "app_installation",
    "install_store_app_exec": "app_installation",
}

# Tools whose success means the user is now doing filesystem navigation, not
# app-launching — a successful call clears any stale current_app/current_
# platform left over from an earlier, now-irrelevant app-launch turn (Part 5
# fix: "active context remained stale with app=settings" after opening a
# drive/folder — open_drive wasn't in _GOAL_FROM_TOOL at all before this, so
# nothing ever refreshed the context on a drive open).
_FILESYSTEM_NAV_TOOLS = frozenset({
    "open_directory", "smart_open", "create_folder", "open_drive",
})


def _infer_platform(tool_name: str, params: dict, result_data: dict) -> Optional[str]:
    """Infer the active platform from a successful tool execution."""
    app = (params.get("app_name") or params.get("app") or
           result_data.get("app_name") or "").lower()
    path = (params.get("path") or result_data.get("path") or
            result_data.get("action_path") or "").lower()

    for key, plat in _PLATFORM_FROM_APP.items():
        if key in app or key in path:
            return plat

    if tool_name == "search_youtube":
        return "youtube"
    if tool_name in ("open_url", "search_web"):
        return "browser"
    if tool_name in ("open_directory", "smart_open", "create_folder"):
        return "explorer"
    return None


class ActiveContextService:
    """Thread-safe singleton tracking what Xyron is currently doing."""

    def __init__(self) -> None:
        self._lock        = threading.Lock()
        self._ctx: dict   = self._empty()
        self._last_update = 0.0

    def _empty(self) -> dict[str, Any]:
        return {
            "current_app":         None,
            "current_window":      None,
            "current_platform":    None,
            "current_url":         None,
            "current_folder":      None,
            "current_drive":       None,
            "current_file":        None,
            "current_entity":      None,
            "current_entity_type": None,
            "current_goal":        None,
            "last_action":         None,
            "last_tool":           None,
            "last_success":        None,
            "last_error":          None,
            "updated_at":          None,
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    def update_from_tool(
        self,
        tool_name: str,
        params: dict,
        result_data: dict,
        success: bool,
    ) -> None:
        """Update active context after a tool execution. Must be called in a thread (not event loop)."""
        t0 = time.monotonic()

        with self._lock:
            if not success:
                self._ctx["last_error"]   = tool_name
                self._ctx["last_success"] = False
                logger.debug(
                    "[ACTIVE_CONTEXT_UPDATE] tool=%s success=False ms=%.1f",
                    tool_name, (time.monotonic() - t0) * 1000,
                )
                return

            now = time.time()
            self._last_update = now
            self._ctx["last_tool"]    = tool_name
            self._ctx["last_success"] = True
            self._ctx["last_error"]   = None
            self._ctx["updated_at"]   = now

            # Goal
            goal = _GOAL_FROM_TOOL.get(tool_name)
            if goal:
                self._ctx["current_goal"] = goal

            # Filesystem navigation (drive/folder open) means the previous
            # turn's app-launch context is no longer relevant — clear it so
            # later reasoning can't be biased toward "the user is launching
            # an app" just because that was true several turns ago. Without
            # this, current_app stayed "settings" (or whatever was last
            # launched) forever, across every subsequent drive/folder open —
            # this was a real, confirmed contributor to "perfume folder"
            # being treated as an app-launch request.
            if tool_name in _FILESYSTEM_NAV_TOOLS:
                self._ctx["current_app"] = None
                self._ctx["current_entity_type"] = "drive" if tool_name == "open_drive" else "folder"

            # App opened
            app_name = (params.get("app_name") or params.get("app") or "").strip()
            if tool_name == "open_application" and app_name:
                self._ctx["current_app"]  = app_name
                self._ctx["current_entity_type"] = "application"
                self._ctx["last_action"]  = f"opened {app_name}"
                # Microsoft Store detection
                _app_low = app_name.lower()
                if any(s in _app_low for s in ("microsoft store", "windowsstore", "winstore", "store")):
                    self._ctx["current_platform"] = "microsoft_store"
                    self._ctx["current_goal"]     = "app_installation"
                    logger.info("[ACTIVE_CONTEXT_UPDATE] platform=microsoft_store app=%s", app_name)

            # Drive opened — previously not handled at all (open_drive was
            # missing from _GOAL_FROM_TOOL), which is exactly why stale
            # app/platform context survived opening C drive then E drive.
            if tool_name == "open_drive":
                drive_letter = (params.get("drive") or result_data.get("drive") or "").strip().upper()[:1]
                if not drive_letter:
                    _dpath = result_data.get("path") or result_data.get("action_path") or ""
                    _dm = re.match(r'^([A-Za-z]):', _dpath)
                    if _dm:
                        drive_letter = _dm.group(1).upper()
                if drive_letter:
                    self._ctx["current_drive"]  = drive_letter
                    self._ctx["current_folder"] = f"{drive_letter}:\\"
                    self._ctx["current_entity"] = f"{drive_letter} drive"
                    self._ctx["last_action"]    = f"opened {drive_letter} drive"
                self._ctx["current_platform"] = "explorer"

            # Folder/file opened
            path = (result_data.get("path") or result_data.get("action_path") or
                    params.get("path") or params.get("query") or "")
            if path and tool_name in ("open_directory", "smart_open", "create_folder"):
                # Handle both Unix (/mnt/e/Foo) and Windows (E:\Foo) paths
                _norm = path.rstrip("/\\").replace("\\", "/")
                folder_name = _norm.split("/")[-1] if "/" in _norm else _norm
                self._ctx["current_folder"]   = folder_name
                self._ctx["current_entity"]   = folder_name
                self._ctx["last_action"]      = f"opened folder {folder_name}"
                _pm = re.match(r'^([A-Za-z]):', path) or re.match(r'^/mnt/([a-zA-Z])/', path)
                if _pm:
                    self._ctx["current_drive"] = _pm.group(1).upper()
                # A successful filesystem navigation always makes Explorer
                # the current platform — this used to only fire if no
                # platform was set yet, which is exactly how a stale
                # microsoft_store/app platform from an earlier turn survived
                # a later, unrelated folder open.
                self._ctx["current_platform"] = "explorer"

            # YouTube / media search
            if tool_name == "search_youtube":
                self._ctx["current_platform"] = "youtube"
                self._ctx["current_goal"]     = "media_search"
                query = params.get("query") or ""
                self._ctx["current_entity"]   = query
                self._ctx["last_action"]      = f"searched youtube for {query}"

            # Store PDP navigation / install tracking
            if tool_name in ("install_store_app", "open_store_app_page", "install_store_app_exec"):
                self._ctx["current_platform"] = "microsoft_store"
                self._ctx["current_goal"]     = "app_installation"
                _rapp  = result_data.get("app_name") or params.get("app_name") or ""
                _rid   = result_data.get("app_id") or result_data.get("product_id") or params.get("app_id") or ""
                if _rapp:
                    self._ctx["current_app"] = _rapp
                if _rid:
                    self._ctx["current_entity"] = _rid  # product_id for follow-up "install it"

            # Platform inference fallback
            if not self._ctx["current_platform"]:
                plat = _infer_platform(tool_name, params, result_data)
                if plat:
                    self._ctx["current_platform"] = plat

        ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[ACTIVE_CONTEXT_UPDATE] tool=%s platform=%s goal=%s folder=%s app=%s ms=%.1f",
            tool_name,
            self._ctx.get("current_platform"),
            self._ctx.get("current_goal"),
            self._ctx.get("current_folder"),
            self._ctx.get("current_app"),
            ms,
        )

        # World State bridge — dual-write, not a replacement: active_context
        # remains the source of truth for its own consumers, this just
        # forwards the goal inference it already computed so GoalTracker
        # (a World State component) sees it without re-deriving it.
        try:
            from .goal_tracker import goal_tracker
            goal_tracker.update_from_active_context_goal(self._ctx.get("current_goal"), source="active_context")
        except Exception:
            pass

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        """Return current context dict, or empty dict if expired."""
        with self._lock:
            if self._last_update == 0.0:
                return self._empty()
            age = time.time() - self._last_update
            if age > CONTEXT_TTL:
                logger.info(
                    "[ACTIVE_CONTEXT_EXPIRED] age=%.0fs platform=%s",
                    age, self._ctx.get("current_platform"),
                )
                self._ctx         = self._empty()
                self._last_update = 0.0
                return self._empty()
            return dict(self._ctx)

    def is_active(self) -> bool:
        ctx = self.get()
        return (ctx.get("current_platform") is not None or
                ctx.get("current_folder") is not None)

    def current_platform(self) -> Optional[str]:
        return self.get().get("current_platform")

    def current_folder(self) -> Optional[str]:
        return self.get().get("current_folder")

    def log_current(self) -> None:
        ctx = self.get()
        logger.info(
            "[ACTIVE_CONTEXT_CURRENT] platform=%s goal=%s folder=%s app=%s entity=%s",
            ctx.get("current_platform"),
            ctx.get("current_goal"),
            ctx.get("current_folder"),
            ctx.get("current_app"),
            ctx.get("current_entity"),
        )

    def reset(self) -> None:
        with self._lock:
            self._ctx         = self._empty()
            self._last_update = 0.0


# Module-level singleton
active_context = ActiveContextService()
