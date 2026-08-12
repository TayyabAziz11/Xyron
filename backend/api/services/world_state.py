"""
world_state.py — Central World State Engine.

The primary reasoning context for Xyron. Aggregates what used to be five
separately-queried subsystems — window_context, workspace_context,
explorer_context, active_context, and context_stack — into one
continuously-updated, publish/subscribe state store, so new consumers read
one thing (`world_state.get_context()`) instead of importing and calling
each sensor/tracker individually the way file_resolver.py used to.

Scope note (read before extending): this phase deliberately does NOT rip
out or replace active_context.py / context_stack.py / memory_service.py /
context_memory.py — those are live, tested, and wired into the production
voice pipeline (voice_ws.py, follow_up_resolver_v2.py) in multiple places.
WorldState *aggregates* them (context_stack is used directly as the entity
tracker — it already does that job well, see ENTITY TRACKER below) and
becomes the single new place additional context (workspace/Explorer/goal/
activity-timeline/focus) lives. Fully migrating the existing pronoun/
follow-up resolution pipelines onto WorldState is future cleanup, flagged
here rather than attempted silently — touching follow_up_resolver_v2's
already-benchmarked <50ms path was judged too risky to bundle into this
phase without a separate, dedicated pass.

Components:
  - Pub/sub core          — publish()/subscribe(), diff-only (no notification
                             if the value didn't actually change), dispatched
                             off-thread so a slow subscriber never blocks
                             the publisher (voice pipeline stays unaffected).
  - FocusGraph             — active_application / active_window / focused_object
                             / selected_object (selected_object is a Phase 2/3
                             stub — nothing produces selections yet).
  - ActivityTimeline       — see activity_timeline.py.
  - GoalTracker            — see goal_tracker.py.
  - Entity Tracker         — context_stack.py, used directly (not duplicated).
  - Reasoning Context API  — get_context(): the one dict downstream reasoning
                             should read.

Sensor fields (current_application, current_foreground_window,
current_workspace, current_project, current_explorer_folder) are refreshed
via refresh_sensors() — moved here from file_resolver.get_context_snapshot(),
which is now a thin wrapper — see file_resolver.py.
current_browser / current_url / current_tab / current_product are explicit
None stubs: Phase 2 (screen) and Phase 3 (browser/CDP) are the intended
publishers and haven't been built yet.

Logs: [WORLD_STATE_PUBLISH] [WORLD_STATE_REFRESH] [WORLD_STATE_SUBSCRIBE]
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Fields that are directly owned/settable via publish(). Everything else in
# get_context() is derived live from a sub-component (activity_timeline,
# goal_tracker, context_stack) rather than duplicated here.
_OWNED_FIELDS = frozenset({
    "current_application", "current_foreground_window", "current_workspace",
    "current_project", "current_explorer_folder", "current_file",
    "current_document", "current_task", "current_intent",
    "current_browser", "current_url", "current_tab", "current_product",
    # Phase 2 — Perception Engine publishers
    "current_selection", "current_visible_error", "monitors",
    # Phase 3.5 — GitHub structured page extraction (browser_perception)
    "current_repository",
})

_REFRESH_INTERVAL_SECONDS = 3.0


@dataclass
class FocusGraph:
    active_application: Optional[str] = None
    active_window: Optional[dict] = None
    focused_object: Optional[dict] = None   # {"type": "file"|"folder"|"workspace", "value": str}
    selected_object: Optional[dict] = None  # Phase 2/3 stub — no producer yet

    def to_dict(self) -> dict:
        return {
            "active_application": self.active_application,
            "active_window": self.active_window,
            "focused_object": self.focused_object,
            "selected_object": self.selected_object,
        }


class WorldStateService:
    """Thread-safe singleton — the central publish/subscribe reasoning context."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._fields: dict[str, Any] = {f: None for f in _OWNED_FIELDS}
        self._meta: dict[str, dict] = {}  # field -> {"updated_at": ts, "source": str}
        self._subscribers: dict[str, list[Callable[[Any, Any], None]]] = {}
        self._focus = FocusGraph()
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_stop = threading.Event()

    # ------------------------------------------------------------------
    # Pub/sub core
    # ------------------------------------------------------------------

    def publish(self, field: str, value: Any, source: str = "") -> None:
        """
        Set *field* and notify subscribers — but only if the value actually
        changed (diff-only), so a background refresher polling every few
        seconds doesn't spam subscribers when nothing moved.
        """
        with self._lock:
            old = self._fields.get(field)
            if old == value:
                return
            self._fields[field] = value
            self._meta[field] = {"updated_at": time.time(), "source": source}
            callbacks = list(self._subscribers.get(field, [])) + list(self._subscribers.get("*", []))

        logger.debug("[WORLD_STATE_PUBLISH] field=%s source=%s", field, source)
        for cb in callbacks:
            threading.Thread(target=self._dispatch, args=(cb, field, old, value), daemon=True).start()

    def _dispatch(self, cb: Callable, field: str, old: Any, new: Any) -> None:
        try:
            cb(old, new)
        except Exception:
            logger.debug("[WORLD_STATE] subscriber callback failed for field=%s", field, exc_info=True)

    def subscribe(self, field: str, callback: Callable[[Any, Any], None]) -> None:
        """field='*' subscribes to every field change."""
        with self._lock:
            self._subscribers.setdefault(field, []).append(callback)
        logger.debug("[WORLD_STATE_SUBSCRIBE] field=%s", field)

    def unsubscribe(self, field: str, callback: Callable[[Any, Any], None]) -> None:
        with self._lock:
            lst = self._subscribers.get(field)
            if lst and callback in lst:
                lst.remove(callback)

    def get(self, field: str) -> Any:
        with self._lock:
            return self._fields.get(field)

    def set_focused_object(self, obj: Optional[dict], source: str = "") -> None:
        """
        Update FocusGraph.focused_object directly — current_focus_object in
        get_context() is *derived* from FocusGraph, not from _fields, so a
        plain publish("current_focus_object", ...) would be silently
        ignored (get_context() always recomputes it from self._focus).
        Used by Vision Perception (perception_engine.request_vision) when a
        capture answers "what am I looking at" and no sensor already has an
        opinion — refresh_sensors() overwrites this on its next tick if a
        higher-priority focus (Explorer folder/workspace) is present.
        """
        with self._lock:
            old = self._focus.focused_object
            self._focus.focused_object = obj
        if old != obj:
            logger.debug("[WORLD_STATE_PUBLISH] field=current_focus_object source=%s", source)

    # ------------------------------------------------------------------
    # Sensor refresh — the migrated body of what was
    # file_resolver.get_context_snapshot() in Phase 1.5.
    # ------------------------------------------------------------------

    def refresh_sensors(self) -> None:
        """
        Query the OS-observation sensors (window/workspace/Explorer) and
        publish diffs. Cheap in the common case — window_context caches
        its own PowerShell query for 2s, so calling this repeatedly (from
        the background loop AND on-demand from file_resolver) doesn't
        multiply subprocess cost.
        """
        window = None
        try:
            from .window_context import window_context
            window = window_context.get_active_window()
        except Exception:
            pass

        proc = (window.get("proc_name") or "").lower() if window else None
        self.publish("current_application", proc, source="window_context")
        self.publish("current_foreground_window", window, source="window_context")

        workspace = None
        try:
            from .workspace_context import get_active_workspace
            workspace = get_active_workspace(window)
        except Exception:
            pass
        self.publish("current_workspace", workspace, source="workspace_context")
        project_name = workspace["root"].name if workspace and workspace.get("root") else None
        self.publish("current_project", project_name, source="workspace_context")

        try:
            from .goal_tracker import goal_tracker
            if workspace:
                goal_tracker.update_from_workspace(workspace.get("app"))
        except Exception:
            pass

        folder = None
        try:
            from .explorer_context import explorer_context
            folder = explorer_context.get_focused_folder(window)
        except Exception:
            pass
        self.publish("current_explorer_folder", str(folder) if folder else None, source="explorer_context")

        with self._lock:
            self._focus.active_application = proc
            self._focus.active_window = window
            focused = None
            if folder:
                focused = {"type": "explorer_folder", "value": str(folder)}
            elif workspace and workspace.get("root"):
                focused = {"type": "workspace", "value": str(workspace["root"])}
            self._focus.focused_object = focused

        logger.debug("[WORLD_STATE_REFRESH] app=%s workspace=%s folder=%s", proc, project_name, folder)

    # ------------------------------------------------------------------
    # Action recording — feeds ActivityTimeline + GoalTracker together,
    # so callers (voice_ws.py, file_resolver.py) have one call to make
    # instead of touching two separate trackers.
    # ------------------------------------------------------------------

    def record_action(
        self, description: str, tool: Optional[str] = None,
        entity: Optional[str] = None, success: bool = True, source: str = "",
    ) -> None:
        try:
            from .activity_timeline import activity_timeline
            activity_timeline.record(description, tool=tool, entity=entity, success=success, source=source)
        except Exception:
            logger.debug("[WORLD_STATE] activity_timeline record failed", exc_info=True)

        if tool:
            self.publish("current_intent", tool, source=source or "record_action")
            try:
                from .goal_tracker import goal_tracker
                goal_tracker.update_from_tool(tool, source=source or "record_action")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Background refresh loop
    # ------------------------------------------------------------------

    def start_background_refresh(self, interval: float = _REFRESH_INTERVAL_SECONDS) -> None:
        if self._refresh_thread is not None:
            return
        self._refresh_stop.clear()

        def _loop() -> None:
            logger.info("[WORLD_STATE] background refresh loop started (interval=%.1fs)", interval)
            while not self._refresh_stop.wait(interval):
                try:
                    self.refresh_sensors()
                except Exception:
                    logger.exception("[WORLD_STATE] background refresh failed")

        self._refresh_thread = threading.Thread(target=_loop, name="world-state-refresh", daemon=True)
        self._refresh_thread.start()

    def stop_background_refresh(self) -> None:
        self._refresh_stop.set()
        self._refresh_thread = None

    # ------------------------------------------------------------------
    # Reasoning Context API
    # ------------------------------------------------------------------

    def get_context(self, refresh: bool = False) -> dict:
        """
        The single reasoning-context snapshot downstream code should read
        instead of querying window_context/workspace_context/
        explorer_context/active_context/context_stack individually.

        refresh=True forces a fresh sensor query first (what file_resolver
        needs — it's resolving a command right now and can't work off a
        snapshot that might be several seconds stale). refresh=False (the
        default) returns whatever the background loop last published —
        for lower-priority reads (dashboards, logging) that shouldn't pay
        even a cached-lookup cost.
        """
        if refresh:
            self.refresh_sensors()

        with self._lock:
            snapshot = dict(self._fields)
            focus = self._focus.to_dict()

        lt = time.localtime()

        # Entity tracker — context_stack is used directly, not duplicated.
        recent_files: list[str] = []
        recent_folders: list[str] = []
        active_entities: list[dict] = []
        try:
            from .context_stack import context_stack
            active_entities = [
                {"type": e.type, "display": e.display, "value": e.value}
                for e in context_stack.recent(10)
            ]
            recent_files = [e.display for e in context_stack.get_all("file")[:10]]
            recent_folders = [e.display for e in context_stack.get_all("folder")[:10]]
        except Exception:
            pass

        goal = None
        goal_history: list[dict] = []
        try:
            from .goal_tracker import goal_tracker
            goal = goal_tracker.get_goal()
            goal_history = goal_tracker.history()
        except Exception:
            pass

        recent_actions: list[dict] = []
        try:
            from .activity_timeline import activity_timeline
            recent_actions = activity_timeline.to_list(20)
        except Exception:
            pass

        active_folder = None
        try:
            from .active_context import active_context
            active_folder = active_context.current_folder()
        except Exception:
            pass

        return {
            # Named fields per the World State Engine spec
            "current_application":     snapshot.get("current_application"),
            "current_foreground_window": snapshot.get("current_foreground_window"),
            "current_workspace":       snapshot.get("current_workspace"),
            "current_project":         snapshot.get("current_project"),
            "current_explorer_folder": snapshot.get("current_explorer_folder"),
            "current_browser":         snapshot.get("current_browser"),      # Phase 3 stub
            "current_url":             snapshot.get("current_url"),          # Phase 3 stub
            "current_tab":             snapshot.get("current_tab"),          # Phase 3 stub
            "current_document":        snapshot.get("current_document"),
            "current_file":            snapshot.get("current_file"),
            "current_product":         snapshot.get("current_product"),      # Phase 2/3 stub
            "current_repository":      snapshot.get("current_repository"),   # Phase 3.5 — GitHub
            "current_conversation_entities": active_entities,
            "current_task":            snapshot.get("current_task"),
            "current_goal":            goal,
            "goal_history":            goal_history,
            "current_intent":          snapshot.get("current_intent"),
            "recent_actions":          recent_actions,
            "active_entities":         active_entities,
            "recent_files":            recent_files,
            "recent_folders":          recent_folders,
            "current_focus_object":    focus.get("focused_object"),
            "focus_graph":             focus,
            "current_selection":       snapshot.get("current_selection"),
            "current_visible_error":   snapshot.get("current_visible_error"),
            "monitors":                snapshot.get("monitors"),
            # Backward-compat keys — consumed by file_resolver.py's tiers and
            # fs_index.get_usage_affinity() unchanged since Phase 1.5.
            "window":                  snapshot.get("current_foreground_window"),
            "active_app":              snapshot.get("current_application") or "",
            "active_folder":           active_folder or "",
            "active_project":          snapshot.get("current_project") or "",
            "hour":                    lt.tm_hour,
            "weekday":                 lt.tm_wday,
        }


world_state = WorldStateService()
