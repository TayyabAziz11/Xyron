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

# "His iron" / "the iron" are only treated as a Xyron mishearing at the very
# start of an utterance AND only when immediately followed by a comma — the
# live-observed pattern is always "His iron, <command>" (Whisper hearing the
# wake-word pause as a comma). Requiring the comma is what keeps this from
# false-positiving on genuine mentions of an actual iron, e.g. "the iron is
# hot, be careful" (no comma right after "iron") or "his iron content is low".
_XYRON_LEADING_VARIANTS_RE = re.compile(
    r"^(?:his|the)\s+iron\s*,\s*",
    re.IGNORECASE,
)

import logging as _logging
_norm_log = _logging.getLogger(__name__)

# "WhatsApp" is not a dictionary word Whisper reliably transcribes as one
# token — it commonly comes out as two words ("whats app" / "what's app").
# Requiring the "'s"/"s" (what'?s, not bare "what") is what keeps this from
# ever touching a genuinely different phrase like "what app is this" (no s).
# Placed BEFORE contraction expansion (step 2) — otherwise "what's app"
# would already be "what is app" by the time this ran, one word further
# from being fixable with a single targeted pattern.
_WHATSAPP_VARIANTS_RE = re.compile(r"\bwhat'?s\s+app\b", re.IGNORECASE)


def _normalize_whatsapp_variants(text: str) -> str:
    """Fix STT splitting 'WhatsApp' into two words ('whats app'/'what's app')."""
    def _replace(m: re.Match) -> str:
        raw = m.group(0)
        _norm_log.info("[NAME_NORMALIZE] raw=%r normalized='whatsapp'", raw)
        return "whatsapp"
    return _WHATSAPP_VARIANTS_RE.sub(_replace, text)

# "Watch on my screen/skin" is a recurring tiny.en mishearing of "what's on
# my screen" (live-observed 3x across two sessions: "screen" x2, "skin" x1)
# — it silently misses the dedicated screen-query fast path's regex entirely
# and falls through to the slow generic LLM route instead. Requiring "watch"
# to be followed IMMEDIATELY by "on my/the <noun>" (no object in between)
# keeps this from clobbering a real command like "watch this movie on my
# screen", which always has an object between "watch" and "on". "skin" has
# no legitimate meaning here (this assistant has no reason to ever discuss
# skin) so it's always corrected to "screen"; "display"/"monitor" are real
# synonyms a user might actually say, so those are preserved as-is.
_SCREEN_QUERY_MISHEARING_RE = re.compile(
    r"\bwatch\s+on\s+(?:my\s+|the\s+)?(screen|display|monitor|skin)\b",
    re.IGNORECASE,
)


def _normalize_screen_query_mishearing(text: str) -> str:
    """Fix STT mishearing 'watch on my screen/skin' → 'what's on my screen'."""
    def _replace(m: re.Match) -> str:
        raw  = m.group(0)
        noun = m.group(1)
        if noun.lower() == "skin":
            noun = "screen"
        fixed = f"what's on my {noun}"
        _norm_log.info("[SCREEN_QUERY_MISHEARING_FIX] raw=%r normalized=%r", raw, fixed)
        return fixed
    return _SCREEN_QUERY_MISHEARING_RE.sub(_replace, text)


def _normalize_xyron_variants(text: str) -> str:
    """Replace STT phonetic mishearings of 'Xyron' with the canonical spelling."""
    def _replace(m: re.Match) -> str:
        raw = m.group(0)
        _norm_log.info("[NAME_NORMALIZE] raw=%r normalized='xyron'", raw)
        return "xyron"
    def _replace_leading(m: re.Match) -> str:
        raw = m.group(0)
        _norm_log.info("[NAME_NORMALIZE] raw=%r normalized='xyron, '", raw)
        return "xyron, "
    text = _XYRON_LEADING_VARIANTS_RE.sub(_replace_leading, text)
    return _XYRON_VARIANTS_RE.sub(_replace, text)


# ── Drive-letter phonetic correction (real-mic Urdu test Issue 2A) ────────────
# Whisper spells a spoken drive LETTER as a homophone word ("C drive kholo"
# → "see/sea/cee/seed drive", "D drive" → "dee drive"). Correction is
# strictly context-scoped: only a homophone IMMEDIATELY before "drive" is
# rewritten, so ordinary verbs/nouns ("see my files", "dee is a name") are
# never touched. This is the SHARED post-Whisper layer — whisper_service's
# _CORRECTIONS handles the raw-STT comma variants, intent_router's drive
# rules consume the corrected "<letter> drive" form. Do not add parallel
# drive resolvers elsewhere.
_DRIVE_PHONETIC_MAP: dict[str, str] = {
    "see": "c", "sea": "c", "si": "c", "cee": "c", "seed": "c",
    "dee": "d",
    "ee": "e",
    "eff": "f",
}
_DRIVE_PHONETIC_RE = re.compile(
    r'\b(?:' + '|'.join(sorted(_DRIVE_PHONETIC_MAP, key=len, reverse=True)) + r')\s+drive\b',
    re.IGNORECASE,
)


def _correct_drive_phonetics(text: str) -> str:
    """Rewrite drive-letter homophones ("open see drive" → "open c drive")."""
    def _repl(m: re.Match) -> str:
        word = m.group(0).split()[0].lower()
        letter = _DRIVE_PHONETIC_MAP.get(word)
        if not letter:
            return m.group(0)
        _norm_log.info("[DRIVE_PHONETIC_FIX] %r → '%s drive'", m.group(0), letter)
        return f"{letter} drive"
    return _DRIVE_PHONETIC_RE.sub(_repl, text)


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

    # 0.2. Fix "whats app"/"what's app" -> "whatsapp" before contraction
    # expansion turns "what's" into "what is" (see _WHATSAPP_VARIANTS_RE).
    text = _normalize_whatsapp_variants(text)

    # 0.5. Fix "watch on my screen" mishearing of "what's on my screen"
    text = _normalize_screen_query_mishearing(text)

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

    # 6. Drive-letter phonetic correction (context-scoped to "<word> drive")
    text = _correct_drive_phonetics(text)

    # 7. Strip trailing sentence punctuation — routing regexes and object
    #    resolution work on clean phrases ("open c drive." → "open c drive").
    text = text.rstrip(".,!?;: ").strip()

    return text
