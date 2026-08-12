"""
Response Localizer — converts English tool acknowledgement text into
natural-sounding responses in the user's language.

This is the KEY component for ChatGPT/Gemini-level multilingual feel.
Instead of speaking "Opening Chrome." via XTTS in Urdu mode (which sounds odd),
Xyron now says "Chrome khol raha hoon." — a real Urdu/Roman Urdu sentence.

Logs:
  [RESPONSE_LOCALIZED] lang=... template=... result=...
  [RESPONSE_LOCALIZE_MISS] no template matched — caller should use original
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# ── Template registry ─────────────────────────────────────────────────────────
# Key: action_id, Value: dict[lang_code → template string]
# {app}, {folder}, {query}, {media}, {page} are format placeholders.
TEMPLATES: dict[str, dict[str, str]] = {
    "opening_app": {
        "en":       "Opening {app}.",
        "ur_roman": "{app} khol raha hoon.",
        "ur":       "{app} کھول رہا ہوں.",
        "hi":       "{app} खोल रहा हूँ.",
        "hi_roman": "{app} khol raha hoon.",
        "ar":       "أفتح {app} الآن.",
        "mixed":    "{app} khol raha hoon.",
    },
    "downloading_app": {
        "en":       "Downloading {app} from Microsoft Store.",
        "ur_roman": "{app} Microsoft Store se download ho raha hai.",
        "ur":       "{app} مائیکروسافٹ اسٹور سے ڈاؤن لوڈ ہو رہا ہے.",
        "hi":       "{app} Microsoft Store से डाउनलोड हो रहा है.",
        "hi_roman": "{app} Microsoft Store se download ho raha hai.",
        "ar":       "جارٍ تنزيل {app} من متجر Microsoft.",
        "mixed":    "{app} Microsoft Store se download ho raha hai.",
    },
    "installing_app": {
        "en":       "Installing {app}.",
        "ur_roman": "{app} install ho raha hai.",
        "ur":       "{app} انسٹال ہو رہا ہے.",
        "hi":       "{app} इंस्टॉल हो रहा है.",
        "hi_roman": "{app} install ho raha hai.",
        "ar":       "جارٍ تثبيت {app}.",
        "mixed":    "{app} install ho raha hai.",
    },
    "install_it": {
        "en":       "Installing it.",
        "ur_roman": "Install ho raha hai.",
        "ur":       "انسٹال ہو رہا ہے.",
        "hi":       "इंस्टॉल हो रहा है.",
        "hi_roman": "Install ho raha hai.",
        "ar":       "جارٍ التثبيت.",
        "mixed":    "Install ho raha hai.",
    },
    "closing_app": {
        "en":       "Closing it.",
        "ur_roman": "Band kar raha hoon.",
        "ur":       "بند کر رہا ہوں.",
        "hi":       "बंद कर रहा हूँ.",
        "hi_roman": "Band kar raha hoon.",
        "ar":       "جارٍ الإغلاق.",
        "mixed":    "Band kar raha hoon.",
    },
    "volume_up": {
        "en":       "Volume increased.",
        "ur_roman": "Awaaz barha di.",
        "ur":       "آواز بڑھا دی.",
        "hi":       "आवाज़ बढ़ा दी.",
        "hi_roman": "Awaaz barha di.",
        "ar":       "تم رفع الصوت.",
        "mixed":    "Awaaz barha di.",
    },
    "volume_down": {
        "en":       "Volume decreased.",
        "ur_roman": "Awaaz kam kar di.",
        "ur":       "آواز کم کر دی.",
        "hi":       "आवाज़ कम कर दी.",
        "hi_roman": "Awaaz kam kar di.",
        "ar":       "تم خفض الصوت.",
        "mixed":    "Awaaz kam kar di.",
    },
    "muted": {
        "en":       "Muted.",
        "ur_roman": "Mute kar diya.",
        "ur":       "میوٹ کر دیا.",
        "hi":       "म्यूट कर दिया.",
        "hi_roman": "Mute kar diya.",
        "ar":       "تم كتم الصوت.",
        "mixed":    "Mute kar diya.",
    },
    "opening_settings": {
        "en":       "Opening {page} settings.",
        "ur_roman": "{page} settings khol raha hoon.",
        "ur":       "{page} سیٹنگز کھول رہا ہوں.",
        "hi":       "{page} सेटिंग्स खोल रहा हूँ.",
        "hi_roman": "{page} settings khol raha hoon.",
        "ar":       "أفتح إعدادات {page}.",
        "mixed":    "{page} settings khol raha hoon.",
    },
    "opening_folder": {
        "en":       "Opening {folder}.",
        "ur_roman": "{folder} khol raha hoon.",
        "ur":       "{folder} کھول رہا ہوں.",
        "hi":       "{folder} खोल रहा हूँ.",
        "hi_roman": "{folder} khol raha hoon.",
        "ar":       "أفتح {folder}.",
        "mixed":    "{folder} khol raha hoon.",
    },
    "searching": {
        "en":       "Searching for {query}.",
        "ur_roman": "{query} search kar raha hoon.",
        "ur":       "{query} تلاش کر رہا ہوں.",
        "hi":       "{query} खोज रहा हूँ.",
        "hi_roman": "{query} search kar raha hoon.",
        "ar":       "أبحث عن {query}.",
        "mixed":    "{query} search kar raha hoon.",
    },
    "playing_media": {
        "en":       "Playing {media}.",
        "ur_roman": "{media} chala raha hoon.",
        "ur":       "{media} چلا رہا ہوں.",
        "hi":       "{media} चला रहा हूँ.",
        "hi_roman": "{media} chala raha hoon.",
        "ar":       "جارٍ تشغيل {media}.",
        "mixed":    "{media} chala raha hoon.",
    },
    "done": {
        "en":       "Done.",
        "ur_roman": "Ho gaya.",
        "ur":       "ہو گیا.",
        "hi":       "हो गया.",
        "hi_roman": "Ho gaya.",
        "ar":       "تم.",
        "mixed":    "Ho gaya.",
    },
    "not_understood": {
        "en":       "Sorry, I didn't understand that.",
        "ur_roman": "Maafi chahta hoon, mujhe samajh nahi aaya.",
        "ur":       "معذرت، مجھے سمجھ نہیں آیا۔",
        "hi":       "क्षमा करें, मैं समझ नहीं पाया।",
        "hi_roman": "Maafi chahta hoon, mujhe samajh nahi aaya.",
        "ar":       "آسف، لم أفهم ذلك.",
        "mixed":    "Maafi chahta hoon, mujhe samajh nahi aaya.",
    },
    "taking_screenshot": {
        "en":       "Taking a screenshot.",
        "ur_roman": "Screenshot le raha hoon.",
        "ur":       "اسکرین شاٹ لے رہا ہوں.",
        "hi":       "स्क्रीनशॉट ले रहा हूँ.",
        "hi_roman": "Screenshot le raha hoon.",
        "ar":       "جارٍ التقاط لقطة شاشة.",
        "mixed":    "Screenshot le raha hoon.",
    },
}

# ── English response → action_id matching ────────────────────────────────────
# Maps patterns in the English response text to (action_id, extracted_context)
_RESPONSE_PATTERNS: list[tuple[re.Pattern, str, list[str]]] = [
    # Opening app
    (re.compile(r"[Oo]pening\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s*(?:app|application)?\s*\.", re.I),
     "opening_app", ["app"]),
    # Downloading
    (re.compile(r"[Dd]ownloading\s+([A-Za-z0-9 .\-]+?)\s+(?:from\s+Microsoft\s+Store)?\s*\.", re.I),
     "downloading_app", ["app"]),
    # Installing (generic "installing it" vs "installing X")
    (re.compile(r"[Ii]nstalling\s+it\s*\.", re.I),
     "install_it", []),
    (re.compile(r"[Ii]nstalling\s+([A-Za-z0-9 .\-]+?)\s*\.", re.I),
     "installing_app", ["app"]),
    # Closing
    (re.compile(r"[Cc]los(?:ing|ed)\s+(?:it|the\s+app|the\s+window)?\s*\.", re.I),
     "closing_app", []),
    # Volume
    (re.compile(r"[Vv]olume\s+(?:increased|raised|turned\s+up)\s*\.", re.I),
     "volume_up", []),
    (re.compile(r"[Vv]olume\s+(?:decreased|lowered|turned\s+down)\s*\.", re.I),
     "volume_down", []),
    (re.compile(r"[Vv]olume\s+(?:up|higher)\s*\.", re.I),
     "volume_up", []),
    (re.compile(r"[Vv]olume\s+(?:down|lower)\s*\.", re.I),
     "volume_down", []),
    (re.compile(r"[Mm]ut(?:ed?|ing)\s*\.", re.I),
     "muted", []),
    # Settings
    (re.compile(r"[Oo]pening\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s+[Ss]ettings\s*\.", re.I),
     "opening_settings", ["page"]),
    # Folder
    (re.compile(r"[Oo]pening\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s+[Ff]older\s*\.", re.I),
     "opening_folder", ["folder"]),
    # Searching
    (re.compile(r"[Ss]earching\s+(?:for\s+)?(.+?)\s*\.", re.I),
     "searching", ["query"]),
    # Playing
    (re.compile(r"[Pp]laying\s+(.+?)\s*\.", re.I),
     "playing_media", ["media"]),
    # Screenshot
    (re.compile(r"[Tt]aking\s+a?\s*[Ss]creenshot\s*\.", re.I),
     "taking_screenshot", []),
    # Done
    (re.compile(r"^[Dd]one\.?\s*$", re.I),
     "done", []),
]

# Simple "Opening X." shortcut — covers the most common Xyron ACK format
_OPEN_RE = re.compile(r"^[Oo]pening\s+(.+?)\s*\.\s*$")


def localize_response(action_or_english: str, lang: str, **context) -> str | None:
    """
    Localize a response into the target language.

    Can be called two ways:
      1. With a known action_id:    localize_response("opening_app", "ur_roman", app="Chrome")
      2. With an English response:  localize_response("Opening Chrome.", "ur_roman")

    Returns the localized string, or None if no template matched.
    Falls back to "en" template if the specific language has no template.
    """
    if lang == "en":
        return None  # English responses are already correct — no localization needed

    # ── If action_or_english is a known action_id, use directly ──────────────
    if action_or_english in TEMPLATES:
        return _fill_template(action_or_english, lang, context)

    # ── Otherwise, try to parse the English response text ────────────────────
    text = action_or_english.strip()

    # Fast path: "Opening X." is by far the most common Xyron ACK
    m = _OPEN_RE.match(text)
    if m:
        entity = m.group(1).strip()
        # Is it settings?
        if "settings" in entity.lower():
            page = entity.lower().replace("settings", "").strip().title() or ""
            result = _fill_template("opening_settings", lang, {"page": page})
        elif "folder" in entity.lower():
            folder = entity.lower().replace("folder", "").strip().title()
            result = _fill_template("opening_folder", lang, {"folder": folder})
        else:
            result = _fill_template("opening_app", lang, {"app": entity})

        if result:
            logger.info("[RESPONSE_LOCALIZED] lang=%s template=opening_app result=%r", lang, result)
            return result

    # Scan full pattern list
    for pattern, action_id, capture_groups in _RESPONSE_PATTERNS:
        m = pattern.match(text)
        if m:
            ctx = {}
            for i, key in enumerate(capture_groups):
                ctx[key] = m.group(i + 1).strip()
            result = _fill_template(action_id, lang, ctx)
            if result:
                logger.info("[RESPONSE_LOCALIZED] lang=%s template=%s result=%r",
                            lang, action_id, result)
                return result

    logger.debug("[RESPONSE_LOCALIZE_MISS] lang=%s text=%r", lang, text[:60])
    return None


def _fill_template(action_id: str, lang: str, context: dict) -> str | None:
    """Look up template and fill placeholders."""
    tmap = TEMPLATES.get(action_id)
    if not tmap:
        return None
    # Try exact lang, then "mixed" fallback for "ur_roman", then "en"
    template = tmap.get(lang) or tmap.get("en")
    if not template:
        return None
    try:
        return template.format(**{k: v for k, v in context.items() if v})
    except KeyError:
        return template  # Return with unfilled placeholders if context incomplete
