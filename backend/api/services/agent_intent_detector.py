"""
Phase 3 Agent Intent Detector.

Detects whether a voice transcript should be routed to a long-running agent
(BrowserAgent, CodingAgent, AutomationAgent, PersonalityEngine) vs the
existing fast-path tool dispatch.

This runs BEFORE the orchestrator in voice_ws.py.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Execution mode — the one place a goal's dispatch target is decided ───────
# DIRECT_TOOL:         existing intent_router/tool-registry tiers (unchanged)
# DIRECT_AGENT:        goal touches exactly one domain (browser/coding/
#                       automation) — launch that specialist directly, no
#                       Coordinator/DelegationPlanner/TaskGraph/verifier/
#                       reflection/collaboration-memory involved.
# COORDINATED_WORKFLOW: goal genuinely spans >1 domain — only then does the
#                       Coordinator's TaskGraph machinery earn its keep.
DIRECT_TOOL = "DIRECT_TOOL"
DIRECT_AGENT = "DIRECT_AGENT"
COORDINATED_WORKFLOW = "COORDINATED_WORKFLOW"


# ── Canonical domain classification ───────────────────────────────────────────
# The only place "does this goal need browser/coding/automation" is decided.
# DelegationPlanner.GoalAnalysis imports classify_domains() from here instead
# of keeping its own copy — a goal used to be classified twice, independently,
# by two different regex sets that could (and did) disagree.
_NEEDS_BROWSER_RE = re.compile(
    r"\b(research|search|browse|find|compare|book|download|apply|monitor)\b", re.IGNORECASE,
)
_NEEDS_CODING_RE = re.compile(
    r"\b(build|create|make|generate|code|develop|website|app|project|dashboard|portfolio)\b",
    re.IGNORECASE,
)
_NEEDS_AUTOMATION_RE = re.compile(
    r"\b(clean|organize|delete|remove|scan|junk|temp|cache|duplicate|large\s+files|startup)\b",
    re.IGNORECASE,
)


def classify_domains(goal: str) -> tuple[bool, bool, bool]:
    """Returns (needs_browser, needs_coding, needs_automation) for a goal."""
    return (
        bool(_NEEDS_BROWSER_RE.search(goal)),
        bool(_NEEDS_CODING_RE.search(goal)),
        bool(_NEEDS_AUTOMATION_RE.search(goal)),
    )


def _execution_mode_for(goal: str) -> str:
    needs_browser, needs_coding, needs_automation = classify_domains(goal)
    domain_count = needs_browser + needs_coding + needs_automation
    return COORDINATED_WORKFLOW if domain_count > 1 else DIRECT_AGENT


@dataclass
class AgentIntentResult:
    is_agent_command: bool
    agent_type: Optional[str] = None         # "browser" | "coding" | "automation" | "personality" | "control"
    control_action: Optional[str] = None     # "cancel" | "pause" | "resume" | "progress"
    personality_mode: Optional[str] = None  # if agent_type=="personality"
    confidence: float = 0.0
    reason: str = ""
    execution_mode: str = DIRECT_TOOL         # DIRECT_TOOL | DIRECT_AGENT | COORDINATED_WORKFLOW


# ── Control commands — highest priority ──────────────────────────────────────

_CANCEL_RE = re.compile(
    r'\b(?:cancel|stop|abort|quit|never\s+mind|forget\s+it|cancel\s+that|stop\s+that|'
    r'stop\s+the\s+(?:task|agent|research|download|browser|coding)|band\s+karo|rok\s+do)\b',
    re.IGNORECASE,
)

_PAUSE_RE = re.compile(
    r'\b(?:pause|hold\s+on|wait|ruk|ruko|hold\s+(?:it|that)|pause\s+(?:that|it|the\s+(?:task|agent)))\b',
    re.IGNORECASE,
)

_RESUME_RE = re.compile(
    r'\b(?:resume|continue|carry\s+on|proceed|jari\s+raho|chalao\s+wapas|resume\s+(?:that|it|the\s+(?:task|agent)))\b',
    re.IGNORECASE,
)

_PROGRESS_RE = re.compile(
    r'\b(?:what(?:\'?s|\s+is)\s+(?:the\s+)?(?:progress|status|update)|'
    r'how\s+(?:far|much|long)|progress\s+(?:report|update)?|'
    r'kya\s+hua|kitna\s+hua|status|update\s+me|any\s+update)\b',
    re.IGNORECASE,
)


# ── Browser agent patterns ────────────────────────────────────────────────────

_BROWSER_RESEARCH_RE = re.compile(
    r'\b(?:research|summarize|find\s+information|look\s+up|investigate|'
    r'tell\s+me\s+about|what\s+is\s+(?!the\s+time|the\s+date)|'
    r'explain|give\s+me\s+a\s+summary|latest\s+news|search\s+for\s+info|'
    r'dhundo|maloomat|research\s+karo|batao)\b',
    re.IGNORECASE,
)

_BROWSER_COMPARE_RE = re.compile(
    r'\b(?:compare|vs\b|versus|which\s+is\s+better|price\s+comparison|'
    r'cheapest|best\s+deal|review(?:s)?\s+of|ratings?\s+for)\b',
    re.IGNORECASE,
)

_BROWSER_BOOK_RE = re.compile(
    r'\b(?:book(?:\s+(?:me|a|us))*\s+(?:a\s+)?(?:cheap(?:est)?\s+)?(?:flight|hotel|ticket|room|reservation|table|restaurant)|'
    r'reserve\s+(?:a\s+)?(?:table|room|seat|flight)|'
    r'find\s+(?:me\s+)?(?:a\s+)?(?:cheap(?:est)?\s+)?flights?|cheapest\s+flight|'
    r'search\s+(?:for\s+)?flights?|show\s+(?:me\s+)?flights?|'
    r'flights?\s+(?:from|to)|hotel\s+in|book\s+(?:me\s+)?(?:a\s+)?ticket)\b',
    re.IGNORECASE,
)

_BROWSER_JOB_RE = re.compile(
    r'\b(?:apply\s+for\s+(?:this\s+)?job|job\s+application|submit\s+(?:my\s+)?(?:cv|resume)|'
    r'apply\s+to\s+(?:this\s+)?(?:position|role|opening))\b',
    re.IGNORECASE,
)

_BROWSER_DOWNLOAD_RE = re.compile(
    r'\b(?:download\s+(?:the\s+)?(?:invoice|receipt|report|file|document|pdf)|'
    r'get\s+(?:the\s+)?(?:invoice|receipt|pdf)\s+from|'
    r'save\s+(?:the\s+)?(?:invoice|receipt|report))\b',
    re.IGNORECASE,
)

_BROWSER_MONITOR_RE = re.compile(
    r'\b(?:monitor\s+(?:this\s+)?(?:website|page|price|stock)|'
    r'watch\s+(?:the\s+)?price|alert\s+me\s+when)\b',
    re.IGNORECASE,
)

# Generic "go to website and do something" — last resort browser pattern
_BROWSER_GENERIC_RE = re.compile(
    r'\b(?:go\s+to\s+(?:the\s+)?website|open\s+(?:the\s+)?website\s+and|'
    r'browse\s+to|navigate\s+to)\b.*\b(?:and\s+(?:do|find|check|see|look))\b',
    re.IGNORECASE,
)


# ── Coding agent patterns ─────────────────────────────────────────────────────

_CODING_RE = re.compile(
    r'\b(?:create\s+(?:(?:me|us|a)\s+)*(?:website|web\s+app|landing\s+page|dashboard|portfolio|'
    r'clothing\s+website|e-?commerce|admin\s+panel|react\s+app|next\.?js\s+app|'
    r'python\s+script|automation\s+script|api|flask\s+app|project)|'
    r'build\s+(?:(?:me|us|a)\s+)*(?:website|app|project|dashboard|portfolio|landing\s+page|'
    r'clothing\s+site|react\s+app)|'
    r'make\s+(?:(?:me|us|a)\s+)*(?:website|app|project|landing\s+page|dashboard|'
    r'clothing\s+website|portfolio)|'
    r'generate\s+(?:(?:me|us|a)\s+)*(?:website|project|app|code)|'
    r'code\s+(?:(?:me|a)\s+)?(?:website|app|project)|'
    r'develop\s+(?:(?:a|me)\s+)?(?:website|app|project)|'
    r'set\s+up\s+(?:a\s+)?(?:project|react|nextjs|flask|python\s+project))\b',
    re.IGNORECASE,
)


# ── Automation agent patterns ─────────────────────────────────────────────────

_AUTO_CLEAN_RE = re.compile(
    r'\b(?:clean\s+(?:only\s+|just\s+)?(?:my\s+)?(?:pc|computer|disk|system|files|temp(?:orary)?\s*files?|browser\s*cache|cache)|'
    r'clear\s+(?:my\s+)?(?:temp|cache|junk|trash|recycle)|'
    r'remove\s+(?:junk|temp|cache|old\s+files)|'
    r'free\s+up\s+(?:disk\s+)?space|'
    r'pc\s+clean(?:up)?|disk\s+cleanup|'
    r'system\s+clean(?:up)?|cleanup\s+my\s+pc)\b',
    re.IGNORECASE,
)

_AUTO_ORGANIZE_RE = re.compile(
    r'\b(?:organize\s+(?:my\s+)?(?:downloads|desktop|files|folder)|'
    r'sort\s+(?:my\s+)?(?:downloads|files|desktop)|'
    r'(?:downloads|files)\s+(?:ko\s+)?organize)\b',
    re.IGNORECASE,
)

_AUTO_DUPLICATE_RE = re.compile(
    r'\b(?:find\s+(?:duplicate|duplicate\s+files|duplicates)|'
    r'remove\s+duplicates|delete\s+duplicates|'
    r'duplicate\s+(?:files?\s+)?finder)\b',
    re.IGNORECASE,
)

_AUTO_LARGE_RE = re.compile(
    r'\b(?:find\s+large\s+files|show\s+(?:me\s+)?(?:large|big)\s+files|'
    r'what\s+(?:files?\s+)?(?:is|are)\s+taking\s+(?:up\s+)?(?:space|storage)|'
    r'storage\s+hog(?:s)?|big\s+files)\b',
    re.IGNORECASE,
)

_AUTO_STARTUP_RE = re.compile(
    r'\b(?:optimize\s+(?:startup|boot)|disable\s+startup|startup\s+apps?|'
    r'(?:make|speed\s+up)\s+(?:my\s+)?(?:boot|startup)|boot\s+(?:time|speed))\b',
    re.IGNORECASE,
)


# ── Personality engine patterns ───────────────────────────────────────────────

_PERSONALITY_RE = re.compile(
    r'\b(?:switch\s+to\s+(?P<mode1>\w+)\s+mode|'
    r'(?P<mode2>jarvis|professional|formal|friendly|casual|minimal|simple|funny|humor|'
    r'developer|dev|technical|creative|research|academic|default|normal|xyron|standard)'
    r'\s+mode(?:\s+(?:on|activate|please|enable|start))?|'
    r'(?:be|act)\s+(?:like\s+)?(?P<mode3>jarvis|professional|friendly|funny|minimal|creative)|'
    r'turn\s+on\s+(?P<mode4>\w+)\s+mode)\b',
    re.IGNORECASE,
)

_MODE_MAP = {
    "jarvis": "jarvis",
    "professional": "professional",
    "formal": "professional",
    "friendly": "friendly",
    "casual": "friendly",
    "minimal": "minimal",
    "simple": "minimal",
    "funny": "funny",
    "humor": "funny",
    "humorous": "funny",
    "developer": "developer",
    "dev": "developer",
    "technical": "developer",
    "creative": "creative",
    "research": "research",
    "academic": "research",
    "default": "default",
    "normal": "default",
    "xyron": "default",
    "standard": "default",
}


# ── Exclusion filter — phrases that look like agent commands but aren't ───────
# These are handled by existing fast-path tools

_NOT_AGENT = re.compile(
    r'\b(?:'
    r'search\s+(?:google|youtube|web|online)\s+for\b|'        # web search tool
    r'open\s+(?:chrome|firefox|edge|browser)\b|'              # app launch tool
    r'open\s+a\s+(?:new\s+)?(?:tab|window)\b|'                # browser tool
    r'open\s+(?:vs\s*code|vscode|visual\s+studio\s+code)\b|'  # app launch tool —
                                                                 # "vs" alone would
                                                                 # otherwise match
                                                                 # _BROWSER_COMPARE_RE
    r'clear\s+(?:clipboard|notifications?)\b|'                # existing tools
    r'run\s+disk\s+cleanup\b|'                                 # existing tool
    r'empty\s+recycle\s+bin\b'                                 # existing tool
    r')\b',
    re.IGNORECASE,
)


# ── Main detector ─────────────────────────────────────────────────────────────

class AgentIntentDetector:
    """Detects Phase 3 agent intents from voice transcripts."""

    def detect(self, transcript: str) -> AgentIntentResult:
        result = self._detect_raw(transcript)
        if result.agent_type in ("browser", "coding", "automation"):
            result.execution_mode = _execution_mode_for(transcript)
        elif result.is_agent_command:
            # control / personality — already dispatched directly today,
            # never through Coordinator.
            result.execution_mode = DIRECT_AGENT
        else:
            result.execution_mode = DIRECT_TOOL
        return result

    def _detect_raw(self, transcript: str) -> AgentIntentResult:
        t = transcript.strip()
        t_lower = t.lower()

        # ── 1. Control commands (highest priority — always check first) ────────
        if _CANCEL_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="control",
                control_action="cancel", confidence=0.95, reason="cancel_pattern",
            )
        if _PAUSE_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="control",
                control_action="pause", confidence=0.90, reason="pause_pattern",
            )
        if _RESUME_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="control",
                control_action="resume", confidence=0.90, reason="resume_pattern",
            )
        if _PROGRESS_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="control",
                control_action="progress", confidence=0.85, reason="progress_pattern",
            )

        # ── 2. Personality mode switch ─────────────────────────────────────────
        pm = _PERSONALITY_RE.search(t)
        if pm:
            raw_mode = (
                pm.group("mode1") or pm.group("mode2") or
                pm.group("mode3") or pm.group("mode4") or ""
            ).lower()
            mapped = _MODE_MAP.get(raw_mode)
            if mapped:
                return AgentIntentResult(
                    is_agent_command=True, agent_type="personality",
                    personality_mode=mapped, confidence=0.95, reason="personality_mode_switch",
                )

        # ── 3. Exclusion check — don't intercept fast-path commands ───────────
        if _NOT_AGENT.search(t):
            return AgentIntentResult(is_agent_command=False, reason="excluded_fast_path")

        # ── 4. Coding agent ────────────────────────────────────────────────────
        if _CODING_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="coding",
                confidence=0.90, reason="coding_pattern",
            )

        # ── 5. Automation agent ────────────────────────────────────────────────
        if _AUTO_CLEAN_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="automation",
                confidence=0.90, reason="clean_pattern",
            )
        if _AUTO_ORGANIZE_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="automation",
                confidence=0.88, reason="organize_pattern",
            )
        if _AUTO_DUPLICATE_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="automation",
                confidence=0.88, reason="duplicate_pattern",
            )
        if _AUTO_LARGE_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="automation",
                confidence=0.85, reason="large_files_pattern",
            )
        if _AUTO_STARTUP_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="automation",
                confidence=0.85, reason="startup_pattern",
            )

        # ── 6. Browser agent ───────────────────────────────────────────────────
        if _BROWSER_BOOK_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="browser",
                confidence=0.92, reason="booking_pattern",
            )
        if _BROWSER_JOB_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="browser",
                confidence=0.92, reason="job_apply_pattern",
            )
        if _BROWSER_DOWNLOAD_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="browser",
                confidence=0.88, reason="browser_download_pattern",
            )
        if _BROWSER_COMPARE_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="browser",
                confidence=0.85, reason="compare_pattern",
            )
        if _BROWSER_RESEARCH_RE.search(t):
            # Only if the transcript is longer than a simple lookup
            # "research AI agents" → yes; "what is the weather" → no (too simple)
            words = t_lower.split()
            if len(words) >= 4:
                return AgentIntentResult(
                    is_agent_command=True, agent_type="browser",
                    confidence=0.82, reason="research_pattern",
                )
        if _BROWSER_MONITOR_RE.search(t):
            return AgentIntentResult(
                is_agent_command=True, agent_type="browser",
                confidence=0.80, reason="monitor_pattern",
            )

        return AgentIntentResult(is_agent_command=False, reason="no_match")


agent_intent_detector = AgentIntentDetector()
