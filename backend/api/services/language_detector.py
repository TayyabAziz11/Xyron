"""
Language detector — identifies the language of a voice transcript.

Supported codes: en | ur | ur_roman | hi | ar | mixed

Detection strategy:
  1. Arabic/Urdu script characters (U+0600–U+06FF range) → ur or ar
  2. Devanagari characters → hi
  3. Roman text keyword matching → ur_roman, mixed, hi
  4. STT language hint from Whisper as a tie-breaker

Logs:
  [LANG_DETECT_START]  — entry with raw inputs
  [LANG_DETECT_RESULT] — final detected code + confidence
  [LANG_DETECT_REASON] — decision reason string
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# Arabic/Urdu/Persian Unicode block
_ARABIC_SCRIPT_RE = re.compile(
    r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]"
)

# Devanagari (Hindi script)
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

# Urdu-script keywords that appear in common Urdu commands
_URDU_SCRIPT_KWS: frozenset[str] = frozenset({
    "کھولو", "کھول", "بند", "ڈاؤن", "لوڈ", "انسٹال", "بتاؤ", "چلاؤ",
    "سیٹنگ", "وائی", "فائی", "کروم", "یوٹیوب", "آواز", "بڑھاؤ",
    "دکھاؤ", "تلاش", "بھیجو", "پڑھو", "لکھو", "سمجھاؤ",
    "رکو", "شروع", "مکمل", "تیار", "ہوگیا", "ٹھیک",
    "شکریہ", "مہربانی", "ضرور", "بالکل",
    "فائل", "فولڈر", "ای میل", "واٹس ایپ",
})

# Arabic-specific keywords (MSA / Gulf dialect patterns, NOT shared with Urdu)
_ARABIC_KWS: frozenset[str] = frozenset({
    "افتح", "أغلق", "ابحث", "تشغيل", "إيقاف", "تحميل", "ماذا",
    "كيف", "أين", "لماذا", "شكرا", "مرحبا", "نعم",
})

# Roman Urdu command verbs, connectors, and common words.
# Keep only URDU-SPECIFIC grammar markers — NOT English loanwords like install/download,
# to avoid inflating en_hits and mis-scoring "isko install karo" as mixed.
_ROMAN_URDU_KWS: frozenset[str] = frozenset({
    "kholo", "karo", "chalao", "mujhe", "batao", "kya", "kaise",
    "se", "mein", "hai", "kar", "de", "raha", "hoon", "aur",
    "nahi", "bhi", "barhao", "kam", "isko", "yeh", "ye", "wala", "wali",
    "bata", "chalo", "dikha", "khol", "bandh",
    "hain", "tha", "thi", "ho", "hoga", "hogi",
    "hun", "hoon", "hue", "huay",
    "nahin", "nai",
    "abhi", "kal", "aaj", "parso", "ab",
    "theek", "achha", "accha", "shukriya", "meherbani",
    "zaroor", "bilkul", "seedha", "sahi",
    "pehle", "baad", "phir", "phirse",
    "walay", "wale",
    "pe", "ko", "ka", "ki", "ke",
    "rahi", "rahe", "diya", "diye",
    "zara", "thora", "thori", "zyada",
    "sab", "kuch", "sirf", "bas",
    "bohat", "bahut", "buhat", "boht",
    "haan", "han", "ji", "jii",
    "mujhe", "tujhe", "aap", "tum",
    "yahan", "wahan", "idhar", "udhar",
    "andar", "bahar", "upar", "neeche",
    "paas", "aagay", "peechay",
    "jaldi", "dheere", "ahista",
    "lekin", "magar", "kyunke", "isliye",
    "agar", "jab", "tab", "warna",
    "matlab", "yani", "yaani",
    "karo", "karna", "karein", "kijiye", "karlo", "kardo",
    "batao", "batana", "batain",
    "sunao", "suno", "dikhao", "dekho",
    "rako", "ruko", "ruk",
    "dhundo", "dhund", "talash",
    "bhejo", "bhej",
    "banao", "banado",
    "samjho", "samjhao",
    "pucho", "puch",
    # Media/system control verbs previously missing (media pause/next/prev,
    # recycle-bin empty) — added alongside the mixed_language_engine and
    # intent_router rules that now handle these commands.
    "rok", "roko", "rukjao",
    "agla", "agli", "pichla", "pichli",
    "khali", "saaf", "kachra", "kachray",
    "wapis", "vapis",
    # Common greetings/politeness — these carry no command meaning on their
    # own but were absent from the Roman-Urdu vocabulary entirely, so a
    # turn that opened with one (e.g. "Assalam o alaikum, Chrome kholo")
    # only scored on "kholo" and never got credit for the greeting itself.
    "salam", "assalam", "walaikum", "alvida",
    "shaabash", "wah",
})

# English homographs that ALSO exist as Roman-Urdu words. These used to
# live in _ROMAN_URDU_KWS, but a single hit was enough to flip a whole
# turn non-English — live bug: a user speaking ONLY English ("Now can you
# show me the available Wi-Fi network?") matched "the" → ur_roman → 2.3s
# Ollama Urdu localization + Urdu Edge-TTS on speech that had zero Urdu
# in it. Weak tokens NEVER trigger non-English detection on their own;
# genuine Roman-Urdu commands always carry at least one strong marker
# ("kholo", "karo", "nahin", "hai", "mein", ...) so nothing real is lost.
_ROMAN_URDU_WEAK_KWS: frozenset[str] = frozenset({
    "the", "do", "band", "sun", "mat", "na", "ya", "door",
})

# Roman Hindi words NOT found in Roman Urdu (to distinguish the two)
_ROMAN_HINDI_ONLY_KWS: frozenset[str] = frozenset({
    "bolo", "dekho", "dikhao", "theek", "accha", "haan",
})

# English command words used alongside Roman Urdu in mixed commands
_EN_CMD_KWS: frozenset[str] = frozenset({
    "open", "close", "download", "install", "search", "play", "stop",
    "chrome", "youtube", "store", "settings", "wifi", "calculator",
    "volume", "screen", "microsoft", "instagram", "facebook", "notepad",
    "explorer", "vs", "code", "edge", "teams", "spotify",
    "whatsapp", "email", "gmail", "linkedin", "twitter",
    "file", "folder", "document", "presentation", "spreadsheet",
    "delete", "copy", "paste", "rename", "move", "create",
    "new", "save", "undo", "redo", "refresh", "update",
    "google", "github", "docker", "terminal", "browser",
    "music", "video", "photo", "camera", "screenshot",
    "pending", "approved", "rejected", "unpaid", "paid",
    "invoice", "order", "report", "dashboard", "analytics",
})


def detect(transcript: str, stt_language: str | None = None) -> dict:
    """
    Detect the language of a voice transcript.

    Args:
        transcript:    raw STT output
        stt_language:  ISO-639-1 code Whisper reported (may be None or "en")

    Returns:
        {
            "lang":       "en" | "ur" | "ur_roman" | "hi" | "ar" | "mixed",
            "confidence": 0.0–1.0,
            "reason":     str,
        }
    """
    logger.info("[LANG_DETECT_START] transcript=%r stt_language=%s",
                transcript[:80], stt_language)

    text       = (transcript or "").strip()
    text_lower = text.lower()
    # Strip punctuation from each word so "kholo." matches "kholo" in keyword sets
    _punct = '.,!?;:"\'-—'
    words   = [w.strip(_punct) for w in text_lower.split()]
    words   = [w for w in words if w]  # drop empty tokens
    n_words = max(len(words), 1)

    # ── 1. Arabic / Urdu script detection ────────────────────────────────────
    arabic_chars = len(_ARABIC_SCRIPT_RE.findall(text))
    char_count   = max(len(text.replace(" ", "")), 1)
    arabic_ratio = arabic_chars / char_count

    if arabic_ratio > 0.25:
        urdu_hits   = sum(1 for kw in _URDU_SCRIPT_KWS if kw in text)
        arabic_hits = sum(1 for kw in _ARABIC_KWS      if kw in text)
        if arabic_hits > urdu_hits:
            lang, reason = "ar", f"arabic_script ratio={arabic_ratio:.2f} arabic_kw={arabic_hits}"
        else:
            lang, reason = "ur", f"urdu_script ratio={arabic_ratio:.2f} urdu_kw={urdu_hits}"
        return _emit(lang, 0.92, reason)

    # ── 2. Devanagari / Hindi script ─────────────────────────────────────────
    if _DEVANAGARI_RE.search(text):
        return _emit("hi", 0.92, "devanagari_script")

    # ── 3. Roman text — keyword matching ─────────────────────────────────────
    ru_hits = sum(1 for w in words if w in _ROMAN_URDU_KWS)
    ru_weak_hits = sum(1 for w in words if w in _ROMAN_URDU_WEAK_KWS)
    rh_hits = sum(1 for w in words if w in _ROMAN_HINDI_ONLY_KWS)
    en_hits = sum(1 for w in words if w in _EN_CMD_KWS)

    # Even 1 STRONG Roman Urdu grammar marker is a strong signal — short
    # commands like "chrome kholo" or "band karo" only have 1–2 Urdu words
    # ("kholo"/"karo" are strong; the weak English-homograph tokens around
    # them — "band", "the", "do"… — never count on their own, so a pure
    # English sentence can't be flipped non-English by a homograph).
    if ru_hits >= 1:
        conf = 0.88 if ru_hits >= 2 else 0.78
        if en_hits >= 1:
            # Mixed: English noun/app-name + Urdu verb/grammar
            reason = f"roman_urdu_hits={ru_hits} en_hits={en_hits}"
            return _emit("mixed", conf, reason)
        reason = f"roman_urdu_hits={ru_hits} ratio={ru_hits/n_words:.2f}"
        return _emit("ur_roman", conf, reason)

    if rh_hits >= 2:
        return _emit("hi", 0.78, f"roman_hindi_hits={rh_hits}")

    # ── 4. STT language hint as tie-breaker for low-signal text ─────────────
    # Live bug: Whisper's acoustic language-ID guessed "hi" at only ~0.60
    # confidence for pure English speech ("Play a song called Believer.",
    # "Now, play it.") — purely a coin-flip on short/accented audio, with
    # zero textual corroboration. Previously this branch only checked that
    # no Urdu/Hindi keywords matched; it never checked whether English
    # command words DID match. Requiring en_hits == 0 means: only trust
    # Whisper's shaky language guess when the transcript itself has no
    # English-command-word evidence contradicting it.
    stt = (stt_language or "en").lower()
    if stt in ("ur", "hi", "ar") and ru_hits == 0 and rh_hits == 0 and en_hits == 0 and ru_weak_hits == 0:
        _stt_map = {"ur": "ur", "hi": "hi", "ar": "ar"}
        return _emit(_stt_map[stt], 0.60, f"stt_reported={stt}")

    # ── 5. Default: English ───────────────────────────────────────────────────
    return _emit("en", 0.95, "no_non_english_signals")


def _emit(lang: str, confidence: float, reason: str) -> dict:
    logger.info("[LANG_DETECT_RESULT] lang=%s confidence=%.2f", lang, confidence)
    logger.info("[LANG_DETECT_REASON] %s", reason)
    return {"lang": lang, "confidence": confidence, "reason": reason}
