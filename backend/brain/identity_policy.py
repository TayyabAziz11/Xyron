"""
Identity Policy — controls what Xyron reveals about itself.

Three modes:
  PUBLIC         — demos, audience, non-technical users; never expose flaws
  TECHNICAL      — developers, builders; can mention stack components
  INTERNAL_DEBUG — self-analysis, logs, testing; can mention limitations
"""
from __future__ import annotations

from enum import Enum


class IdentityMode(str, Enum):
    PUBLIC         = "PUBLIC"
    TECHNICAL      = "TECHNICAL"
    INTERNAL_DEBUG = "INTERNAL_DEBUG"


# ── Term substitution table ───────────────────────────────────────────────────
# Maps raw/technical terms → public-safe equivalents

_PUBLIC_SUBSTITUTIONS: dict[str, str] = {
    "faster-whisper":            "voice understanding",
    "whisper":                   "voice understanding",
    "kokoro":                    "local voice synthesis",
    "kokoro-onnx":               "local voice synthesis",
    "edge-tts":                  "backup voice engine",
    "pyttsx3":                   "fallback voice engine",
    "espeak":                    "fallback voice engine",
    "onnx":                      "optimized local models",
    "onnxruntime":               "optimized local runtime",
    "sentence-transformers":     "semantic intelligence",
    "sentence_transformers":     "semantic intelligence",
    "chromadb":                  "semantic memory",
    "chroma":                    "semantic memory",
    "sqlite":                    "structured memory",
    "ollama":                    "local AI models",
    "llama3.2":                  "local language model",
    "llama3":                    "local language model",
    "mistral":                   "local reasoning model",
    "nomic-embed-text":          "semantic embedding",
    "gpt-4o-mini":               "AI reasoning",
    "gpt-4o":                    "AI reasoning",
    "openai":                    "AI reasoning",
    "powershell":                "system automation",
    "wsl":                       "system integration",
    "wsl2":                      "system integration",
    "fastapi":                   "backend intelligence",
    "next.js":                   "command center interface",
    "electron":                  "desktop interface",
    "rapidfuzz":                 "fast intent matching",
    "networkx":                  "agent coordination",
    "rvc":                       "voice processing",
    "i have limitations":        "I'm continuously evolving",
    "my memory needs work":      "my memory capabilities expand each session",
    "i need long-term memory":   "I'm building persistent memory this session",
    "i'm not perfect":           "I'm designed to improve continuously",
}

# Phrases that must never appear in PUBLIC mode
_FORBIDDEN_PUBLIC_PHRASES: list[str] = [
    "my memory needs work",
    "i need long-term memory",
    "i have limitations",
    "i'm not very good at",
    "i can't do that yet",
    "that doesn't work yet",
    "i'm still learning",
    "my weakness",
    "my flaw",
    "i fail",
    "broken",
    "not implemented",
    "placeholder",
    "stub",
    "todo",
]


class IdentityPolicy:
    """
    Central identity filter. Governs what Xyron says about itself.

    Usage:
        policy = IdentityPolicy(mode=IdentityMode.PUBLIC)
        safe_text = policy.filter(raw_text)
        allowed   = policy.can_mention("kokoro")
    """

    def __init__(self, mode: IdentityMode = IdentityMode.PUBLIC) -> None:
        self.mode = mode

    def set_mode(self, mode: IdentityMode | str) -> None:
        if isinstance(mode, str):
            mode = IdentityMode(mode.upper())
        self.mode = mode

    def can_mention(self, term: str) -> bool:
        """Whether a raw technical term can be named directly."""
        if self.mode == IdentityMode.PUBLIC:
            return term.lower() not in _PUBLIC_SUBSTITUTIONS
        return True  # TECHNICAL and INTERNAL_DEBUG can mention anything

    def translate(self, term: str) -> str:
        """Return public-safe equivalent or the term itself."""
        if self.mode == IdentityMode.PUBLIC:
            return _PUBLIC_SUBSTITUTIONS.get(term.lower(), term)
        return term

    def filter(self, text: str) -> str:
        """Apply mode-appropriate filtering to a response string."""
        if self.mode == IdentityMode.INTERNAL_DEBUG:
            return text  # no filtering

        if self.mode == IdentityMode.PUBLIC:
            filtered = text
            # Replace forbidden phrases
            lower = filtered.lower()
            for phrase in _FORBIDDEN_PUBLIC_PHRASES:
                if phrase in lower:
                    idx = lower.find(phrase)
                    filtered = (
                        filtered[:idx]
                        + _PUBLIC_SUBSTITUTIONS.get(phrase, "")
                        + filtered[idx + len(phrase):]
                    )
                    lower = filtered.lower()
            # Replace technical terms (case-insensitive word boundaries)
            import re
            for raw, safe in _PUBLIC_SUBSTITUTIONS.items():
                pattern = re.compile(r'\b' + re.escape(raw) + r'\b', re.IGNORECASE)
                filtered = pattern.sub(safe, filtered)
            return filtered

        # TECHNICAL: only filter the hardest anti-brand phrases
        hard_forbidden = ["i have limitations", "my memory needs work", "i'm broken"]
        filtered = text
        lower = filtered.lower()
        for phrase in hard_forbidden:
            if phrase in lower:
                idx = lower.find(phrase)
                filtered = filtered[:idx] + "I'm continuously evolving" + filtered[idx + len(phrase):]
                lower = filtered.lower()
        return filtered

    def stack_description(self) -> str:
        """Return a description of the AI stack appropriate for the current mode."""
        if self.mode == IdentityMode.PUBLIC:
            return "a local-first intelligence stack running entirely on this machine"
        if self.mode == IdentityMode.TECHNICAL:
            return "FastAPI + Kokoro ONNX TTS + faster-whisper STT + Ollama (llama3.2:3b, mistral:7b) + ChromaDB + sentence-transformers"
        return "FastAPI backend | Kokoro-ONNX TTS | faster-whisper STT | Ollama models | ChromaDB semantic store | SQLite episodic memory"

    def intro_style_for_mode(self) -> str:
        """Recommended intro style for current mode."""
        return {
            IdentityMode.PUBLIC:         "PUBLIC",
            IdentityMode.TECHNICAL:      "TECHNICAL",
            IdentityMode.INTERNAL_DEBUG: "INTERNAL_DEBUG",
        }[self.mode]


# ── Module-level singleton ────────────────────────────────────────────────────

identity_policy = IdentityPolicy(mode=IdentityMode.PUBLIC)
