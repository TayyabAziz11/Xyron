"""
Urdu expressions module — greetings, acknowledgments, and common phrases.

Provides culturally-appropriate Urdu/Roman Urdu expressions for:
- Wake-up greetings (Assalam o alaikum variants)
- "I'm Home" protocol greetings
- Common acknowledgments and confirmations
- Time-based greetings (morning, afternoon, evening, night)
- Farewell expressions

Used by:
- voice/response_generator.py for greeting responses
- cognition/expression_engine.py for culturally-aware reactions
- api/routers/voice_ws.py for language switch confirmations
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Optional


# ── Greetings ─────────────────────────────────────────────────────────────────

_GREETINGS_FORMAL: list[str] = [
    "Assalam o alaikum! Kya haal hai?",
    "Assalam o alaikum. Main tayyar hun, batao kya karna hai?",
    "Walaikum assalam. Kya madad kar sakta hun?",
    "Assalam o alaikum. Xyron online hai, hukum karo.",
]

_GREETINGS_CASUAL: list[str] = [
    "Salam bhai! Kya scene hai?",
    "Arey salam! Batao kya karna hai?",
    "Salam! Main hun, bolo kya chahiye?",
    "Waalaikum salam! Chalo batao, aaj kya plan hai?",
    "Salam yaar! Ready hun, bolo.",
]

_GREETINGS_LATE_NIGHT: list[str] = [
    "Itni raat tak jaage ho? Bolo kya chahiye.",
    "Salam... raat ho gayi hai par main hun.",
    "Abhi tak kaam kar rahe ho? Batao kya karna hai.",
    "Salam. Late night session chal raha hai?",
]

_GREETINGS_MORNING: list[str] = [
    "Subah bakhair! Aaj kya plan hai?",
    "Good morning bhai! Naya din, naya kaam. Batao shuru karein?",
    "Salam! Subah ho gayi, chalo kaam pe lagte hain.",
    "Assalam o alaikum! Subah ka waqt hai, batao kya karna hai.",
]

_GREETINGS_EVENING: list[str] = [
    "Shaam bakhair! Kya haal hai?",
    "Salam! Shaam ho gayi, batao kya karna hai.",
    "Assalam o alaikum. Shaam ka waqt hai, kuch relaxed karein ya kaam?",
]


# ── "I'm Home" protocol ──────────────────────────────────────────────────────

_IM_HOME_GREETINGS: list[str] = [
    "Khush aamdeed! Ghar aa gaye. Main tayyar hun, batao kya karna hai.",
    "Wapas aa gaye! Kya haal hai? Main aapke liye tayyar hun.",
    "Assalam o alaikum, ghar mein khush aamdeed! Aaj ka kya plan hai?",
    "Salam bhai! Ghar aa gaye, ab batao kya karna hai aaj.",
]


# ── Acknowledgments ──────────────────────────────────────────────────────────

_ACK_DONE: list[str] = [
    "Ho gaya!",
    "Done boss!",
    "Ho gaya bhai, koi aur kaam?",
    "Tayyar hai!",
    "Theek se ho gaya.",
    "Mukammal!",
    "Bilkul done.",
]

_ACK_TRYING: list[str] = [
    "Dekhta hun...",
    "Try karta hun...",
    "Ek minute, kar raha hun...",
    "Abhi karta hun...",
    "Ruko, dekhta hun...",
    "Theek hai, koshish karta hun.",
]

_ACK_FAILED: list[str] = [
    "Nahi ho paya yaar, doosra try karta hun.",
    "Yeh kaam nahi hua, koi aur tareeqa dekhta hun.",
    "Sorry bhai, yeh nahi hua. Kya doosra option hai?",
    "Mushkil hai yaar, dekho kya alternative hai.",
    "Nahi hua abhi, thora baad try karta hun.",
]

_ACK_LISTENING: list[str] = [
    "Ji bolo?",
    "Haan, sun raha hun?",
    "Batao?",
    "Ji?",
    "Bolo bhai?",
    "Haan ji?",
]

_ACK_UNDERSTOOD: list[str] = [
    "Samajh gaya.",
    "Theek hai, samajh aa gaya.",
    "Haan, clear hai.",
    "Okay, samajh gaya. Karta hun.",
    "Ji bilkul, samajh gaya.",
]


# ── Farewell ──────────────────────────────────────────────────────────────────

_FAREWELL: list[str] = [
    "Allah hafiz! Jab bhi zaroorat ho, main yahin hun.",
    "Khuda hafiz! Apna khayal rakhna.",
    "Theek hai, Allah hafiz. Jab bulaoge, aa jaunga.",
    "Bye bhai! Jab chahiye, main ready hun.",
    "Allah hafiz! Dua mein yaad rakhna.",
]


# ── Public API ────────────────────────────────────────────────────────────────

def get_greeting(
    time_of_day: Optional[str] = None,
    casual: bool = True,
) -> str:
    """
    Get a culturally-appropriate Urdu greeting.

    Args:
        time_of_day: "morning" | "afternoon" | "evening" | "night" | None
                     If None, auto-detected from current time.
        casual:      If True, use casual greetings. If False, use formal.

    Returns:
        A greeting string in Roman Urdu.
    """
    if time_of_day is None:
        hour = datetime.now().hour
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "night"

    if time_of_day == "night":
        return random.choice(_GREETINGS_LATE_NIGHT)
    elif time_of_day == "morning":
        return random.choice(_GREETINGS_MORNING)
    elif time_of_day == "evening":
        return random.choice(_GREETINGS_EVENING)
    elif casual:
        return random.choice(_GREETINGS_CASUAL)
    else:
        return random.choice(_GREETINGS_FORMAL)


def get_im_home_greeting() -> str:
    """Get an 'I'm Home' protocol greeting in Roman Urdu."""
    return random.choice(_IM_HOME_GREETINGS)


def get_acknowledgment(kind: str = "done") -> str:
    """
    Get a natural Urdu acknowledgment.

    Args:
        kind: "done" | "trying" | "failed" | "listening" | "understood" | "farewell"

    Returns:
        An acknowledgment string in Roman Urdu.
    """
    _map = {
        "done": _ACK_DONE,
        "trying": _ACK_TRYING,
        "failed": _ACK_FAILED,
        "listening": _ACK_LISTENING,
        "understood": _ACK_UNDERSTOOD,
        "farewell": _FAREWELL,
    }
    candidates = _map.get(kind, _ACK_DONE)
    return random.choice(candidates)


def get_farewell() -> str:
    """Get a farewell expression in Roman Urdu."""
    return random.choice(_FAREWELL)
