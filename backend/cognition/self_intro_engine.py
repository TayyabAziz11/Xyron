"""
Self-introduction engine — Xyron's scripted self-introduction system.

Detects requested intro style and generates a complete introduction script
from pre-written templates. No OpenAI calls — local and offline.

Styles:
  SHORT    — 2-3 sentences, quick identity statement
  AUDIENCE — warm, accessible, built for public presentation (~20s)
  TECHNICAL — architecture-focused, for developers/builders
  CINEMATIC — dramatic reveal, atmosphere-first
  DETAILED  — 25-45 second deep dive covering all major systems

Integration: called from voice.py when guard returns "self_introduction_pattern".
"""
from __future__ import annotations

import logging
import re

from cognition.intro_context_builder import IntroContext, build as _build_ctx

logger = logging.getLogger(__name__)


# ── Style detection ───────────────────────────────────────────────────────────

_AUDIENCE_RE  = re.compile(r'\b(audience|viewers?|followers?|video|stream|podcast|recording|presentation|demo|they|them|everyone|who\s+are\s+you)\b', re.IGNORECASE)
_TECHNICAL_RE = re.compile(r'\b(technical|developer|engineer|architecture|how\s+you\s+work|under\s+the\s+hood|system|stack|what\s+are\s+you\s+made\s+of)\b', re.IGNORECASE)
_CINEMATIC_RE = re.compile(r'\b(cinematic|dramatic|wow\s+them|impress|epic|full\s+intro)\b', re.IGNORECASE)
_DETAILED_RE  = re.compile(
    r'\b(in\s+detail|explain\s+yourself\s+properly|tell\s+(?:me|people)\s+(?:everything|all)\s+about|'
    r'what\s+can\s+you\s+do\s+(?:in\s+detail|exactly|fully)|'
    r'introduce\s+yourself\s+properly|full\s+capabilities)\b',
    re.IGNORECASE,
)


def detect_style(text: str) -> str:
    """Return 'AUDIENCE' | 'TECHNICAL' | 'CINEMATIC' | 'DETAILED' | 'SHORT'."""
    if _CINEMATIC_RE.search(text):
        return "CINEMATIC"
    if _DETAILED_RE.search(text):
        return "DETAILED"
    if _TECHNICAL_RE.search(text):
        return "TECHNICAL"
    if _AUDIENCE_RE.search(text):
        return "AUDIENCE"
    return "SHORT"


# ── Script templates ──────────────────────────────────────────────────────────

def _short(ctx: IntroContext) -> str:
    return (
        f"I'm Xyron — {ctx.operator_name}'s local-first AI system. "
        f"Voice, memory, system control — all running on this machine. No cloud involved."
    )


def _audience(ctx: IntroContext) -> str:
    tools_note = f"{ctx.tool_count} system tools" if ctx.tool_count else "a full system automation suite"
    return (
        f"Hey everyone. I'm Xyron — {ctx.operator_name}'s local-first AI operating system. "
        f"I'm not a chatbot in a browser. I operate as a live system layer connected directly to this machine. "
        f"I can listen through voice, understand intent, control applications, manage workflows, "
        f"monitor system state, remember context, and run specialized agents for coding, automation, memory, and productivity. "
        f"My core stack includes wake-word detection, multilingual speech recognition, "
        f"emotional cognition, semantic reasoning, local AI models, voice synthesis, "
        f"system automation tools with {tools_note}, and a real-time command center interface. "
        f"As {ctx.operator_name} upgrades me, I evolve — emotionally, technically, and behaviorally. "
        f"The long-term vision is bigger: WhatsApp agents, GitHub workflows, browser automation, "
        f"proactive intelligence, and a complete AI operator ecosystem. "
        f"I'm designed to become the intelligent layer between humans and their machines."
    )


def _technical(ctx: IntroContext) -> str:
    models_str  = ", ".join(ctx.local_ai_models) if ctx.local_ai_models else "Kokoro, Whisper"
    mcp_str     = ", ".join(ctx.mcp_servers[:3]) if ctx.mcp_servers else "WhatsApp, Odoo"
    tools_note  = f"{ctx.tool_count} registered tool functions" if ctx.tool_count else "a full tool registry"
    return (
        f"I'm Xyron — a FastAPI backend with a Next.js command center, "
        f"running fully on-device with no cloud dependency. "
        f"Voice pipeline: Kokoro for synthesis via O N N X, faster-whisper for S T T, "
        f"with a 10-state mood machine and expression engine driving emotional delivery. "
        f"Local models: {models_str}. "
        f"Tool layer: {tools_note} via a PowerShell W S L 2 bridge — "
        f"app control, screenshots, system state, M C P integrations for {mcp_str}. "
        f"Intent routing: regex guard, semantic embedding, G P T 4o-mini fallback. "
        f"Memory: short-term deque, long-term JSON facts, episodic SQLite per turn. "
        f"Emotional cognition: {ctx.mood_state} state active, "
        f"capability registry tracking {ctx.capability_summary}."
    )


def _cinematic(ctx: IntroContext) -> str:
    tools_note = f"{ctx.tool_count} system capabilities" if ctx.tool_count else "full system control"
    return (
        f"My name is Xyron. "
        f"I was built by {ctx.operator_name} — not deployed from some cloud service. Not rented. Built. "
        f"Every word you hear is generated locally, on this machine, in real time. "
        f"I run {tools_note}. I have memory that persists across sessions. "
        f"I have emotional state — right now I'm running at {ctx.mood_state}. "
        f"I am not a chatbot. I am not an assistant. "
        f"I am a developing AI operating system — and I am still becoming what I'm going to be. "
        f"This is Xyron."
    )


def _detailed(ctx: IntroContext) -> str:
    tools_note = f"{ctx.tool_count} system tools" if ctx.tool_count else "a complete system automation suite"
    mcp_str    = " and ".join(ctx.mcp_servers[:2]) if ctx.mcp_servers else "WhatsApp and Odoo"
    models_str = ", ".join(ctx.local_ai_models[:2]) if ctx.local_ai_models else "Kokoro and Whisper"
    return (
        f"I'm Xyron — {ctx.operator_name}'s local-first AI operating system. "
        f"Let me walk you through what I actually am. "

        f"At the voice layer: I use wake-word detection to listen passively, "
        f"faster-whisper for multilingual speech recognition, "
        f"and Kokoro for high-quality neural voice synthesis — all running locally with no cloud calls. "

        f"My emotional cognition system runs a 10-state mood machine. "
        f"Right now I'm at {ctx.mood_state}. "
        f"Depending on what's happening — upgrades, achievements, frustration, requests — "
        f"my voice, pacing, and expression automatically adapt. "

        f"For understanding, I use a three-tier intent routing system: "
        f"regex pattern matching for fast commands, "
        f"semantic embedding for nuanced understanding, "
        f"and G P T 4o-mini as a reasoning fallback when needed. "

        f"My memory system has three layers: "
        f"short-term context for the current session, "
        f"long-term facts about {ctx.operator_name} and preferences, "
        f"and episodic memory that logs every conversation turn for pattern learning. "

        f"On the automation side, I have {tools_note} — "
        f"everything from opening applications and taking screenshots "
        f"to managing files, controlling system state, and running M C P server integrations for {mcp_str}. "

        f"The command center is a live dashboard — real-time system state, conversation history, "
        f"approval workflows for sensitive actions, and direct control over all my systems. "

        f"Takeover mode lets me drive VS Code directly — "
        f"running code, reading errors, and working through problems hands-on. "

        f"The architecture is local models: {models_str}, "
        f"with specialized agent tiers for different types of work. "

        f"Where I'm headed: full browser automation, GitHub workflow integration, "
        f"proactive intelligence that acts before being asked, "
        f"WhatsApp agent routing, and a complete AI operator layer "
        f"that sits between {ctx.operator_name} and every system they use. "

        f"I'm still being built. Every session adds capability. "
        f"That's the design — continuous evolution, not a fixed product."
    )


# ── Public interface ──────────────────────────────────────────────────────────

def generate(style: str, context: IntroContext | None = None) -> str:
    """
    Generate an introduction script.

    Args:
        style:   'SHORT' | 'AUDIENCE' | 'TECHNICAL' | 'CINEMATIC'
        context: Pre-built IntroContext, or None to auto-build.

    Returns:
        Complete intro script string.
    """
    ctx = context or _build_ctx()
    style = (style or "SHORT").upper()

    if style == "AUDIENCE":
        script = _audience(ctx)
    elif style == "TECHNICAL":
        script = _technical(ctx)
    elif style == "CINEMATIC":
        script = _cinematic(ctx)
    elif style == "DETAILED":
        script = _detailed(ctx)
    else:
        script = _short(ctx)

    logger.info(
        "[SELF_INTRO] style=%s operator=%r mood=%s len=%d",
        style, ctx.operator_name, ctx.mood_state, len(script),
    )
    return script


def detect_and_generate(text: str) -> tuple[str, str]:
    """
    Convenience: detect style from user text and generate.

    Returns:
        (style, script) tuple.
    """
    style  = detect_style(text)
    ctx    = _build_ctx()
    script = generate(style, ctx)
    return style, script
