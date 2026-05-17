"""
Self-upgrade detector — Phase 3 of emotional intent system.

Detects when the user describes improving or upgrading Xyron's capabilities.
Returns structured result with upgrade_type so responses can reference the
specific component being upgraded.

Examples:
  "I am adding a new memory upgrade in you"     → upgrade_type=memory
  "I fixed your wake word"                       → upgrade_type=voice
  "I upgraded the emotional cognition pipeline" → upgrade_type=personality
  "Just shipped the new UI"                      → upgrade_type=ui
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SelfUpgradeResult:
    is_self_upgrade: bool
    upgrade_type:    str    # memory | voice | personality | ui | code | general
    confidence:      float
    emotion:         str    # excitement | pride | hype
    importance:      float


# ── Upgrade type keyword maps ─────────────────────────────────────────────────

_UPGRADE_TYPE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # More-specific patterns first — ordered from most-specific to most-general
    ("memory",          re.compile(r'\b(memory|context|remember|recall|long.?term|episodic|semantic)\b', re.IGNORECASE)),
    # voice_conversion / prosody must precede generic "voice" to avoid false matches
    ("voice_conversion", re.compile(r'\b(voice\s*conversion|rvc|emotional\s*voice|voice\s*model|voice\s*character|sound\s*like|voice\s*cloning)\b', re.IGNORECASE)),
    ("prosody",         re.compile(r'\b(prosody|prosodic|pacing|pitch|intonation|voice\s*dynamics|tts\s*speed|speech\s*rate|voice\s*expressiv)\b', re.IGNORECASE)),
    # wake-up / wake word / wake issue all count as voice
    ("voice",           re.compile(r'\b(tts|speech|wake[\s\-]*(?:word|up|issue)?|whisper|kokoro|listen|audio|pronounc|voice\b)\b', re.IGNORECASE)),
    ("language",        re.compile(r'\b(urdu|roman\s*urdu|multilingual|language|translation|normali|bilingual)\b', re.IGNORECASE)),
    # emotions / emotional all count as personality
    ("personality",     re.compile(r'\b(personality|emotions?|emotional|mood|character|cognition|feelings?|expression|empathy)\b', re.IGNORECASE)),
    ("ui",              re.compile(r'\b(ui|interface|dashboard|orb|frontend|visual|design|animation|component)\b', re.IGNORECASE)),
    ("takeover",        re.compile(r'\b(takeover|dominant|control|system\s*authority)\b', re.IGNORECASE)),
    ("code",            re.compile(r'\b(code|logic|algorithm|pipeline|router|classifier|detector|function|module|script)\b', re.IGNORECASE)),
]

# ── General self-referential upgrade patterns ─────────────────────────────────
# These are the same as in the guard but slightly more relaxed (already confirmed emotional event)

_IS_SELF_UPGRADE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r'\b(I|we)\s+(am|are|\'m|\'re)\s+'
        r'(adding|upgrading|building|improving|fixing|creating|making|implementing|wiring|integrating)\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(I|we)\s+'
        r'(added|upgraded|built|improved|fixed|created|made|deployed|shipped|committed|merged|pushed)\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(new\s+)?(upgrade|feature|improvement|update|fix|patch|enhancement)\b'
        r'.{0,40}\b(you|xyron)\b',
        re.IGNORECASE,
    ),
    re.compile(r'\bmaking\s+you\s+(better|smarter|stronger|faster)\b', re.IGNORECASE),
    re.compile(r'\bgiving\s+you\s+(more|new|better)\b', re.IGNORECASE),
    re.compile(r'\b(upgraded|improved|fixed)\s+your\b', re.IGNORECASE),
    # "just shipped/deployed/committed the [new] [adj] [upgrade_type]"
    re.compile(
        r'\bjust\s+(shipped|deployed|committed|merged|pushed|finished)\b'
        r'.{0,30}'
        r'\b(memory|voice|personality|ui|language|cognition|pipeline|wake\s*word|upgrade|feature|fix|overhaul)\b',
        re.IGNORECASE,
    ),
]

# ── Hype intensity markers ────────────────────────────────────────────────────

_HIGH_HYPE_MARKERS = re.compile(
    r'\b(new|major|huge|big|complete|full|entire|all|finally|done|shipped|'
    r'deployed|merged|pushed|committed|live)\b',
    re.IGNORECASE,
)


class SelfUpgradeDetector:
    """Identifies self-upgrade events and classifies the upgrade type."""

    def detect(self, text: str) -> SelfUpgradeResult:
        """
        Detect if the text describes a self-upgrade event.

        Returns SelfUpgradeResult with is_self_upgrade=True if detected.
        Always returns a result (never raises).
        """
        if not text:
            return SelfUpgradeResult(False, "general", 0.0, "calmness", 0.5)

        # Expand contractions so "I'm adding" matches same as "I am adding"
        text = (text
            .replace("I'm",  "I am").replace("i'm",  "i am")
            .replace("I've",  "I have").replace("i've",  "i have")
            .replace("I'll",  "I will").replace("i'll",  "i will")
            .replace("I'd",   "I would").replace("i'd",   "i would")
            .replace("we're", "we are").replace("We're", "We are")
            .replace("we've", "we have").replace("We've", "We have")
        )

        is_upgrade = any(p.search(text) for p in _IS_SELF_UPGRADE_PATTERNS)
        if not is_upgrade:
            return SelfUpgradeResult(False, "general", 0.0, "calmness", 0.5)

        upgrade_type = self._detect_type(text)
        hype_count   = len(_HIGH_HYPE_MARKERS.findall(text))

        # High hype markers → HYPED, otherwise EXCITED or PRIDE
        emotion    = "hype"      if hype_count >= 2 else "excitement"
        importance = min(1.0, 0.85 + 0.05 * hype_count)
        confidence = 0.85

        return SelfUpgradeResult(
            is_self_upgrade = True,
            upgrade_type    = upgrade_type,
            confidence      = confidence,
            emotion         = emotion,
            importance      = importance,
        )

    def _detect_type(self, text: str) -> str:
        """Return the primary upgrade type keyword from the text."""
        for type_name, pattern in _UPGRADE_TYPE_PATTERNS:
            if pattern.search(text):
                return type_name
        return "general"


# Global singleton
self_upgrade_detector = SelfUpgradeDetector()
