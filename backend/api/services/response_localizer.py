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
        "ur":       "اسکرین شاٹ لے رہا ہوں۔",
        "hi":       "स्क्रीनशॉट ले रहा हूँ।",
        "hi_roman": "Screenshot le raha hoon.",
        "ar":       "جارٍ التقاط لقطة شاشة.",
        "mixed":    "Screenshot le raha hoon.",
    },
    "locking": {
        "en":       "Locking screen.",
        "ur_roman": "Screen lock kar raha hoon.",
        "ur":       "اسکرین لاک کر رہا ہوں۔",
        "hi":       "स्क्रीन लॉक कर रहा हूँ।",
        "hi_roman": "Screen lock kar raha hoon.",
        "ar":       "جارٍ قفل الشاشة.",
        "mixed":    "Screen lock kar raha hoon.",
    },
    "sleeping": {
        "en":       "Going to sleep.",
        "ur_roman": "Sleep mode mein ja raha hoon.",
        "ur":       "اسلیپ موڈ میں جا رہا ہوں۔",
        "hi":       "स्लीप मोड में जा रहा हूँ।",
        "hi_roman": "Sleep mode mein ja raha hoon.",
        "ar":       "جارٍ الدخول في وضع السكون.",
        "mixed":    "Sleep mode mein ja raha hoon.",
    },
    "shutting_down": {
        "en":       "Shutting down.",
        "ur_roman": "Band kar raha hoon.",
        "ur":       "بند کر رہا ہوں۔",
        "hi":       "बंद कर रहा हूँ।",
        "hi_roman": "Band kar raha hoon.",
        "ar":       "جارٍ الإيقاف.",
        "mixed":    "Band kar raha hoon.",
    },
    "restarting": {
        "en":       "Restarting.",
        "ur_roman": "Restart kar raha hoon.",
        "ur":       "ریسٹارٹ کر رہا ہوں۔",
        "hi":       "रीस्टार्ट कर रहा हूँ।",
        "hi_roman": "Restart kar raha hoon.",
        "ar":       "جارٍ إعادة التشغيل.",
        "mixed":    "Restart kar raha hoon.",
    },
    "unmuted": {
        "en":       "Unmuted.",
        "ur_roman": "Unmute kar diya.",
        "ur":       "انمیوٹ کر دیا۔",
        "hi":       "अनम्यूट कर दिया।",
        "hi_roman": "Unmute kar diya.",
        "ar":       "تم إلغاء كتم الصوت.",
        "mixed":    "Unmute kar diya.",
    },
    "deleting": {
        "en":       "Deleting {item}.",
        "ur_roman": "{item} delete kar raha hoon.",
        "ur":       "{item} ڈیلیٹ کر رہا ہوں۔",
        "hi":       "{item} डिलीट कर रहा हूँ।",
        "hi_roman": "{item} delete kar raha hoon.",
        "ar":       "جارٍ حذف {item}.",
        "mixed":    "{item} delete kar raha hoon.",
    },
    "creating": {
        "en":       "Creating {item}.",
        "ur_roman": "{item} bana raha hoon.",
        "ur":       "{item} بنا رہا ہوں۔",
        "hi":       "{item} बना रहा हूँ।",
        "hi_roman": "{item} bana raha hoon.",
        "ar":       "جارٍ إنشاء {item}.",
        "mixed":    "{item} bana raha hoon.",
    },
    "showing": {
        "en":       "Showing {item}.",
        "ur_roman": "{item} dikha raha hoon.",
        "ur":       "{item} دکھا رہا ہوں۔",
        "hi":       "{item} दिखा रहा हूँ।",
        "hi_roman": "{item} dikha raha hoon.",
        "ar":       "جارٍ عرض {item}.",
        "mixed":    "{item} dikha raha hoon.",
    },
    "searching_web": {
        "en":       "Searching the web for {query}.",
        "ur_roman": "Internet pe {query} search kar raha hoon.",
        "ur":       "انٹرنیٹ پر {query} تلاش کر رہا ہوں۔",
        "hi":       "इंटरनेट पर {query} खोज रहा हूँ।",
        "hi_roman": "Internet pe {query} search kar raha hoon.",
        "ar":       "أبحث في الإنترنت عن {query}.",
        "mixed":    "Internet pe {query} search kar raha hoon.",
    },
    "locked": {
        "en":       "Locked.",
        "ur_roman": "Lock kar diya.",
        "ur":       "لاک کر دیا۔",
        "hi":       "लॉक कर दिया।",
        "hi_roman": "Lock kar diya.",
        "ar":       "تم القفل.",
        "mixed":    "Lock kar diya.",
    },
    "media_pause": {
        "en":       "Paused.",
        "ur_roman": "Pause kar diya.",
        "ur":       "پاز کر دیا۔",
        "hi":       "पॉज़ कर दिया।",
        "hi_roman": "Pause kar diya.",
        "ar":       "تم الإيقاف المؤقت.",
        "mixed":    "Pause kar diya.",
    },
    "media_next": {
        "en":       "Next track.",
        "ur_roman": "Agla gana laga diya.",
        "ur":       "اگلا گانا لگا دیا۔",
        "hi":       "अगला गाना लगा दिया।",
        "hi_roman": "Agla gana laga diya.",
        "ar":       "تم تشغيل المقطع التالي.",
        "mixed":    "Agla gana laga diya.",
    },
    "media_prev": {
        "en":       "Previous track.",
        "ur_roman": "Pichla gana laga diya.",
        "ur":       "پچھلا گانا لگا دیا۔",
        "hi":       "पिछला गाना लगा दिया।",
        "hi_roman": "Pichla gana laga diya.",
        "ar":       "تم تشغيل المقطع السابق.",
        "mixed":    "Pichla gana laga diya.",
    },
    "media_stop": {
        "en":       "Stopped.",
        "ur_roman": "Band kar diya.",
        "ur":       "بند کر دیا۔",
        "hi":       "बंद कर दिया।",
        "hi_roman": "Band kar diya.",
        "ar":       "تم الإيقاف.",
        "mixed":    "Band kar diya.",
    },
}

# ── English response → action_id matching ────────────────────────────────────
# Maps patterns in the English response text to (action_id, extracted_context)
_RESPONSE_PATTERNS: list[tuple[re.Pattern, str, list[str]]] = [
    # Opening app (covers both "Opening X." and "Launching X.")
    (re.compile(r"(?:[Oo]pening|[Ll]aunching)\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s*(?:app|application)?\s*\.", re.I),
     "opening_app", ["app"]),
    # Opening drive ("Opening your C drive." → opening_folder with folder="C drive")
    (re.compile(r"(?:[Oo]pening|[Ll]aunching)\s+(?:your|the)\s+([A-Z])\s+drive\s*\.", re.I),
     "opening_folder", ["folder"]),
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
    # Settings ("Opening Settings.", "Launching Settings.", "Opening Display Settings.")
    (re.compile(r"(?:[Oo]pening|[Ll]aunching)\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s+[Ss]ettings\s*\.", re.I),
     "opening_settings", ["page"]),
    # Folder
    (re.compile(r"[Oo]pening\s+(?:the\s+)?([A-Za-z0-9 .\-]+?)\s+[Ff]older\s*\.", re.I),
     "opening_folder", ["folder"]),
    # Searching the web (must come BEFORE generic searching)
    (re.compile(r"[Ss]earching\s+(?:the\s+)?(?:web|internet)\s+(?:for\s+)?(.+?)\s*\.", re.I),
     "searching_web", ["query"]),
    # Searching (generic)
    (re.compile(r"[Ss]earching\s+(?:for\s+)?(.+?)\s*\.", re.I),
     "searching", ["query"]),
    # Playing
    (re.compile(r"[Pp]laying\s+(.+?)\s*\.", re.I),
     "playing_media", ["media"]),
    # Screenshot
    (re.compile(r"[Tt]aking\s+a?\s*[Ss]creenshot\s*\.", re.I),
     "taking_screenshot", []),
    # Locking
    (re.compile(r"[Ll]ocking\s+(?:screen|the\s+screen|computer|pc|laptop)?\s*\.", re.I),
     "locking", []),
    (re.compile(r"^[Ll]ocked\.?\s*$", re.I),
     "locked", []),
    # Sleeping
    (re.compile(r"(?:[Gg]oing\s+to\s+sleep|[Ss]leeping)\s*\.", re.I),
     "sleeping", []),
    # Shutting down
    (re.compile(r"[Ss]hutting\s+down\s*\.", re.I),
     "shutting_down", []),
    # Restarting
    (re.compile(r"[Rr]estarting\s*\.", re.I),
     "restarting", []),
    # Unmuted
    (re.compile(r"[Uu]nmut(?:ed?|ing)\s*\.", re.I),
     "unmuted", []),
    # Deleting
    (re.compile(r"[Dd]eleting\s+(.+?)\s*\.", re.I),
     "deleting", ["item"]),
    # Creating
    (re.compile(r"[Cc]reating\s+(.+?)\s*\.", re.I),
     "creating", ["item"]),
    # Showing
    (re.compile(r"[Ss]howing\s+(.+?)\s*\.", re.I),
     "showing", ["item"]),
    # Media controls (conversational_replies._MEDIA_REPLIES — fixed variant
    # pools, matched by their distinctive fragment since they don't follow
    # the "Verb + noun." shape every other pattern above relies on).
    (re.compile(r"^(?:There\s+you\s+go,\s+playback\s+toggled|Done,\s+switched\s+the\s+playback\s+over|Okay,\s+toggled\s+it\s+for\s+you)\.?\s*$", re.I),
     "media_pause", []),
    (re.compile(r"^(?:Skipped\s+ahead,\s+next\s+one'?s\s+up|Moving\s+on\s+to\s+the\s+next\s+track|There\s+you\s+go,\s+next\s+track)\.?\s*$", re.I),
     "media_next", []),
    (re.compile(r"^(?:Back\s+one\s+track,\s+there\s+you\s+go|Took\s+it\s+back\s+to\s+the\s+previous\s+one|Rewound\s+to\s+the\s+last\s+track\s+for\s+you)\.?\s*$", re.I),
     "media_prev", []),
    (re.compile(r"^(?:Stopped\s+the\s+playback|All\s+quiet,\s+playback\s+stopped|Okay,\s+stopped\s+it\s+for\s+you)\.?\s*$", re.I),
     "media_stop", []),
    # Done — covers every fixed variant in conversational_replies._COMPLETION_POOLS["_generic"]
    # across all four emotion tones, not just the plain "Done." neutral form. Without
    # this, an emotion-toned generic completion (e.g. "There we go, done!") matched
    # NOTHING here, localize_response returned None, and urdu_ack_generator's
    # template fallback returned the raw English text — i.e. if qwen/Ollama was
    # also unavailable that turn, an Urdu-mode session would hear/read plain
    # English for any tool using the generic completion pool (brightness, mute,
    # recycle bin, wifi, and any future tool without a dedicated pool).
    (re.compile(r"^(?:[Dd]one|All\s+set|All\s+done\s+for\s+you|There\s+you\s+go,\s+all\s+set|"
                r"There\s+we\s+go,\s+done|And\s+that'?s\s+a\s+wrap,\s+done|"
                r"Done\.\s+We'?re\s+good|All\s+handled,\s+don'?t\s+worry\s+about\s+it)\.?!?\s*$", re.I),
     "done", []),
    # Generic acks — same reasoning as above, but for conversational_replies.
    # _ACK_POOLS["_generic"] (spoken before the tool runs).
    (re.compile(r"^(?:[Oo]n\s+it|[Rr]ight\s+away|[Ww]orking\s+on\s+it(?:\s+now)?|"
                r"Sure,\s+on\s+it|Happy\s+to,\s+one\s+moment|On\s+it,\s+let'?s\s+go|"
                r"Easy,\s+right\s+on\s+it|Okay,\s+I'?m\s+on\s+it|"
                r"Don'?t\s+worry,\s+handling\s+it\s+now)\.?!?\s*$", re.I),
     "done", []),
]

# Simple "Opening X." / "Launching X." shortcut — covers the most common
# Xyron ACK formats. Both verbs are used interchangeably by _build_ack_text.
_OPEN_RE = re.compile(r"^(?:[Oo]pening|[Ll]aunching|[Pp]ulling\s+up)\s+(.+?)\s*\.\s*$")

# Trailing filler adverbs that _build_ack_text's varied phrasing sometimes
# appends after the entity name — never part of the entity itself.
_TRAILING_FILLER_RE = re.compile(
    r"\s+(?:now|right away|immediately|quickly|for you|please|real quick)$", re.I
)

# Generic fallback acknowledgments when no template matches or the entity
# name is garbled (too long / clearly a full transcript instead of an app name).
_GENERIC_ACK: dict[str, str] = {
    "ur":       "ہو گیا۔",
    "ur_roman": "Ho gaya.",
    "hi":       "हो गया।",
    "ar":       "تم.",
    "mixed":    "Ho gaya.",
    "en":       "Done.",
}

# ── Entity name sanity check ─────────────────────────────────────────────────
# Live-caught bug: the object resolver sometimes passes the FULL transcript
# as the entity name (e.g. "Open, Close, Do Some Work, Settings Open")
# instead of a clean entity like "System". This produces garbled localized
# output like "Open, Close, Do Some Work, Open سیٹنگز کھول رہا ہوں."
# If the entity is > 4 words or > 30 chars, treat it as garbled.
_MAX_ENTITY_WORDS = 4
_MAX_ENTITY_CHARS = 30


def _is_entity_clean(entity: str) -> bool:
    """Return True if entity looks like a real app/folder name, not a transcript."""
    if not entity:
        return True
    words = entity.split()
    return len(words) <= _MAX_ENTITY_WORDS and len(entity) <= _MAX_ENTITY_CHARS


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
        # Safety: sanitize context values — garbled entity names produce
        # garbled localized output (e.g. full transcript as {app}).
        _clean_ctx = {}
        for k, v in context.items():
            if isinstance(v, str) and not _is_entity_clean(v):
                # Replace garbled entity with empty → template will use
                # generic form (e.g. "سیٹنگز کھول رہا ہوں۔" without page prefix)
                _clean_ctx[k] = ""
            else:
                _clean_ctx[k] = v
        return _fill_template(action_or_english, lang, _clean_ctx)

    # ── Otherwise, try to parse the English response text ────────────────────
    text = action_or_english.strip()

    # Fast path: "Opening X." / "Launching X." / "Pulling up X." — by far
    # the most common Xyron ACK format. _build_ack_text picks randomly from
    # these three verbs so the pattern must accept all of them.
    m = _OPEN_RE.match(text)
    if m:
        entity = m.group(1).strip()
        # Strip trailing filler adverbs _build_ack_text sometimes appends
        # ("Opening Settings now.", "...right away.") — left in, these get
        # mistaken for part of the entity name (e.g. "settings now" → page
        # name "now") and echoed literally into the localized sentence
        # ("Now settings khol raha hoon."). Live-caught 2026-08-24.
        entity = _TRAILING_FILLER_RE.sub("", entity).strip()
        # Strip leading articles for cleaner entity names
        _entity_clean = entity
        for _art in ("your ", "the ", "a ", "an "):
            if _entity_clean.lower().startswith(_art):
                _entity_clean = _entity_clean[len(_art):]
                break
        # Is it settings?
        if "settings" in entity.lower():
            page = entity.lower().replace("settings", "").strip().title() or ""
            # Guard: garbled transcript passed as page name?
            if not _is_entity_clean(page):
                page = ""  # use generic "Opening settings" without page prefix
            result = _fill_template("opening_settings", lang, {"page": page})
        elif "folder" in entity.lower():
            folder = entity.lower().replace("folder", "").strip().title()
            if not _is_entity_clean(folder):
                folder = ""
            result = _fill_template("opening_folder", lang, {"folder": folder})
        elif "drive" in entity.lower():
            # "your A drive" → folder="A drive" for the opening_folder template
            folder = _entity_clean.strip().title()
            result = _fill_template("opening_folder", lang, {"folder": folder})
        else:
            # Guard: garbled entity → use generic acknowledgment
            if not _is_entity_clean(entity):
                return _GENERIC_ACK.get(lang, _GENERIC_ACK["en"])
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
                val = m.group(i + 1).strip()
                # Garbled entity guard
                if not _is_entity_clean(val):
                    ctx[key] = ""
                else:
                    ctx[key] = val
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
        # Pass ALL context values (including empty strings) so placeholders
        # are always resolved. Then clean up double spaces from empty fills
        # (e.g. "{page} settings" with page="" → " settings" → "Settings").
        result = template.format(**context)
        # Collapse double spaces and strip leading/trailing whitespace
        import re as _re
        result = _re.sub(r'\s{2,}', ' ', result).strip()
        # Capitalize first letter if result starts with lowercase after empty fill
        if result and result[0].islower():
            result = result[0].upper() + result[1:]
        return result
    except KeyError:
        return template  # Return with unfilled placeholders if context incomplete
