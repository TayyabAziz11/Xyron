"""
Language detection for multilingual voice input.

Detects: english | urdu | roman_urdu | mixed
Returns confidence score, script type, and primary language.
Target latency: <15ms (pure Python, no ML model).

Detection signals (in priority order):
1. Unicode Urdu/Arabic script characters  (definitive)
2. Roman Urdu vocabulary tokens           (strong signal)
3. Bilingual token frequency ratio        (mixed detection)
"""
from __future__ import annotations

import re
import time
from typing import TypedDict

# ── Urdu Unicode ranges ────────────────────────────────────────────────────────
# Arabic Block: U+0600–U+06FF  covers Urdu script
_URDU_CHAR_MIN = 0x0600
_URDU_CHAR_MAX = 0x06FF

# ── Roman Urdu vocabulary ──────────────────────────────────────────────────────
# Grouped by type for maintainability — covers Pakistani conversational speech.
_ROMAN_VERBS: frozenset[str] = frozenset({
    "kholo", "khol", "kholna", "kholain", "kholay",
    "band", "banda", "bandkaro",
    "karo", "kar", "karna", "karein", "kijiye",
    "batao", "bata", "btao", "btana", "batana",
    "badhao", "badha", "barhao", "barha", "barhado", "badhado",
    "ghataو", "ghata", "kam", "kamkaro",
    "chalao", "chala", "chalana", "chalao",
    "lo", "lena", "lelo", "dedo", "dena", "do",
    "sunao", "suno", "dikhao", "dekho", "padho", "likho",
    "jaو", "jao", "aao", "aaja", "idhar",
    "rakh", "rakho", "rakhna",
    "utha", "uthao",
    "laga", "lagao",
    "shuru", "band",
    "bhejo", "bhej",
    "kholo", "dekhna",
})

_ROMAN_QUESTION: frozenset[str] = frozenset({
    "kya", "kahan", "kab", "kaun", "kyun", "kaise", "kitna",
    "kitni", "kitne", "konsa", "konsi",
    "batao", "bata",
})

_ROMAN_PRONOUNS: frozenset[str] = frozenset({
    "mein", "main", "mera", "meri", "mere",
    "tera", "teri", "tere", "aap", "tum", "tu",
    "yeh", "woh", "is", "us", "in", "un",
    "hum", "humara", "humari",
})

_ROMAN_CONNECTORS: frozenset[str] = frozenset({
    "aur", "ya", "lekin", "magar", "phir", "toh", "bhi",
    "agar", "jab", "tabhi", "kyunke", "isliye",
    "aur", "phir", "dobara", "warna",
})

_ROMAN_COMMON: frozenset[str] = frozenset({
    "hai", "hain", "tha", "thi", "the", "ho", "hoga", "hogi",
    "hun", "hoon", "hue",
    "nahi", "nahin", "mat", "na",
    "abhi", "kal", "aaj", "parso",
    "theek", "achha", "accha", "shukriya", "meherbani",
    "zaroor", "bilkul", "seedha",
    "pehle", "baad", "phir",
    "wala", "wali", "walay",
    "pe", "se", "ko", "ka", "ki", "ke", "mein",
    "raha", "rahi", "rahe",
    "diya", "diye",
    "zara", "thora", "thori", "zyada", "zyada",
    "sab", "kuch", "sirf",
})

_ROMAN_COMMANDS: frozenset[str] = frozenset({
    "awaaz", "awaz", "awaaze",
    "waqt", "waqat", "vakta",
    "screenshot", "screenshottlo",
    "yaad", "note",
    "talash", "dhundo",
    "battery", "charge",
    "settings", "setting",
    "folder", "file", "files",
    "music", "gaana", "gana",
    "screen",
})

_ROMAN_URDU_ALL: frozenset[str] = (
    _ROMAN_VERBS | _ROMAN_QUESTION | _ROMAN_PRONOUNS |
    _ROMAN_CONNECTORS | _ROMAN_COMMON | _ROMAN_COMMANDS
)

# Pre-compile tokenizer
_TOKEN_RE = re.compile(r"[a-zA-Z؀-ۿ]+")


class LangResult(TypedDict):
    language:   str    # "english" | "urdu" | "roman_urdu" | "mixed"
    confidence: float  # 0.0 – 1.0
    script:     str    # "latin" | "arabic" | "roman" | "mixed"
    primary:    str    # dominant language: "urdu" | "english"


def detect(text: str, whisper_lang: str | None = None) -> LangResult:
    """
    Detect language of input text.

    Args:
        text:         Raw transcription from Whisper or typed input.
        whisper_lang: ISO code Whisper reported (e.g. "ur", "en") — used as
                      a strong prior when available.

    Returns:
        LangResult dict with language, confidence, script, primary fields.
    """
    t0 = time.perf_counter()
    result = _detect(text, whisper_lang)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > 15:
        import logging
        logging.getLogger(__name__).debug(
            "[LangDetect] slow detection %.1fms for %r", elapsed_ms, text[:40]
        )
    return result


def _detect(text: str, whisper_lang: str | None) -> LangResult:
    if not text or not text.strip():
        return LangResult(language="english", confidence=0.5, script="latin", primary="english")

    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return LangResult(language="english", confidence=0.5, script="latin", primary="english")

    # ── Signal 1: Urdu script characters ──────────────────────────────────────
    urdu_chars  = sum(1 for c in text if _URDU_CHAR_MIN <= ord(c) <= _URDU_CHAR_MAX)
    latin_chars = sum(1 for c in text if c.isalpha() and ord(c) <= 127)
    total_alpha = urdu_chars + latin_chars

    if total_alpha > 0:
        urdu_script_ratio = urdu_chars / total_alpha
    else:
        urdu_script_ratio = 0.0

    # ── Signal 2: Roman Urdu vocabulary ───────────────────────────────────────
    latin_tokens    = [t for t in tokens if all(ord(c) <= 127 for c in t)]
    roman_hits      = sum(1 for t in latin_tokens if t in _ROMAN_URDU_ALL)
    roman_ratio     = roman_hits / len(latin_tokens) if latin_tokens else 0.0

    # ── Signal 3: Whisper language prior ──────────────────────────────────────
    whisper_urdu = whisper_lang == "ur"
    whisper_en   = whisper_lang == "en"

    # ── Classification ────────────────────────────────────────────────────────
    if urdu_script_ratio >= 0.85:
        # Predominantly Urdu script
        return LangResult(language="urdu", confidence=round(0.7 + urdu_script_ratio * 0.3, 2),
                          script="arabic", primary="urdu")

    if urdu_script_ratio >= 0.25:
        # Mixed script (Urdu + English words like "کھولو Chrome")
        conf = round(0.65 + urdu_script_ratio * 0.25, 2)
        return LangResult(language="mixed", confidence=conf, script="mixed", primary="urdu")

    if roman_ratio >= 0.35 or (roman_ratio >= 0.20 and whisper_urdu):
        # Roman Urdu — Latin script but Urdu vocabulary
        conf = round(min(0.95, 0.55 + roman_ratio * 1.1 + (0.15 if whisper_urdu else 0)), 2)
        primary = "urdu"
        lang    = "roman_urdu"
        if roman_ratio >= 0.15 and latin_tokens and roman_hits < len(latin_tokens) * 0.7:
            lang = "mixed"
        return LangResult(language=lang, confidence=conf, script="roman", primary=primary)

    if whisper_urdu and roman_ratio >= 0.10:
        # Weak Roman Urdu but Whisper reported Urdu — trust Whisper
        return LangResult(language="roman_urdu", confidence=0.65, script="roman", primary="urdu")

    # Default: English
    conf = 0.90 if whisper_en else (0.75 if roman_ratio < 0.05 else 0.60)
    return LangResult(language="english", confidence=conf, script="latin", primary="english")


def is_urdu(result: LangResult) -> bool:
    """True for urdu, roman_urdu, or mixed with Urdu primary."""
    return result["primary"] == "urdu"
