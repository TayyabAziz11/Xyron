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
    "band", "banda", "bandkaro", "bandkro",
    "karo", "kar", "karna", "karein", "kijiye", "karlo", "kardo",
    "batao", "bata", "btao", "btana", "batana", "batain", "bataen",
    "badhao", "badha", "barhao", "barha", "barhado", "badhado",
    "ghataو", "ghata", "kam", "kamkaro",
    "chalao", "chala", "chalana", "chalao", "chalein",
    "lo", "lena", "lelo", "dedo", "dena", "do", "dein", "de",
    "sunao", "suno", "dikhao", "dekho", "padho", "likho",
    "jaو", "jao", "aao", "aaja", "idhar", "aajao",
    "rakh", "rakho", "rakhna", "rakhlo",
    "utha", "uthao", "uthana", "uthalo",
    "laga", "lagao", "lagana",
    "shuru", "band",
    "bhejo", "bhej", "bhejna",
    "kholo", "dekhna", "dekhein",
    "ruko", "ruk", "rukna", "thehro",
    "bulo", "bulao", "bulana",
    "chuno", "chun", "chunna",
    "gino", "gin", "ginna",
    "samjho", "samjh", "samjhana", "samjhao",
    "pucho", "puch", "puchna", "poocho",
    "socho", "soch", "sochna",
    "nikalo", "nikal", "nikalna",
    "chup", "chupkaro", "chupraho",
    "khareedo", "khareed", "khareedna",
    "becho", "bech", "bechna",
    "banao", "bana", "banana", "banado",
    "mitao", "mita", "mitana",
    "chalaao", "chalu",
    "dhundo", "dhund", "dhundna", "talash",
    "chhupo", "chhup", "chhupana",
    "hilao", "hila", "hilana",
    "pakro", "pakar", "pakarna",
    "chhoro", "chhor", "chhorna",
    "bithao", "bitha", "bithana",
    "uthao", "suno", "sunna", "sunlo",
    "khao", "kha", "khana",
    "rok", "roko", "rukjao",
    "piyo", "pi", "pina",
    "sao", "so", "sona",
    "jago", "jag", "jagna",
    "aao", "ja", "jana", "jao",
})

_ROMAN_QUESTION: frozenset[str] = frozenset({
    "kya", "kahan", "kab", "kaun", "kyun", "kaise", "kitna",
    "kitni", "kitne", "konsa", "konsi", "konse",
    "batao", "bata",
    "kaisa", "kaisi", "kaisey",
    "kiske", "kiska", "kiski",
    "kahanpe", "kidhar",
})

_ROMAN_PRONOUNS: frozenset[str] = frozenset({
    "mein", "main", "mera", "meri", "mere",
    "tera", "teri", "tere", "aap", "tum", "tu",
    "yeh", "woh", "is", "us", "in", "un",
    "hum", "humara", "humari", "humare",
    "aapka", "aapki", "aapke",
    "tumhara", "tumhari", "tumhare",
    "uska", "uski", "uske",
    "iska", "iski", "iske",
    "unka", "unki", "unke",
    "inka", "inki", "inke",
    "khud", "apna", "apni", "apne",
    "yehlog", "wohlog",
    "koi", "kuch", "sab", "sablog",
    # Common Pakistani address terms (extremely high frequency in speech)
    "bhai", "yaar", "boss", "dost", "bhaiya",
    "bhaijan", "bhaijan", "ammi", "abba", "abu",
    "beta", "bhen", "baji",
})

_ROMAN_CONNECTORS: frozenset[str] = frozenset({
    "aur", "ya", "lekin", "magar", "phir", "toh", "bhi",
    "agar", "jab", "tabhi", "kyunke", "isliye",
    "aur", "phir", "dobara", "warna",
    "matlab", "yani", "yaani",
    "balke", "balkay", "balkeh",
    "halaanki", "halankeh",
    "albatta", "tahum", "lihaza",
    "isiliye", "isliye",
    "tab", "jab", "jabse", "tabse",
    "pehle", "baad", "phir", "phirse",
    "saath", "saathmein", "baghair",
})

_ROMAN_COMMON: frozenset[str] = frozenset({
    "hai", "hain", "tha", "thi", "the", "ho", "hoga", "hogi",
    "hun", "hoon", "hue", "huay", "huwa",
    "nahi", "nahin", "mat", "na", "nai",
    "abhi", "kal", "aaj", "parso", "ab",
    "theek", "achha", "accha", "shukriya", "meherbani",
    "zaroor", "bilkul", "seedha", "sahi",
    "pehle", "baad", "phir",
    "wala", "wali", "walay", "wale",
    "pe", "se", "ko", "ka", "ki", "ke", "mein",
    "raha", "rahi", "rahe",
    "diya", "diye", "dia",
    "zara", "thora", "thori", "zyada", "zyada",
    "sab", "kuch", "sirf", "bas",
    "bohat", "bahut", "buhat", "boht",
    "acha", "bura", "burra",
    "haan", "han", "ji", "jii",
    "nahi", "naheen", "nhi",
    "toh", "to", "phir",
    "theek", "thik",
    "mujhe", "tujhe", "usse", "humse",
    "tumse", "aapse", "issey", "ussey",
    "yahan", "wahan", "idhar", "udhar",
    "andar", "bahar", "upar", "neeche",
    "paas", "door", "aagay", "peechay",
    "dayen", "bayen", "seedha",
    "sach", "jhoot", "sahi", "ghalat",
    "pyara", "pyari", "khoobsurat",
    "aasan", "mushkil", "sakht",
    "jaldi", "dheere", "ahista",
    "naya", "nayi", "purana", "purani",
    "bara", "bari", "chota", "choti",
    "zyada", "kam", "thora", "thori",
    "poora", "poori", "adhura", "adhoori",
    "khush", "naraaz", "pareshan",
    "thaka", "thaki", "taza", "tazi",
    "garam", "thanda", "thandi",
    "acha", "bura", "behtareen", "zabardast",
    "lazim", "zaroori", "mumkin", "namumkin",
    "shukriya", "meharbani",
    "taqreeban", "lagbhag", "kareeb",
    # Previously missing: media next/prev, empty/clean, greetings/politeness.
    "agla", "agli", "pichla", "pichli",
    "khali", "saaf", "kachra", "kachray",
    "wapis", "vapis",
    "salam", "assalam", "walaikum", "alvida",
    "shaabash", "wah",
})

_ROMAN_COMMANDS: frozenset[str] = frozenset({
    "awaaz", "awaz", "awaaze",
    "waqt", "waqat", "vakta",
    "yaad", "note",
    "talash", "dhundo",
    "gaana", "gana",
    # Urdu-specific command nouns (NOT English loanwords like chrome/youtube —
    # those are English tech words used in Roman Urdu grammar, detected as
    # "mixed" when combined with Urdu verbs via the verb/connector sets)
    "tasveer",
    "naqsha",
    "ghari", "ghanta",
    "tareekh", "tarikh",
    "din", "raat", "subah", "shaam",
    "hukam", "hukum",
    "madad",
    "khabar", "khabrein",
    "mausam",
    "raasta", "rasta",
    "dukaan", "dukan",
    "daftar",
    "ghar", "gher",
    "tabdeeli", "tabdili",
    "ijaazat", "ijazat",
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

    if roman_ratio >= 0.30 or (roman_ratio >= 0.20 and whisper_urdu):
        # Roman Urdu — Latin script but Urdu vocabulary
        # Threshold 0.30 catches short commands like "pending orders dikhao" (1/3 = 0.33)
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
