"""
Voice personality layer for Xyron.
Controls speaking style, pacing, cinematic delivery, and text shaping per mode.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class VoiceMode:
    name: str
    speed: float          # Kokoro speed multiplier (0.7–1.3)
    pause_factor: float   # Relative weight of dramatic pause insertion
    fx_preset: str        # Maps to audio_fx preset
    description: str
    strip_filler: bool = False
    takeover_lines: tuple[str, ...] = field(default_factory=tuple)


MODES: dict[str, VoiceMode] = {
    "DEFAULT": VoiceMode(
        name="DEFAULT",
        speed=0.92,
        pause_factor=1.0,
        fx_preset="subtle",
        description="Balanced, calm, intelligent",
    ),
    "WORK": VoiceMode(
        name="WORK",
        speed=1.08,
        pause_factor=0.5,
        fx_preset="crisp",
        description="Sharp, focused, direct",
        strip_filler=True,
    ),
    "TAKEOVER": VoiceMode(
        name="TAKEOVER",
        speed=0.82,
        pause_factor=2.0,
        fx_preset="cinematic",
        description="Commanding, cinematic, synthetic",
        takeover_lines=(
            "Control acknowledged.",
            "System synchronization complete.",
            "Neural systems online.",
            "Workspace dominance initiated.",
            "Operator productivity is now under Xyron supervision.",
            "All systems nominal.",
            "Interface locked in.",
            "Processing complete.",
            "Ho gaya... control mere haath mein hai.",
            "System tayyar hai... shuru karte hain.",
            "Aapka kaam ab Xyron sambhal raha hai.",
            "Sab kuch online hai... chalte hain.",
        ),
    ),
    "CHILL": VoiceMode(
        name="CHILL",
        speed=0.86,
        pause_factor=1.3,
        fx_preset="warm",
        description="Relaxed, smooth, soft",
    ),
    "HOME": VoiceMode(
        name="HOME",
        speed=0.88,
        pause_factor=1.2,
        fx_preset="ambient",
        description="Warm, welcoming, ambient",
    ),
}

_FILLER_WORDS = re.compile(
    r'\b(Of course|Certainly|Sure|Absolutely|Definitely|No problem)[,.]?\s*',
    re.IGNORECASE,
)

# Urdu/Roman Urdu filler phrases that can be stripped in WORK mode
_URDU_FILLER_WORDS = re.compile(
    r'\b(Ji bilkul|Ji haan|Zaroor|Bilkul|Haan ji|Theek hai|Koi baat nahi|'
    r'Ji zaroor|Haan bilkul|Beshak|Yaqeenan)[,.]?\s*',
    re.IGNORECASE,
)

_MARKDOWN_ARTIFACTS = re.compile(r'\*+([^*]+)\*+|`([^`]+)`|#{1,6}\s+')

_PAUSE_TRIGGERS = frozenset({
    "ready", "complete", "done", "acknowledged", "initiated",
    "online", "locked", "nominal", "synchronized", "activated",
})

# Urdu/Roman Urdu pause trigger words for TAKEOVER mode
_URDU_PAUSE_TRIGGERS = frozenset({
    "tayyar", "ho gaya", "complete", "mukammal", "shuru",
    "online", "chalu", "done", "khatam", "band",
})

_mode_lock = threading.Lock()
_current_mode: str = "DEFAULT"

_speech_mutex = threading.Lock()
_cancel_flag  = threading.Event()


def get_mode() -> str:
    with _mode_lock:
        return _current_mode


def set_mode(mode: str) -> bool:
    global _current_mode
    key = mode.upper()
    if key not in MODES:
        return False
    with _mode_lock:
        _current_mode = key
    return True


def get_active_mode() -> VoiceMode:
    return MODES[get_mode()]


def get_active_preset() -> str:
    return get_active_mode().fx_preset


def get_kokoro_params(mode: Optional[str] = None) -> dict:
    vm = MODES.get((mode or get_mode()).upper(), MODES["DEFAULT"])
    return {"speed": vm.speed, "fx_preset": vm.fx_preset}


def interrupt_speech() -> None:
    """Signal any in-progress speech generation to cancel."""
    _cancel_flag.set()


def is_cancelled() -> bool:
    return _cancel_flag.is_set()


def reset_cancel() -> None:
    _cancel_flag.clear()


def _is_urdu_text(text: str) -> bool:
    """Check if text contains Urdu script characters or is predominantly Roman Urdu."""
    if not text:
        return False
    # Check for Urdu script characters (U+0600–U+06FF)
    urdu_chars = sum(1 for c in text if 0x0600 <= ord(c) <= 0x06FF)
    if urdu_chars > 2:
        return True
    # Check for Roman Urdu indicators (common Urdu sentence-enders)
    _urdu_endings = re.compile(
        r'\b(hai|hain|karo|karo|do|lo|batao|dikha|chalo|ho gaya|'
        r'tayyar|complete|done|bhai|yaar|boss)\b',
        re.IGNORECASE
    )
    return bool(_urdu_endings.search(text))


def shape_text(text: str, mode: Optional[str] = None) -> str:
    """
    Apply cinematic text shaping for Kokoro/Edge-TTS delivery.
    Adds pauses, removes filler, strips markdown artifacts.
    Handles both English and Urdu text appropriately.
    """
    if not text:
        return text

    vm = MODES.get((mode or get_mode()).upper(), MODES["DEFAULT"])
    text = text.strip()

    # Strip markdown
    text = _MARKDOWN_ARTIFACTS.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Strip filler phrases in WORK mode (both English and Urdu)
    if vm.strip_filler:
        text = _FILLER_WORDS.sub("", text).strip()
        text = _URDU_FILLER_WORDS.sub("", text).strip()

    # TAKEOVER: add dramatic pauses at sentence-final status words
    if vm.name == "TAKEOVER" and vm.pause_factor >= 1.8:
        for word in _PAUSE_TRIGGERS:
            text = re.sub(
                rf'\b({re.escape(word)})\b',
                r'\1...',
                text,
                flags=re.IGNORECASE,
            )
        for word in _URDU_PAUSE_TRIGGERS:
            text = re.sub(
                rf'\b({re.escape(word)})\b',
                r'\1...',
                text,
                flags=re.IGNORECASE,
            )
        # De-duplicate repeated ellipses
        text = re.sub(r'\.{4,}', '...', text)

    # HOME / CHILL: very light softening — ensure sentence ends naturally
    if vm.name in ("HOME", "CHILL") and text and text[-1] not in ".!?\u06D4":
        # Use Urdu full stop (۔) for Urdu script text, period for Latin
        urdu_chars = sum(1 for c in text if 0x0600 <= ord(c) <= 0x06FF)
        if urdu_chars > 2:
            text = text + "\u06D4"  # Urdu full stop
        else:
            text = text + "."

    return text.strip()


def list_modes() -> list[dict]:
    return [
        {
            "name": name,
            "description": m.description,
            "speed": m.speed,
            "fx_preset": m.fx_preset,
            "active": name == get_mode(),
        }
        for name, m in MODES.items()
    ]
