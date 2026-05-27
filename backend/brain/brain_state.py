"""
Brain State — persistent runtime state for the Xyron brain.

Saved to data/brain/brain_state.json on every update.
Loaded at startup. Thread-safe via a simple lock.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = Path(__file__).parent.parent / "data" / "brain" / "brain_state.json"

# Autonomy level semantics
AUTONOMY_LEVELS = {
    0: "reactive",          # only responds when asked
    1: "suggestive",        # suggests but never acts
    2: "assisted",          # acts on low-risk, confirms medium/high
    3: "autonomous+confirm", # acts autonomously, confirms only high-risk
    4: "high",              # acts autonomously with minimal confirmation
}

_DEFAULT_STATE: dict[str, Any] = {
    "current_mode":          "voice",
    "active_goal":           None,
    "operator_name":         "Tayyab",
    "recent_topics":         [],
    "recent_upgrades":       [],
    "active_agents":         [],
    "last_agent_used":       None,
    "current_emotion":       "neutral",
    "current_focus":         None,
    "conversation_summary":  "",
    "pending_tasks":         [],
    "planned_upgrades":      [],
    "last_decision":         {},
    "confidence_history":    [],
    "autonomy_level":        2,
    "identity_mode":         "PUBLIC",
    "session_count":         0,
    "total_commands":        0,
    "last_updated":          None,
}


class BrainState:
    """
    Thread-safe, auto-persisting brain state.

    Access fields directly: brain_state.current_emotion
    Update via .update(**kwargs) — triggers auto-save.
    """

    def __init__(self) -> None:
        self._lock   = threading.Lock()
        self._state: dict[str, Any] = dict(_DEFAULT_STATE)
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if _STATE_PATH.exists():
                raw = json.loads(_STATE_PATH.read_text())
                with self._lock:
                    self._state.update(raw)
                logger.info("[BRAIN_STATE] loaded from %s", _STATE_PATH)
        except Exception as exc:
            logger.warning("[BRAIN_STATE] load failed (%s), using defaults", exc)

    def _save(self) -> None:
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = dict(self._state)
            data["last_updated"] = datetime.now().isoformat()
            _STATE_PATH.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.warning("[BRAIN_STATE] save failed: %s", exc)

    # ── Read access ────────────────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        with self._lock:
            if name in self._state:
                return self._state[name]
        raise AttributeError(f"BrainState has no field '{name}'")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    # ── Write access ───────────────────────────────────────────────────────────

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if k in self._state:
                    self._state[k] = v
                else:
                    logger.debug("[BRAIN_STATE] unknown field %r, adding", k)
                    self._state[k] = v
        self._save()

    def push_topic(self, topic: str) -> None:
        with self._lock:
            topics: list = self._state["recent_topics"]
            if topic and topic not in topics:
                topics.insert(0, topic)
                self._state["recent_topics"] = topics[:10]
        self._save()

    def push_upgrade(self, upgrade: str) -> None:
        with self._lock:
            upgrades: list = self._state["recent_upgrades"]
            upgrades.insert(0, {"upgrade": upgrade, "at": datetime.now().isoformat()})
            self._state["recent_upgrades"] = upgrades[:20]
        self._save()

    def record_decision(self, decision: dict[str, Any]) -> None:
        with self._lock:
            self._state["last_decision"] = {**decision, "at": datetime.now().isoformat()}
            hist: list = self._state["confidence_history"]
            if "confidence" in decision:
                hist.insert(0, decision["confidence"])
                self._state["confidence_history"] = hist[:50]
            self._state["total_commands"] = self._state.get("total_commands", 0) + 1
        self._save()

    def set_agent_active(self, agent_id: str) -> None:
        with self._lock:
            active: list = self._state["active_agents"]
            if agent_id not in active:
                active.append(agent_id)
            self._state["last_agent_used"] = agent_id
        self._save()

    def clear_agent(self, agent_id: str) -> None:
        with self._lock:
            active: list = self._state["active_agents"]
            self._state["active_agents"] = [a for a in active if a != agent_id]
        self._save()

    @property
    def autonomy_label(self) -> str:
        return AUTONOMY_LEVELS.get(self._state.get("autonomy_level", 2), "assisted")

    def increment_session(self) -> None:
        with self._lock:
            self._state["session_count"] = self._state.get("session_count", 0) + 1
        self._save()

    def reset_to_defaults(self) -> None:
        with self._lock:
            self._state = dict(_DEFAULT_STATE)
        self._save()


# ── Module-level singleton ────────────────────────────────────────────────────

brain_state = BrainState()
