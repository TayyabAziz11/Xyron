"""
Semantic Understanding Layer — 3-tier intent parsing.

Tier 1  Fast rules  — regex + keyword, <1 ms
Tier 2  Embedding   — nomic-embed-text via Ollama + ChromaDB, ~20 ms
Tier 3  LLM judge   — llama3.2:3b JSON judgment, only for low-confidence cases

Output: SemanticFrame
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── SemanticFrame ─────────────────────────────────────────────────────────────

@dataclass
class SemanticFrame:
    route:                str                  # "tool|agent|conversation|emotional|intro|clarify"
    intent:               str                  # e.g. "open_app"
    target:               str                  # e.g. "system_agent"
    entities:             dict[str, Any]       = field(default_factory=dict)
    emotion_hint:         str                  = "neutral"
    confidence:           float                = 1.0
    requires_confirmation: bool               = False
    reason:               str                  = ""
    tier:                 int                  = 1   # which tier resolved it


# ── Tier 1: Fast rules ────────────────────────────────────────────────────────

_XYRON_VARIANTS = re.compile(
    r'\b(xyron|zairon|ziron|xeron|zyron|kyron|xyron\'?s?)\b', re.IGNORECASE
)

# (pattern, route, intent, target, emotion_hint, confidence)
_FAST_RULES: list[tuple[re.Pattern, str, str, str, str, float]] = [

    # Self-intro — audience (must come before generic intro to avoid shadowing)
    (re.compile(r'\b(audience|viewers?|followers?|stream|demo|video|recording|presentation)\b', re.IGNORECASE),
     "intro", "intro_audience", "self_intro", "confident", 0.93),

    # Self-intro — catch "explain yourself [to X]" generically
    (re.compile(r'\b(explain yourself|introduce yourself|who are you|what are you|your name|aap kaun ho|aap kaun hain|tum kya ho|kaun hai|batao apne|xyron.*explain|zairon.*explain|xyron.*who|zairon.*who)\b', re.IGNORECASE),
     "intro", "intro_short", "self_intro", "neutral", 0.95),

    (re.compile(r'\b(technical|architecture|how.*work|under.*hood|tech stack|made of)\b', re.IGNORECASE),
     "intro", "intro_technical", "self_intro", "neutral", 0.90),

    # Self-upgrade — must come before memory_query to win on "upgrading your memory"
    # Matches both root forms and gerunds: upgrade/upgrading, improve/improving, etc.
    # Also catches indirect phrasing: "thinking of upgrading", "considering improving"
    (re.compile(
        r'\b(upgrading|upgrade|improving|improve|enhancing|enhance|make.*better|'
        r'boost(ing)?|smarter|more capable|better memory|'
        r'upgrade.*memory|upgrading.*memory|memory upgrade|'
        r'thinking\s+of\s+upgrad|thinking\s+about\s+upgrad|'
        r'considering\s+upgrad|planning\s+to\s+upgrad|'
        r'want\s+to\s+upgrad|going\s+to\s+upgrad)\b',
        re.IGNORECASE,
    ),
     "emotional", "self_upgrade", "emotion_agent", "warm_surprise", 0.90),

    # Takeover deactivate — MUST be before activate (deactivate phrases also contain "takeover")
    # and before open_app ("start/stop" catch-alls)
    (re.compile(
        r'\b('
        r'stop\s+take\s*over'
        r'|exit\s+take\s*over'
        r'|disable\s+take\s*over'
        r'|deactivate\s+take\s*over'
        r'|leave\s+take\s*over(?:\s+mode)?'
        r'|give\s+control\s+back'
        r'|hand\s+back\s+control'
        r'|release\s+control'
        r'|stand\s*down'
        r')\b',
        re.IGNORECASE,
    ), "agent", "deactivate_takeover", "automation_agent", "focused", 0.95),

    # Takeover activate — all phrases, Tier-1 conf=0.95
    # Must be before open_app so "start takeover" isn't caught by generic start+\w+ rule
    (re.compile(
        r'\b('
        r'take\s*over'
        r'|start\s+take\s*over'
        r'|enter\s+take\s*over'
        r'|activate\s+take\s*over'
        r'|enable\s+take\s*over'
        r'|take\s*over\s+mode'
        r'|drive.*vs\s*code'
        r'|autonomous\s+mode'
        r'|you\s+take.*wheel'
        r'|takeover\s+shuru\s+karo'
        r'|control\s+le\s+lo'
        r'|system\s+take\s*over'
        r')\b',
        re.IGNORECASE,
    ), "agent", "takeover_mode", "automation_agent", "focused", 0.95),

    # App launching — English + Roman Urdu
    (re.compile(r'\b(open|launch|start|kholo|chalaao|chalao|banda karo|band karo)\b.{1,30}\b(chrome|firefox|vs code|visual studio|spotify|discord|notepad|explorer|vlc|slack|terminal|browser|editor)\b', re.IGNORECASE),
     "tool", "open_app", "system_agent", "neutral", 0.95),

    # "chrome kholo", "spotify kholo" — app name BEFORE kholo
    (re.compile(r'\b(chrome|firefox|spotify|discord|vlc|notepad|vs\s*code|terminal|explorer|slack)\b.{0,10}\b(kholo|chalaao|chalao|open karo)\b', re.IGNORECASE),
     "tool", "open_app", "system_agent", "neutral", 0.93),

    (re.compile(r'\b(open|launch|start|kholo)\b\s+\w+', re.IGNORECASE),
     "tool", "open_app", "system_agent", "neutral", 0.80),

    # File operations
    (re.compile(r'\b(delete|remove|trash|erase|hatao)\b.{1,40}\b(file|folder|directory|folder|document)\b', re.IGNORECASE),
     "tool", "file_action", "system_agent", "neutral", 0.90),

    (re.compile(r'\b(create|make|banao|new)\b.{1,20}\b(folder|directory|file)\b', re.IGNORECASE),
     "tool", "file_action", "system_agent", "neutral", 0.90),

    (re.compile(r'\b(move|copy|rename|cut|paste)\b.{1,30}\b(file|folder|to)\b', re.IGNORECASE),
     "tool", "file_action", "system_agent", "neutral", 0.88),

    # System status
    (re.compile(r'\b(battery|cpu|ram|memory usage|disk space|system health|processes|system status)\b', re.IGNORECASE),
     "tool", "system_status", "system_agent", "neutral", 0.90),

    (re.compile(r'\b(what time|what.*date|current time)\b', re.IGNORECASE),
     "tool", "system_status", "system_agent", "neutral", 0.92),

    # Prepare workspace — multi-turn folder creation (must be before work_mode)
    (re.compile(
        r'\b(prepare|setup|set\s+up|ready)\b.{0,15}\b(workspace|work\s+space)\b'
        r'|\bworkspace\b.{0,10}\b(ready|prepare|setup|tayyar)\b'
        r'|\b(coding\s+setup\s+ready|workspace\s+tayyar|kaam\s+jagah|work\s+jagah)\b',
        re.IGNORECASE,
    ),
     "agent", "prepare_workspace", "automation_agent", "focused", 0.95),

    # Work/chill/home modes
    (re.compile(r'\b(work mode|start work|prepare.*work|kaam.*mode|coding mode|focus mode|dev.*setup)\b', re.IGNORECASE),
     "agent", "work_mode", "automation_agent", "focused", 0.92),

    (re.compile(r'\b(chill mode|relax|entertainment|free time|lofi|I.?m done working)\b', re.IGNORECASE),
     "agent", "chill_mode", "automation_agent", "relaxed", 0.88),

    (re.compile(r'\b(home mode|I.?m home|just got home|ghar|evening mode)\b', re.IGNORECASE),
     "agent", "home_mode", "automation_agent", "relaxed", 0.88),

    # Capabilities
    (re.compile(r'\b(what can you do|your capabilities|list.*features|what.*capable|kya kar sakte)\b', re.IGNORECASE),
     "conversation", "explain_capability", "brain", "neutral", 0.92),

    # Future desires
    (re.compile(r'\b(what do you want|your.*goal|what.*dream|aage kya chahiye|what.*next upgrade)\b', re.IGNORECASE),
     "emotional", "ask_future_desire", "emotion_agent", "ambitious", 0.88),

    # Memory queries
    (re.compile(r'\b(remember|memory|recall|what.*last time|pichli baat|yaad hai)\b', re.IGNORECASE),
     "agent", "memory_query", "memory_agent", "neutral", 0.82),

    # Screen help
    (re.compile(r'\b(screenshot|what.*screen|screen.*error|read.*screen|active window|kya hai.*screen)\b', re.IGNORECASE),
     "agent", "screen_help", "screen_agent", "neutral", 0.88),

    # Frustration — broad emotional distress signals (English + Roman Urdu)
    (re.compile(r'\b(killing me|so frustrated|nothing.*work|everything.*broken|losing.*mind|so annoying|annoying me|kuch kaam nahi|stuck|can\'?t figure|been trying|hours on this|driving me|ughhh?|ugh|slow hai|lag.{0,5}hai|yeh system|yeh app|crash.{0,5}hai|kaam nahi kar|nahi chal)\b', re.IGNORECASE),
     "emotional", "frustration", "emotion_agent", "empathy", 0.88),

    # Dev help
    (re.compile(r'\b(debug|fix.*bug|error.*code|write.*function|refactor|explain.*code|test likhne|code.*review)\b', re.IGNORECASE),
     "agent", "dev_help", "dev_agent", "focused", 0.88),

    # Delete confirmation guard
    (re.compile(r'\b(delete|remove|erase|permanently)\b.{1,50}\b(all|everything|whole|entire)\b', re.IGNORECASE),
     "tool", "file_action", "system_agent", "neutral", 0.85),
]

def _apply_fast_rules(text: str) -> Optional[SemanticFrame]:
    # Normalise Xyron variants first
    normalised = _XYRON_VARIANTS.sub("xyron", text)

    for pattern, route, intent, target, emotion_hint, conf in _FAST_RULES:
        if pattern.search(normalised):
            requires_confirm = intent == "file_action" and any(
                w in normalised.lower() for w in ("delete", "remove", "erase", "hatao")
            )
            return SemanticFrame(
                route=route, intent=intent, target=target,
                emotion_hint=emotion_hint, confidence=conf,
                requires_confirmation=requires_confirm,
                reason="fast_rule", tier=1,
            )
    return None


# ── Tier 2: Embedding similarity ──────────────────────────────────────────────

_chroma_client = None
_chroma_lock   = threading.Lock()
_collection    = None


def _get_collection():
    global _chroma_client, _collection
    with _chroma_lock:
        if _collection is not None:
            return _collection
        try:
            import chromadb
            from pathlib import Path
            db_path = Path(__file__).parent.parent / "data" / "chroma"
            db_path.mkdir(parents=True, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=str(db_path))
            _collection = _chroma_client.get_or_create_collection(
                name="brain_intents",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("[SEMANTIC] ChromaDB collection loaded (%d docs)", _collection.count())
            return _collection
        except Exception as exc:
            logger.warning("[SEMANTIC] ChromaDB unavailable: %s", exc)
            return None


def _embed_ollama(text: str) -> Optional[list[float]]:
    try:
        import urllib.request
        payload = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("embedding")
    except Exception as exc:
        logger.debug("[SEMANTIC] ollama embed failed: %s", exc)
        return None


def _query_embedding(text: str) -> Optional[SemanticFrame]:
    col = _get_collection()
    if col is None or col.count() == 0:
        return None

    vec = _embed_ollama(text)
    if vec is None:
        return None

    try:
        results = col.query(query_embeddings=[vec], n_results=3, include=["documents", "metadatas", "distances"])
        if not results["ids"][0]:
            return None

        top_dist     = results["distances"][0][0]
        top_meta     = results["metadatas"][0][0]
        top_conf     = 1.0 - min(top_dist, 1.0)

        if top_conf < 0.60:
            return None

        requires_confirm = top_meta.get("intent") == "file_action" and any(
            w in text.lower() for w in ("delete", "remove", "erase")
        )
        return SemanticFrame(
            route=top_meta.get("route", "conversation"),
            intent=top_meta.get("intent", "unknown"),
            target=top_meta.get("target", "brain"),
            emotion_hint=top_meta.get("emotion_hint", "neutral"),
            confidence=round(top_conf, 3),
            requires_confirmation=requires_confirm,
            reason="embedding_similarity",
            tier=2,
        )
    except Exception as exc:
        logger.debug("[SEMANTIC] chroma query failed: %s", exc)
        return None


# ── Tier 3: LLM judge ─────────────────────────────────────────────────────────

_LLM_SYSTEM = """You are an intent classifier for Xyron, a local AI assistant.
Given a user utterance, respond ONLY with a JSON object like:
{
  "route": "tool|agent|conversation|emotional|intro|clarify",
  "intent": "<one of: self_upgrade|frustration|intro_audience|intro_technical|intro_short|open_app|file_action|system_status|dev_help|prepare_workspace|work_mode|chill_mode|takeover_mode|deactivate_takeover|home_mode|explain_capability|ask_future_desire|automation_request|memory_query|screen_help|unknown>",
  "target": "<system_agent|automation_agent|dev_agent|memory_agent|emotion_agent|screen_agent|self_intro|brain>",
  "emotion_hint": "<neutral|empathy|warm_surprise|confident|focused|relaxed|ambitious>",
  "confidence": 0.0,
  "requires_confirmation": false,
  "reason": "<one sentence>"
}
No markdown. No explanation. Only valid JSON."""


def _llm_judge(text: str) -> Optional[SemanticFrame]:
    try:
        import urllib.request
        payload = json.dumps({
            "model": "llama3.2:3b",
            "prompt": f"{_LLM_SYSTEM}\n\nUtterance: {text}",
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1) as resp:
            raw = json.loads(resp.read())
            response_text = raw.get("response", "").strip()
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                return None
            parsed = json.loads(json_match.group())
            return SemanticFrame(
                route=parsed.get("route", "conversation"),
                intent=parsed.get("intent", "unknown"),
                target=parsed.get("target", "brain"),
                emotion_hint=parsed.get("emotion_hint", "neutral"),
                confidence=float(parsed.get("confidence", 0.5)),
                requires_confirmation=bool(parsed.get("requires_confirmation", False)),
                reason=parsed.get("reason", "llm_judge"),
                tier=3,
            )
    except Exception as exc:
        logger.debug("[SEMANTIC] LLM judge failed: %s", exc)
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

class SemanticUnderstanding:
    """
    Main entry point. parse() applies all three tiers in order.

    Skips to tier 3 only if tiers 1+2 both fail or return conf < 0.70.
    """

    def parse(self, text: str, use_llm: bool = True) -> SemanticFrame:
        import os as _os_parse
        text = text.strip()
        if not text:
            return SemanticFrame(
                route="clarify", intent="empty", target="brain",
                confidence=1.0, reason="empty_input", tier=1,
            )

        # Tier 1 — always runs, < 2ms
        frame = _apply_fast_rules(text)
        if frame and frame.confidence >= 0.80:
            logger.debug("[SEMANTIC] T1 intent=%s conf=%.2f", frame.intent, frame.confidence)
            return frame

        # LOCAL_ONLY_MODE — skip Tier-2/3 entirely (ChromaDB/Ollama not needed).
        # This is the real fix for the "fake timeout" problem: the thread never blocks.
        if _os_parse.getenv("LOCAL_ONLY_MODE", "").lower() in ("1", "true", "yes"):
            logger.debug("[SEMANTIC_BYPASS] LOCAL_ONLY_MODE skip_tier2_3=true")
            return frame or SemanticFrame(
                route="conversation", intent="unknown", target="brain",
                confidence=0.3, reason="local_only_tier1_only", tier=0,
            )

        # Tier 2
        t2 = _query_embedding(text)
        if t2 and t2.confidence >= 0.72:
            logger.debug("[SEMANTIC] T2 intent=%s conf=%.2f", t2.intent, t2.confidence)
            return t2

        # Tier 3 (only if requested and both tiers weak)
        if use_llm:
            t3 = _llm_judge(text)
            if t3 and t3.confidence >= 0.50:
                logger.debug("[SEMANTIC] T3 intent=%s conf=%.2f", t3.intent, t3.confidence)
                return t3

        # Return best available
        best = t2 or frame or SemanticFrame(
            route="conversation", intent="unknown", target="brain",
            confidence=0.3, reason="no_match", tier=0,
        )
        return best


semantic_understanding = SemanticUnderstanding()
