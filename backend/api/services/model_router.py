"""
Intelligent model router — scores complexity, reads conversation context,
then picks the cheapest model that will produce a good answer.

Decision logic:
  1. tool_matched              → local         (0 API calls, instant)
  2. hard complexity signal    → gpt-4o        (explain, write code, analyze…)
  3. medium signal + >5 words  → gpt-4o        (why, difference, comparison…)
  4. follow-up in 4o session   → gpt-4o        (momentum — stay on good model)
  5. local-tool pattern        → local
  6. context-adjusted score ≥ 0.55 → gpt-4o
  7. default                   → gpt-4o-mini

score_complexity() is used for the context-adjusted path and for the
response_validator to decide whether to retry a mini answer with 4o.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

ModelChoice = Literal["local", "gpt-4o-mini", "gpt-4o", "offline"]

# ── Signal patterns ───────────────────────────────────────────────────────────

# Hard signals → ALWAYS route to gpt-4o (complex reasoning / generation)
_HARD_SIGNALS = re.compile(
    r'\b(?:'
    r'explain|analyz[ei]|compar[ei]|contras[ti]|evaluat[ei]|critique'
    r'|debug|refactor|implement|architect|optimiz[ei]'
    r'|write\s+(?:a\s+|an\s+|me\s+|some\s+)?(?:\w+\s+){0,4}'
    r'(?:essay|story|poem|code|script|function|program|email|report|letter'
    r'|scraper|bot|tool|template|plan|summary|proposal|algorithm|parser)'
    r'|write\s+(?:a\s+|an\s+)?(?:python|javascript|typescript|bash|sql|rust|go|java|c\+\+)\b'
    r'|pros\s+and\s+cons|advantages?\s+and\s+disadvantages?'
    r'|step[\s\-]by[\s\-]step|in\s+detail|thoroughly|in\s+depth|comprehensive'
    r'|translate\s+(?:this|the|from)|paraphrase|rewrite\s+(?:this|the)'
    r'|summarize|summarise'
    r')\b',
    re.IGNORECASE,
)

# Medium signals → gpt-4o when combined with meaningful word count (>5 words)
_MEDIUM_SIGNALS = re.compile(
    r'\b(?:'
    r'why\s+(?:does|is|are|do|did|would|should|can|could)'
    r'|how\s+does\s+.{0,40}\s+work'
    r'|what\s+is\s+the\s+(?:difference|reason|cause|effect|impact|relationship|purpose|role)'
    r'|help\s+me\s+(?:understand|learn|figure\s+out|decide|choose|build|create)'
    r'|what\s+(?:should|would|could)\s+(?:i|we|you)'
    r'|can\s+you\s+(?:explain|help\s+me|suggest|recommend|show\s+me\s+how|walk\s+me)'
    r'|tell\s+me\s+(?:about|how|why|what)'
    r'|give\s+me\s+(?:a\s+)?(?:detailed|full|complete|thorough)'
    r')\b',
    re.IGNORECASE,
)

# Follow-up patterns — short continuations of a previous complex exchange
_FOLLOWUP_RE = re.compile(
    r'^(?:what\s+else|tell\s+me\s+more|go\s+on|continue|more\s+(?:on|about)'
    r'|can\s+you\s+(?:elaborate|expand|give\s+me\s+more|be\s+more\s+specific)'
    r'|and\s+(?:what|how|why|also)'
    r'|anything\s+else|what\s+about\s+that|how\s+about\s+that'
    r')\b',
    re.IGNORECASE,
)

# Multi-step connectors — user is chaining requests
_MULTISTEP_RE = re.compile(
    r'\b(?:and\s+(?:then|also|after|additionally)|furthermore|moreover'
    r'|first\s+.{0,50}\s+then|step\s+\d+|finally|lastly|in\s+addition)\b',
    re.IGNORECASE,
)

# Technical vocabulary density
_TECH_TERM_RE = re.compile(
    r'\b(?:[a-z]{11,}'          # very long words (likely technical)
    r'|api|sql|json|xml|http[s]?|regex|algorithm|database|recursion'
    r'|neural|quantum|blockchain|cryptocurrency|gradient|epoch'
    r'|async|thread|concurrent|distributed|microservice|kubernetes'
    r'|machine\s+learning|deep\s+learning|transformer|large\s+language'
    r'|photosynthesis|mitochondria|thermodynamics|electromagnetic'
    r')\b',
    re.IGNORECASE,
)

# Local-tool invocations — never need an LLM
_LOCAL_RE = re.compile(
    r'\b(?:'
    r'open|launch|start|close|quit|exit|kill'
    r'|volume|brightness|mute|unmute|screenshot'
    r'|battery|charging|wifi|bluetooth'
    r'|shutdown|restart|sleep|hibernate|lock'
    r'|time|date|today|day|clock'
    r'|downloads?|documents?|desktop|pictures?|music|videos?'
    r'|create\s+folder|new\s+folder|make\s+folder|delete\s+file'
    r'|type|click|scroll|press|hotkey|minimize|maximize'
    r'|cpu\s+usage|disk\s+usage|system\s+health|uptime'
    r'|process|task\s+manager'
    r')\b',
    re.IGNORECASE,
)

_SCORE_THRESHOLD = 0.55   # context-adjusted score above this → gpt-4o


# ── Context dataclass ─────────────────────────────────────────────────────────

@dataclass
class RouteContext:
    """Conversation context passed into select_model()."""
    last_model:    Optional[str] = None
    conv_depth:    int           = 0
    recent_scores: list[float]   = field(default_factory=list)
    last_intent:   Optional[str] = None

    @property
    def avg_recent_score(self) -> float:
        if not self.recent_scores:
            return 0.0
        return sum(self.recent_scores) / len(self.recent_scores)

    @classmethod
    def from_memory(cls, session_id: str) -> "RouteContext":
        try:
            from api.services.memory_service import memory_service
            return memory_service.get_routing_context(session_id)
        except Exception:
            return cls()


# ── Complexity scorer ─────────────────────────────────────────────────────────

def score_complexity(text: str) -> float:
    """
    Score request complexity on 0.0 → 1.0.

    Used by:
    - select_model() for the context-adjusted path
    - response_validator to decide whether to retry with gpt-4o
    - memory_service to build user complexity profile

    Breakdown:
      hard signal match    0.40 each (capped 0.40)
      medium signal match  0.20 each (capped 0.25)
      word count           up to 0.20 (linear to 40 words)
      tech term density    up to 0.15
      multi-step markers   up to 0.10
    """
    if not text or not text.strip():
        return 0.0

    s     = text.strip()
    words = s.split()
    wc    = len(words)
    score = 0.0

    # 1. Hard signals
    score += min(len(_HARD_SIGNALS.findall(s)) * 0.40, 0.40)

    # 2. Medium signals
    score += min(len(_MEDIUM_SIGNALS.findall(s)) * 0.20, 0.25)

    # 3. Word count (up to 0.20)
    score += min(wc / 40.0, 1.0) * 0.20

    # 4. Technical terms (up to 0.15)
    score += min(len(_TECH_TERM_RE.findall(s)) * 0.05, 0.15)

    # 5. Multi-step (up to 0.10)
    score += min(len(_MULTISTEP_RE.findall(s)) * 0.05, 0.10)

    return round(min(score, 1.0), 3)


# ── Main router ───────────────────────────────────────────────────────────────

def select_model(
    text:         str,
    context:      Optional[RouteContext] = None,
    tool_matched: bool = False,
) -> ModelChoice:
    """
    Return the cheapest model that will answer this request well.

    Decision order (fast-fail, top wins):
      1. tool_matched          → local
      2. hard signal detected  → gpt-4o
      3. medium + >5 words     → gpt-4o
      4. follow-up in 4o conv  → gpt-4o  (momentum)
      5. local-tool pattern    → local
      6. context-adjusted score ≥ 0.55 → gpt-4o
      7. fallback              → gpt-4o-mini
    """
    if tool_matched:
        return "local"

    s = text.strip()
    if not s:
        return "gpt-4o-mini"

    # ── Step 2: Hard complexity signal → always 4o ────────────────────────────
    if _HARD_SIGNALS.search(s):
        return "gpt-4o"

    # ── Step 3: Medium signal + meaningful length ─────────────────────────────
    if _MEDIUM_SIGNALS.search(s) and len(s.split()) > 5:
        return "gpt-4o"

    ctx = context or RouteContext()

    # ── Step 4: Follow-up in an active gpt-4o conversation ───────────────────
    if ctx.last_model == "gpt-4o" and ctx.conv_depth >= 2 and _FOLLOWUP_RE.search(s):
        return "gpt-4o"

    # ── Step 5: Pure local-tool invocation ───────────────────────────────────
    base_score = score_complexity(s)
    if base_score < 0.12 and _LOCAL_RE.search(s):
        return "local"

    # ── Step 6: Context-adjusted score ───────────────────────────────────────
    adjusted = base_score

    # Momentum: previous 4o response and this has some substance
    if ctx.last_model == "gpt-4o" and base_score > 0.20:
        adjusted += 0.15

    # Deep conversation → questions tend to get harder
    if ctx.conv_depth > 5:
        adjusted += 0.08

    # User profile: habitually complex questions
    if ctx.avg_recent_score > 0.50:
        adjusted += 0.10

    if round(min(adjusted, 1.0), 3) >= _SCORE_THRESHOLD:
        return "gpt-4o"

    return "gpt-4o-mini"


def describe(
    text:         str,
    context:      Optional[RouteContext] = None,
    tool_matched: bool = False,
) -> str:
    """Human-readable routing explanation for logs / debug endpoint."""
    score  = score_complexity(text)
    choice = select_model(text, context, tool_matched)
    depth  = context.conv_depth if context else 0
    last   = context.last_model if context else None
    return f"{choice} | complexity={score:.2f} depth={depth} last={last}"


# ── Autonomy placeholder ──────────────────────────────────────────────────────

def decide_autonomous_action(context: RouteContext) -> Optional[str]:
    """
    Stub for future proactive behaviour.

    Will detect patterns (battery always checked at 9am, morning briefing, etc.)
    and surface suggestions before the user asks.

    Returns a suggested action string, or None.
    """
    # TODO: query episodic_memory for recurring tool patterns by hour
    return None
