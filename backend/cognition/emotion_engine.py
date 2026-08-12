"""
Text-based emotion engine — fast heuristic detection for voice pipeline.

Detects 10 emotional states from transcript text using regex, punctuation
analysis, capitalization, and vocabulary scoring. Falls back to local LLM
only when heuristic confidence < 0.4.

Target: <15ms heuristic path. LLM path adds ~200-400ms (llama3.2:3b).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EMOTIONS = frozenset({
    "excitement", "frustration", "stress", "curiosity", "pride",
    "hype", "calmness", "seriousness", "humor", "sarcasm",
})


@dataclass
class EmotionResult:
    emotion:    str    # one of EMOTIONS
    energy:     float  # 0.0–1.0 — signal intensity
    confidence: float  # 0.0–1.0 — detection certainty
    importance: float  # 0.0–1.0 — influence on response style


# ── Vocabulary sets ───────────────────────────────────────────────────────────

_HYPE_WORDS = frozenset({
    "fire", "insane", "goat", "bussin", "sheesh", "slay", "lit",
    "banger", "beast", "monster", "absurd", "cracked", "unreal",
    "lets", "go", "wagwan", "yessir", "bro", "actually",
})

_EXCITEMENT_WORDS = frozenset({
    "finally", "amazing", "incredible", "wow", "omg", "awesome",
    "perfect", "great", "brilliant", "love", "excellent",
    "nice", "cool", "sick", "dope", "sweet", "beautiful", "fantastic",
    "wonderful", "outstanding", "superb", "woooo", "yesss",
})

_FRUSTRATION_WORDS = frozenset({
    "ugh", "broken", "again", "still", "failing", "wrong", "error",
    "crash", "bug", "ruined", "hate", "terrible", "awful",
    "stupid", "dumb", "annoying", "useless", "wtf", "seriously",
    "nothing", "keeps", "cant", "wont", "doesnt", "isnt",
})

_STRESS_WORDS = frozenset({
    "asap", "urgent", "hurry", "rush", "quickly", "deadline",
    "pressure", "panic", "emergency", "immediately", "now",
    "fast", "time", "running", "out",
})

_CURIOSITY_WORDS = frozenset({
    "wonder", "curious", "interesting", "hmm", "maybe", "possibly",
    "perhaps", "explore", "test", "trying", "experiment", "what",
    "why", "how", "could", "would", "might", "think",
})

_PRIDE_WORDS = frozenset({
    "fixed", "done", "finished", "built", "shipped", "deployed",
    "working", "solved", "passed", "nailed", "completed", "achieved",
    "got", "made", "created", "added", "merged",
})

_HUMOR_WORDS = frozenset({
    "lol", "lmao", "lmfao", "haha", "hehe", "rofl", "xd", "joking",
    "funny", "joke", "hilarious", "💀", "😂", "bruh", "no way",
})

# ── Regex patterns ────────────────────────────────────────────────────────────

_CAPS_WORD      = re.compile(r'\b[A-Z]{3,}\b')
_MULTI_EXCLAIM  = re.compile(r'!{2,}')
_MULTI_QUESTION = re.compile(r'\?{2,}|\?\s*\?')
_LETS_GO        = re.compile(
    r'\b(lets?\s*go|yessir|wagwan|no\s*way|bro\s*what|are\s*you\s*serious)\b', re.I
)
_SARCASM_PAT = [
    re.compile(r'\b(oh\s+great|yeah\s+sure|oh\s+wow|as\s+if|totally|right)\b', re.I),
    re.compile(r'\b(brilliant|wonderful|perfect|amazing)\b.{0,20}\b(not|never|broken|failed)\b', re.I),
]
_PRIDE_PAST = re.compile(
    r'\b(i\s+)(fixed|built|shipped|solved|finished|got\s+it|made\s+it|nailed\s+it)\b', re.I
)


class EmotionEngine:
    """Fast heuristic text emotion detection with LLM fallback."""

    def detect_text(self, text: str) -> EmotionResult:
        """Detect emotion. Returns result in <15ms (heuristic path)."""
        t0 = time.monotonic()
        result = self._heuristic(text)
        ms = (time.monotonic() - t0) * 1000
        logger.debug(
            "[EmotionEngine] text=%r → emotion=%s energy=%.2f conf=%.2f (%.1fms)",
            text[:50], result.emotion, result.energy, result.confidence, ms,
        )
        # LLM fallback disabled on voice hot path: synchronous 3s Ollama call blocks event loop.
        # Heuristic is sufficient for routing decisions; full LLM analysis is not needed here.
        return result

    # ── Heuristic detection ───────────────────────────────────────────────────

    def _heuristic(self, text: str) -> EmotionResult:
        t     = text.strip()
        lower = t.lower()
        words = set(re.findall(r'\b\w+\b', lower))

        caps_count    = len(_CAPS_WORD.findall(t))
        exclaim_count = t.count('!')
        question_count = t.count('?')
        multi_exclaim  = bool(_MULTI_EXCLAIM.search(t))
        multi_q        = bool(_MULTI_QUESTION.search(t))
        word_count     = len(words)

        hype_hits       = len(words & _HYPE_WORDS)
        excite_hits     = len(words & _EXCITEMENT_WORDS)
        frustrate_hits  = len(words & _FRUSTRATION_WORDS)
        stress_hits     = len(words & _STRESS_WORDS)
        curiosity_hits  = len(words & _CURIOSITY_WORDS)
        pride_hits      = len(words & _PRIDE_WORDS)
        humor_hits      = len(words & _HUMOR_WORDS)

        sarcasm_hit   = any(p.search(t) for p in _SARCASM_PAT)
        lets_go_hit   = bool(_LETS_GO.search(t))
        pride_pattern = bool(_PRIDE_PAST.search(t))

        # ── Decision tree ─────────────────────────────────────────────────

        # HYPE: explicit pattern OR hype vocab + caps/multi-exclaim
        if lets_go_hit or (hype_hits >= 2 and (caps_count >= 1 or multi_exclaim)):
            en = min(1.0, 0.75 + 0.05 * exclaim_count + 0.05 * hype_hits)
            return EmotionResult("hype", en, 0.88, 0.92)

        # HUMOR: explicit laugh words
        if humor_hits >= 1:
            return EmotionResult("humor", 0.55, 0.88, 0.62)

        # EXCITEMENT: excite vocab + exclamation energy
        if excite_hits >= 1 and (exclaim_count >= 1 or caps_count >= 1 or multi_exclaim):
            en = min(0.92, 0.50 + 0.08 * excite_hits + 0.04 * exclaim_count)
            conf = 0.82 if excite_hits >= 2 else 0.68
            return EmotionResult("excitement", en, conf, 0.80)

        # SARCASM: pattern fires before frustration — "oh great another crash"
        # Genuine excitement needs punctuation energy (! or caps). If no punctuation
        # backs up the excite vocab, the sarcasm pattern wins.
        if sarcasm_hit and not (excite_hits >= 1 and exclaim_count >= 1):
            return EmotionResult("sarcasm", 0.40, 0.72, 0.58)

        # FRUSTRATION: frustration vocab
        if frustrate_hits >= 1:
            en = min(0.78, 0.40 + 0.10 * frustrate_hits + 0.04 * question_count)
            conf = 0.80 if frustrate_hits >= 2 else 0.62
            return EmotionResult("frustration", en, conf, 0.78)

        # STRESS: urgency words or rapid-fire questions
        if stress_hits >= 1 or (multi_q and question_count >= 2):
            en = min(0.72, 0.42 + 0.10 * stress_hits)
            return EmotionResult("stress", en, 0.72, 0.70)

        # PRIDE: achievement pattern or pride vocab + no frustration
        if (pride_pattern or pride_hits >= 1) and frustrate_hits == 0 and word_count >= 3:
            en = min(0.78, 0.50 + 0.08 * pride_hits)
            conf = 0.80 if pride_pattern else 0.68
            return EmotionResult("pride", en, conf, 0.75)

        # CURIOSITY: multiple curiosity words OR one + question mark
        if curiosity_hits >= 2 or (curiosity_hits >= 1 and question_count >= 1):
            return EmotionResult("curiosity", 0.35, 0.72, 0.50)

        # EXCITEMENT from pure punctuation energy
        if multi_exclaim or (exclaim_count >= 2 and caps_count >= 1):
            return EmotionResult("excitement", 0.62, 0.60, 0.62)

        # CALMNESS: measured text with no strong signals
        if word_count >= 6 and exclaim_count == 0 and caps_count == 0 and question_count <= 1:
            return EmotionResult("calmness", 0.15, 0.62, 0.28)

        # SERIOUSNESS: short direct text, no emotion markers (1+ words covers all commands)
        if word_count >= 1 and exclaim_count == 0 and humor_hits == 0 and question_count <= 1:
            return EmotionResult("seriousness", 0.25, 0.55, 0.32)

        # Uncertain — return calmness at safe confidence; LLM fallback skipped in voice path
        return EmotionResult("calmness", 0.20, 0.50, 0.20)

    # ── LLM fallback ─────────────────────────────────────────────────────────

    def _llm_fallback(self, text: str, prior: EmotionResult) -> EmotionResult:
        """Classify via llama3.2:3b when heuristic confidence is low (<0.4)."""
        try:
            import requests
            prompt = (
                "Classify the emotion in this text into exactly one word. "
                "Choose from: excitement, frustration, stress, curiosity, pride, "
                "hype, calmness, seriousness, humor, sarcasm.\n\n"
                f"Text: {text[:200]}\n\nEmotion:"
            )
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "llama3.2:3b", "prompt": prompt, "stream": False},
                timeout=3,
            )
            if resp.ok:
                raw = resp.json().get("response", "").strip().lower().split()[0]
                for e in EMOTIONS:
                    if raw.startswith(e[:5]):
                        logger.debug("[EmotionEngine] LLM → %s", e)
                        return EmotionResult(e, prior.energy, 0.70, prior.importance + 0.10)
        except Exception as exc:
            logger.debug("[EmotionEngine] LLM fallback error: %s", exc)
        return prior


# Global singleton
emotion_engine = EmotionEngine()
