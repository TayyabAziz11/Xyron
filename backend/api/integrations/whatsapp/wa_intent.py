"""
wa_intent.py — deterministic WhatsApp intent parser (Phase 4 fast path).

parse_wa_intent(text) turns a raw utterance into a WAIntent WITHOUT
resolving anything: it never talks to ContactResolver, the identity store,
or the transport. It only extracts references (contact_ref, message,
artifact_ref) and a confidence score. Resolution happens later, in
wa_command_handler.py, once — this module can be called freely from a hot
routing path without ever costing a network round trip.

Anti-hijack design (handoff §29 — "do not let broad WhatsApp regexes
hijack unrelated queries")
--------------------------------------------------------------------
Every shape that names "whatsapp" explicitly is unambiguous by
construction and gets `requires_cache_hit=False`: the user said the app
name, there is nothing else this could mean.

Every other shape ("message Tayyab I'm outside", "tell him I'll be late")
is inherently ambiguous — "message"/"tell" are common English verbs used
for all sorts of things — so those get `requires_cache_hit=True`. The
caller (IntentRouter's WhatsApp tier) MUST verify contact_ref is either a
contextual pronoun or an already-known identity-store entry before
committing to the WhatsApp route; otherwise it must fall through to the
next tier. A bare "message <stranger>" for a name Xyron has never heard of
therefore never hijacks anything — it silently falls through to the LLM,
exactly like it would today.

This module is typed-first but voice-ready (handoff §27/§28): matching is
done on lowercased, whitespace-normalized text so common STT punctuation
noise doesn't break a match. Urdu phrasing ("Tayyab ko message karo ke...")
is explicitly out of scope for Phase 4 (handoff §28/§44) — only English
shapes are implemented; unmatched text returns None and falls through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WAIntent:
    action: str                        # send_text | send_file | show_chat | get_messages | reply
    contact_ref: Optional[str] = None
    message: Optional[str] = None
    artifact_ref: Optional[dict] = None
    show_ui: bool = False
    confidence: float = 0.0
    raw_utterance: str = ""
    # True when contact_ref came from an ambiguous verb shape ("message X",
    # "tell X") rather than an explicit "whatsapp" mention — the caller
    # must confirm contact_ref resolves via context/cache before committing.
    requires_cache_hit: bool = True


_WHATSAPP_WORD_RE = re.compile(r"\bwhatsapp\b", re.IGNORECASE)

_SHOW_ME_TAIL_RE = re.compile(
    r"\s*(?:,?\s*and\s+show\s+me(?:\s+too)?|,?\s*show\s+me\s+too)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# A leading acknowledgment before the real command — live-caught after a
# spoken WhatsApp announcement: the user answers their own "should I reply?"
# impulse out loud before giving the command ("yes. reply him. i am busy
# tonight", "sure, send him a reply"). Every rule below is `^`-anchored, so
# without stripping this first, the whole utterance falls through to the
# LLM fallback instead of being recognized as a WhatsApp command at all.
_LEADING_ACK_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|alright|right)\s*[,.!]?\s+",
    re.IGNORECASE,
)

# No apostrophe in the per-word class: with one, "tayyab's" (possessive)
# gets swallowed whole into the contact capture ("tayyab's" rather than
# "tayyab"), which then fails every downstream cache/context lookup since
# those normalize punctuation away. The one place a possessive marker is
# expected (show_chat's "<contact>'s whatsapp/chat") handles it via an
# explicit `'?s?` right after this token instead.
# "saying"/"that" are reserved separator words (see _SEP below) — without
# excluding them here, a multi-word contact capture greedily swallows the
# separator itself: "send a whatsapp to Tayyab saying: hi" matched
# contact="Tayyab saying" (2 words, both valid per [A-Za-z][\w\.]*) before
# the pattern ever got to test _SEP, because "Tayyab saying" + the bare
# colon right after still satisfied the rest of the pattern. The negative
# lookahead stops a word position from ever consuming "saying"/"that".
_RESERVED_SEP_WORD = r"(?:saying|say|that)\b"
# No literal "." in the char class: with one, a captured word can swallow
# a sentence-ending period and keep going into the NEXT sentence — a real,
# live-observed bug. Whisper hallucinates a leading clause on short audio
# ("open whatsapp" alone sometimes transcribes as "Open Microsoft. Open
# WhatsApp." or "Open VS Code. Open WhatsApp." — a different fabricated
# first sentence each time, but always ending in the real "open whatsapp").
# With "." allowed, "microsoft." + "open" both got swallowed as a 2-word
# contact ("microsoft. open"), so the WhatsApp parser incorrectly claimed
# an utterance that was never actually about a contact at all. Dropping
# "." makes a period a hard stop: the pattern can no longer bridge from
# one sentence into the next, so this shape now correctly fails to match
# and falls through to whatever already handles a bare "open whatsapp".
_CONTACT_WORD = r"(?!" + _RESERVED_SEP_WORD + r")[A-Za-z]\w*"
_CONTACT_TOKEN = _CONTACT_WORD + r"(?:\s+" + _CONTACT_WORD + r"){0,2}"
# Single-token contact — used when contact and free-text message are NOT
# separated by punctuation/"saying"/"that". _CONTACT_TOKEN's up-to-3-word
# span is unbounded on the right, so "message tayyab i am outside" would
# otherwise greedily swallow "tayyab i am" as the contact and leave only
# "outside" as the message. Restricting to one word (or him/her) when there
# is no separator sacrifices multi-word names in that unpunctuated shape —
# an acceptable, much safer trade: those names still work fine with a
# separator ("message tayyab aziz, i am outside") or via the explicit
# "whatsapp <contact>, ..." / "send a whatsapp to <contact> saying ..." shapes.
_CONTACT_TOKEN_BARE = r"him|her|" + _CONTACT_WORD
# "saying"/"say"/"that" are often immediately followed by ':', ',' or '.'
# with no space ("saying: hi", "that, hi") — tolerate it before the
# mandatory whitespace so the separator match doesn't fail on that
# punctuation. A bare '.' is also accepted as its own separator (not just
# trailing a separator word) — live-caught: "reply him. i am busy tonight"
# pauses between the command and the message with just a sentence break,
# no "saying"/"that"/comma/colon. Safe here (unlike _CONTACT_WORD's char
# class above) because this is a distinct token matched AFTER the contact
# capture, not a character allowed inside it — it can't bridge two
# unrelated sentences into one contact name the way that bug did.
_SEP_TOKEN = r"(?:(?:saying|say|that)[:,.]?|[:,.])"
# Up to two separator tokens in a row — live-caught: "reply him. say i am
# busy tonight" pairs a sentence-break period AND the word "say" before the
# actual message. Matching only one token would leave "say" glued onto the
# front of the captured message ("say i am busy tonight" instead of "i am
# busy tonight"), which is what would get literally sent to the contact.
_SEP = r"(?:" + _SEP_TOKEN + r")(?:\s+" + _SEP_TOKEN + r")?"

# ── File-send artifact matching ─────────────────────────────────────────────
# Two distinct shapes, tried in order (specific → general):
#
# 1. An exact filename with extension ("resume.pdf", "photo 2024.jpg") —
#    routed as {"kind": "filename", "name": ...}, resolved via an exact
#    (case-insensitive) search across the whole indexed filesystem plus the
#    common user folders (FileSendPlanner._plan_filename / fs_index) — not
#    limited to Downloads. Ambiguous (2+ files with that name) asks which
#    one; none found says so cleanly. Spaces are allowed inside the token
#    since real filenames often have them; a filename containing the literal
#    word " to " would break this (rare enough to accept as a trade-off).
_FILE_EXT_WORDS = (
    r"pdf|docx?|xlsx?|pptx?|txt|csv|zip|rar|7z|"
    r"jpe?g|png|gif|webp|bmp|svg|heic|"
    r"mp4|mov|avi|mkv|"
    r"mp3|wav|m4a"
)
_FILENAME_TOKEN = r"[\w][\w\-. ]{0,60}?\.(?:" + _FILE_EXT_WORDS + r")"

# 2. A type/description phrase with no extension ("this pdf", "the invoice
#    pdf", "my march expense report") — routed as {"kind": "context",
#    "query": ...}, resolved via file_resolver's broader fuzzy/recency
#    matching. Requires a demonstrative ("this"/"that"/"the"/"my") and MUST
#    end in a real file-type noun — up to 2 free descriptor words are
#    allowed before that noun ("the invoice pdf" = 1 descriptor + "pdf"),
#    so this can't be triggered by an ordinary non-file sentence shaped like
#    "send the good news to Tayyab" ("news" isn't a file-type noun).
_ARTIFACT_TYPE_WORDS = (
    r"pdf|images?|photos?|pictures?|pics?|"
    r"screenshots?|documents?|docs?|files?|"
    r"videos?|clips?|invoices?|resumes?|cv|"
    r"reports?|receipts?|tickets?|contracts?|"
    r"presentations?|ppt|spreadsheets?|sheets?|"
    r"attachments?|forms?"
)
_ARTIFACT_PHRASE = (
    r"(?:this|that|the|my)\s+(?:[A-Za-z][\w\-]*\s+){0,2}"
    r"(?:" + _ARTIFACT_TYPE_WORDS + r")"
    r"(?:\s+i\s+(?:just\s+)?(?:took|sent))?"
    r"|it"
)


def _strip_show_me(text: str) -> tuple[str, bool]:
    m = _SHOW_ME_TAIL_RE.search(text)
    if not m:
        return text, False
    return text[: m.start()].strip(), True


def _clean_contact(raw: str) -> str:
    return raw.strip().strip(",.:;!?").strip()


def _artifact_ref_from_phrase(phrase: str) -> dict:
    """
    Build a FileSendPlanner-shaped file_ref for a matched artifact phrase.
    Everything resolves through the "context" kind — wa_command_handler
    delegates the actual lookup to wa_context.resolve_artifact_reference /
    ScreenshotResolver / FSIndex, never re-implements filesystem scanning
    here (handoff §31).
    """
    return {"kind": "context", "query": phrase.strip()}


def parse_wa_intent(text: str) -> Optional[WAIntent]:
    """
    Parse a raw utterance into a WAIntent, or None if it doesn't match any
    known WhatsApp command shape. Never raises, never resolves contacts or
    files — see module docstring.
    """
    if not text or not text.strip():
        return None

    raw = text
    has_whatsapp_word = bool(_WHATSAPP_WORD_RE.search(text))
    body, show_ui = _strip_show_me(text.strip())
    if not body:
        return None
    body = _LEADING_ACK_RE.sub("", body).strip() or body

    requires_cache_hit = not has_whatsapp_word

    # ── 0b. show_chat — compound "open whatsapp and open/show <contact> chat[s]" ──
    # Real, live-observed STT shape (not the canonical "show me X's whatsapp"
    # this parser otherwise expects): the user says "open WhatsApp" as its
    # own leading clause, then a second clause naming the contact. Without
    # this rule the WHOLE utterance falls through Tier 0.5 (no match), and a
    # generic Tier 2 "open <anything>" catch-all in intent_router.py wins
    # instead, greedily swallowing "whatsapp and open tayyab chat" as one
    # garbled app_name — confirmed from a real backend log. Checked before
    # rule 1 since it's a strict superset shape (has a leading "open
    # whatsapp" clause that rule 1 doesn't expect at all).
    m = re.search(
        r"^open\s+whatsapp\s*(?:and|,)?\s*(?:open|show(?:\s+me)?)\s+"
        r"(?:the\s+)?(?P<contact>" + _CONTACT_TOKEN + r")(?:'?s)?\s+chats?\b[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="show_chat",
            contact_ref=_clean_contact(m.group("contact")),
            show_ui=True,
            confidence=1.0,
            raw_utterance=raw,
            requires_cache_hit=False,  # explicit "whatsapp" keyword present
        )

    # ── 0c. show_chat — bare "open whatsapp" / "open whatsapp web" (no contact) ──
    # No contact named at all — the user just wants the WhatsApp surface
    # itself shown/focused (reusing an already-open, already-logged-in
    # Chrome tab, per WhatsAppUIAdapter.open_whatsapp()), not a specific
    # chat. contact_ref=None here is deliberate and handled explicitly
    # downstream (WACommandHandler.open_whatsapp() / _exec_wa_show_chat) —
    # it must NOT fall into the "open_application" generic app-launcher,
    # which has no way to reuse a Chrome tab and was observed failing
    # outright (LAUNCH_UNVERIFIED) since WhatsApp Desktop isn't installed.
    # End-anchored with nothing after "whatsapp[ web]" — a genuine contact
    # or "chat"/"chats" tail always falls through to rule 0b/1 instead.
    m = re.search(
        r"^(?:open|show(?:\s+me)?)\s+(?:my\s+|the\s+)?whatsapp(?:\s+web)?\b[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="show_chat",
            contact_ref=None,
            show_ui=True,
            confidence=1.0,
            raw_utterance=raw,
            requires_cache_hit=False,
        )

    # ── 1. show_chat — "show me <contact>'s whatsapp/chat", "open <contact>'s chat" ──
    # "me" is optional (not "show\s+me" only): Xyron's voice-pipeline
    # normalizer (api/services/normalizer.py) has a pre-existing, unrelated
    # synonym rule that rewrites "show me" -> "show" ANYWHERE in the
    # utterance before this parser ever sees the text, so a spoken "Show me
    # Tayyab's WhatsApp" arrives here as "show tayyab's whatsapp" — bare
    # "show me" (without "'s whatsapp/chat/conversation" after it) would
    # already have to survive on its own, which it doesn't since this whole
    # pattern still requires the "<contact>'s <surface>" tail regardless.
    m = re.search(
        r"^(?:show(?:\s+me)?|open)\s+(?P<contact>" + _CONTACT_TOKEN + r")'?s?\s+"
        r"(?P<surface>whatsapp|chat|conversation)\b[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        surface_is_wa = m.group("surface").lower() == "whatsapp"
        return WAIntent(
            action="show_chat",
            contact_ref=_clean_contact(m.group("contact")),
            show_ui=True,
            confidence=1.0 if (has_whatsapp_word or surface_is_wa) else 0.75,
            raw_utterance=raw,
            requires_cache_hit=not (has_whatsapp_word or surface_is_wa),
        )

    # ── 2. get_messages — "what did <contact> say (on whatsapp)?" ──────────
    m = re.search(
        r"^what\s+did\s+(?P<contact>" + _CONTACT_TOKEN + r")\s+say"
        r"(?:\s+on\s+whatsapp)?\s*\??\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="get_messages",
            contact_ref=_clean_contact(m.group("contact")),
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.7,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 3. reply — "reply to <contact> [saying|:|,] <message>" ─────────────
    # With separator: multi-word contact allowed (unambiguous — the
    # separator marks where the contact ends). Without: single token only
    # (see _CONTACT_TOKEN_BARE).
    m = re.search(
        r"^reply\s+to\s+(?P<contact>him|her|" + _CONTACT_TOKEN + r")\s*" + _SEP + r"\s+(?P<msg>.+)$",
        body, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"^reply\s+to\s+(?P<contact>" + _CONTACT_TOKEN_BARE + r")\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if not m:
        # "reply him ..." / "reply her ..." — "to" dropped, live-caught
        # natural phrasing ("reply him. i am busy tonight").
        m = re.search(
            r"^reply\s+(?P<contact>him|her|" + _CONTACT_TOKEN + r")\s*" + _SEP + r"\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if not m:
        # "send him/her [a] reply ..." — alternate verb order, also
        # live-caught ("send him reply. say i am busy tonight").
        m = re.search(
            r"^send\s+(?P<contact>him|her|" + _CONTACT_TOKEN + r")\s+(?:a\s+)?repl(?:y|ies)\s*"
            + _SEP + r"\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if m and m.group("msg").strip():
        return WAIntent(
            action="reply",
            contact_ref=_clean_contact(m.group("contact")),
            message=m.group("msg").strip(),
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.75,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 4a. send_file — exact filename, artifact-first: "send/share <file.ext> to <contact>" ──
    # Tried before the type/description rules below since a real filename
    # is unambiguous — no need to fall back to fuzzy resolution when the
    # user gave an exact name.
    m = re.search(
        r"^(?:send|share)\s+(?:this\s+|that\s+|the\s+|my\s+)?"
        r"(?P<artifact>" + _FILENAME_TOKEN + r")\s+to\s+(?P<contact>" + _CONTACT_TOKEN + r")[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="send_file",
            contact_ref=_clean_contact(m.group("contact")),
            artifact_ref={"kind": "filename", "name": m.group("artifact").strip()},
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.85,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 4b. send_file — exact filename, contact-first: "send <contact> <file.ext>" ──
    m = re.search(
        r"^send\s+(?P<contact>" + _CONTACT_TOKEN + r")\s+"
        r"(?:this\s+|that\s+|the\s+|my\s+)?(?P<artifact>" + _FILENAME_TOKEN + r")[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="send_file",
            contact_ref=_clean_contact(m.group("contact")),
            artifact_ref={"kind": "filename", "name": m.group("artifact").strip()},
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.85,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 4c. send_file — type/description phrase, artifact-first: "send/share <artifact> to <contact>" ──
    m = re.search(
        r"^(?:send|share)\s+(?P<artifact>" + _ARTIFACT_PHRASE + r")\s+to\s+(?P<contact>" + _CONTACT_TOKEN + r")[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="send_file",
            contact_ref=_clean_contact(m.group("contact")),
            artifact_ref=_artifact_ref_from_phrase(m.group("artifact")),
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.8,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 4d. send_file — type/description phrase, contact-first: "send <contact> this/that <artifact>" ──
    m = re.search(
        r"^send\s+(?P<contact>" + _CONTACT_TOKEN + r")\s+"
        r"(?P<artifact>" + _ARTIFACT_PHRASE + r")[.!?]*\s*$",
        body, re.IGNORECASE,
    )
    if m:
        return WAIntent(
            action="send_file",
            contact_ref=_clean_contact(m.group("contact")),
            artifact_ref=_artifact_ref_from_phrase(m.group("artifact")),
            show_ui=show_ui,
            confidence=0.9 if has_whatsapp_word else 0.8,
            raw_utterance=raw,
            requires_cache_hit=requires_cache_hit,
        )

    # ── 5. send_text — explicit "whatsapp" verb shapes ──────────────────────
    #    "whatsapp <contact>[,:]? <message>"
    #    "send (a )?whatsapp (message )?to <contact> (saying|that|:)? <message>"
    # With separator: multi-word contact allowed. Without: single token
    # only (see _CONTACT_TOKEN_BARE / the greedy-match note above it).
    m = re.search(
        r"^send\s+(?:a\s+)?whatsapp(?:\s+message)?\s+to\s+(?P<contact>" + _CONTACT_TOKEN + r")\s*"
        + _SEP + r"\s+(?P<msg>.+)$",
        body, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"^send\s+(?:a\s+)?whatsapp(?:\s+message)?\s+to\s+(?P<contact>" + _CONTACT_TOKEN_BARE + r")\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if not m:
        m = re.search(
            r"^whatsapp\s+(?P<contact>" + _CONTACT_TOKEN + r")\s*[,:]\s*(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if not m:
        m = re.search(
            r"^whatsapp\s+(?P<contact>" + _CONTACT_TOKEN_BARE + r")\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if m and m.group("msg").strip():
        return WAIntent(
            action="send_text",
            contact_ref=_clean_contact(m.group("contact")),
            message=m.group("msg").strip(),
            show_ui=show_ui,
            confidence=1.0,
            raw_utterance=raw,
            requires_cache_hit=False,
        )

    # ── 6. send_text — ambiguous verb shapes: "message X ...", "tell X ..." ─
    #    Deliberately last and always requires_cache_hit=True: only fires
    #    for a contact the identity store or context already knows. Same
    #    with-separator/without-separator split as rule 5.
    m = re.search(
        r"^(?:message|tell)\s+(?P<contact>him|her|" + _CONTACT_TOKEN + r")\s*" + _SEP + r"\s+(?P<msg>.+)$",
        body, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"^(?:message|tell)\s+(?P<contact>" + _CONTACT_TOKEN_BARE + r")\s+(?P<msg>.+)$",
            body, re.IGNORECASE,
        )
    if m and m.group("msg").strip():
        return WAIntent(
            action="send_text",
            contact_ref=_clean_contact(m.group("contact")),
            message=m.group("msg").strip(),
            show_ui=show_ui,
            confidence=0.85,
            raw_utterance=raw,
            requires_cache_hit=True,
        )

    return None
