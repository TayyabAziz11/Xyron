"""
Memory Service — short-term session memory + long-term user facts.

Short-term:  per-session circular buffer of last 20 conversation turns,
             keyed by session_id generated on the frontend per session.
Long-term:   rule-based extraction of user facts (name, contacts, prefs,
             profession, location, interests, work schedule, email).
             Also stores explicit "remember that..." notes from the user.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict, deque
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_TURNS = 40   # stored per session (20 user + 20 assistant)
_MAX_FACTS = 100  # increased to hold richer profile

# Persist facts to disk so memory survives restarts
_MEMORY_DIR  = Path.home() / ".ai-operator"
_MEMORY_FILE = _MEMORY_DIR / "memory.json"

# Human-readable labels for known fact keys
_FACT_LABELS: dict[str, str] = {
    "user_name":           "Your name",
    "profession":          "Your profession",
    "location":            "Your location",
    "work_schedule":       "Your work schedule",
    "interest":            "Your interests",
    "language_preference": "Your preferred language",
    "last_contact":        "Last mentioned contact",
    "preference":          "Your preferences",
    "email":               "Your email",
    "employer":            "Where you work",
}


class MemoryService:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # session_id → deque[{"role": "user"|"assistant", "text": str}]
        self._sessions: dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_TURNS))
        # long-term: {key → value}  — loaded from disk at startup
        self._facts: dict[str, str] = {}
        # Last executed action — for "do it again" / "open it" resolution (legacy, kept for compat)
        self._last_action: dict | None = None
        # Disambiguation state — stores multiple_matches list when user must choose
        self._disambiguation: dict | None = None  # {"matches": [...], "query": str, "params": {...}}
        # Multi-slot context — typed slots for precise pronoun resolution
        # Each slot: None or a small dict with entity-specific fields.
        self._context: dict[str, dict | None] = {
            "last_app":    None,   # {"name": str, "pid": int|None}
            "last_file":   None,   # {"path": str, "name": str}
            "last_folder": None,   # {"path": str, "name": str}
            "last_url":    None,   # {"url": str, "title": str}
        }
        # Per-session routing state (model used, complexity scores, depth)
        self._routing: dict[str, dict] = {}
        # Load persisted facts from disk
        self._load_facts()

    # ── Disk persistence ──────────────────────────────────────────────────────

    def _load_facts(self) -> None:
        """Load persisted facts + context slots from ~/.ai-operator/memory.json."""
        try:
            if _MEMORY_FILE.exists():
                data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Support both old format (flat dict) and new format (with _context key)
                    if "_context" in data:
                        self._context.update({
                            k: v for k, v in data.pop("_context", {}).items()
                            if k in self._context
                        })
                    self._facts = data
                    logger.info("Memory loaded: %d facts from %s", len(self._facts), _MEMORY_FILE)
        except Exception as exc:
            logger.warning("Could not load memory file: %s", exc)

    def _save_facts(self) -> None:
        """Persist current facts + context slots to ~/.ai-operator/memory.json."""
        try:
            _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            payload = dict(self._facts)
            payload["_context"] = self._context
            _MEMORY_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Could not save memory file: %s", exc)

    # ── Short-term memory ─────────────────────────────────────────────────────

    def add_turn(self, session_id: str, user_text: str, assistant_text: str) -> None:
        """Store one conversation turn and extract facts from user message."""
        with self._lock:
            q = self._sessions[session_id]
            q.append({"role": "user",      "text": user_text})
            q.append({"role": "assistant", "text": assistant_text})
        self._maybe_extract_facts(user_text)

    def get_history(self, session_id: str) -> list[dict]:
        """Return stored turns for this session (oldest first)."""
        with self._lock:
            return list(self._sessions[session_id])

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    # ── Long-term facts ───────────────────────────────────────────────────────

    def set_fact(self, key: str, value: str) -> None:
        with self._lock:
            if len(self._facts) >= _MAX_FACTS:
                oldest = next(iter(self._facts))
                del self._facts[oldest]
            self._facts[key] = value
        self._save_facts()  # persist immediately after every write

    def set_personality_style(self, style: str) -> None:
        """Store the user's preferred personality style (e.g. 'more casual', 'formal')."""
        self.set_fact("personality_style", style.strip()[:120])

    def get_personality_style(self) -> str | None:
        with self._lock:
            return self._facts.get("personality_style")

    def get_facts(self) -> dict[str, str]:
        with self._lock:
            return dict(self._facts)

    # ── Last action tracking ──────────────────────────────────────────────────

    def set_last_action(self, tool: str, params: dict, result: str) -> None:
        """Store most recently executed tool (legacy single-slot — kept for compatibility)."""
        with self._lock:
            self._last_action = {"tool": tool, "params": dict(params), "result": result}
        # Also populate the appropriate typed slot automatically
        self._update_context_from_tool(tool, params)

    def get_last_action(self) -> dict | None:
        with self._lock:
            return dict(self._last_action) if self._last_action else None

    def set_disambiguation_matches(self, matches: list, query: str, params: dict) -> None:
        """Store a list of paths for the user to choose from (e.g. after multiple_matches)."""
        with self._lock:
            self._disambiguation = {"matches": list(matches), "query": query, "params": dict(params)}

    def get_disambiguation_matches(self) -> dict | None:
        with self._lock:
            return dict(self._disambiguation) if self._disambiguation else None

    def clear_disambiguation(self) -> None:
        with self._lock:
            self._disambiguation = None

    # ── Multi-slot context API ────────────────────────────────────────────────

    # Maps tool names to their context slot
    _TOOL_SLOT: dict[str, str] = {
        "open_application": "last_app",
        "kill_app":         "last_app",
        "kill_process":     "last_app",
        "open_file":        "last_file",
        "write_file":       "last_file",
        "delete_file":      "last_file",
        "move_file":        "last_file",
        "open_directory":   "last_folder",
        "create_folder":    "last_folder",
        "smart_open":       "last_file",   # refined by params at runtime
        "open_url":         "last_url",
        "search_web":       "last_url",
        "search_youtube":   "last_url",
    }

    def _update_context_from_tool(self, tool: str, params: dict) -> None:
        """Populate the right typed slot based on which tool just ran."""
        slot = self._TOOL_SLOT.get(tool)
        if not slot:
            return
        try:
            data: dict = {}

            def _basename(p: str) -> str:
                # Works for both Windows backslash and Unix slash paths
                return p.replace("\\", "/").rstrip("/").split("/")[-1]

            if slot == "last_app":
                name = (params.get("app_name") or params.get("app") or
                        params.get("process_name") or params.get("name") or "")
                data = {"name": str(name), "pid": params.get("pid")}
            elif slot == "last_file":
                # smart_open may target a folder — let the type param decide
                if tool == "smart_open":
                    ftype = params.get("type", "file")
                    if ftype in ("folder", "directory"):
                        slot = "last_folder"
                path = str(params.get("path") or params.get("query") or "")
                data = {"path": path, "name": _basename(path) if path else ""}
            elif slot == "last_folder":
                path = str(params.get("path") or params.get("name") or
                           params.get("folder_path") or "")
                data = {"path": path, "name": _basename(path) if path else ""}
            elif slot == "last_url":
                url = str(params.get("url") or params.get("query") or params.get("site") or "")
                data = {"url": url, "title": ""}
            if data:
                self.set_context_slot(slot, data)
        except Exception:
            pass

    def set_context_slot(self, slot: str, data: dict) -> None:
        """Update a typed context slot. slot ∈ {last_app, last_file, last_folder, last_url}."""
        if slot not in self._context:
            return
        with self._lock:
            self._context[slot] = dict(data)
        self._save_facts()

    def get_context_slot(self, slot: str) -> dict | None:
        """Return the current value of a typed context slot."""
        with self._lock:
            val = self._context.get(slot)
            return dict(val) if val else None

    def get_context(self) -> dict[str, dict | None]:
        """Return all context slots as a snapshot."""
        with self._lock:
            return {k: dict(v) if v else None for k, v in self._context.items()}

    def remember_explicit(self, text: str) -> None:
        """Store an explicit user-stated fact ("remember that X").

        Also runs fact extraction on the text so structured fields
        (name, preference, location, etc.) are populated automatically.
        """
        with self._lock:
            # Generate a unique key for this note
            note_idx = sum(1 for k in self._facts if k.startswith("note_"))
            key = f"note_{note_idx}"
            if len(self._facts) < _MAX_FACTS:
                self._facts[key] = text.strip()
        # Also try structured extraction from the text itself
        self._maybe_extract_facts(text)

    def get_context_string(self) -> str:
        """Structured context string injected into every LLM system prompt."""
        facts = self.get_facts()
        if not facts:
            return ""
        lines: list[str] = []
        style_note = ""
        for k, v in facts.items():
            if k == "personality_style":
                style_note = f"\nTone instruction: The user wants you to be more {v} — adjust your speaking style accordingly."
                continue
            if k.startswith("note_"):
                lines.append(f"- User explicitly noted: {v}")
            else:
                label = _FACT_LABELS.get(k, k.replace("_", " ").title())
                lines.append(f"- {label}: {v}")
        result = ""
        if lines:
            result = "Remembered context about the user:\n" + "\n".join(lines)
        if style_note:
            result += style_note
        return result

    def get_memories_spoken(self) -> str:
        """Return all stored facts as a natural spoken sentence for the assistant."""
        facts = self.get_facts()
        if not facts:
            return "I don't have anything stored about you yet — just say 'remember that' followed by anything you want me to keep in mind."
        parts: list[str] = []
        for k, v in facts.items():
            if k.startswith("note_"):
                parts.append(f"you told me: {v}")
            else:
                label = _FACT_LABELS.get(k, k.replace("_", " "))
                parts.append(f"{label} is {v}")
        joined = "; ".join(parts)
        return f"Here's what I remember about you: {joined}."

    # ── Rule-based fact extraction ────────────────────────────────────────────

    def _maybe_extract_facts(self, user_text: str) -> None:  # noqa: C901
        try:
            t = user_text.strip()

            # User name — English and Urdu/Hindi patterns
            m = (
                re.search(r"\bmy name is ([A-Za-z]+(?:\s+[A-Za-z]+)?)", t, re.IGNORECASE)
                or re.search(r"\bmera\s+naam\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+hai\b", t, re.IGNORECASE)
                or re.search(r"\bmain\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+h[uo]n\b", t, re.IGNORECASE)
                or re.search(r"\bname(?:\s+is)?\s*[:\-]?\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)", t, re.IGNORECASE)
            )
            if m:
                self.set_fact("user_name", m.group(1).title())

            # Founder / role
            founder_m = re.search(
                r"\b(?:i(?:\'m| am)(?: the)?\s+)?founder(?:\s+of)?\s+([A-Za-z][A-Za-z0-9\s]{2,40})",
                t, re.IGNORECASE,
            )
            if founder_m:
                self.set_fact("profession", "founder")
                self.set_fact("employer", founder_m.group(1).strip().title()[:60])

            # Last mentioned contact (email / message target)
            m = re.search(
                r"\b(?:email|send|message|write)\s+(?:to\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
                t, re.IGNORECASE,
            )
            if m:
                self.set_fact("last_contact", m.group(1).title())

            # Preferences
            pref = re.search(r"\bi (?:prefer|always use|like to use)\s+(.{3,60})", t, re.IGNORECASE)
            if pref:
                self.set_fact("preference", pref.group(1).strip()[:80])

            # Profession / job title
            prof = re.search(
                r"\b(?:i(?:\'m| am)(?: a| an)?|i work as(?: a| an)?)\s+"
                r"(developer|engineer|designer|student|teacher|doctor|lawyer|manager|"
                r"writer|analyst|programmer|coder|architect|entrepreneur|freelancer|"
                r"researcher|scientist|artist|photographer|marketer|accountant)\b",
                t, re.IGNORECASE,
            )
            if prof:
                self.set_fact("profession", prof.group(1).lower())

            # Employer / company
            employer = re.search(
                r"\bi (?:work at|work for|am at|am employed at|am employed by)\s+([A-Za-z][A-Za-z0-9\s]{2,40})",
                t, re.IGNORECASE,
            )
            if employer:
                self.set_fact("employer", employer.group(1).strip().title()[:60])

            # Location
            loc = re.search(
                r"\b(?:i(?:\'m| am) (?:from|in|based in)|i live in|i(?:\'m| am) located in)\s+"
                r"([A-Za-z][A-Za-z\s]{2,40})",
                t, re.IGNORECASE,
            )
            if loc:
                self.set_fact("location", loc.group(1).strip().title()[:60])

            # Work schedule
            sched = re.search(
                r"\bi (?:usually |typically |mostly )?work (?:at |in the |during |late )?"
                r"(night|nights|morning|mornings|afternoon|evenings?|weekends?|daytime)",
                t, re.IGNORECASE,
            )
            if sched:
                self.set_fact("work_schedule", sched.group(1).lower())

            # Interests / hobbies
            interest = re.search(
                r"\bi (?:love|enjoy|like|am into|am interested in|am passionate about)\s+"
                r"(.{3,60}?)(?:\.|,|$)",
                t, re.IGNORECASE,
            )
            if interest:
                val = interest.group(1).strip().lower()
                # Skip generic / non-useful phrases
                if not re.search(r"\b(you|this|that|it|how|what|when|where)\b", val):
                    self.set_fact("interest", val[:60])

            # Email address (explicit: "my email is foo@bar.com")
            email_m = re.search(
                r"\bmy (?:email|email address|mail)\s+is\s+([\w.+\-]+@[\w.\-]+\.\w{2,})",
                t, re.IGNORECASE,
            )
            if email_m:
                self.set_fact("email", email_m.group(1).lower())

            # Language preference
            lang = re.search(
                r"\b(?:speak|talk|reply|respond|communicate)\s+(?:to me\s+)?in\s+([A-Za-z]+)",
                t, re.IGNORECASE,
            )
            if lang:
                lang_val = lang.group(1).strip()
                if lang_val.lower() not in ("english", "en"):
                    self.set_fact("language_preference", lang_val.title())

            # Personality / tone preference ("be more casual", "from now on be formal")
            style = re.search(
                r"\b(?:from\s+now\s+on\s+)?(?:be|sound|act|talk)\s+(?:more\s+)?"
                r"(casual|formal|professional|friendly|chill|concise|brief|detailed|humorous|serious)\b",
                t, re.IGNORECASE,
            )
            if style:
                self.set_personality_style(style.group(1).lower())

        except Exception:
            pass  # never crash on memory extraction

    # ── Semantic recall (ChromaDB) ────────────────────────────────────────────

    def semantic_recall(self, query: str, n: int = 5) -> list[dict]:
        """Return semantically similar memories from ChromaDB."""
        try:
            from cognition.memory.semantic_store import semantic_store
            return semantic_store.recall(query, n)
        except Exception as exc:
            logger.warning("[MemoryService] semantic_recall error: %s", exc)
            return []

    # ── Routing state (per-session, in-memory only) ───────────────────────────
    # Keeps track of which model was last used, conversation depth, and a
    # rolling window of complexity scores — fed into model_router.RouteContext.

    def update_routing(
        self,
        session_id: str,
        model:      str,
        score:      float,
        intent:     str = "",
    ) -> None:
        """Record routing decision after each turn."""
        with self._lock:
            r = self._routing.setdefault(session_id, {
                "last_model":    None,
                "conv_depth":    0,
                "recent_scores": [],   # capped at 20
                "last_intent":   None,
            })
            r["last_model"]  = model
            r["last_intent"] = intent or r["last_intent"]
            r["conv_depth"]  = r["conv_depth"] + 1
            scores = r["recent_scores"]
            scores.append(round(score, 3))
            if len(scores) > 20:
                scores.pop(0)

    def get_routing_context(self, session_id: str) -> "RouteContext":
        """Return a RouteContext for model_router.select_model()."""
        from api.services.model_router import RouteContext
        with self._lock:
            r = self._routing.get(session_id, {})
        return RouteContext(
            last_model    = r.get("last_model"),
            conv_depth    = r.get("conv_depth", 0),
            recent_scores = list(r.get("recent_scores", [])),
            last_intent   = r.get("last_intent"),
        )


    # ── Language mode per session ─────────────────────────────────────────────

    def set_language_mode(self, session_id: str, lang: str) -> None:
        """Persist the detected language for a session (e.g. 'roman_urdu', 'mixed')."""
        with self._lock:
            r = self._routing.setdefault(session_id, {})
            r["language_mode"] = lang

    def get_language_mode(self, session_id: str) -> "str | None":
        """Return the last detected language for a session, or None if unknown."""
        with self._lock:
            return self._routing.get(session_id, {}).get("language_mode")


memory_service = MemoryService()
