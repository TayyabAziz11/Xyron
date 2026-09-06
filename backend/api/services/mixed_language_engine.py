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
    # Media pause/next/previous/empty-trash — MUST precede the generic
    # "open"/"install" verb entries below: "open" matches bare "chala[oa]?"
    # (a Roman Urdu synonym for "play") and "install" matches bare "lagao",
    # both of which also appear inside "dobara chalao" / "agla gana lagao"
    # style phrases. Since analyze() takes the FIRST pattern that matches
    # anywhere in the text (not the most specific one), leaving these after
    # "open"/"install" let bare "chalao"/"lagao" win first, mis-canonicalizing
    # e.g. "dobara chalao" (resume) as "open dobara" — live-caught while
    # adding these patterns, not a pre-existing report.
    (re.compile(r'\b(?:gana|song|music|playback|video)\s+(?:rok\s*do|roko|band\s+kar[oa]?|pause\s+kar[oa]?)\b', re.I), "media_pause"),
    (re.compile(r'\b(?:dobara|wapis|vapis)\s+chala[oa]?\b', re.I), "media_pause"),
    (re.compile(r'\b(?:agla|agli)\s+(?:gana|song|track|video)\b', re.I), "media_next"),
    (re.compile(r'\b(?:pichla|pichli)\s+(?:gana|song|track|video)\b', re.I), "media_prev"),
    (re.compile(r'\b(?:recycle\s*bin|kachra|kachray|trash)\s+(?:khali\s+kar[oa]?|saaf\s+kar[oa]?|clear\s+kar[oa]?)\b', re.I), "empty_trash"),
    # Ambiguous "chalao"/"chala"/"chalu karo" — checked AFTER the specific
    # media-noun-gated patterns above (so "dobara chalao"/"agla gana ...
    # chalao" are already claimed by those), but BEFORE the plain "open"
    # verb group so this ambiguous form doesn't silently default to "open"
    # by list order. Resolved by _disambiguate_chalao() using real signals
    # (known app names, media-ish nouns, current-app context) instead of a
    # blind regex guess — see that function for the actual decision logic.
    (re.compile(r'\b(?:chala[oa]?|chla[oa]?|chalu\s+karo|chala\s+dena)\b', re.I), "chalao_ambiguous"),
    # Urdu-script "چلاؤ" (chalao) — same ambiguity as the Roman form above
    # (launch-app vs play-media), resolved by the same _disambiguate_chalao.
    # Live bug this fixes (2026-08-24): language_detector.py's own
    # _URDU_SCRIPT_KWS list already treats "چلاؤ" as a recognized Urdu
    # command word (it's used to DETECT the utterance as Urdu), but no
    # verb-map entry ever existed to canonicalize it once detected — every
    # Urdu-script "چلاؤ" command silently skipped this entire deterministic
    # engine and fell through to the slow, unreliable Qwen fallback
    # (confirmed live: "کوئی گانا چلاؤ" → Qwen hallucinated
    # "gaana in spotify" as a file search instead of playing anything).
    # "چلا دو" (chala do — "play/do it", colloquial imperative with a
    # separate helper verb دو) was not covered by the چلاؤ/چلاو forms
    # above (those require the alef/hamza-waw ending, "چلا دو" ends the
    # verb stem at چلا and dou is d/separate word). Live-caught bug
    # (2026-09-04): "کوئی گانا چلا دو" ("play some song") never matched
    # this ambiguous-chalao tier at all and fell through unclassified.
    (re.compile(r'چلاؤ|چلاو|چلا\s*دو|چلا\s*دے'),                                            "chalao_ambiguous"),
    # Open / launch verbs (includes common Whisper mispronunciations like
    # "kolo" for "kholo", "khulo" for "kholo"). NOTE: "chala[oa]?" is
    # deliberately NOT here — see the ambiguous "chalao" entry below.
    (re.compile(r'\b(?:kholo|khol|kholein|kolo|khulo|khul|open\s+karo|launch\s+karo)\b', re.I), "open"),
    (re.compile(r'\b(?:افتح|کھولو|کھول)\b'),                                                 "open"),
    # Close / quit verbs
    (re.compile(r'\b(?:band\s+karo|band|bandh\s+karo|bandh|bund\s+karo|quit\s+karo|close\s+karo|close\s+kardo)\b', re.I), "close"),
    (re.compile(r'\b(?:اغلق|بند\s+کرو|بند)\b'),                                              "close"),
    # Install verbs
    (re.compile(r'\b(?:install\s+karo|install\s+kar[oa]?|lagao|lagana|install\s+kardo)\b', re.I), "install"),
    (re.compile(r'انسٹال(?:\s+کرو)?'),                                                        "install"),
    # Download verbs
    (re.compile(r'\b(?:download\s+karo|download\s+kar[oa]?|اتارو|ڈاؤن\s*لوڈ\s+کرو)\b', re.I), "download"),
    # Play/search YouTube — unambiguous play-only verbs. "chalao"/"chalu karo"
    # are deliberately NOT here (see the dedicated ambiguous "chalao" entry
    # below, checked earlier in this list) since "chalao" alone is genuinely
    # ambiguous between "launch an app" and "play media" in Urdu.
    (re.compile(r'\b(?:play\s+karo|bajao)\b', re.I),              "play"),
    (re.compile(r'بجاؤ'),                                          "play"),
    # Volume
    (re.compile(r'\b(?:barhao|barha[oa]?|tez\s+karo|increase\s+karo|barha\s+do)\b', re.I),             "increase"),
    (re.compile(r'بڑھاؤ|بڑھا\s*دو'),                                                                    "increase"),
    (re.compile(r'\b(?:kam\s+karo|ghata[oa]?|decrease\s+karo|slow\s+karo|kam\s+kar\s+do)\b', re.I),        "decrease"),
    (re.compile(r'کم\s*کرو|گھٹاؤ'),                                                                       "decrease"),
    # Screenshot
    (re.compile(r'\b(?:screenshot\s+lo|screenshot\s+lao|screenshot\s+le\s+lo|pakro|capture\s+karo)\b', re.I),    "take screenshot"),
    # Search
    (re.compile(r'\b(?:search\s+karo|dhundho|talash\s+karo|talash\s+ka[oa]?|search\s+kardo)\b', re.I),                       "search"),
    # Mixed English-verb + Urdu-script helper — common Pakistani
    # code-switching ("Google پر X search کرو" / "X سرچ کرو"). Neither the
    # Roman pattern above (requires ASCII "karo") nor the pure Urdu-script
    # pattern below (requires تلاش/ڈھونڈو) matched literal English "search"
    # immediately followed by Urdu-script "کرو".
    (re.compile(r'search\s*کرو|سرچ\s*کرو'),                                                                                    "search"),
    (re.compile(r'تلاش(?:\s+کرو)?|ڈھونڈو'),                                                                                    "search"),
    # Show/display
    (re.compile(r'\b(?:dikha[oa]?|dikhao|show\s+karo|batao|dikha\s+do)\b', re.I),                       "show"),
    (re.compile(r'دکھاؤ|بتاؤ'),                                                                          "show"),
    # Lock/sleep/shutdown
    (re.compile(r'\b(?:lock\s+karo|lock\s+kardo|qfl|تالا\s+لگاؤ)\b', re.I),             "lock"),
    (re.compile(r'\b(?:sleep\s+karo|so\s+jao|neaend\s+karo)\b', re.I),                "sleep"),
    (re.compile(r'\b(?:shutdown\s+karo|band\s+kar\s+do|restart\s+karo)\b', re.I),   "shutdown"),
    # Mute/unmute
    (re.compile(r'\b(?:mute\s+karo|mute\s+kardo|khamosh\s+karo|khamosh)\b', re.I),    "mute"),
    # Delete
    (re.compile(r'\b(?:delete\s+karo|mita[oa]?|mitao|hata[oa]?|hatao|remove\s+karo)\b', re.I), "delete"),
    # Create/make (folder, file). "banau" added alongside "banao" — both
    # are real Whisper STT outputs for the same spoken word بناؤ (live-
    # caught 2026-08-24: "C drive mein naya folder banau." transcribed
    # correctly by STT but "banau" wasn't recognized by this pattern, so
    # the whole utterance skipped canonicalization and fell through to
    # intent_router's raw-text catch-all, which misparsed "mein naya" as
    # a filename to search for instead of running create_folder at all).
    (re.compile(r'\b(?:banao|banau|bana|create\s+karo|make\s+karo|naya\s+banao)\b', re.I), "create"),
    (re.compile(r'بناؤ|بناو'),                                                              "create"),
]

# ── Connector words to strip ─────────────────────────────────────────────────
_CONNECTORS = re.compile(
    r'\b(?:se|mein|ko|ka|ki|ke|par|pe|bhi|aur|ya|ne|hi|'
    r'the|a|an|it|me|my|please|just|now|quickly|'
    # Politeness/softener + possessive/helper-verb words found missing
    # during real-pipeline validation (2026-08-24): "Zara mera Downloads
    # folder khol dena." left "Zara mera ... dena" attached to the entity
    # span untouched (neither word was in this connector list nor
    # _FILLERS), so the whole phrase — including the polite softener and
    # possessive — was passed to intent_router as if it were literally
    # part of the object's name.
    r'zara|thora|thori|mera|meri|mere|dena|dene|do|den)\b',
    re.I,
)

# Urdu-SCRIPT connector/postposition words. _CONNECTORS above only covers
# the Roman-Urdu spellings ("ko", "aur", "mein", ...) — Urdu-script tokens
# had no equivalent entry at all, so a script transcript's postpositions/
# conjunctions were NEVER stripped before this fix. Live-caught bug
# (2026-09-04 real backend log): "YouTube کو کھولو اور کوئی گانا چلا دو"
# canonicalized to "open YouTube کو اور کوئی گانا چلا دو" — the "کو"
# (postposition) survived straight into the entity span and then into
# open_application's app_name.
#
# EDGE-ONLY, not a global substitution like _CONNECTORS above. A blind
# .sub() across the whole remaining span would also corrupt free-form
# PAYLOAD content that legitimately contains these exact words in its
# interior — e.g. a search query "دل کے ارماں" genuinely contains "کے" as
# part of the phrase, a folder literally named "علی کا کمرہ" genuinely
# contains "کا", a quoted message body may contain "میں" as "I" (the
# pronoun) rather than the postposition "in". Urdu/Roman-Urdu grammatical
# glue overwhelmingly sits at the boundary between the verb (already
# stripped by the caller) and the object/payload — never buried inside
# the payload — so trimming only the LEADING/TRAILING edges (repeated
# until stable, so "X کو اور" strips both stacked trailing connectors)
# closes the real leak without ever touching interior content. See
# _strip_urdu_connectors_at_edges() below and its regression tests in
# test_urdu_language_parity.py (TestUrduConnectorPayloadPreservation).
_URDU_CONNECTORS_EDGE_RE = re.compile(
    r'^(?:کو|کا|کی|کے|میں|پر|پہ|بھی|نے|سے|اور|یا)\s+'
    r'|\s+(?:کو|کا|کی|کے|میں|پر|پہ|بھی|نے|سے|اور|یا)$'
)


def _strip_urdu_connectors_at_edges(text: str) -> str:
    """Strip Urdu-script connector/postposition words only from the
    leading/trailing edges of `text`, never its interior — see
    _URDU_CONNECTORS_EDGE_RE's comment for why. Loops until stable so
    multiple stacked edge connectors (e.g. a trailing "کو اور" left after
    verb-stripping) are all removed, not just the outermost one."""
    prev = None
    while prev != text:
        prev = text
        text = _URDU_CONNECTORS_EDGE_RE.sub("", text).strip()
    return text

# ── Negation words ─────────────────────────────────────────────────────────────
# If ANY of these appear in the text, the command is a negation/cancel
# (e.g. "kholo nahi, Chrome ko nahi" = "don't open Chrome"). The mixed
# engine cannot produce a meaningful canonical form for negation —
# returning None lets the intent router handle it as-is, which is far
# better than producing garbled output like "open nahi, Chrome nahi".
_NEGATION_RE = re.compile(r'\b(?:nahi|nahin|naheen|nhi|mat|na)\b', re.I)

# ── Filler words to strip ─────────────────────────────────────────────────────
# Common Pakistani exclamation words that carry no command meaning
_FILLERS = re.compile(r'\b(?:aree|areee|arey|arre|oye|abey|sun|suno|dekho|suno)\b', re.I)

# ── Location/source markers ───────────────────────────────────────────────────
_FROM_STORE = re.compile(
    r'\b(?:microsoft\s+store|store|ms\s+store|app\s+store)\s+'
    r'(?:se|from|mein|ko|ka)\b',
    re.I,
)
_ON_YOUTUBE = re.compile(r'\b(?:youtube|yt)\s+(?:pe|par|pr|on)\b', re.I)
_ON_SPOTIFY = re.compile(r'\b(?:spotify)\s+(?:pe|par|on)\b', re.I)

# Search-engine/site designator at the START of a search entity span
# ("Google پر ... search کرو" / "Google par ... search karo") — the SAME
# "on <platform>" shape _ON_YOUTUBE/_ON_SPOTIFY already strip for play
# requests, generalized to any leading single word (not just youtube/
# spotify) since a search command can name Google, Bing, YouTube, an
# internal site, etc. as WHERE to search, never WHAT to search for.
# Anchored to the very START of the (already verb/connector-stripped)
# entity span, so it can only ever consume a genuine leading location
# designator — it cannot reach into interior/quoted payload content,
# which by this point is still an opaque placeholder token (quote
# protection runs before this in analyze()) even if it wasn't.
_LEADING_SEARCH_SITE_RE = re.compile(r'^([A-Za-z]+)\s+(?:پر|پہ|par|pe)\s+', re.I)

# ── Volume / brightness targets ───────────────────────────────────────────────
_VOLUME_TARGETS = re.compile(r'\b(?:awaz|volume|آواز|والیوم)\b', re.I)
_BRIGHT_TARGETS = re.compile(r'\b(?:brightness|روشنی)\b', re.I)

# ── Pronoun / ordinal-reference normalization ────────────────────────────────
# "isko band karo" (close it), "pehla wala kholo" (open the first one),
# "wo wala kholo" (open that one) — the OLD behavior left the Roman-Urdu
# reference word untouched as the entity name ("close isko", "open pehla
# wala"), which no downstream system (context_stack, follow_up_resolver_v2,
# object_resolver) recognizes as a pronoun/ordinal reference, since all of
# those are English-word-only.
#
# Rather than teach every one of those modules Roman Urdu (duplicating the
# same reference-resolution logic per module — the "endless regex" anti-
# pattern), this table normalizes the REFERENCE WORD ITSELF to its English
# equivalent ("it", "this one", "the first one", ...). The resulting
# canonical string ("close it", "open the first one") is then something
# context_stack/follow_up_resolver_v2/object_resolver/context_resolver
# ALREADY understand natively — one small lexical fix upstream instead of
# N context-resolution reimplementations downstream.
# Longest/most specific phrases first so "pehla wala" matches before a
# generic single-word fallback would.
_PRONOUN_PHRASE_MAP: list[tuple[str, str]] = [
    ("pehla wala", "the first one"), ("pehli wali", "the first one"),
    ("doosra wala", "the second one"), ("dusra wala", "the second one"),
    ("doosri wali", "the second one"), ("dusri wali", "the second one"),
    ("teesra wala", "the third one"), ("teesri wali", "the third one"),
    ("chautha wala", "the fourth one"), ("chauthi wali", "the fourth one"),
    ("agla wala", "the next one"), ("agli wali", "the next one"),
    ("pichla wala", "the previous one"), ("pichli wali", "the previous one"),
    ("yehi wala", "this one"), ("yehi wali", "this one"),
    ("yeh wala", "this one"), ("ye wala", "this one"), ("ye wali", "this one"),
    ("wohi wala", "that one"), ("wohi wali", "that one"),
    ("wahi wala", "the same one"), ("wahi wali", "the same one"),
    ("woh wala", "that one"), ("wo wala", "that one"), ("wo wali", "that one"),
    ("اسے", "it"), ("یہ والا", "this one"), ("یہ والی", "this one"),
    ("وہ والا", "that one"), ("وہ والی", "that one"),
    ("پہلا والا", "the first one"), ("دوسرا والا", "the second one"),
    ("isko", "it"), ("usko", "it"), ("isay", "it"), ("usay", "it"),
    ("issay", "it"), ("ussay", "it"), ("ise", "it"), ("use", "it"),
    ("yeh", "it"), ("ye", "it"), ("woh", "it"), ("wo", "it"),
]


def _normalize_entity_reference(span: str) -> str:
    """Map a Roman-Urdu/Urdu-script pronoun or ordinal reference to its
    English equivalent so it converges into the existing English
    context-resolution machinery. Full-string match only (the span at this
    point is already isolated from its verb) — never a substring
    replacement, so a real object name that happens to contain "ye" or
    similar is never corrupted."""
    key = span.strip().lower()
    for phrase, replacement in _PRONOUN_PHRASE_MAP:
        if key == phrase.lower():
            return replacement
    return span


# Found via real-pipeline validation (2026-08-24): "Is repo ka README kholo."
# canonicalized to "open Is repo README" — "Is" ("this", Urdu demonstrative)
# was left untranslated as if it were part of the object's NAME, and
# intent_router's generic open_application catch-all confidently (but
# wrongly) tried to launch an app literally named "Is repo README" instead
# of ever reaching Qwen's context_reference resolution ("is repo" ==
# current_repository — exactly what local_comprehension.py's prompt already
# knows how to resolve, one tier up). The fix is general, not per-phrase:
# a demonstrative word LEFT AS A MODIFIER in front of more text (as
# opposed to _normalize_entity_reference's exact-phrase case above, where
# the whole span IS the reference) means this tier didn't actually resolve
# the object — better to signal "not understood" (return None, fall to
# Qwen) than hand intent_router a half-Urdu string it will confidently
# misroute. Safe to key on bare "is"/"us"/"in"/"un" here specifically
# because this function only ever runs on already-detected non-English
# text (analyze() returns None outright for detected_lang == "en"), so
# there is no English "is"/"us" text this could misfire on.
_UNRESOLVED_REFERENCE_RE = re.compile(
    r'^(?:is|us|ye|yeh|wo|woh|in|un|اس|یہ|وہ|جو|ان)\s+\S', re.I,
)


def _has_unresolved_reference(span: str) -> bool:
    return bool(_UNRESOLVED_REFERENCE_RE.match(span.strip()))


# Found in the same pass: "E drive mein perfume wala folder kholo." (drive
# scope stated BEFORE the object, normal Urdu word order) canonicalized to
# "open E drive perfume wala folder" — intent_router's drive-open regex
# (`open ... <letter> drive`) matches that as a PREFIX and stops, silently
# dropping "perfume wala folder" entirely. English speakers don't trigger
# this because English puts the scope clause last ("open the perfume
# folder in E drive") — Urdu's default word order does the opposite. This
# reorders a LEADING drive-scope clause to a trailing one so the
# synthesized canonical matches the sentence shape intent_router (and
# object_resolver, one tier up) already expect from English callers —
# general to any leading-drive-scope phrase, not specific to "perfume".
_LEADING_DRIVE_RE = re.compile(r'^([a-zA-Z])\s+drive\s+(.+)$', re.I)


def _reorder_leading_drive_scope(span: str) -> str:
    m = _LEADING_DRIVE_RE.match(span.strip())
    if not m:
        return span
    letter, rest = m.group(1), m.group(2).strip()
    if not rest:
        return span
    return f"{rest} in {letter.upper()} drive"


# ── "chalao" disambiguation ───────────────────────────────────────────────────
# "chalao"/"chala"/"chalu karo" is genuinely ambiguous in Urdu — it can mean
# "launch this application" or "play this media" depending on what the
# object actually is. Resolved with real signals (reusing the SAME app-name
# list entity_corrector/object_resolver already use, plus a media-noun
# check and current-foreground-app context) instead of a coin-flip regex —
# see object_resolver.py's identical reasoning for why an explicit-type
# noun outranks a fuzzy guess.
_MEDIA_NOUNS_RE = re.compile(
    r'\b(?:gana|geet|song|music|video|movie|film|track|playlist)\b'
    # Urdu-script equivalents (گانا=gana/song, گیت=geet, موسیقی=music) —
    # added alongside the Urdu-script "چلاؤ" verb entry above; without
    # these, "کوئی گانا چلاؤ" reaches _disambiguate_chalao with a Roman-only
    # media-noun check that can't see its own Urdu-script object.
    r'|گانا|گیت|موسیقی',
    re.I,
)

# Filler words that carry no song/artist identity ("koi gana" = "some/any
# song", not a title). Stripped when building a search_youtube query so
# "koi gana chalao" doesn't literally search YouTube for "koi". Includes
# Urdu-script equivalents (کوئی=koi, کچھ=kuch) alongside the Roman forms.
_MEDIA_FILLER_RE = re.compile(
    r'\b(?:koi|kuch|ek|thora|thori|zara|acha|achi|please|plz|kindly)\b|کوئی|کچھ', re.I,
)


def _media_noun_query(name: str) -> str:
    """
    Build a search_youtube query from a "play X" object. Strips the generic
    media noun ("gana"/"song"/...) and filler words ("koi"/"kuch"/...),
    leaving only an actual song/artist title if one was said. Falls back to
    a generic query when nothing descriptive remains — "gana chalao" /
    "play a song" genuinely names no title, so there's nothing else to
    search for.

    Live bug this fixes (2026-08-24): "koi gana chalao" ("play a/some
    song") resolved to bare "play gana", which intent_router's generic
    play/pause pattern grabs as a media-key press — a no-op when nothing
    is already playing ("Media key failed."), never anything a user could
    call "playing a song". Routing through search_youtube instead actually
    plays something.
    """
    stripped = _MEDIA_NOUNS_RE.sub(' ', name)
    stripped = _MEDIA_FILLER_RE.sub(' ', stripped)
    stripped = re.sub(r'\s{2,}', ' ', stripped).strip()
    return stripped or "trending songs"


def _disambiguate_chalao(entity_span: str, full_text: str) -> Optional[str]:
    name = entity_span.strip()

    # Explicit media platform mention wins outright — "youtube pe X chalao"
    if _ON_YOUTUBE.search(full_text) or "youtube" in full_text.lower() or "yt" in full_text.lower().split():
        query = re.sub(r'\b(?:youtube|yt)\b', '', name, flags=re.I).strip()
        return f"play {query} on youtube" if query else "open youtube"
    if _ON_SPOTIFY.search(full_text) or "spotify" in full_text.lower():
        query = re.sub(r'\bspotify\b', '', name, flags=re.I).strip()
        return f"play {query} on spotify" if query else "open spotify"

    if not name:
        return None

    # A media-ish noun in the object itself ("gana chalao", "video chalao")
    # → play, regardless of app-name lookalikes. Routed through YouTube
    # search (not a bare "play {name}") — see _media_noun_query docstring.
    if _MEDIA_NOUNS_RE.search(name):
        return f"play {_media_noun_query(name)} on youtube"

    # A known, installed application name → launch it. Reuses the exact
    # same app-name list object_resolver._looks_like_known_app() already
    # draws from — never a second, Urdu-specific app registry.
    try:
        from .entity_corrector import _COMMON_APPS, _APP_ALIASES
        n = name.lower()
        if n in _APP_ALIASES or any(n == app.lower() for app in _COMMON_APPS):
            return f"open {name}"
    except Exception:
        pass

    # No decisive signal either way — fall back to "open", the behavior
    # this replaces (bare "chalao" used to always resolve to "open" via
    # list order, so this preserves that default rather than introducing a
    # new, equally-unverified assumption in the opposite direction).
    return f"open {name}"


def _strip_action_words(text: str) -> str:
    """Remove mapped action verbs (already extracted) from the entity span."""
    result = text
    for pat, _ in _VERB_MAP:
        result = pat.sub(" ", result)
    result = _CONNECTORS.sub(" ", result)
    # Urdu-script punctuation (۔ = full stop U+06D4, ؟ = question mark
    # U+061F) was missing from the strip set — an ASCII-only '.!?,;'.
    # Found via real-pipeline validation (2026-08-24): "پہلا والا کھولو۔"
    # left "پہلا والا ۔" (with the Urdu full stop still attached) as the
    # entity span, which then failed _normalize_entity_reference's exact
    # match against "پہلا والا" and fell through to a raw open_application
    # guess instead of resolving as a pending-disambiguation reference.
    result = re.sub(r'\s{2,}', ' ', result).strip().strip('.!?,;۔؟')
    # .strip('.!?,;') above can expose a NEW trailing/leading whitespace
    # character once the punctuation it removed is gone (e.g. "folder ."
    # -> stripping "." alone leaves "folder " with an unstripped trailing
    # space) — found via real-pipeline validation (2026-08-24) as a stray
    # trailing space silently riding along in every canonical string this
    # function produces whenever the source ended in punctuation preceded
    # by a stripped connector word. A second whitespace strip closes it.
    result = result.strip()
    # Urdu-script connector/postposition strip — edge-only, applied AFTER
    # the punctuation strip above (so a trailing "۔" doesn't block the
    # \s+$ anchor in _URDU_CONNECTORS_EDGE_RE from seeing a real trailing
    # connector). See _strip_urdu_connectors_at_edges' docstring for why
    # this is edge-only rather than a global substitution.
    result = _strip_urdu_connectors_at_edges(result)
    return result.strip()


# ── Quoted-payload protection ─────────────────────────────────────────────────
# Defense-in-depth alongside the edge-only connector strip above: an
# explicitly quoted span (a search query, a note/message body, a song
# title someone said with quote marks) must survive verb/connector/filler
# stripping completely untouched, regardless of what grammatical words it
# contains. Swapped out for an opaque placeholder BEFORE any regex in this
# module touches the text, restored verbatim after canonical synthesis —
# general mechanism, not specific to any one action.
_QUOTE_PH_OPEN, _QUOTE_PH_CLOSE = "", ""
_QUOTED_SPAN_RE = re.compile(r'"([^"]*)"|“([^“”]*)”')
_QUOTE_PH_RESTORE_RE = re.compile(f"{_QUOTE_PH_OPEN}(\\d+){_QUOTE_PH_CLOSE}")


def _protect_quoted_spans(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        content = m.group(1) if m.group(1) is not None else m.group(2)
        spans.append(content)
        return f"{_QUOTE_PH_OPEN}{len(spans) - 1}{_QUOTE_PH_CLOSE}"

    return _QUOTED_SPAN_RE.sub(_sub, text), spans


def _restore_quoted_spans(text: Optional[str], spans: list[str]) -> Optional[str]:
    if not text or not spans:
        return text

    def _sub(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        return spans[idx] if 0 <= idx < len(spans) else m.group(0)

    return _QUOTE_PH_RESTORE_RE.sub(_sub, text)


def analyze(transcript: str, detected_lang: str, trace_id: str = "") -> Optional[str]:
    """
    Convert a mixed-language transcript to a canonical English command.

    Returns:
        Canonical English string, or None if no mapping was found
        (caller should keep original transcript).

    trace_id: per-turn ID from api.services.tracer, stamped on the
    [MIXED_LANGUAGE] log so this fast-tier canonicalization can be
    correlated with the rest of the turn's logs the same way the Qwen
    tier's [TRACE ...] [TURN_SUMMARY] already is.
    """
    if not transcript or detected_lang == "en":
        return None

    text = transcript.strip()

    # ── Early bail: negation present → don't canonicalize ──────────────────
    # "kholo nahi" / "band mat karo" etc. are negation/cancel commands.
    # The engine can't produce a meaningful canonical for negation —
    # returning None preserves the original for the intent router, which
    # is far better than producing garbled "open nahi" output.
    if _NEGATION_RE.search(text):
        logger.info("[MIXED_LANGUAGE_SKIP] reason=negation text=%r", text[:60])
        return None

    # Protect any explicitly quoted span (search query, message/note body,
    # song title) BEFORE any verb/connector/filler regex below can touch
    # it — see _protect_quoted_spans' comment. Restored verbatim just
    # before this function returns.
    text, _quoted_spans = _protect_quoted_spans(text)

    # Strip filler words ("areee", "arey", etc.) that don't affect intent
    text = _FILLERS.sub(" ", text)
    text = re.sub(r'\s{2,}', ' ', text).strip()

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
    entity_span = _normalize_entity_reference(entity_span)
    entity_span = _reorder_leading_drive_scope(entity_span)

    # ── Build canonical command ────────────────────────────────────────────────
    canonical: Optional[str] = None

    # Volume up/down
    if matched_action in ("increase", "decrease") and _VOLUME_TARGETS.search(text):
        canonical = f"volume {'up' if matched_action == 'increase' else 'down'}"
    elif matched_action in ("increase", "decrease") and _BRIGHT_TARGETS.search(text):
        canonical = f"brightness {'up' if matched_action == 'increase' else 'down'}"

    # Ambiguous "chalao" — resolved via real signals, see _disambiguate_chalao.
    elif matched_action == "chalao_ambiguous":
        canonical = _disambiguate_chalao(entity_span, text)

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

    # Generic open — bail to Qwen (return None) rather than pass a
    # half-Urdu entity ("Is repo README") to intent_router as if it were
    # a real name; see _has_unresolved_reference's comment.
    elif matched_action == "open" and entity_span and not _has_unresolved_reference(entity_span):
        canonical = f"open {entity_span}"

    # Generic close
    elif matched_action == "close" and entity_span and not _has_unresolved_reference(entity_span):
        canonical = f"close {entity_span}"

    # Generic install
    elif matched_action == "install" and entity_span and not _has_unresolved_reference(entity_span):
        canonical = f"install {entity_span}"

    # Generic download
    elif matched_action == "download" and entity_span and not _has_unresolved_reference(entity_span):
        canonical = f"download {entity_span}"

    # Generic play — no youtube/spotify mentioned. "play" only ever comes
    # from a media verb ("bajao"/"play karo"), never an app-launch verb, so
    # route through YouTube search rather than a bare "play {entity_span}"
    # that intent_router's generic pattern turns into a no-op media-key
    # press when nothing is already playing. See _media_noun_query.
    elif matched_action == "play" and entity_span and not _has_unresolved_reference(entity_span):
        canonical = f"play {_media_noun_query(entity_span)} on youtube"

    # Take screenshot
    elif matched_action == "take screenshot":
        canonical = "take screenshot"

    # Search
    elif matched_action == "search" and entity_span:
        _query = _LEADING_SEARCH_SITE_RE.sub("", entity_span).strip()
        canonical = f"search for {_query or entity_span}"

    # Lock
    elif matched_action == "lock":
        canonical = "lock"

    # Sleep
    elif matched_action == "sleep":
        canonical = "sleep"

    # Shutdown / restart
    elif matched_action == "shutdown":
        if "restart" in text.lower():
            canonical = "restart"
        else:
            canonical = "shutdown"

    # Mute
    elif matched_action == "mute":
        canonical = "mute"

    # Delete
    elif matched_action == "delete" and entity_span:
        canonical = f"delete {entity_span}"

    # Create (folder/file)
    elif matched_action == "create" and entity_span:
        if "folder" in entity_span.lower():
            name = re.sub(r'\b(?:folder|file)\b', '', entity_span, flags=re.I).strip()
            canonical = f"create folder {name}" if name else "create folder"
        else:
            canonical = f"create {entity_span}"

    # Show/display
    elif matched_action == "show":
        canonical = f"show {entity_span}" if entity_span else "show screen"

    # Media pause/resume/next/previous — canonical strings match
    # intent_router's existing English media_control patterns directly.
    elif matched_action == "media_pause":
        canonical = "pause music"
    elif matched_action == "media_next":
        canonical = "next song"
    elif matched_action == "media_prev":
        canonical = "previous song"

    # Empty recycle bin / trash
    elif matched_action == "empty_trash":
        canonical = "empty recycle bin"

    canonical = _restore_quoted_spans(canonical, _quoted_spans)

    if canonical:
        logger.info(
            "[TRACE %s] [MIXED_LANGUAGE] lang=%s original=%r canonical=%r action=%s entity=%r qwen_used=false",
            trace_id or "?", detected_lang, transcript[:60], canonical[:60], matched_action, entity_span[:40],
        )

    return canonical


# ── Compound-command splitting (Urdu/Roman-Urdu "and"/"then") ───────────────
# Mirrors brain/planner.py's own _SPLIT_RE (English "and then"/"also") for
# the Urdu/Roman-Urdu equivalents. This is deliberately the FAST
# deterministic tier for compound commands: split on the connector, then run
# the SAME single-clause analyze() above on each piece — no new NLU, just N
# calls to the existing per-clause analyzer. Only ever meaningful for
# detected_lang != "en" (see orchestrator.py's call site, gated the same way
# single-clause analyze() already is).
_COMPOUND_SPLIT_RE = re.compile(
    r'\s*(?:اور\s+پھر|اس\s+کے\s+بعد|اور|پھر|'
    r'us\s+ke\s+baad|uske\s+baad|aur\s+phir|aur|phir)\s*',
    re.I,
)


def split_compound(transcript: str, detected_lang: str, trace_id: str = "") -> Optional[list[str]]:
    """
    Deterministic compound-command splitter for Urdu/Roman-Urdu "and"/"then"
    connectors ("اور"/"aur", "پھر"/"phir", "اس کے بعد"/"us ke baad").

    Splits the ORIGINAL transcript into clauses on those connectors, then
    runs the SAME single-clause analyze() (above) on each piece
    independently. Returns a list of canonical English sentences ONLY if
    there are >=2 clauses AND every single one of them independently
    canonicalizes — a partial split (one clause fails to analyze) is
    treated as a false-positive split (e.g. "aur"/"phir" used as an
    ordinary word rather than a real conjunction) and returns None so the
    caller falls through to single-shot routing / the OpenAI-or-Qwen Tier 4
    fallback, rather than silently dropping a clause the way the OLD
    single-action local_comprehension schema did.

    This is the deterministic-first fast path for compound commands like
    "YouTube کو کھولو اور کوئی گانا چلا دو" — each half converges to the
    exact canonical string a single-action Urdu command would already
    produce ("open YouTube", "play <query> on youtube"), so the caller
    (orchestrator.py) can hand the resulting list straight to the EXISTING
    brain/planner.py Plan/PlanStep machinery with no new execution logic.
    """
    if not transcript or detected_lang == "en":
        return None

    parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(transcript) if p.strip()]
    if len(parts) < 2:
        return None

    canonicals: list[str] = []
    for part in parts:
        c = analyze(part, detected_lang, trace_id=trace_id)
        if not c:
            logger.info(
                "[MIXED_LANGUAGE_COMPOUND_SKIP] reason=clause_unresolved clause=%r full=%r",
                part[:60], transcript[:80],
            )
            return None
        canonicals.append(c)

    logger.info(
        "[TRACE %s] [MIXED_LANGUAGE_COMPOUND] lang=%s original=%r steps=%s",
        trace_id or "?", detected_lang, transcript[:80], canonicals,
    )
    return canonicals
