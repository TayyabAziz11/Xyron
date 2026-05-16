"""
Input normalizer — runs on every user utterance before routing.

Pipeline (in order):
  1. Strip wake-word prefix  ("hey xyron open chrome" → "open chrome")
  2. Expand contractions     ("what's" → "what is")
  3. Strip leading filler    ("please can you open chrome" → "open chrome")
  4. Synonym expansion       ("launch chrome" → "open chrome")
  5. Collapse whitespace + lowercase

All transforms are pure string ops — zero latency, zero dependencies.
"""
from __future__ import annotations

import re

# ── 0. Xyron STT phonetic variant normalization ───────────────────────────────
# Whisper mishears "Xyron" as several phonetically similar words.
# Normalize them back to "xyron" BEFORE wake-word stripping so the wake-word
# pattern fires correctly on all variants.

_XYRON_VARIANTS_RE = re.compile(
    r"\b(?:"
    r"here['’]?s?\s+aaron|here\s+is\s+aaron|"  # "here's Aaron", "here is Aaron"
    r"zairon|zyron|zaron|zeiron|zaharon|"
    r"xylone|xiron|siron|syron|"
    r"zairan|searon|"
    r"herons?"                                         # rare but heard
    r")\b",
    re.IGNORECASE,
)

import logging as _logging
_norm_log = _logging.getLogger(__name__)


def _normalize_xyron_variants(text: str) -> str:
    """Replace STT phonetic mishearings of 'Xyron' with the canonical spelling."""
    def _replace(m: re.Match) -> str:
        raw = m.group(0)
        _norm_log.info("[NAME_NORMALIZE] raw=%r normalized='xyron'", raw)
        return "xyron"
    return _XYRON_VARIANTS_RE.sub(_replace, text)


# ── 1. Wake-word stripping ────────────────────────────────────────────────────

_WAKE_WORD_RE = re.compile(
    r"^(?:hey\s+xyron|ok\s+xyron|okay\s+xyron|yo\s+xyron|xyron)[,.\s]+",
    re.IGNORECASE,
)

# ── 2. Contractions ───────────────────────────────────────────────────────────

_CONTRACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bwhat's\b",  re.I), "what is"),
    (re.compile(r"\bhow's\b",   re.I), "how is"),
    (re.compile(r"\bwhat're\b", re.I), "what are"),
    (re.compile(r"\bwhat've\b", re.I), "what have"),
    (re.compile(r"\bdon't\b",   re.I), "do not"),
    (re.compile(r"\bdoesn't\b", re.I), "does not"),
    (re.compile(r"\bcan't\b",   re.I), "cannot"),
    (re.compile(r"\bwon't\b",   re.I), "will not"),
    (re.compile(r"\bit's\b",    re.I), "it is"),
    (re.compile(r"\bthere's\b", re.I), "there is"),
    (re.compile(r"\bthat's\b",  re.I), "that is"),
    (re.compile(r"\bwhere's\b", re.I), "where is"),
    (re.compile(r"\bwho's\b",   re.I), "who is"),
    (re.compile(r"\bhow'd\b",   re.I), "how did"),
    (re.compile(r"\bI'm\b",     re.I), "I am"),
    (re.compile(r"\bI'll\b",    re.I), "I will"),
]

# ── 3. Leading filler removal ─────────────────────────────────────────────────
# Applied iteratively so "please can you please open" collapses fully.

_FILLER_RE = re.compile(
    r"^(?:"
    r"please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|"
    r"i\s+want\s+(?:you\s+)?to\s+|i\s+need\s+(?:you\s+)?to\s+|"
    r"i(?:'d| would)\s+like\s+(?:you\s+)?to\s+|"
    r"go\s+ahead\s+and\s+"
    r")+",
    re.IGNORECASE,
)

# ── 4. Synonym table — ordered: longest phrase first ─────────────────────────

_SYNONYMS: list[tuple[re.Pattern, str]] = [
    # Volume (before generic "up/down" patterns)
    (re.compile(r"\bturn\s+(?:the\s+)?(?:volume|sound|audio)\s+up\b",    re.I), "volume up"),
    (re.compile(r"\bturn\s+(?:the\s+)?(?:volume|sound|audio)\s+down\b",  re.I), "volume down"),
    (re.compile(r"\braise\s+(?:the\s+)?(?:volume|sound)\b",              re.I), "volume up"),
    (re.compile(r"\blower\s+(?:the\s+)?(?:volume|sound)\b",              re.I), "volume down"),
    (re.compile(r"\blouder\b",                                            re.I), "volume up"),
    (re.compile(r"\bquieter\b",                                           re.I), "volume down"),
    (re.compile(r"\bsilence\b",                                           re.I), "mute"),
    # Brightness
    (re.compile(r"\bdim\s+(?:the\s+)?(?:screen|display|brightness)\b",   re.I), "decrease brightness"),
    (re.compile(r"\bbrighter\b",                                          re.I), "brightness up"),
    (re.compile(r"\bdarker\b",                                            re.I), "brightness down"),
    # App launch
    (re.compile(r"\blaunch\b",           re.I), "open"),
    (re.compile(r"\bstart\s+up\b",       re.I), "open"),
    (re.compile(r"\bfire\s+up\b",        re.I), "open"),
    (re.compile(r"\bboot\s+up\b",        re.I), "open"),
    (re.compile(r"\bkick\s+off\b",       re.I), "open"),
    (re.compile(r"\bbring\s+up\b",       re.I), "open"),
    (re.compile(r"\bnavigate\s+to\b",    re.I), "open"),
    (re.compile(r"\bgo\s+to\b",          re.I), "open"),
    (re.compile(r"\bvisit\b",            re.I), "open"),
    # Close / stop / kill
    (re.compile(r"\bhalt\b",             re.I), "stop"),
    (re.compile(r"\bterminate\b",        re.I), "kill"),
    (re.compile(r"\bend\s+process\b",    re.I), "kill process"),
    (re.compile(r"\bquit\b",             re.I), "close"),
    (re.compile(r"\bexit\b",             re.I), "close"),
    # Power
    (re.compile(r"\bshut\s+down\b",      re.I), "shutdown"),
    (re.compile(r"\bpower\s+off\b",      re.I), "shutdown"),
    (re.compile(r"\bturn\s+off\s+(?:the\s+)?(?:pc|computer|laptop|system)\b", re.I), "shutdown"),
    (re.compile(r"\breboot\b",           re.I), "restart"),
    (re.compile(r"\bsecure\s+(?:the\s+)?(?:screen|computer|pc)\b",       re.I), "lock screen"),
    # Search
    (re.compile(r"\bgoogle\b",           re.I), "search"),
    (re.compile(r"\blook\s+up\b",        re.I), "search"),
    (re.compile(r"\bfind\s+out\b",       re.I), "search"),
    # File / folder
    (re.compile(r"\bmake\s+(?:a\s+)?(?:new\s+)?folder\b", re.I), "create folder"),
    (re.compile(r"\bnew\s+folder\b",     re.I), "create folder"),
    (re.compile(r"\bremove\b",           re.I), "delete"),
    (re.compile(r"\berase\b",            re.I), "delete"),
    (re.compile(r"\bwipe\b",             re.I), "delete"),
    # Navigation
    (re.compile(r"\bshow\s+me\b",        re.I), "show"),
    # Determiner stripping for open/close commands
    # "open any setting" → "open setting", "open the settings" → "open settings"
    (re.compile(r"\bopen\s+(?:a[n]?\s+|the\s+|any\s+|my\s+|some\s+)", re.I), "open "),
    (re.compile(r"\bclose\s+(?:a[n]?\s+|the\s+|any\s+|my\s+)", re.I), "close "),
]

_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """
    Normalize a raw user utterance before routing.
    Returns clean, lowercase text. Safe to call on empty string.
    """
    if not text or not text.strip():
        return text

    # 0. Fix STT phonetic mishearings of "Xyron" before wake-word strip
    text = _normalize_xyron_variants(text)

    # 1. Strip wake word
    text = _WAKE_WORD_RE.sub("", text).strip()

    # 2. Expand contractions
    for pattern, replacement in _CONTRACTIONS:
        text = pattern.sub(replacement, text)

    # 3. Strip leading fillers (iterate until stable)
    prev = None
    while prev != text:
        prev = text
        text = _FILLER_RE.sub("", text).strip()

    # 4. Synonym expansion
    for pattern, replacement in _SYNONYMS:
        text = pattern.sub(replacement, text)

    # 5. Collapse whitespace + lowercase
    text = _WHITESPACE_RE.sub(" ", text).strip().lower()

    return text
