"""
Pronunciation preprocessing for multilingual TTS synthesis — technical
vocabulary (WhatsApp, Chrome, GitHub, Excel, ...) mixed into Urdu/Roman-Urdu
text before it reaches the TTS model. Called from both edge_tts_service.py
(the active Urdu-family production path) and xtts_service.py (currently
unable to load on this machine's dependency stack, kept as a legacy path).

IMPORTANT — this module is infrastructure, not a validated result. Whether a
term sounds best in its original Latin spelling, a phonetic respelling, or
Urdu transliteration on the target engine can only be judged by actually
listening to the synthesized audio — something this implementation has no
way to do. TERM_OVERRIDES starts EMPTY (pass-through: original spelling,
each engine's current default behavior) and must only gain entries after a
human has A/B-listened to real synthesized output and confirmed a specific
respelling is better. Do not add entries here from a guess.

Logs:
  [PRONUNCIATION_PREPROCESSED] before -> after (only when TERM_OVERRIDES is non-empty and matched)
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# lowercase term -> respelling to substitute before XTTS synthesis.
# Candidates worth listening-testing (from the hackathon technical-vocab
# list): WhatsApp, Chrome, GitHub, Excel, CNIC, Instagram, YouTube, invoice,
# delivery, order, sales, Daraz, Microsoft, VS Code, PowerPoint.
# Left empty until confirmed by ear — see module docstring.
TERM_OVERRIDES: dict[str, str] = {}

_WORD_RE = re.compile(r"[A-Za-z]+")


def preprocess(text: str) -> str:
    """Apply confirmed pronunciation overrides to `text`. No-op while
    TERM_OVERRIDES is empty (current state) — safe to call unconditionally."""
    if not TERM_OVERRIDES or not text:
        return text

    def _sub(m: re.Match) -> str:
        word = m.group(0)
        repl = TERM_OVERRIDES.get(word.lower())
        return repl if repl else word

    result = _WORD_RE.sub(_sub, text)
    if result != text:
        logger.info("[PRONUNCIATION_PREPROCESSED] %r -> %r", text[:60], result[:60])
    return result
