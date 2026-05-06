"""
ContextMemoryService — session-scoped action memory for pronoun/reference resolution.

Tracks the last executed action with its entity list and paths so commands like
"delete them", "open it", "do the same again" resolve correctly without GPT.

Thread-safe singleton.  Persists to ~/.xyron/context_memory.json across restarts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STORE_PATH = Path.home() / ".xyron" / "context_memory.json"

# Pronoun / reference patterns that should resolve to remembered context
_PRONOUN_DELETE_RE = (
    r'\b(?:delete|remove|erase|wipe)\s+'
    r'(?:them|those|it|that|all|all\s+(?:of\s+)?(?:them|those)|'
    r'those\s+folders?|those\s+files?|the\s+(?:folders?|files?)|'
    r'that\s+folder|that\s+file)\b'
)

_PRONOUN_OPEN_RE = (
    r'\b(?:open|show|navigate\s+to|go\s+to)\s+'
    r'(?:it|that|the\s+folder|that\s+folder|it\s+now)\b'
)

_REPEAT_RE = (
    r'\b(?:do\s+(?:it\s+)?again|repeat\s+(?:that|last|it)|'
    r'same\s+(?:thing|action)|one\s+more\s+time)\b'
)


class ContextMemoryService:
    """Lightweight action memory — stores what was last done and what entities were involved."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memory: dict[str, Any] = {
            "last_action": None,
            "recent_entities": [],
            "session_history": [],
        }
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if _STORE_PATH.exists():
                data = json.loads(_STORE_PATH.read_text())
                if isinstance(data, dict):
                    self._memory.update(data)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STORE_PATH.write_text(json.dumps(self._memory, indent=2))
        except Exception:
            pass

    # ── Write ─────────────────────────────────────────────────────────────────

    def record_action(
        self,
        action_type: str,
        entities: list[str],
        paths: list[str],
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Call after every tool execution.

        action_type: "create_folder", "create_subfolders", "delete_file", "open_directory", etc.
        entities:    human-readable names ["top secret", "Documents", "Pictures"]
        paths:       resolved Windows paths  ["C:\\top secret", "C:\\top secret\\Documents"]
        extra:       any additional metadata to store
        """
        entry: dict[str, Any] = {
            "type":      action_type,
            "entities":  entities,
            "paths":     paths,
            "timestamp": int(time.time()),
        }
        if extra:
            entry.update(extra)

        with self._lock:
            self._memory["last_action"] = entry
            # Prepend to recent_entities (deduplicated, max 20)
            for e in entities:
                if e and e not in self._memory["recent_entities"]:
                    self._memory["recent_entities"].insert(0, e)
            self._memory["recent_entities"] = self._memory["recent_entities"][:20]
            # Session history (last 50 actions)
            self._memory["session_history"].insert(0, entry)
            self._memory["session_history"] = self._memory["session_history"][:50]
        self._save()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_last_action(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._memory["last_action"]) if self._memory["last_action"] else None

    def get_last_paths(self) -> list[str]:
        """Return paths from the most recent action (e.g. for 'delete them')."""
        with self._lock:
            la = self._memory["last_action"]
            return list(la["paths"]) if la else []

    def get_last_path(self) -> str:
        """Return single path from last action (e.g. for 'open it')."""
        paths = self.get_last_paths()
        return paths[0] if paths else ""

    def get_recent_entities(self) -> list[str]:
        with self._lock:
            return list(self._memory["recent_entities"])

    def resolve_references(self, command: str) -> dict[str, Any]:
        """
        Analyse a command for pronoun references and return a resolution dict.

        Returns:
          {
            "is_delete_ref":  bool,   # "delete them/those/it"
            "is_open_ref":    bool,   # "open it/that"
            "is_repeat":      bool,   # "do it again"
            "resolved_paths": [],     # paths to act on
            "last_action":    {...},  # last action record
          }
        """
        import re
        result: dict[str, Any] = {
            "is_delete_ref":  False,
            "is_open_ref":    False,
            "is_repeat":      False,
            "resolved_paths": [],
            "last_action":    self.get_last_action(),
        }

        lower = command.lower().strip()

        if re.search(_PRONOUN_DELETE_RE, lower, re.IGNORECASE):
            result["is_delete_ref"] = True
            result["resolved_paths"] = self.get_last_paths()

        elif re.search(_PRONOUN_OPEN_RE, lower, re.IGNORECASE):
            result["is_open_ref"] = True
            p = self.get_last_path()
            if p:
                result["resolved_paths"] = [p]

        elif re.search(_REPEAT_RE, lower, re.IGNORECASE):
            result["is_repeat"] = True
            result["resolved_paths"] = self.get_last_paths()

        return result

    def clear_session(self) -> None:
        with self._lock:
            self._memory["last_action"] = None
            self._memory["recent_entities"] = []
        self._save()


# Singleton
context_memory = ContextMemoryService()
