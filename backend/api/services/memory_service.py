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
        # Last executed action — for "do it again" / "open it" resolution
        self._last_action: dict | None = None
        # Load persisted facts from disk
        self._load_facts()

    # ── Disk persistence ──────────────────────────────────────────────────────

    def _load_facts(self) -> None:
        """Load persisted facts from ~/.ai-operator/memory.json."""
        try:
            if _MEMORY_FILE.exists():
                data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._facts = data
                    logger.info("Memory loaded: %d facts from %s", len(self._facts), _MEMORY_FILE)
        except Exception as exc:
            logger.warning("Could not load memory file: %s", exc)

    def _save_facts(self) -> None:
        """Persist current facts to ~/.ai-operator/memory.json (called on every write)."""
        try:
            _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            _MEMORY_FILE.write_text(
                json.dumps(self._facts, ensure_ascii=False, indent=2),
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
        """Store the most recently executed tool for context-aware follow-ups."""
        with self._lock:
            self._last_action = {"tool": tool, "params": dict(params), "result": result}

    def get_last_action(self) -> dict | None:
        with self._lock:
            return dict(self._last_action) if self._last_action else None

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

            # User name
            m = re.search(r"\bmy name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", t, re.IGNORECASE)
            if m:
                self.set_fact("user_name", m.group(1).title())

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


memory_service = MemoryService()
