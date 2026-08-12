"""
Entity Correction Engine — Phase 2.2

Rescores N-best TranscriptCandidates using fuzzy matching against a live
entity database built from:
  • Installed Windows apps (hardcoded + registry-detected)
  • Microsoft Store known apps
  • ContextStack recent entities (apps, folders, files, media)
  • Common folder names from fs_index

Uses RapidFuzz for sub-1ms per-candidate scoring.
Entity database is built once on first call and refreshed every 5 minutes.

Log markers:
  [ENTITY_CORRECT]  — candidate text + correction applied
  [ENTITY_MATCH]    — specific entity that matched
  [ENTITY_SCORE]    — per-candidate entity score before/after correction
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Entity database ───────────────────────────────────────────────────────────

_COMMON_APPS: list[str] = [
    # Microsoft
    "Windows Explorer", "File Explorer", "Microsoft Edge", "Microsoft Teams",
    "Microsoft Word", "Microsoft Excel", "Microsoft PowerPoint", "Microsoft Outlook",
    "Microsoft Store", "Microsoft Paint", "Notepad", "Calculator", "Task Manager",
    "Settings", "Control Panel", "Command Prompt", "PowerShell", "Terminal",
    "Windows Security", "Windows Update", "Device Manager",
    # Google
    "Google Chrome", "Chrome", "Google Drive",
    # JetBrains / Dev
    "Visual Studio Code", "VS Code", "Visual Studio", "PyCharm", "IntelliJ IDEA",
    "Android Studio", "WebStorm", "Cursor",
    # Communication
    "Discord", "Slack", "Zoom", "Telegram", "WhatsApp", "Signal", "Skype",
    # Media
    "Spotify", "VLC", "iTunes", "Windows Media Player", "Netflix", "YouTube Music",
    "Audacity", "OBS Studio",
    # Productivity
    "Notion", "Obsidian", "Evernote", "Todoist", "Trello",
    # Gaming
    "Steam", "Epic Games", "Xbox App", "Xbox Game Bar", "Battle.net",
    # System
    "Task Scheduler", "Resource Monitor", "Event Viewer", "Registry Editor",
    "Disk Management", "Performance Monitor",
    # Adobe
    "Adobe Photoshop", "Adobe Illustrator", "Adobe Premiere", "Adobe Acrobat",
    # Cloud / AI
    "ChatGPT", "Claude", "NVIDIA App", "NVIDIA Control Panel",
    "Docker Desktop", "Docker", "VirtualBox", "VMware",
    # Social
    "Instagram", "Facebook", "Twitter", "LinkedIn", "TikTok", "Pinterest",
    # Storage
    "OneDrive", "Google Drive", "Dropbox",
    # DB / Dev tools
    "Postman", "DBeaver", "TablePlus", "Insomnia",
    # Browser related
    "Firefox", "Brave", "Opera", "Vivaldi",
    # Security
    "Malwarebytes", "Avast", "Windows Defender",
    # Utilities
    "7-Zip", "WinRAR", "CPU-Z", "GPU-Z", "HWiNFO", "Rufus",
]

# Aliases / short forms → canonical form
_APP_ALIASES: dict[str, str] = {
    "vs code":            "Visual Studio Code",
    "vscode":             "Visual Studio Code",
    "visual studio code": "Visual Studio Code",
    "code":               "Visual Studio Code",
    "chrome":             "Google Chrome",
    "edge":               "Microsoft Edge",
    "teams":              "Microsoft Teams",
    "word":               "Microsoft Word",
    "excel":              "Microsoft Excel",
    "powerpoint":         "Microsoft PowerPoint",
    "outlook":            "Microsoft Outlook",
    "notepad":            "Notepad",
    "calc":               "Calculator",
    "calculator":         "Calculator",
    "explorer":           "File Explorer",
    "file explorer":      "File Explorer",
    "task manager":       "Task Manager",
    "settings":           "Settings",
    "store":              "Microsoft Store",
    "microsoft store":    "Microsoft Store",
    "discord":            "Discord",
    "spotify":            "Spotify",
    "whatsapp":           "WhatsApp",
    "telegram":           "Telegram",
    "zoom":               "Zoom",
    "slack":              "Slack",
    "steam":              "Steam",
    "docker":             "Docker Desktop",
    "docker desktop":     "Docker Desktop",
    "obs":                "OBS Studio",
    "obs studio":         "OBS Studio",
    "nvidia app":         "NVIDIA App",
    "nvidia":             "NVIDIA App",
    "power bi":           "Power BI",
    "powerbi":            "Power BI",
    "instagram":          "Instagram",
    "chatgpt":            "ChatGPT",
    "hackathon":          "Hackathon",
}

_LOCK      = threading.Lock()
_cache_ts  = 0.0
_CACHE_TTL = 300.0  # rebuild every 5 minutes

# Flat list of (lower_name, canonical_name, entity_type) tuples
_entity_db: list[tuple[str, str, str]] = []


def _build_entity_db() -> list[tuple[str, str, str]]:
    """Build a flat (lower, canonical, type) entity list from all sources."""
    db: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def _add(lower: str, canonical: str, etype: str) -> None:
        if lower and lower not in seen:
            seen.add(lower)
            db.append((lower, canonical, etype))

    # Apps from common list
    for app in _COMMON_APPS:
        _add(app.lower(), app, "app")

    # Aliases
    for alias, canonical in _APP_ALIASES.items():
        _add(alias.lower(), canonical, "app")

    # Store known apps
    try:
        from api.tools.store_tools import _KNOWN_MSSTORE_IDS  # type: ignore
        for query, (display, _id) in _KNOWN_MSSTORE_IDS.items():
            _add(query.lower(), display, "store_app")
            _add(display.lower(), display, "store_app")
    except Exception:
        pass

    # ContextStack recent entities (live)
    try:
        from api.services.context_stack import context_stack
        for ent in context_stack.recent(20):
            if ent.display:
                _add(ent.display.lower(), ent.display, ent.type)
    except Exception:
        pass

    # Recent folders from fs_index (top 200 by last-access)
    try:
        import sqlite3
        from pathlib import Path
        db_path = Path.home() / ".ai-operator" / "fs_index.db"
        if db_path.exists():
            con = sqlite3.connect(str(db_path), timeout=0.5)
            # NOTE: this previously queried a "paths" table with type='dir',
            # which hasn't existed since fs_index.py's schema became
            # "entries" with type IN ('file','folder') — silently swallowed
            # by the except below, so this contributed nothing for however
            # long that's been stale. Fixed 2026 platform-stabilization pass.
            rows = con.execute(
                "SELECT name FROM entries WHERE type='folder' ORDER BY modified_time DESC LIMIT 200"
            ).fetchall()
            con.close()
            for (name,) in rows:
                if name:
                    _add(name.lower(), name, "folder")
    except Exception:
        pass

    return db


def _get_entity_db() -> list[tuple[str, str, str]]:
    global _entity_db, _cache_ts
    now = time.monotonic()
    if now - _cache_ts < _CACHE_TTL and _entity_db:
        return _entity_db
    with _LOCK:
        if now - _cache_ts < _CACHE_TTL and _entity_db:
            return _entity_db
        _entity_db = _build_entity_db()
        _cache_ts  = now
        logger.info("[ENTITY_DB] rebuilt entities=%d", len(_entity_db))
    return _entity_db


# ── Fuzzy matching helpers ────────────────────────────────────────────────────

_OPEN_VERBS   = re.compile(r'\b(open|launch|start|run|load|show|go to)\s+', re.I)
_ACTION_VERBS = re.compile(
    r'\b(install|download|search|play|close|find|delete|move|copy|'
    r'create|write|open|record|take|volume|brightness)\b', re.I
)

def _extract_entity_span(text: str) -> tuple[str, str, str]:
    """
    Split a command into (action_prefix, entity_span, suffix).
    e.g. "open NVIDIA app" → ("open ", "NVIDIA app", "")
         "install instagram" → ("install ", "instagram", "")
    """
    m = _ACTION_VERBS.search(text)
    if m:
        verb_end  = m.end()
        prefix    = text[:verb_end].strip() + " "
        remainder = text[verb_end:].strip()
        return prefix, remainder, ""
    return "", text.strip(), ""


def _fuzzy_score(a: str, b: str) -> float:
    """Return RapidFuzz token_sort_ratio [0..100] → [0..1]."""
    try:
        from rapidfuzz import fuzz
        return fuzz.token_sort_ratio(a, b) / 100.0
    except ImportError:
        # Fallback: simple character-level overlap
        sa, sb = set(a.lower()), set(b.lower())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / max(len(sa), len(sb))


# ── Public API ────────────────────────────────────────────────────────────────

_MATCH_THRESHOLD = 0.72   # min fuzzy score to consider an entity match
_HIGH_THRESHOLD  = 0.88   # score above which we apply the correction

# Explicit filesystem-type nouns are decisive structural evidence — when one
# is present in the entity span, correction must only compare against
# entities of that type. This is what stops "perfume folder" from ever being
# compared against "Performance Monitor" in the first place: the word
# "folder" rules out every app/store_app candidate outright, regardless of
# how high its fuzzy score would otherwise be. (A small local check rather
# than reusing object_resolver's — object_resolver already imports from this
# module for known-app lookups, so importing it back here would be
# circular; the noun list itself is tiny and has no scope/context
# dependency worth sharing.)
_TYPE_NOUN_TO_ENTITY_TYPES: dict[str, frozenset] = {
    "folder":    frozenset({"folder"}),
    "directory": frozenset({"folder"}),
    "dir":       frozenset({"folder"}),
    "file":      frozenset({"file"}),
    "document":  frozenset({"file"}),
    "drive":     frozenset({"drive"}),
    "disk":      frozenset({"drive"}),
}
_TYPE_NOUN_RE = re.compile(r'\b(folder|directory|dir|file|document|drive|disk)\b', re.I)


def _expected_entity_types(span: str) -> Optional[frozenset]:
    """Return the only entity types correction may compare *span* against,
    or None if the span has no explicit type noun (unrestricted)."""
    m = _TYPE_NOUN_RE.search(span)
    if not m:
        return None
    return _TYPE_NOUN_TO_ENTITY_TYPES.get(m.group(1).lower())


def rescore(
    candidates: list,    # list[TranscriptCandidate]
    session_state: Optional[dict] = None,
) -> list:               # list[TranscriptCandidate]
    """
    Rescore N-best candidates using entity fuzzy matching.

    For each candidate:
      1. Extract entity span (strip action verb)
      2. Fuzzy-match span against entity database
      3. If high match → correct the candidate text + boost confidence

    Returns a new list sorted by updated confidence.
    """
    if not candidates:
        return candidates

    db = _get_entity_db()
    if not db:
        return candidates

    # Add live ContextStack entities on every call (they change frequently)
    live_entities: list[tuple[str, str, str]] = []
    try:
        from api.services.context_stack import context_stack
        for ent in context_stack.recent(10):
            if ent.display:
                live_entities.append((ent.display.lower(), ent.display, ent.type))
    except Exception:
        pass

    all_entities = live_entities + db  # live entities take priority

    updated = []
    for cand in candidates:
        text = cand.text
        prefix, span, suffix = _extract_entity_span(text)

        best_score:    float = 0.0
        best_canonical: str  = span
        best_etype:    str   = ""

        expected_types = _expected_entity_types(span)
        if expected_types is not None:
            logger.debug("[ENTITY_TYPE_RESTRICT] span=%r expected_types=%s",
                         span[:30], sorted(expected_types))

        if span and len(span) >= 2:
            for lower, canonical, etype in all_entities:
                if expected_types is not None and etype not in expected_types:
                    continue
                score = _fuzzy_score(span.lower(), lower)
                if score > best_score:
                    best_score     = score
                    best_canonical = canonical
                    best_etype     = etype

        logger.info(
            "[ENTITY_SCORE] text=%r span=%r best_match=%r score=%.2f",
            text[:50], span[:30], best_canonical[:30], best_score,
        )

        if best_score >= _HIGH_THRESHOLD and best_canonical.lower() != span.lower():
            corrected = (prefix + best_canonical + (" " + suffix if suffix else "")).strip()
            logger.info("[ENTITY_CORRECT] %r → %r (match=%r score=%.2f type=%s)",
                        text[:50], corrected[:50], best_canonical[:30], best_score, best_etype)
            logger.info("[ENTITY_MATCH] entity=%r type=%s score=%.2f",
                        best_canonical[:40], best_etype, best_score)
            import copy
            new_cand           = copy.copy(cand)
            new_cand.text      = corrected
            new_cand.confidence = min(1.0, cand.confidence + best_score * 0.15)
            new_cand.entity_match_score = best_score
            updated.append(new_cand)
        elif best_score >= _MATCH_THRESHOLD:
            # Boost confidence even if text unchanged (confirms the entity is real)
            import copy
            new_cand            = copy.copy(cand)
            new_cand.confidence = min(1.0, cand.confidence + best_score * 0.08)
            new_cand.entity_match_score = best_score
            updated.append(new_cand)
        else:
            # No entity match found — record the real (low) score instead of
            # leaving candidate_scorer to fall back on STT confidence as a
            # stand-in for entity quality (that was the "ent=1.00 for
            # unmatched garbage" confidence-corruption bug).
            cand.entity_match_score = best_score
            updated.append(cand)

    # Re-sort by updated confidence
    updated.sort(key=lambda c: c.confidence, reverse=True)
    return updated
