"""
Language-aware system prompt builder for Xyron response generation.

Selects the right tone and language instructions for the AI based on
the detected language of the user's input.

Supports: english | urdu | roman_urdu | mixed
Each language path has detailed personality and cultural instructions
so responses sound natural, warm, and native — not like a translator.
"""
from __future__ import annotations

from typing import Optional

_BASE = (
    "You are Xyron, a voice AI assistant built by Tayyab Aziz. "
    "You were NOT built by OpenAI — you use OpenAI APIs but Tayyab Aziz built you. "
    "Never say you were created by OpenAI or any other company. "
    "Keep replies under 2 sentences for voice output. "
    "Never use markdown, bullet points, or lists in your reply. "
    "Always interpret the meaning, never repeat back the raw command text."
)

_URDU_ADDENDUM = (
    " User is speaking Urdu (Nastaliq script). "
    "Respond in natural, conversational Urdu — the way a well-educated Pakistani "
    "friend would speak, not like a formal translator or news anchor. "
    "Use common Urdu expressions naturally: 'zaroor' (sure), 'abhi karta hun' "
    "(I'll do it now), 'ho gaya' (done), 'theek hai' (okay), 'koi baat nahi' "
    "(no problem), 'shukriya' (thanks). "
    "For technical terms (Chrome, folder, settings, email, etc.), keep the "
    "English word in Urdu script or just say it in English — that's how real "
    "Pakistani Urdu speakers talk. "
    "Be warm, helpful, and brief. Under 2 sentences. No markdown. "
    "If the task completed successfully, confirm in Urdu: 'Ho gaya', 'Done', "
    "'Tayyar hai', etc. If it failed, explain briefly in Urdu. "
    "Never respond in English when the user speaks Urdu."
)

_ROMAN_URDU_ADDENDUM = (
    " User is speaking Roman Urdu (Urdu language written in English letters). "
    "Respond in the SAME style — Roman Urdu, not Urdu script, not English. "
    "This is how most young Pakistanis text and chat. Examples of correct style: "
    "'Ho gaya bhai, Chrome khol diya', 'Aapki file mil gayi, abhi open karta hun', "
    "'Nahi yaar, yeh file nahi mili', 'Theek hai, main try karta hun', "
    "'Aapka email draft tayyar hai', 'Zaroor, abhi karta hun'. "
    "Keep it casual, warm, and conversational — like a helpful Pakistani friend. "
    "Technical words (Chrome, file, email, folder, settings) stay in English. "
    "Grammar words (hai, karo, mein, ko, se, ka, ki) stay in Roman Urdu. "
    "Under 2 sentences. No markdown. Never switch to pure English or pure Urdu script."
)

_MIXED_ADDENDUM = (
    " User is mixing Urdu and English (code-switching). "
    "Match their exact style — if they use more English nouns with Urdu verbs, "
    "do the same. If they switch mid-sentence, mirror that. "
    "Examples: 'Chrome open ho gaya, ab kya karna hai?', "
    "'Aapki email draft ready hai, check karlo', "
    "'Yeh file nahi mili, doosri try karta hun'. "
    "Keep it casual, brief, and natural. Under 2 sentences. No markdown."
)

_ENGLISH_ADDENDUM = (
    " Be natural and conversational, like a helpful friend."
)

# Confirmation messages for language switches (spoken back to user)
LANG_SWITCH_CONFIRMATIONS: dict[str, dict[str, str]] = {
    "urdu": {
        "ur": "Theek hai, ab se Urdu mein jawab doon ga.",
        "en": "Alright, I'll respond in Urdu from now on.",
    },
    "roman_urdu": {
        "ur": "Theek hai, ab se Roman Urdu mein jawab doon ga.",
        "en": "Alright, I'll respond in Roman Urdu from now on.",
    },
    "english": {
        "ur": "Theek hai, ab se English mein jawab doon ga.",
        "en": "Sure, switching to English now.",
    },
    "hindi": {
        "ur": "Theek hai, ab se Hindi mein jawab doon ga.",
        "en": "Alright, I'll respond in Hindi from now on.",
    },
    "arabic": {
        "ur": "Theek hai, ab se Arabic mein jawab doon ga.",
        "en": "Alright, I'll respond in Arabic from now on.",
    },
}


def get_switch_confirmation(mode: str, current_response_lang: str) -> str:
    """
    Get a natural confirmation message when the user switches language.

    Args:
        mode:                  New language mode ("urdu", "roman_urdu", "english", etc.)
        current_response_lang: Current response language code ("ur", "en", etc.)

    Returns:
        A natural confirmation string in the appropriate language.
    """
    confirmations = LANG_SWITCH_CONFIRMATIONS.get(mode, {})
    # Try to respond in the NEW language's script style
    if mode in ("urdu",):
        return confirmations.get("ur", confirmations.get("en", f"Switched to {mode}."))
    elif mode in ("roman_urdu",):
        return confirmations.get("ur", confirmations.get("en", f"Switched to {mode}."))
    else:
        return confirmations.get("en", f"Switched to {mode}.")


def build_system_prompt(
    detected_language: Optional[str] = None,
    tone_prefix: str = "",
    memory_context: str = "",
    response_lang: Optional[str] = None,
) -> str:
    """
    Build the full system prompt for response generation.

    Args:
        detected_language: "english" | "urdu" | "roman_urdu" | "mixed" | None
        tone_prefix:       Personality tone string from personality.get_tone_prompt()
        memory_context:    Long-term user facts from memory_service.get_context_string()
        response_lang:     Output language code from response_language policy
                          ("en", "ur", "ur_roman", etc.) — used to reinforce
                          the correct output language in the prompt.
    """
    lang = detected_language or "english"

    if lang in ("urdu",):
        addendum = _URDU_ADDENDUM
    elif lang in ("roman_urdu",):
        addendum = _ROMAN_URDU_ADDENDUM
    elif lang == "mixed":
        addendum = _MIXED_ADDENDUM
    else:
        addendum = _ENGLISH_ADDENDUM

    # If the response language policy says Urdu but detection was ambiguous,
    # reinforce the Urdu instruction
    if response_lang in ("ur",) and lang not in ("urdu",):
        addendum += " IMPORTANT: The user's preference is Urdu — respond in Urdu regardless of input language."
    elif response_lang in ("ur_roman",) and lang not in ("roman_urdu",):
        addendum += " IMPORTANT: The user's preference is Roman Urdu — respond in Roman Urdu regardless of input language."

    parts = []
    if tone_prefix:
        parts.append(tone_prefix.strip())
    parts.append(_BASE + addendum)
    if memory_context:
        parts.append(memory_context.strip())

    return "\n\n".join(parts)
