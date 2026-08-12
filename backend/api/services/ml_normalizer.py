"""
Multilingual command normalizer.

Converts Roman Urdu, mixed (Roman Urdu + English), and Urdu-script commands
to their English equivalents so the existing intent router can handle them
without any modifications.

Logs:
  [ML_NORMALIZE_INPUT]  — language + raw transcript
  [ML_NORMALIZE_OUTPUT] — normalized English command
  [ML_NORMALIZE_RULE]   — which rule matched
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# ── Urdu-script (Nastaliq) → English map ─────────────────────────────────────
# Exact phrase lookup before falling back to regex rules.
_URDU_EXACT: list[tuple[str, str]] = [
    ("وائی فائی سیٹنگز کھولو",  "open wifi settings"),
    ("وائی فائی سیٹنگ کھولو",   "open wifi settings"),
    ("وائی فائی کھولو",          "open wifi"),
    ("کروم کھولو",               "open chrome"),
    ("کیلکولیٹر کھولو",         "open calculator"),
    ("نوٹ پیڈ کھولو",           "open notepad"),
    ("سیٹنگز کھولو",            "open settings"),
    ("سیٹنگ کھولو",             "open settings"),
    ("یوٹیوب کھولو",            "open youtube"),
    ("ایکسپلورر کھولو",         "open file explorer"),
    ("اسٹور کھولو",             "open microsoft store"),
    ("مائیکروسافٹ اسٹور کھولو", "open microsoft store"),
    ("بند کرو",                  "close it"),
    ("اسے بند کرو",             "close it"),
    ("انسٹال کرو",              "install it"),
    ("اسے انسٹال کرو",         "install it"),
    ("ڈاؤن لوڈ کرو",           "download it"),
    ("آواز بڑھاؤ",              "volume up"),
    ("آواز کم کرو",            "volume down"),
    ("میوٹ کرو",               "mute"),
    ("اسکرین شاٹ لو",          "take screenshot"),
]

# ── Arabic (Modern Standard / Gulf) → English map ────────────────────────────
_ARABIC_EXACT: list[tuple[str, str]] = [
    ("افتح كروم",               "open chrome"),
    ("افتح كروم",               "open chrome"),          # alt spelling
    ("افتح جوجل كروم",          "open chrome"),
    ("افتح إعدادات الواي فاي",  "open wifi settings"),
    ("إعدادات الواي فاي",      "open wifi settings"),
    ("الواي فاي",              "open wifi settings"),
    ("افتح إعدادات الويفاي",    "open wifi settings"),
    ("ارفع الصوت",              "volume up"),
    ("زد الصوت",               "volume up"),
    ("خفض الصوت",              "volume down"),
    ("اخفض الصوت",             "volume down"),
    ("كتم الصوت",              "mute"),
    ("افتحه",                  "open it"),
    ("ثبت انستغرام",           "install instagram"),
    ("حمل انستغرام",           "download instagram"),
    ("ثبت",                    "install it"),
    ("التقط لقطة شاشة",       "take screenshot"),
    ("لقطة شاشة",             "take screenshot"),
    ("اغلق",                   "close it"),
    ("اغلقه",                  "close it"),
]

# ── Roman Urdu / Mixed regex rules ───────────────────────────────────────────
# Ordered — first match wins.
# Each entry: (compiled Pattern, replacement_string, rule_name)
_RULES: list[tuple[re.Pattern, str, str]] = [
    # "X se Y download/install karo" → "download/install Y from microsoft store"
    # Accept "star" for "store" (tiny.en mishears neural Urdu "store" as "star")
    # Accept "kero"/"kare" for "karo" (Edge-TTS phonetic variant)
    (re.compile(r"microsoft\s+(?:store|star)\s+(?:se|ac|say|sa)\s+(.+?)\s+download[,.]?\s*(?:[ck]ar[oea]?[lr]?\b|kero\b|kare\b)\b", re.I),
     r"download \1 from microsoft store", "store_se_X_download"),
    (re.compile(r"microsoft\s+(?:store|star)\s+(?:se|ac|say|sa)\s+(.+?)\s+install[,.]?\s*(?:[ck]ar[oea]?[lr]?\b|kero\b|kare\b)\b", re.I),
     r"install \1 from microsoft store", "store_se_X_install"),
    (re.compile(r"\b(?:store|star)\s+(?:se|ac|say|sa)\s+(.+?)\s+download[,.]?\s*(?:[ck]ar[oea]?[lr]?\b|kero\b|kare\b)\b", re.I),
     r"download \1 from microsoft store", "se_X_download"),
    (re.compile(r"\b(?:store|star)\s+(?:se|ac|say|sa)\s+(.+?)\s+install[,.]?\s*(?:[ck]ar[oea]?[lr]?\b|kero\b|kare\b)\b", re.I),
     r"install \1 from microsoft store", "se_X_install"),
    # Pronoun: "isko / ise / ye / yeh download/install/open karo"
    # Must come BEFORE generic \b(\S+) rules so pronouns are caught first.
    (re.compile(r"\b(?:isko|ise|ye|yeh)\s+install\s+(?:kar[oa]?)?\b", re.I),
     "install it", "isko_install_karo"),
    (re.compile(r"\b(?:isko|ise|ye|yeh)\s+download\s+(?:kar[oa]?)?\b", re.I),
     "download it", "isko_download_karo"),
    (re.compile(r"\b(?:isko|ise|ye|yeh)\s+uninstall\s+(?:kar[oa]?)?\b", re.I),
     "uninstall it", "isko_uninstall_karo"),
    (re.compile(r"\b(?:isko|ise|ye|yeh)\s+(?:open|khol[oa]?)\s*(?:kar[oa]?)?\b", re.I),
     "open it", "isko_open"),
    # "X download/install karo"
    (re.compile(r"\b(\S+)\s+download\s+kar[oa]?\b", re.I),
     r"download \1", "X_download_karo"),
    (re.compile(r"\b(\S+)\s+install\s+kar[oa]?\b", re.I),
     r"install \1", "X_install_karo"),
    (re.compile(r"\b(\S+)\s+uninstall\s+kar[oa]?\b", re.I),
     r"uninstall \1", "X_uninstall_karo"),
    # "X open karo" / "X launch karo" — reversed verb-object order (Urdu grammar)
    (re.compile(r"\b(.+?)\s+(?:open|launch)\s+kar[oa]?\b", re.I),
     r"open \1", "X_open_karo"),
    (re.compile(r"\b(.+?)\s+(?:open|launch)\s+kar[oa]?\b", re.I),
     r"open \1", "X_launch_karo"),
    # Specific apps + kholo (order matters — more specific before generic)
    (re.compile(r"\bwifi\s+settings?\s+khol[oa]?\b", re.I),
     "open wifi settings", "wifi_settings_kholo"),
    (re.compile(r"\bvs\s+code\s+khol[oa]?\b", re.I),
     "open vs code", "vscode_kholo"),
    (re.compile(r"\bmicrosoft\s+store\s+khol[oa]?\b", re.I),
     "open microsoft store", "ms_store_kholo"),
    (re.compile(r"\bfile\s+explorer\s+khol[oa]?\b", re.I),
     "open file explorer", "file_explorer_kholo"),
    (re.compile(r"\btask\s+manager\s+khol[oa]?\b", re.I),
     "open task manager", "task_mgr_kholo"),
    (re.compile(r"\bsettings?\s+khol[oa]?\b", re.I),
     "open settings", "settings_kholo"),
    (re.compile(r"\bcalculator\s+khol[oa]?\b", re.I),
     "open calculator", "calc_kholo"),
    (re.compile(r"\bnotepad\s+khol[oa]?\b", re.I),
     "open notepad", "notepad_kholo"),
    (re.compile(r"\bchrome\s+khol[oa]?\b", re.I),
     "open chrome", "chrome_kholo"),
    (re.compile(r"\byoutube\s+khol[oa]?\b", re.I),
     "open youtube", "youtube_kholo"),
    (re.compile(r"\bspotify\s+khol[oa]?\b", re.I),
     "open spotify", "spotify_kholo"),
    (re.compile(r"\bdiscord\s+khol[oa]?\b", re.I),
     "open discord", "discord_kholo"),
    (re.compile(r"\bwhatsapp\s+khol[oa]?\b", re.I),
     "open whatsapp", "whatsapp_kholo"),
    (re.compile(r"\binstagram\s+khol[oa]?\b", re.I),
     "open instagram", "instagram_kholo"),
    (re.compile(r"\bstore\s+khol[oa]?\b", re.I),
     "open microsoft store", "store_kholo"),
    # Generic: "X kholo" (anything else)
    (re.compile(r"\b(.+?)\s+khol[oa]?\b", re.I),
     r"open \1", "X_kholo_generic"),
    # Volume
    (re.compile(r"\bvolume\s+barha[oa]?\b", re.I),
     "volume up", "volume_barhao"),
    (re.compile(r"\bvolume\s+kam\s+kar[oa]?\b", re.I),
     "volume down", "volume_kam_karo"),
    (re.compile(r"\bvolume\s+mute\s+kar[oa]?\b", re.I),
     "mute", "volume_mute"),
    (re.compile(r"\bawaz\s+barha[oa]?\b", re.I),
     "volume up", "awaz_barhao"),
    (re.compile(r"\bawaz\s+kam\s+kar[oa]?\b", re.I),
     "volume down", "awaz_kam"),
    # Close
    (re.compile(r"\bband\s+kar[oa]?\b", re.I),
     "close it", "band_karo"),
    (re.compile(r"\bbandh\s+kar[oa]?\b", re.I),
     "close it", "bandh_karo"),
    # Screenshot
    (re.compile(r"\bscreenshot\s+(?:lo|lao)\b", re.I),
     "take screenshot", "screenshot_lo"),
    # YouTube: "YouTube par X chalao" / "X YouTube par chalao"
    (re.compile(r"\byoutube\s+par\s+(.+?)\s+chala[oa]?\b", re.I),
     r"play \1 on youtube", "youtube_par_X_chalao"),
    (re.compile(r"\b(.+?)\s+youtube\s+par\s+chala[oa]?\b", re.I),
     r"play \1 on youtube", "X_youtube_par_chalao"),
    # Search: "X search karo" / "X ko search karo"
    (re.compile(r"\b(.+?)\s+(?:ko\s+)?search\s+kar[oa]?\b", re.I),
     r"search for \1", "X_search_karo"),
    # Screenshot
    (re.compile(r"\bscreenshot\s+(?:lo|lao|le[oa]?)\b", re.I),
     "take screenshot", "screenshot_lo"),
    # Weather / time queries
    (re.compile(r"\baaj\s+ka\s+mausam\s+(?:kya\s+hai|bata[oa]?)\b", re.I),
     "what is the weather today", "aaj_ka_mausam"),
    (re.compile(r"\babhi\s+(?:kitne\s+baj[ey]?\s+hain?|time\s+kya\s+hai)\b", re.I),
     "what time is it", "abhi_time_kya_hai"),
    # Volume with awaaz variant
    (re.compile(r"\bawaaz\s+barha[oa]?\b", re.I),
     "volume up", "awaaz_barhao"),
    (re.compile(r"\bawaaz\s+kam\s+kar[oa]?\b", re.I),
     "volume down", "awaaz_kam"),
    # "X ke baare mein batao" / "batao X"
    (re.compile(r"\b(?:mujhe\s+)?(.+?)\s+ke\s+baare\s+mein\s+bata[oa]?\b", re.I),
     r"what is \1", "ke_baare_mein_batao"),
    (re.compile(r"\bbata[oa]?\s+(.+)", re.I),
     r"what is \1", "batao_X"),
]


def normalize(transcript: str, lang: str) -> str:
    """
    Normalize a non-English transcript to an English command string.

    Args:
        transcript: raw transcript (may be Roman Urdu, Urdu script, mixed, Hindi)
        lang:       detected language code from language_detector

    Returns:
        English command string, or original transcript if no rule matched.
    """
    logger.info("[ML_NORMALIZE_INPUT] lang=%s transcript=%r", lang, transcript[:80])

    if lang == "en":
        return transcript

    text = transcript.strip()

    # ── Urdu / Arabic script — exact map first ────────────────────────────────
    if lang in ("ur", "ar"):
        text_key = text.rstrip("۔.!?").strip()
        for urdu_phrase, english_cmd in _URDU_EXACT:
            if urdu_phrase in text_key:
                logger.info("[ML_NORMALIZE_OUTPUT] %r → %r", transcript[:60], english_cmd)
                logger.info("[ML_NORMALIZE_RULE] urdu_exact_map")
                return english_cmd
        for ar_phrase, english_cmd in _ARABIC_EXACT:
            if ar_phrase in text_key:
                logger.info("[ML_NORMALIZE_OUTPUT] %r → %r", transcript[:60], english_cmd)
                logger.info("[ML_NORMALIZE_RULE] arabic_exact_map")
                return english_cmd
        # No map match — return as-is and let the brain handle it
        logger.info("[ML_NORMALIZE_OUTPUT] script_no_map lang=%s — returning original", lang)
        logger.info("[ML_NORMALIZE_RULE] none (%s script, no map entry)", lang)
        return transcript

    # ── Roman Urdu / Mixed / Hindi — regex rules ──────────────────────────────
    # Normalize "download, karo" → "download karo" so patterns match cleanly
    text = re.sub(r'\s*[,;]\s+kar', ' kar', text)
    result = text
    for pattern, replacement, rule_name in _RULES:
        new_result = pattern.sub(replacement, result, count=1)
        if new_result != result:
            result = new_result.strip()
            logger.info("[ML_NORMALIZE_OUTPUT] %r → %r", transcript[:60], result[:60])
            logger.info("[ML_NORMALIZE_RULE] rule=%s", rule_name)
            return result

    logger.info("[ML_NORMALIZE_OUTPUT] no_rule_matched — returning original")
    logger.info("[ML_NORMALIZE_RULE] none")
    return transcript
