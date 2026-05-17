"""
Multilingual semantic normalizer — Roman Urdu / mixed Urdu+English → intent-ready English.

This is NOT translation. It maps command-intent phrases to their English equivalents
so the existing intent router can match them without needing hundreds of new patterns.

Design:
- Phrase-level mapping (longest match first) for common commands
- Token-level synonym replacement for individual Urdu words
- difflib fuzzy matching for phonetic variants (Whisper misreadings)
- Target: <20ms, pure Python, no external dependencies

Examples:
  "awaz badhao"          → "volume up"
  "vs code kholo"        → "open vs code"
  "battery kitni hai"    → "battery status"
  "youtube chala do"     → "open youtube"
  "screenshot lo"        → "take screenshot"
"""
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Optional

# ── Phrase-level mappings (longest match wins) ────────────────────────────────
# Format: "roman urdu phrase" → "english intent phrase"
# Sorted longest-first at build time for greedy matching.

_PHRASE_MAP: dict[str, str] = {
    # Volume
    "volume zara barha do":     "volume up",
    "volume thori barha do":    "volume up",
    "volume zara tez karo":     "volume up",
    "awaz zara tez karo":       "volume up",
    "awaaz zara tez karo":      "volume up",
    "awaz thori barha do":      "volume up",
    "awaaz thori barha do":     "volume up",
    "awaz badhao":              "volume up",
    "awaaz badhao":             "volume up",
    "awaz barhao":              "volume up",
    "awaaz barhao":             "volume up",
    "awaz barhado":             "volume up",
    "awaaz utha":               "volume up",
    "volume barha do":          "volume up",
    "volume badhao":            "volume up",
    "volume barhao":            "volume up",
    "sound badhao":             "volume up",
    "avast bhau":               "volume up",    # Whisper mishearing of "awaaz barhao"
    "awaz zara kam karo":       "volume down",
    "awaaz zara kam karo":      "volume down",
    "awaz thori kam karo":      "volume down",
    "awaz kam karo":            "volume down",
    "awaaz kam karo":           "volume down",
    "volume kam karo":          "volume down",
    "awaaz ghata":              "volume down",
    "sound kam karo":           "volume down",
    # Time / date
    "waqt batao":               "what time is it",
    "waqat batao":              "what time is it",
    "vakta batao":              "what time is it",
    "vakta batao mujhe":        "what time is it",
    "time batao":               "what time is it",
    "kitna waqt hai":           "what time is it",
    "kya time hai":             "what time is it",
    "abhi kya waqt hai":        "what time is it",
    "aaj ki date batao":        "what is the date",
    "aaj kya date hai":         "what is the date",
    # Battery
    "battery kitni hai":        "battery status",
    "battery batao":            "battery status",
    "charge kitna hai":         "battery status",
    "kitni battery hai":        "battery status",
    "battery ka haal batao":    "battery status",
    "battery level batao":      "battery status",
    # Screenshot
    "screenshot lo":            "take screenshot",
    "screenshot le lo":         "take screenshot",
    "screen shot lo":           "take screenshot",
    "screen capture karo":      "take screenshot",
    "screen ki photo lo":       "take screenshot",
    # Open app
    "settings kholo":           "open settings",
    "setting kholo":            "open settings",
    "settings kholna":          "open settings",
    "chrome kholo":             "open chrome",
    "chrome kholna":            "open chrome",
    "youtube kholo":            "open youtube",
    "youtube chala do":         "open youtube",
    "youtube chalao":           "open youtube",
    "spotify kholo":            "open spotify",
    "spotify chala do":         "open spotify",
    "vs code kholo":            "open vscode",
    "vscode kholo":             "open vscode",
    "notepad kholo":            "open notepad",
    "calculator kholo":         "open calculator",
    "terminal kholo":           "open terminal",
    "gmail kholo":              "open gmail",
    "github kholo":             "open github",
    "netflix kholo":            "open netflix",
    "shuru karo":               "open",
    # Close app
    "band karo":                "close",
    "band kar do":              "close",
    "close karo":               "close",
    "bند karo":                 "close",
    # Remember / note
    "yaad rakho":               "remember",
    "note karo":                "remember",
    "likh lo":                  "remember",
    "save karo":                "remember",
    # Search
    "talash karo":              "search",
    "dhundo":                   "search",
    "search karo":              "search",
    "google karo":              "google search",
    "youtube pe dhundo":        "search youtube",
    # Lock / shutdown
    "lock karo":                "lock screen",
    "screen lock karo":         "lock screen",
    "band kar do computer":     "shutdown",
    "shutdown karo":            "shutdown",
    # WiFi
    "wifi on karo":             "enable wifi",
    "wifi chalu karo":          "enable wifi",
    "wifi lagao":               "enable wifi",
    "wifi band karo":           "disable wifi",
    "wifi off karo":            "disable wifi",
    "wifi check karo":          "wifi status",
    "wifi batao":               "wifi status",
    "network check karo":       "wifi status",
    # Bluetooth
    "bluetooth on karo":        "enable bluetooth",
    "bluetooth chalu karo":     "enable bluetooth",
    "bluetooth band karo":      "disable bluetooth",
    "bluetooth off karo":       "disable bluetooth",
    # Brightness
    "brightness barha do":      "brightness up",
    "screen roshan karo":       "brightness up",
    "brightness kam karo":      "brightness down",
    "screen dim karo":          "brightness down",
    # Mute
    "mute karo":                "mute",
    "awaaz band karo":          "mute",
    "awaz band karo":           "mute",
    "unmute karo":              "unmute",
    # System info
    "system ka haal batao":     "system status",
    "pc ka haal batao":         "system status",
    # File ops
    "folder banao":             "create folder",
    "naya folder banao":        "create folder",
    "file dhundo":              "search files",
    # Clipboard
    "copy karo":                "copy",
    "paste karo":               "paste",
    # Sleep
    "so jao":                   "sleep",
    "pc so jaye":               "sleep",
    "hibernate karo":           "hibernate",
}

# Build longest-first sorted list at import time
_SORTED_PHRASES: list[tuple[str, str]] = sorted(
    _PHRASE_MAP.items(), key=lambda x: len(x[0]), reverse=True
)

# ── Token-level synonyms (single word → English word) ─────────────────────────
_TOKEN_MAP: dict[str, str] = {
    # Verbs
    "kholo":    "open",
    "khol":     "open",
    "kholain":  "open",
    "kholay":   "open",
    "chalao":   "open",
    "chala":    "open",
    "lagao":    "open",
    "shuru":    "start",
    "band":     "close",
    "bandkaro": "close",
    "badhao":   "increase",
    "barhao":   "increase",
    "barha":    "increase",
    "badha":    "increase",
    "ghataو":   "decrease",
    "ghata":    "decrease",
    "kam":      "decrease",
    "lo":       "take",
    "batao":    "tell",
    "btao":     "tell",
    "dhundo":   "find",
    "talash":   "search",
    "yaad":     "remember",
    "banao":    "create",
    "bana":     "create",
    "banana":   "create",
    "hatao":    "delete",
    "mita":     "delete",
    "copy":     "copy",
    "paste":    "paste",
    # Nouns
    "awaaz":    "volume",
    "awaz":     "volume",
    "waqt":     "time",
    "waqat":    "time",
    "vakta":    "time",
    "battery":  "battery",
    "charge":   "battery",
    "settings": "settings",
    "setting":  "settings",
}

# ── Fuzzy matching vocab (for Whisper mishearings) ─────────────────────────────
_FUZZY_VOCAB: list[str] = list(_PHRASE_MAP.keys())

# Pre-compile tokenizer
_SPLIT_RE = re.compile(r"\s+")

# Urdu filler / address words that carry no semantic intent — stripped after normalization
_FILLER_WORDS: frozenset[str] = frozenset({
    "yar", "yaar", "bhai", "bhaijaan", "arre", "oye", "oi",
    "sun", "suno", "dekho", "please", "zara", "jaldi",
    "mujhe", "humein",
})


def normalize(text: str) -> str:
    """
    Normalize multilingual / Roman Urdu text to English intent phrases.

    Returns the normalized English text. If no Urdu signals found, returns
    the original text unchanged (no-op for pure English input).
    """
    if not text:
        return text

    cleaned = text.strip().lower()

    # Quick check — if no Roman Urdu tokens, skip expensive processing
    if not _has_urdu_signals(cleaned):
        return text

    # 1. Phrase-level mapping (longest match first)
    result = _apply_phrases(cleaned)

    # 2. Token-level synonym replacement on remaining text
    result = _apply_tokens(result)

    # 3. Fuzzy fallback for likely Whisper mishearings
    result = _fuzzy_correct(result)

    # 4. Strip filler words so the intent router gets clean semantic text
    result = _strip_fillers(result)

    return result.strip()


def _strip_fillers(text: str) -> str:
    """Remove Urdu address/filler words that add no intent signal."""
    tokens = _SPLIT_RE.split(text.strip())
    cleaned = [t for t in tokens if t not in _FILLER_WORDS]
    # Keep at least one token
    return " ".join(cleaned) if cleaned else text


def _has_urdu_signals(text: str) -> bool:
    """Fast check: any Urdu script chars OR any token in the phrase/token maps."""
    if any(0x0600 <= ord(c) <= 0x06FF for c in text):
        return True
    tokens = _SPLIT_RE.split(text)
    # Check against all token-map keys AND a fast-path set of common signals
    _fast = {
        "kholo", "karo", "batao", "chalao", "chala", "band", "awaaz", "awaz",
        "waqt", "badhao", "barhao", "ghata", "kam", "yaad", "talash",
        "dhundo", "shuru", "lo", "btao", "hai", "hain", "kya", "do", "kholna",
        "banao", "bana", "hatao", "mita", "lagao", "utha", "rakh", "barha",
        "zara", "thori", "thora", "bhi", "mujhe",
    }
    return any(t in _fast or t in _TOKEN_MAP for t in tokens)


def _apply_phrases(text: str) -> str:
    """Replace longest-matching Roman Urdu phrases with English equivalents."""
    for roman, english in _SORTED_PHRASES:
        if roman in text:
            text = text.replace(roman, english)
    return text


def _apply_tokens(text: str) -> str:
    """Replace individual Roman Urdu tokens with English synonyms."""
    tokens = _SPLIT_RE.split(text)
    replaced = [_TOKEN_MAP.get(t, t) for t in tokens]
    return " ".join(replaced)


def _fuzzy_correct(text: str) -> str:
    """
    For short inputs (≤5 words) that STILL look like Roman Urdu after phrase+token
    replacement, try fuzzy-matching against known phrases.

    Skip if the text has already been normalized to English — otherwise difflib
    would match English output (e.g. "volume down") back to a Roman Urdu phrase.
    """
    # Only run if Urdu signals are still present
    if not _has_urdu_signals(text):
        return text

    tokens = _SPLIT_RE.split(text.strip())
    if len(tokens) > 5:
        return text   # skip fuzzy for long sentences

    candidates = get_close_matches(text, _FUZZY_VOCAB, n=1, cutoff=0.72)
    if candidates:
        return _PHRASE_MAP[candidates[0]]

    return text
