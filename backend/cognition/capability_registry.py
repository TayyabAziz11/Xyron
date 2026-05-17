"""
Capability registry — Xyron's honest self-assessment of what it can and can't do.

Used to:
  - inject accurate capability awareness into the LLM system prompt
  - give architecturally accurate answers about limitations
  - avoid generic "I can do everything!" or "I'm limited" hedging
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityInfo:
    status:     str    # "strong" | "partial" | "weak" | "experimental" | "missing"
    confidence: float  # 0.0–1.0 current effectiveness
    notes:      str    # honest assessment — what works, what doesn't, what's the gap
    component:  str    # which file/system handles this


_CAPABILITIES: dict[str, CapabilityInfo] = {
    "semantic_understanding": CapabilityInfo(
        status="partial", confidence=0.63,
        notes="embedding + keyword routing — not true semantic inference, still transcript-dependent",
        component="intent_router.py + command_service.py",
    ),
    "voice_synthesis": CapabilityInfo(
        status="partial", confidence=0.78,
        notes="Kokoro TTS at 1.25x speed for HYPED — mid-range emotional dynamics still compressed",
        component="voice/emotion_tts_mapper.py + audio_fx.py",
    ),
    "emotional_detection": CapabilityInfo(
        status="strong", confidence=0.85,
        notes="10-state mood machine + emotion engine + upgrade guard — fully wired",
        component="cognition/emotion_engine.py + mood_state_machine.py",
    ),
    "long_term_memory": CapabilityInfo(
        status="weak", confidence=0.45,
        notes="basic fact extraction to JSON, no vector store — context fully resets each session",
        component="api/services/memory_service.py",
    ),
    "wake_detection": CapabilityInfo(
        status="partial", confidence=0.70,
        notes="ONNX wake model functional — post-fix improved, latency not yet sub-100ms",
        component="voice/wake_word_service.py",
    ),
    "proactive_intelligence": CapabilityInfo(
        status="experimental", confidence=0.40,
        notes="time-based triggers only — no real-time context-driven or screen-aware foresight",
        component="api/services/proactive_service.py",
    ),
    "code_reasoning": CapabilityInfo(
        status="partial", confidence=0.60,
        notes="reads files linearly — no dependency graph traversal or cascade impact analysis",
        component="api/tools/system_tools.py",
    ),
    "multilingual": CapabilityInfo(
        status="partial", confidence=0.72,
        notes="Urdu + English + Roman Urdu normalization — other languages fall back to English",
        component="api/routers/voice.py language detection",
    ),
    "tool_automation": CapabilityInfo(
        status="strong", confidence=0.88,
        notes="PowerShell WSL2 bridge — 10+ tool modules, app launch, screenshot, system control",
        component="api/tools/system_tools.py",
    ),
    "context_continuity": CapabilityInfo(
        status="weak", confidence=0.55,
        notes="5-turn window per session — full rebuild each call, no cross-session thread persistence",
        component="api/routers/voice.py history window",
    ),
    "environment_awareness": CapabilityInfo(
        status="weak", confidence=0.35,
        notes="periodic screenshot only — no live screen understanding or real-time context injection",
        component="api/services/screen_context_service.py",
    ),
    "emotional_voice_range": CapabilityInfo(
        status="experimental", confidence=0.70,
        notes="HYPED mode at 1.25x + hyped audio preset active — mid-range emotional dynamics flat",
        component="voice/audio_fx.py + emotion_tts_mapper.py",
    ),
}


class CapabilityRegistry:
    """Xyron's self-knowledge of its current capability landscape."""

    def get(self, name: str) -> CapabilityInfo | None:
        return _CAPABILITIES.get(name)

    def get_weak(self) -> list[tuple[str, CapabilityInfo]]:
        """Weak/experimental/missing capabilities sorted by confidence ascending."""
        return sorted(
            [(n, c) for n, c in _CAPABILITIES.items()
             if c.status in ("weak", "experimental", "missing")],
            key=lambda x: x[1].confidence,
        )

    def get_strong(self) -> list[tuple[str, CapabilityInfo]]:
        return [(n, c) for n, c in _CAPABILITIES.items() if c.status == "strong"]

    def get_context_injection(self) -> str:
        """
        System prompt injection — architecturally accurate capability self-knowledge.
        Prevents generic hedging and enables specific, honest self-description.
        """
        weak = self.get_weak()[:4]
        strong = self.get_strong()

        strong_names = ", ".join(
            n.replace("_", " ") for n, _ in strong
        )
        weak_lines = "\n".join(
            f"  - {n.replace('_', ' ').title()} ({c.confidence:.0%}): {c.notes}"
            for n, c in weak
        )
        return (
            "\n\nXYRON CAPABILITY AWARENESS:\n"
            f"Strong systems: {strong_names}.\n"
            "Weakest / in-progress:\n"
            + weak_lines
            + "\n\nWhen describing yourself or your limitations: use these specifics. "
            "Never say 'enhance performance' or 'I can do more'. Reference actual systems."
        )


# Global singleton
capability_registry = CapabilityRegistry()
