"""
Unified approval/cancel intent parser — English, Roman Urdu, Urdu script,
and mixed all resolve through this ONE function.

Why this exists: before this module, yes/no/cancel detection was
implemented separately, in English only, at several unrelated call sites
(store_agent.py's install-cancel handler, voice_ws.py's pending-action
confirmation, its ambiguous-control-action confirmation, and its
open-after-install offer) — each with its own private regex. Adding Urdu
support the "keyword" way would have meant writing a SECOND, Urdu-only
version of each of those regexes — exactly the duplicated-per-tool pattern
this module exists to avoid. There is now exactly one yes/no/cancel
classifier; every caller (English or not) uses it.

This module does not know what is being approved or cancelled — callers
still own that (the pending action, the confirmation state, etc.). It only
answers: did the user just say "yes", "no", or something else?

2026-08-24 fix: the first version anchored the WHOLE utterance against a
single phrase, so natural combinations — "Haan kar do.", "Nahi, cancel
karo.", "ہاں، کر دو۔", "Acha rehne do." — all fell through to "unclear"
because real speech stacks an acknowledgment word in front of the actual
answer, exactly the "several filler/affirmation words before the real
verb" pattern store_agent.py's own _GREETING regex already exists to
strip (see its comment for the live bug that fixed: "Perfect. Now can you
download Telegram" needed the same repeatable-optional-prefix treatment).
Reused that exact pattern here — strip a repeatable, polarity-appropriate
leading acknowledgment, then match the core phrase — instead of trying to
enumerate every two-word combination as its own alternative.

Log markers: none — this is a pure, fast, synchronous classifier. Callers
already log their own confirmation-tier decisions.
"""
from __future__ import annotations

import re
from typing import Literal

ApprovalIntent = Literal["yes", "no", "unclear"]

# Repeatable, polarity-neutral leading acknowledgment — consumed from the
# front before either core pattern is tried. Mirrors store_agent._GREETING.
_LEAD_NEUTRAL = r'(?:acha|accha|okay|ok|well|so|now|theek\s+hai|thik\s+hai)[\s,،]+'

# ── Affirmative ────────────────────────────────────────────────────────────────
_YES_CORE = (
    r'yes|yeah|yep|yup|sure|ok(?:ay)?|correct|right|that\'?s\s+right|'
    r'go\s+ahead|do\s+it|confirm(?:ed)?|proceed|'
    r'proceed\s+karo|kar\s+do|kardo|kar\s+dein|kar\s+dijiye|'
    r'chalo|theek\s+hai|thik\s+hai|theek|thik|'
    r'haan|han|ji|jee|jee\s+haan|bilkul|zaroor|'
    r'ہاں|جی|جی\s*ہاں|ٹھیک\s*ہے|بالکل|ضرور|کر\s*دو|کر\s*دیں'
)
_YES_RE = re.compile(
    r'^\s*(?:' + _LEAD_NEUTRAL + r'|(?:yes|yeah|yep|yup|sure|haan|han|ji|jee|ہاں|جی)[\s,،]+)*'
    r'(?:' + _YES_CORE + r')\s*[.!]?\s*$',
    re.IGNORECASE,
)

# ── Negative / cancel ────────────────────────────────────────────────────────
_NO_CORE = (
    r'no|nah|nope|cancel(?:\s+(?:it|that))?|never\s*mind|nevermind|'
    r'stop(?:\s+(?:it|that))?|forget\s+it|don\'?t(?:\s+do\s+(?:it|that))?|'
    r'nahi|nahin|nai|nhi|'
    r'rehne\s+do|reh\s+nay\s+do|mat\s+karo|mat\s+kar|cancel\s+karo|'
    r'band\s+karo\s+ise|chhoro|choro|'
    r'نہیں|رہنے\s*دو|مت\s*کرو|منسوخ\s*کرو'
)
_NO_RE = re.compile(
    r'^\s*(?:' + _LEAD_NEUTRAL + r'|(?:no|nah|nope|nahi|nahin|nai|nhi|نہیں)[\s,،]+)*'
    r'(?:' + _NO_CORE + r')\s*[.!]?\s*$',
    re.IGNORECASE,
)


def parse_yes_no(text: str) -> ApprovalIntent:
    """
    Classify a short confirmation utterance as "yes", "no", or "unclear".

    Anchored (whole-utterance, after stripping a leading acknowledgment)
    by design: this is for turns that ARE the answer to a pending yes/no
    question, not for scanning a longer sentence for an embedded yes/no
    word (that would false-positive on, e.g., "yes I know but can you
    also open Chrome" — a genuinely different utterance, not a plain
    confirmation — which still correctly returns "unclear" here since
    nothing after stripping "yes " matches a core phrase).
    """
    t = (text or "").strip().rstrip("۔")
    if not t:
        return "unclear"
    if _YES_RE.match(t):
        return "yes"
    if _NO_RE.match(t):
        return "no"
    return "unclear"
