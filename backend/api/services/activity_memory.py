"""
activity_memory.py — persistent cross-session activity memory.

The existing context services (active_context, context_stack, context_memory,
activity_timeline) are all SESSION-scoped and in-memory — after a restart
Xyron forgets every song it played, folder it opened, and app it launched.
This service is the durable layer underneath: one JSONL append log at
~/.xyron/activity_memory.jsonl recording the actions users actually care to
recall, plus voice-query support for questions like:

  "what is my most recent folder?"
  "what was I working on yesterday?"
  "what songs did you play today?"
  "play the same songs you played yesterday"   → replays the newest one

Written from voice_ws._run_tool's success branch via record_from_tool();
queried from voice_ws Tier 0m (memory recall) before intent routing so these
questions never reach the LLM. Pure parsing/query logic is module-level and
unit-tested in tests/test_context_memory_recall.py.

Logs: [ACTIVITY_MEMORY_RECORD] [ACTIVITY_MEMORY_RECALL]
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_STORE = Path.home() / ".xyron" / "activity_memory.jsonl"
MAX_ENTRIES_ON_LOAD = 2000

# Kinds we record — anything else is ignored (keeps the log signal-dense).
KIND_SONG   = "song"       # a specific YouTube video actually played
KIND_FOLDER = "folder"     # folder/drive opened or created
KIND_APP    = "app"        # application launched
KIND_SEARCH = "search"     # youtube/web search (feeds "working on" recall)


# ── Recall-query parsing (pure — testable without I/O) ────────────────────────

# Replay: "play the same songs you played yesterday" / "play those songs
# again" / "play what you played today". Must name a PERIOD or a
# same/those/again marker, otherwise a bare "play songs" is a fresh search.
_REPLAY_RE = re.compile(
    r'\b(?:play|replay|resume|start)\b.*?\b(?:same|again|those|these|those\s+same)\b.*?'
    r'\b(?:songs?|music|tracks?|videos?|tunes?)\b'
    r'|\b(?:play|replay)\b.*?\b(?:songs?|music|tracks?|videos?|tunes?)\b.*?'
    r'\b(?:again|once\s+more)\b'
    r'|\b(?:play|replay)\b\s+(?:what|whatever|the\s+(?:songs?|music|videos?))\b.*?'
    r'\byou\s+(?:played|were\s+playing)\b',
    re.IGNORECASE,
)

# Song recall: "what songs did you play today/yesterday"
_SONG_RECALL_RE = re.compile(
    r'\bwhat\s+(?:songs?|music|videos?|tracks?)\b.*?\b(?:did|do)\s+you\s+'
    r'(?:play|played|put\s+on)\b'
    r'|\bwhich\s+(?:songs?|videos?)\b.*?\byou\s+play',
    re.IGNORECASE,
)

# Folder recall: "what is my most recent folder" / "last folder i opened" /
# "do you remember what folder you opened". Xyron is the one that does the
# opening, so users often phrase this in 2nd person ("you") not 1st ("I") —
# both must match. STT also regularly mangles "last" into noise ("slot",
# "lest", ...), so a bare "remember ... folder ... open" combo (a phrase
# that's never used for anything but a recall question) is matched on its
# own without requiring the superlative word to survive transcription.
_FOLDER_RECALL_RE = re.compile(
    r'\b(?:most\s+recent|latest|last)\s+(?:folder|directory)\b'
    r'|\bwhat\s+(?:is|was)\s+(?:my\s+)?(?:recent|last|latest)\s+(?:folder|directory)\b'
    r'|\b(?:folder|directory)\s+(?:did|was)\s+(?:i|you)\s+(?:open|opened|in)\b'
    r'|\bwhat\s+(?:folder|directory)\s+(?:did|do|does)\s+(?:i|you)\s+(?:open|opened|use)\b'
    r'|\b(?:do\s+you\s+)?remember\b.{0,25}\b(?:folder|directory)\b.{0,25}\b(?:open|opened|in)\b'
    r'|\bwhere\s+was\s+i\s+(?:just\s+)?(?:in|working)\b',
    re.IGNORECASE,
)

# Work recall: "what was i working on (yesterday)" / "what were you doing"
_WORK_RECALL_RE = re.compile(
    r'\bwhat\s+(?:was|were)\s+(?:i|you)\s+(?:working|doing)\b'
    r'|\bwhat\s+did\s+(?:i|you)\s+(?:work|do)\b'
    r'|\bwhat\s+have\s+(?:i|you)\s+been\s+(?:working|doing)\b',
    re.IGNORECASE,
)

# App recall: "what apps did i open today" / "do you remember what app you opened"
_APP_RECALL_RE = re.compile(
    r'\bwhat\s+(?:apps?|applications?|programs?)\b.*?\b(?:did|do|does)\s+(?:i|you)\s+'
    r'(?:open|opened|use|used|launch|launched|run)\b'
    r'|\b(?:do\s+you\s+)?remember\b.{0,25}\b(?:app|application|program)\b.{0,25}'
    r'\b(?:open|opened|launch|launched|use|used)\b',
    re.IGNORECASE,
)

_PERIOD_RE = re.compile(
    r'\b(today|tonight|this\s+morning|this\s+evening|yesterday|last\s+night|'
    r'this\s+week|last\s+week|recently|lately|earlier\s+today|'
    r'just\s+now|a\s+minute\s+ago|a\s+few\s+minutes?\s+ago|few\s+minutes?\s+ago|'
    r'\d+\s+minutes?\s+ago|an?\s+hour\s+ago|\d+\s+hours?\s+ago|a\s+while\s+ago)\b',
    re.IGNORECASE,
)

_MINUTES_AGO_RE = re.compile(r'^(\d+)\s+minutes?\s+ago$')
_HOURS_AGO_RE = re.compile(r'^(\d+)\s+hours?\s+ago$')


def period_bounds(period: Optional[str], now: Optional[datetime] = None) -> tuple[float, float]:
    """Map a spoken period word to an (epoch_start, epoch_end) window."""
    now = now or datetime.now()
    end = now.timestamp()
    p = (period or "").lower().strip()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Fine-grained "a few minutes ago" / "N minutes ago" / "an hour ago" —
    # STT timing is fuzzy, so each gets a small grace window rather than a
    # razor-sharp cutoff.
    m = _MINUTES_AGO_RE.match(p)
    if m:
        return end - (int(m.group(1)) + 5) * 60, end
    m = _HOURS_AGO_RE.match(p)
    if m:
        return end - (int(m.group(1)) * 3600 + 15 * 60), end
    if p in ("just now", "a minute ago"):
        return end - 5 * 60, end
    if p in ("a few minutes ago", "few minutes ago"):
        return end - 20 * 60, end
    if p in ("an hour ago", "a hour ago", "a while ago"):
        return end - 90 * 60, end
    if p == "earlier today":
        return start_of_day.timestamp(), end
    if p in ("today", "tonight", "this morning", "this evening"):
        return start_of_day.timestamp(), end
    if p == "yesterday":
        yday = start_of_day - timedelta(days=1)
        return yday.timestamp(), start_of_day.timestamp()
    if p == "last night":
        yday = start_of_day - timedelta(days=1)
        return yday.replace(hour=18).timestamp(), end
    if p == "this week":
        return (start_of_day - timedelta(days=now.weekday())).timestamp(), end
    if p == "last week":
        this_mon = start_of_day - timedelta(days=now.weekday())
        return (this_mon - timedelta(days=7)).timestamp(), this_mon.timestamp()
    # "recently" / "lately" / unspecified — last 7 days
    return end - 7 * 86400, end


def parse_recall_query(text: str) -> Optional[dict]:
    """Classify a voice query into a recall intent, or None if not one."""
    t = (text or "").strip()
    if not t:
        return None

    pm = _PERIOD_RE.search(t)
    period = pm.group(1).lower() if pm else None

    if _REPLAY_RE.search(t):
        return {"action": "replay_songs", "period": period or "recently"}
    if _SONG_RECALL_RE.search(t):
        return {"action": "recall_songs", "period": period or "recently"}
    if _FOLDER_RECALL_RE.search(t):
        # Only narrow by period when the user actually named one ("a few
        # minutes ago", "yesterday") — a bare "last folder" question means
        # "most recent regardless of when", so period stays None there.
        return {"action": "recent_folder", "period": period}
    if _WORK_RECALL_RE.search(t):
        return {"action": "worked_on", "period": period or "recently"}
    if _APP_RECALL_RE.search(t):
        return {"action": "recent_apps", "period": period or "recently"}
    return None


def _period_phrase(period: Optional[str]) -> str:
    return period if period else "recently"


# ── Persistent store ──────────────────────────────────────────────────────────

class ActivityMemory:
    """Append-only JSONL activity log with typed recall queries."""

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self._path = Path(store_path) if store_path else DEFAULT_STORE
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            entries = []
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if isinstance(e, dict) and e.get("kind"):
                            entries.append(e)
                    except (json.JSONDecodeError, ValueError):
                        continue
            self._entries = entries[-MAX_ENTRIES_ON_LOAD:]
        except Exception as exc:
            logger.debug("[ACTIVITY_MEMORY_LOAD] failed: %s", exc)

    def _append(self, entry: dict) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("[ACTIVITY_MEMORY_APPEND] failed: %s", exc)

    # ── Write ─────────────────────────────────────────────────────────────

    def record(self, kind: str, name: str, url: str = "", path: str = "",
               extra: Optional[dict] = None) -> Optional[dict]:
        if not name:
            return None
        entry = {
            "ts":   int(time.time()),
            "kind": kind,
            "name": name,
        }
        if url:
            entry["url"] = url
        if path:
            entry["path"] = path
        if extra:
            entry.update(extra)
        with self._lock:
            self._entries.append(entry)
            # Cap in-memory copy; the file itself grows and is trimmed on load
            self._entries = self._entries[-MAX_ENTRIES_ON_LOAD:]
        self._append(entry)
        logger.info("[ACTIVITY_MEMORY_RECORD] kind=%s name=%r", kind, name[:60])
        return entry

    def record_from_tool(self, tool_name: str, params: dict,
                         result_data: dict) -> None:
        """Called after every successful tool execution (voice_ws _run_tool)."""
        try:
            rd = result_data or {}
            p  = params or {}

            if tool_name == "play_youtube_video":
                title = (p.get("title") or rd.get("title") or "").strip()
                url   = (p.get("url") or rd.get("url") or "").strip()
                if title:
                    self.record(KIND_SONG, title, url=url)

            elif tool_name == "search_youtube" and rd.get("autoplayed"):
                title = (rd.get("title") or p.get("query") or "").strip()
                url   = (rd.get("url") or "").strip()
                if title:
                    self.record(KIND_SONG, title, url=url)

            elif tool_name == "search_youtube":
                q = (p.get("query") or "").strip()
                if q:
                    self.record(KIND_SEARCH, q, extra={"via": "youtube"})

            elif tool_name in ("open_directory", "create_folder", "open_drive",
                               "smart_open"):
                if tool_name == "smart_open" and (rd.get("type") or "") == "file":
                    return
                path = (rd.get("path") or rd.get("action_path") or
                        p.get("path") or p.get("query") or "").strip()
                if tool_name == "open_drive" and not path:
                    d = (p.get("drive") or rd.get("drive") or "").strip().upper()[:1]
                    path = f"{d}:\\" if d else ""
                if path:
                    norm = path.rstrip("/\\").replace("\\", "/")
                    name = norm.split("/")[-1] if "/" in norm else norm
                    self.record(KIND_FOLDER, name or path, path=path)

            elif tool_name == "open_application":
                app = (p.get("app_name") or p.get("app") or "").strip()
                if app:
                    self.record(KIND_APP, app)
        except Exception as exc:
            logger.debug("[ACTIVITY_MEMORY_RECORD_TOOL] skipped: %s", exc)

    # ── Read ──────────────────────────────────────────────────────────────

    def _by_kind(self, kind: str, period: Optional[str], limit: int) -> list[dict]:
        start, end = period_bounds(period)
        with self._lock:
            items = [e for e in self._entries
                     if e.get("kind") == kind and start <= e.get("ts", 0) <= end]
        # newest first, dedupe consecutive repeats of the same name
        out: list[dict] = []
        seen: set = set()
        for e in reversed(items):
            key = e.get("name", "").lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def songs(self, period: Optional[str] = None, limit: int = 10) -> list[dict]:
        return self._by_kind(KIND_SONG, period, limit)

    def folders(self, period: Optional[str] = None, limit: int = 10) -> list[dict]:
        return self._by_kind(KIND_FOLDER, period, limit)

    def apps(self, period: Optional[str] = None, limit: int = 10) -> list[dict]:
        return self._by_kind(KIND_APP, period, limit)

    def searches(self, period: Optional[str] = None, limit: int = 10) -> list[dict]:
        return self._by_kind(KIND_SEARCH, period, limit)

    def recent_all(self, limit: int = 15) -> list[dict]:
        with self._lock:
            items = list(self._entries)
        return list(reversed(items))[:limit]

    # ── Voice answer composition ──────────────────────────────────────────

    def handle_query(self, text: str) -> Optional[dict]:
        """
        Parse a voice query and answer it from memory.

        Returns None when the utterance isn't a recall question (caller falls
        through). Otherwise returns:
          {"action": str, "response": str, "play": {"url","title"} | None}
        `play` is set only for replay actions with a remembered URL — the
        caller executes play_youtube_video with it.
        """
        decision = parse_recall_query(text)
        if not decision:
            return None
        action = decision["action"]
        period = decision.get("period")
        phrase = _period_phrase(period)

        if action == "replay_songs":
            items = self.songs(period, limit=5)
            if not items:
                return {"action": action,
                        "response": f"I don't remember playing any songs {phrase}.",
                        "play": None}
            top = items[0]
            rest = ", ".join(f"'{e['name']}'" for e in items[1:4])
            resp = f"Sure — playing '{top['name']}' again. You also had {rest} {phrase}." if rest \
                   else f"Sure — playing '{top['name']}' again."
            play = {"url": top.get("url", ""), "title": top["name"]} if top.get("url") else None
            logger.info("[ACTIVITY_MEMORY_RECALL] action=%s period=%s hits=%d", action, phrase, len(items))
            return {"action": action, "response": resp, "play": play}

        if action == "recall_songs":
            items = self.songs(period, limit=6)
            if not items:
                return {"action": action,
                        "response": f"I haven't played any songs {phrase}.",
                        "play": None}
            names = ", ".join(f"'{e['name']}'" for e in items)
            logger.info("[ACTIVITY_MEMORY_RECALL] action=%s period=%s hits=%d", action, phrase, len(items))
            return {"action": action,
                    "response": f"{phrase.capitalize()} I played {names}.",
                    "play": None}

        if action == "recent_folder":
            items = self.folders(period, limit=3)
            if not items:
                msg = ("I don't remember opening any folders yet." if not period else
                       f"I don't remember opening any folders {phrase}.")
                return {"action": action, "response": msg, "play": None}
            top = items[0]
            more = f" Before that: {', '.join(e['name'] for e in items[1:])}." if len(items) > 1 else ""
            lead = f"{phrase.capitalize()} you opened" if period else "Your most recent folder is"
            return {"action": action,
                    "response": f"{lead} '{top['name']}'.{more}",
                    "play": None}

        if action == "worked_on":
            parts: list[str] = []
            for e in self.folders(period, limit=2):
                parts.append(f"the {e['name']} folder")
            for e in self.apps(period, limit=2):
                parts.append(e["name"])
            for e in self.searches(period, limit=2):
                parts.append(f"searching for {e['name']}")
            if not parts:
                return {"action": action,
                        "response": f"I don't have a record of what you worked on {phrase}.",
                        "play": None}
            return {"action": action,
                    "response": f"{phrase.capitalize()} you were working with {', '.join(parts[:4])}.",
                    "play": None}

        if action == "recent_apps":
            items = self.apps(period, limit=5)
            if not items:
                return {"action": action,
                        "response": f"I don't remember you opening any apps {phrase}.",
                        "play": None}
            names = ", ".join(e["name"] for e in items)
            return {"action": action,
                    "response": f"{phrase.capitalize()} you opened {names}.",
                    "play": None}

        return None


# Module-level singleton
activity_memory = ActivityMemory()
