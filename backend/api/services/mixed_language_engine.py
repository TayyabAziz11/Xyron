"""
Mixed-Language Engine — Phase 2.5

Handles code-switching: Pakistanis (and others) freely mix English nouns
with Urdu/Hindi/Arabic verbs. This engine maps mixed input to a single
canonical English command without switching STT models.

Design:
  • Keep English nouns/app-names as-is
  • Map Urdu/Hindi/Arabic verb markers → English action verbs
  • Produce one canonical English command string

Does NOT replace ml_normalizer — runs before it as a pre-pass to handle
patterns the regex rules don't cover.

Log markers:
  [MIXED_LANGUAGE] text=<> lang=<> canonical=<>
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Action verb mappings (multilingual → English) ────────────────────────────
# Covers Roman Urdu, Urdu script transliteration, Hindi, Arabic transliteration

_VERB_MAP: list[tuple[re.Pattern, str]] = [
    # Open / launch verbs
    (re.compile(r'\b(?:kholo|khol|kholein|open\s+karo|launch\s+karo|chala[oa]?)\b', re.I), "open"),
    (re.compile(r'\b(?:افتح|کھولو|کھول)\b'),                                                 "open"),
    # Close / quit verbs
    (re.compile(r'\b(?:band\s+karo|band|bandh\s+karo|bandh|bund\s+karo|quit\s+karo)\b', re.I), "close"),
    (re.compile(r'\b(?:اغلق|بند\s+کرو|بند)\b'),                                              "close"),
    # Install verbs
    (re.compile(r'\b(?:install\s+karo|install\s+kar[oa]?|lagao|lagana|install\s+kardo)\b', re.I), "install"),
    # Download verbs
    (re.compile(r'\b(?:download\s+karo|download\s+kar[oa]?|اتارو|ڈاؤن\s*لوڈ\s+کرو)\b', re.I), "download"),
    # Play/search YouTube
    (re.compile(r'\b(?:chalao|chlao|chala\s+dena|play\s+karo|bajao)\b', re.I),              "play"),
    # Volume
    (re.compile(r'\b(?:barhao|barha[oa]?|tez\s+karo|increase\s+karo)\b', re.I),             "increase"),
    (re.compile(r'\b(?:kam\s+karo|ghata[oa]?|decrease\s+karo|slow\s+karo)\b', re.I),        "decrease"),
    # Screenshot
    (re.compile(r'\b(?:screenshot\s+lo|screenshot\s+lao|pakro|capture\s+karo)\b', re.I),    "take screenshot"),
    # Search
    (re.compile(r'\b(?:search\s+karo|dhundho|talash\s+karo)\b', re.I),                       "search"),
    # Show/display
    (re.compile(r'\b(?:dikha[oa]?|dikhao|show\s+karo|batao)\b', re.I),                       "show"),
]

# ── Connector words to strip ─────────────────────────────────────────────────
_CONNECTORS = re.compile(
    r'\b(?:se|mein|ko|ka|ki|ke|par|pe|bhi|aur|ya|ne|hi|'
    r'the|a|an|it|me|my|please|just|now|quickly)\b',
    re.I,
)

# ── Location/source markers ───────────────────────────────────────────────────
_FROM_STORE = re.compile(
    r'\b(?:microsoft\s+store|store|ms\s+store|app\s+store)\s+'
    r'(?:se|from|mein|ko|ka)\b',
    re.I,
)
_ON_YOUTUBE = re.compile(r'\b(?:youtube|yt)\s+(?:pe|par|pr|on)\b', re.I)
_ON_SPOTIFY = re.compile(r'\b(?:spotify)\s+(?:pe|par|on)\b', re.I)

# ── Volume / brightness targets ───────────────────────────────────────────────
_VOLUME_TARGETS = re.compile(r'\b(?:awaz|volume|آواز|والیوم)\b', re.I)
_BRIGHT_TARGETS = re.compile(r'\b(?:brightness|روشنی)\b', re.I)


def _strip_action_words(text: str) -> str:
    """Remove mapped action verbs (already extracted) from the entity span."""
    result = text
    for pat, _ in _VERB_MAP:
        result = pat.sub(" ", result)
    result = _CONNECTORS.sub(" ", result)
    return re.sub(r'\s{2,}', ' ', result).strip().strip('.!?,;')


def analyze(transcript: str, detected_lang: str) -> Optional[str]:
    """
    Convert a mixed-language transcript to a canonical English command.

    Returns:
        Canonical English string, or None if no mapping was found
        (caller should keep original transcript).
    """
    if not transcript or detected_lang == "en":
        return None

    text = transcript.strip()

    # ── Try each verb pattern ──────────────────────────────────────────────────
    matched_action: Optional[str] = None
    for pat, action in _VERB_MAP:
        if pat.search(text):
            matched_action = action
            break

    if not matched_action:
        return None

    # ── Extract entity span (what the verb acts on) ────────────────────────────
    entity_span = _strip_action_words(text)

    # ── Build canonical command ────────────────────────────────────────────────
    canonical: Optional[str] = None

    # Volume up/down
    if matched_action in ("increase", "decrease") and _VOLUME_TARGETS.search(text):
        canonical = f"volume {'up' if matched_action == 'increase' else 'down'}"
    elif matched_action in ("increase", "decrease") and _BRIGHT_TARGETS.search(text):
        canonical = f"brightness {'up' if matched_action == 'increase' else 'down'}"

    # YouTube play
    elif matched_action == "play" and (_ON_YOUTUBE.search(text) or "youtube" in text.lower()):
        query = re.sub(r'\b(?:youtube|yt)\b', '', entity_span, flags=re.I).strip()
        if query:
            canonical = f"play {query} on youtube"

    # Spotify play
    elif matched_action == "play" and _ON_SPOTIFY.search(text):
        query = re.sub(r'\bspotify\b', '', entity_span, flags=re.I).strip()
        if query:
            canonical = f"play {query} on spotify"

    # Store install/download
    elif matched_action in ("install", "download") and _FROM_STORE.search(text):
        app = re.sub(_FROM_STORE, '', entity_span).strip()
        if not app:
            app = entity_span
        canonical = f"download {app} from microsoft store" if matched_action == "download" else f"install {app} from microsoft store"

    # Generic open
    elif matched_action == "open" and entity_span:
        canonical = f"open {entity_span}"

    # Generic close
    elif matched_action == "close" and entity_span:
        canonical = f"close {entity_span}"

    # Generic install
    elif matched_action == "install" and entity_span:
        canonical = f"install {entity_span}"

    # Generic download
    elif matched_action == "download" and entity_span:
        canonical = f"download {entity_span}"

    # Generic play
    elif matched_action == "play" and entity_span:
        canonical = f"play {entity_span}"

    # Take screenshot
    elif matched_action == "take screenshot":
        canonical = "take screenshot"

    # Search
    elif matched_action == "search" and entity_span:
        canonical = f"search for {entity_span}"

    if canonical:
        logger.info(
            "[MIXED_LANGUAGE] lang=%s original=%r canonical=%r action=%s entity=%r",
            detected_lang, transcript[:60], canonical[:60], matched_action, entity_span[:40],
        )

    return canonical
