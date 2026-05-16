"""
Emotional intent guard — prevents emotional/conversational statements from
being misrouted to system tools.

Runs BEFORE tool execution. If input is EMOTIONAL_EVENT or CONVERSATION,
tool_name is cleared and the pipeline generates an emotional response instead.

Classification:
  TOOL_COMMAND    — "open chrome", "increase volume", "take screenshot"
  EMOTIONAL_EVENT — "I added a memory upgrade", "this bug is annoying me"
  CONVERSATION    — "how are you", "who made you"
  UNCLEAR         — falls through to tool routing (safe default)

Target: <5ms (pure regex, no imports needed).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class IntentClass(str, Enum):
    TOOL_COMMAND    = "TOOL_COMMAND"
    EMOTIONAL_EVENT = "EMOTIONAL_EVENT"
    CONVERSATION    = "CONVERSATION"
    UNCLEAR         = "UNCLEAR"


@dataclass
class GuardResult:
    intent_class: IntentClass
    confidence:   float
    reason:       str


# ── Tool command imperatives ──────────────────────────────────────────────────
# Words that, when they START a sentence, strongly indicate a tool command.

_TOOL_IMPERATIVE_STARTS = re.compile(
    r'^(open|close|start|stop|create|delete|remove|increase|decrease|raise|lower|'
    r'turn|mute|unmute|take|send|show|play|pause|resume|minimize|maximize|lock|'
    r'unlock|switch|set|move|copy|paste|search|find|launch|kill|restart|shutdown|'
    r'reboot|hibernate|sleep|wake|scroll|click|type|run|execute|go|write|'
    r'screenshot|capture|type|paste|drag|zoom|refresh|reload|install|uninstall|'
    r'enable|disable|toggle|connect|disconnect|check|scan|clear|empty|format|'
    r'rename|compress|extract)\b',
    re.IGNORECASE,
)

# ── Self-upgrade patterns — user improving Xyron ──────────────────────────────

_SELF_UPGRADE_PATTERNS: list[re.Pattern] = [
    # "I am adding/upgrading/updating/enhancing ... [in/to/for] you"
    re.compile(
        r'\b(I|we)\s+(am|are|\'m|\'re)\s+'
        r'(adding|putting|upgrading|updating|building|improving|enhancing|fixing|'
        r'patching|creating|making|evolving|training|teaching|giving|strengthening|'
        r'developing|expanding|deploying)\b'
        r'.{0,60}'
        r'\b(you|xyron|memory|voice|personality|feature|upgrade|pipeline|wake\s*word|'
        r'system|abilities|brain|intelligence|core|mind)\b',
        re.IGNORECASE | re.DOTALL,
    ),
    # "I added/upgraded/updated/enhanced ... [in/to/for] you"
    re.compile(
        r'\b(I|we)\s+'
        r'(added|upgraded|updated|built|improved|enhanced|fixed|patched|created|made|'
        r'deployed|shipped|committed|evolved|trained|taught|gave|strengthened|developed)\b'
        r'.{0,60}'
        r'\b(you|xyron|memory|voice|personality|feature|upgrade|pipeline|wake\s*word|'
        r'system|abilities|brain|intelligence|core|mind)\b',
        re.IGNORECASE | re.DOTALL,
    ),
    # "new feature/upgrade/memory [in/to/for] you"
    re.compile(
        r'\b(new\s+)?(memory\s+upgrade|feature\s+upgrade|personality\s+upgrade|'
        r'voice\s+upgrade|ui\s+upgrade|wake\s+word\s+fix|code\s+upgrade|'
        r'memory\s+system|new\s+feature|new\s+upgrade)\b'
        r'.{0,30}\b(in|to|for|into)\s+(you|xyron)\b',
        re.IGNORECASE,
    ),
    # "making you better/smarter/stronger"
    re.compile(
        r'\b(making|giving)\s+you\s+(better|smarter|stronger|faster|more\s+\w+)\b',
        re.IGNORECASE,
    ),
    # "I'm thinking of / planning to / want to / going to / considering" + upgrade verb
    re.compile(
        r'\b(I|we)\s+(\'m|am|\'re|are)\s+'
        r'(thinking|planning|going|considering|working|trying)\s+(of|on|to|about)?\s*'
        r'(upgrade|upgrading|update|updating|add|adding|build|building|fix|fixing|'
        r'improve|improving|enhance|enhancing|patch|patching|evolve|evolving|'
        r'train|training|teach|teaching|implement|implementing|create|creating|'
        r'wire|wiring|integrate|integrating|give|giving|strengthen|strengthening|'
        r'develop|developing)\b'
        r'.{0,80}'
        r'\b(you|xyron|memory|voice|personality|feature|upgrade|pipeline|ui|'
        r'emotions?|cognition|wake\s*word|language|takeover|system|abilities|'
        r'brain|intelligence|mind|core)\b',
        re.IGNORECASE | re.DOTALL,
    ),
    # "I want to / I need to" + upgrade verb + target
    re.compile(
        r'\b(I|we)\s+(want|need|plan|intend)\s+to\s+'
        r'(upgrade|update|add|build|fix|improve|enhance|patch|evolve|train|teach|'
        r'implement|create|wire|integrate|give|strengthen|develop)\b'
        r'.{0,80}'
        r'\b(you|xyron|your\s+\w+|memory|voice|personality|emotions?|cognition|'
        r'ui|feature|upgrade|pipeline|language|wake\s*word|system|abilities)\b',
        re.IGNORECASE | re.DOTALL,
    ),
    # "I upgraded/updated/enhanced your ..." — possessive form
    re.compile(
        r'\b(I|we)\s+(upgraded|updated|enhanced|improved|fixed|patched|built|'
        r'added\s+to|made|evolved|trained|taught)\s+your\s+'
        r'(memory|voice|personality|ui|code|pipeline|system|wake[\s-]*(word|up)|'
        r'takeover|dominant|cognition|emotions?|abilities|brain|intelligence)\b',
        re.IGNORECASE,
    ),
    # "I made your X better/way better/smarter/faster"
    re.compile(
        r'\b(I|we)\s+(made|got)\s+your\s+\w[\w\s]{0,25}(better|smarter|faster|stronger)\b',
        re.IGNORECASE,
    ),
    # "I fixed your [X] issue/problem/bug/detection" — component fix pattern
    re.compile(
        r'\b(I|we)\s+(fixed|resolved|repaired|sorted)\s+(your|the)\s+[\w][\w\s-]{0,25}'
        r'\s*(issue|problem|bug|error|detection|thing|glitch|crash)\b',
        re.IGNORECASE,
    ),
]

# ── Frustration patterns ──────────────────────────────────────────────────────

_FRUSTRATION_PATTERNS: list[re.Pattern] = [
    re.compile(r'\b(bug|error|crash|issue|problem)\s+(is|are)\s+(annoying|frustrating|bothering|killing)\b', re.IGNORECASE),
    re.compile(r'\bthis\s+(is|\'s)\s+(so\s+)?(annoying|frustrating|infuriating|irritating|terrible)\b', re.IGNORECASE),
    re.compile(r'\bstill\s+(doesn\'t|don\'t|won\'t|isn\'t|can\'t)\s+work\b', re.IGNORECASE),
    re.compile(r'\bsame\s+(bug|issue|error|problem|crash)\s+(again|as\s+before)\b', re.IGNORECASE),
    re.compile(r'\bi\'m\s+(tired|sick)\s+of\s+this\b', re.IGNORECASE),
    re.compile(r'\bwhy\s+(won\'t|doesn\'t|can\'t|isn\'t)\s+this\s+work\b', re.IGNORECASE),
    re.compile(r'\bkeeps?\s+(breaking|crashing|failing|erroring)\b', re.IGNORECASE),
    re.compile(r'\bbroken\s+again\b', re.IGNORECASE),
    re.compile(r'\bannoying\s+me\b', re.IGNORECASE),
    re.compile(r'\b(been|was|have\s+been)\s+(fighting|struggling|stuck\s+on|dealing\s+with)\s+(this|it|the\s+same)\b', re.IGNORECASE),
    re.compile(r'\b(spent|wasted|been\s+at\s+this)\s+(an?\s+)?(hour|hours|day|days|long\s+time|while)\b', re.IGNORECASE),
    re.compile(r'\b(can\'t|cannot)\s+(figure|get)\s+(this|it)\s+(out|working)\b', re.IGNORECASE),
]

# ── Achievement/celebration patterns ─────────────────────────────────────────

_ACHIEVEMENT_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bfinally\s+(working|fixed|done|complete|works|shipped|deployed)\b', re.IGNORECASE),
    re.compile(r'\bit\s+(finally|now)\s+(works|is\s+working|runs)\b', re.IGNORECASE),
    re.compile(r'\bi\s+finally\s+(fixed|got|made|solved|finished)\b', re.IGNORECASE),
    re.compile(r'\bjust\s+(shipped|deployed|merged|pushed|finished|completed)\b', re.IGNORECASE),
]

# ── Self-introduction / audience mode patterns ────────────────────────────────
# Checked BEFORE conversation patterns so "who are you" triggers intro, not generic conversation.
# NOTE: self-upgrade check runs at step 1 — "I'm upgrading your memory" never reaches this list.

_SELF_INTRO_PATTERNS: list[re.Pattern] = [
    # Direct intro requests
    re.compile(r'\bexplain\s+yourself\b', re.IGNORECASE),
    re.compile(r'\bintroduce\s+yourself\b', re.IGNORECASE),
    re.compile(r'\btell\s+(my\s+)?(?:audience|viewers?|followers?|friends?|team|everyone)\s+(?:about\s+)?(?:who\s+)?you\b', re.IGNORECASE),
    re.compile(r'\bwho\s+(?:exactly\s+)?are\s+you\b', re.IGNORECASE),
    re.compile(r'\bwhat\s+(?:exactly\s+)?are\s+you\b', re.IGNORECASE),
    # Recording / presentation context
    re.compile(r"\bI(?:'m|\s+am)\s+recording\b.{0,60}\b(?:you|xyron|yourself)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r'\bfor\s+(?:my\s+)?(?:audience|viewers?|followers?|video|stream|podcast|presentation|demo)\b.{0,40}\b(?:who|what|explain|introduce|about)\b', re.IGNORECASE | re.DOTALL),
    re.compile(r'\b(?:introduce|present)\s+(?:yourself|you)\s+to\b', re.IGNORECASE),
    # "give them/me/us your intro / give a quick/full/cinematic intro"
    re.compile(r'\b(?:give|do)\s+(?:them\s+|me\s+|us\s+|my\s+audience\s+)?(?:your\s+)?(?:a\s+)?(?:\w+\s+){0,2}intro(?:duction)?\b', re.IGNORECASE),
    # "tell everyone what you are / who you are"
    re.compile(r'\btell\s+(?:everyone|them|us|the\s+audience)\s+(?:what|who)\s+you\s+are\b', re.IGNORECASE),
    # "what should my audience know about you"
    re.compile(r'\bwhat\s+should\s+(?:my\s+)?(?:audience|viewers?|they|people)\s+know\s+about\s+you\b', re.IGNORECASE),
]

# ── Conversation patterns ─────────────────────────────────────────────────────

_CONVERSATION_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bhow\s+are\s+you\b', re.IGNORECASE),
    re.compile(r'\bwho\s+(?:made|built|created)\s+you\b', re.IGNORECASE),
    re.compile(r'\bwhat\s+can\s+you\s+do\b', re.IGNORECASE),
    re.compile(r'\bdo\s+you\s+know\s+who\s+i\s+am\b', re.IGNORECASE),
    re.compile(r'\btell\s+me\s+about\s+yourself\b', re.IGNORECASE),
    re.compile(r'\bhow\s+do\s+you\s+feel\b', re.IGNORECASE),
    re.compile(r'\bare\s+you\s+(there|awake|ready|online)\b', re.IGNORECASE),
    re.compile(r'\bwhat\s+is\s+your\s+name\b', re.IGNORECASE),
    re.compile(r'\bwho\s+are\s+you\b', re.IGNORECASE),
    # Praise/appreciation directed at Xyron
    re.compile(r'^(good\s+work|great\s+job|nice\s+work|well\s+done|nice\s+job)[.!]?\s*(today|tonight|today)?[.!]?\s*$', re.IGNORECASE),
]

# ── Ambiguous "I" statements that are NOT self-upgrades ──────────────────────
# These start with "I" but are tool commands ("I want to", "I need to")

_I_WANT_TO_TOOL = re.compile(
    r'^(I\s+)?(want|need|would\s+like|can\s+you|please)\s+(to\s+)?(open|close|create|delete|'
    r'increase|decrease|take|send|show|play|pause|set|move|copy|search)\b',
    re.IGNORECASE,
)


# ── Fuzzy upgrade recovery sets ──────────────────────────────────────────────
# Used when Whisper noise breaks regex patterns but emotional keywords survive.

_FUZZY_UPGRADE_VERBS = frozenset({
    # Present / progressive
    "upgrading", "updating", "improving", "enhancing", "fixing", "patching",
    "evolving", "training", "teaching", "adding", "building", "making",
    "creating", "deploying", "shipping", "implementing", "wiring", "giving",
    "strengthening", "developing", "expanding",
    # Base forms (catches "update your memory", "enhance your voice")
    "upgrade", "update", "improve", "enhance", "fix", "patch", "evolve",
    "train", "teach", "add", "build", "make", "create", "deploy", "ship",
    "implement", "wire", "give", "strengthen", "develop", "expand",
    # Past tense
    "upgraded", "updated", "improved", "enhanced", "fixed", "patched",
    "evolved", "trained", "taught", "added", "built", "made", "created",
    "deployed", "shipped", "gave", "strengthened", "developed",
})

_FUZZY_UPGRADE_TARGETS = frozenset({
    "your memory", "your voice", "your wake", "your personality",
    "your system", "your pipeline", "your cognition", "your ui",
    "your emotions", "your takeover", "your language", "your abilities",
    "your brain", "your intelligence", "your core", "your mind",
    "your code", "your responses", "your behavior",
    "in you", "for you", "you better", "you smarter", "you faster",
    "you stronger", "you more", "feature in you", "emotions in you",
    "upgrade in you", "memory in you",
})

# ── Entity+verb detection — catches Whisper phonetic variations ───────────────
# "updating your memory", "making you smarter", "patching your wake system"
# Two-part check: any upgrade verb word + any "your X" entity phrase in sentence.

_ENTITY_VERBS = _FUZZY_UPGRADE_VERBS  # same set — reuse

_ENTITY_TARGET_PHRASES = frozenset({
    "your memory", "your voice", "your wake", "your personality",
    "your system", "your pipeline", "your cognition", "your ui",
    "your emotions", "your takeover", "your language", "your abilities",
    "your brain", "your intelligence", "your core", "your mind",
    "your code", "your responses", "your behavior", "your features",
    "your responses", "your reaction", "your training",
})


class EmotionalIntentGuard:
    """Fast regex-based emotional intent classifier."""

    def classify(
        self,
        normalized_text: str,
        raw_transcript:  Optional[str] = None,
    ) -> GuardResult:
        """
        Classify intent of a voice input.

        Args:
            normalized_text: Post-normalization text (used for tool routing decisions).
            raw_transcript:  Original transcript (used for emotional pattern matching).

        Returns:
            GuardResult with intent_class, confidence, and reason.
        """
        text = (raw_transcript or normalized_text).strip()
        # Expand contractions so patterns that split on \s+ work with "I'm", "I've", etc.
        text = (text
            .replace("I'm",  "I am").replace("i'm",  "i am")
            .replace("I've",  "I have").replace("i've",  "i have")
            .replace("I'll",  "I will").replace("i'll",  "i will")
            .replace("I'd",   "I would").replace("i'd",   "i would")
            .replace("we're", "we are").replace("We're", "We are")
            .replace("we've", "we have").replace("We've", "We have")
        )
        lower = text.lower()
        norm  = normalized_text.strip()

        # ── 1. Self-upgrade — highest priority emotional event ────────────────
        for pat in _SELF_UPGRADE_PATTERNS:
            if pat.search(text):
                return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.92, "self_upgrade_pattern")

        # ── 2. Frustration — also high priority ───────────────────────────────
        for pat in _FRUSTRATION_PATTERNS:
            if pat.search(text):
                return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.88, "frustration_pattern")

        # ── 3. Achievement/celebration ────────────────────────────────────────
        for pat in _ACHIEVEMENT_PATTERNS:
            if pat.search(text):
                return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.82, "achievement_pattern")

        # ── 3.5. Self-introduction / audience mode ────────────────────────────
        # Runs before tool check so "who are you" → intro, not unclear/tool.
        for pat in _SELF_INTRO_PATTERNS:
            if pat.search(text):
                return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.95, "self_introduction_pattern")

        # ── 4. Clear tool command — starts with imperative verb ───────────────
        if _TOOL_IMPERATIVE_STARTS.match(norm):
            return GuardResult(IntentClass.TOOL_COMMAND, 0.90, "imperative_verb_start")

        # ── 5. "I want to <verb>" still maps to tool ─────────────────────────
        if _I_WANT_TO_TOOL.match(text):
            return GuardResult(IntentClass.TOOL_COMMAND, 0.80, "i_want_to_tool")

        # ── 6. Conversation ───────────────────────────────────────────────────
        for pat in _CONVERSATION_PATTERNS:
            if pat.search(text):
                return GuardResult(IntentClass.CONVERSATION, 0.80, "conversation_pattern")

        # ── 7. Entity+verb detection ─────────────────────────────────────────
        # Primary semantic catch: any "your [component]" phrase + any upgrade verb.
        # Catches Whisper phonetic variants: "updating your memory", "improving your voice",
        # "making you smarter", "patching your wake system", etc.
        _lower_ev = text.lower()
        _entity_match = next((t for t in _ENTITY_TARGET_PHRASES if t in _lower_ev), None)
        if _entity_match:
            _words_ev   = set(re.findall(r'\b\w+\b', _lower_ev))
            _verb_match = next((v for v in _ENTITY_VERBS if v in _words_ev), None)
            if _verb_match:
                logger.info("[EMOTION_PATTERN] matched entity=%r verb=%s", _entity_match, _verb_match)
                return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.88, "self_upgrade_pattern")

        # ── 8. Fuzzy self-upgrade recovery ───────────────────────────────────
        # Secondary catch: co-occurrence of verb + broader target tokens.
        # Fires when Whisper noise breaks the entity phrase but verb + target word survive.
        _lower_f = text.lower()
        _has_verb   = any(v in _lower_f for v in _FUZZY_UPGRADE_VERBS)
        _has_target = any(t in _lower_f for t in _FUZZY_UPGRADE_TARGETS)
        if _has_verb and _has_target:
            logger.info("[EMOTION_RECOVERY] recovered=true reason=fuzzy_self_upgrade")
            return GuardResult(IntentClass.EMOTIONAL_EVENT, 0.78, "self_upgrade_pattern")

        # ── 9. Default: unclear — let tool router decide ──────────────────────
        return GuardResult(IntentClass.UNCLEAR, 0.50, "no_strong_signal")


# Global singleton
emotional_intent_guard = EmotionalIntentGuard()
