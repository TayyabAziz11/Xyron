"""
Response quality validator — detects bad gpt-4o-mini answers and decides
whether to retry with gpt-4o.

Checks (in order, fast-exit on first failure):
  1. Empty / whitespace-only response
  2. Response too short relative to question complexity
  3. Incomplete sentence (cut off mid-thought)
  4. Uncertainty markers ("I'm not sure", "I don't know", "maybe")
  5. Capability refusal ("I cannot", "I don't have access to")
  6. Off-topic / generic deflection ("As an AI language model...")
  7. Repeated words / garbled output

Usage:
    from api.services.response_validator import validate

    result = validate(question="explain quantum entanglement", response=reply)
    if result.should_retry_4o:
        reply = openai_client.generate(messages, model="gpt-4o") or reply
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    ok:              bool    # True = response is acceptable
    reason:          str     # short explanation (for logs)
    should_retry_4o: bool    # True = worth retrying with gpt-4o


# ── Signal patterns ───────────────────────────────────────────────────────────

_UNCERTAINTY_RE = re.compile(
    r"\b(?:i(?:'?m|\s+am)\s+not\s+(?:sure|certain|confident)"
    r"|i\s+(?:don(?:'t|ot)\s+(?:know|have|think)|cannot\s+(?:say|confirm|tell))"
    r"|(?:maybe|perhaps|possibly|i\s+believe|i\s+think)\b.{0,30}(?:but|however|although)"
    r"|not\s+(?:sure|certain)\s+(?:about|if|whether)"
    r")\b",
    re.IGNORECASE,
)

_REFUSAL_RE = re.compile(
    r"\b(?:i\s+(?:can(?:not|'t)\s+(?:access|browse|look\s+up|search|retrieve|provide\s+real)"
    r"|don(?:'t|ot)\s+have\s+(?:access|the\s+ability|real[\s\-]time))"
    r"|as\s+an\s+ai(?:\s+language\s+model)?[,\s]"
    r"|my\s+(?:knowledge|training)\s+(?:cutoff|data|only\s+goes)"
    r")\b",
    re.IGNORECASE,
)

_GARBLED_RE = re.compile(
    r'(.)\1{4,}'     # 5+ repeated identical characters
    r'|[^\x00-\x7F]{10,}',  # 10+ consecutive non-ASCII (likely hallucination)
)

# Patterns that suggest generic AI-template boilerplate
_GENERIC_RE = re.compile(
    r'\b(?:certainly!?\s+here|of\s+course!?\s+here|sure!?\s+here|absolutely!?\s+here'
    r'|as\s+requested|happy\s+to\s+help\s+with\s+that'
    r'|great\s+question'
    r')\b',
    re.IGNORECASE,
)

# Sentences must end with proper punctuation
_INCOMPLETE_RE = re.compile(r'[a-z0-9]\s*$', re.IGNORECASE)

# Minimum response lengths (chars) per complexity band
_MIN_LEN_SIMPLE   = 10   # "Paris." is fine for a capital-city query
_MIN_LEN_MODERATE = 30   # factual explanation deserves a sentence
_MIN_LEN_COMPLEX  = 60   # any reasoning task needs substance


def validate(
    question:   str,
    response:   str,
    complexity: float = 0.3,   # score_complexity() output for this question
) -> ValidationResult:
    """
    Check whether `response` is a good answer to `question`.

    Args:
        question:   the user's utterance
        response:   the model's reply (already stripped)
        complexity: 0.0–1.0 score from model_router.score_complexity()

    Returns:
        ValidationResult with ok, reason, should_retry_4o
    """
    r = (response or "").strip()

    # ── 1. Empty ─────────────────────────────────────────────────────────────
    if not r:
        return ValidationResult(ok=False, reason="empty response", should_retry_4o=True)

    # ── 2. Too short ──────────────────────────────────────────────────────────
    if complexity >= 0.55 and len(r) < _MIN_LEN_COMPLEX:
        return ValidationResult(
            ok=False,
            reason=f"response too short ({len(r)} chars) for complex query",
            should_retry_4o=True,
        )
    if complexity >= 0.30 and len(r) < _MIN_LEN_MODERATE:
        return ValidationResult(
            ok=False,
            reason=f"response too short ({len(r)} chars)",
            should_retry_4o=True,
        )
    if len(r) < _MIN_LEN_SIMPLE:
        return ValidationResult(
            ok=False,
            reason=f"response too short ({len(r)} chars)",
            should_retry_4o=False,  # probably just a factual one-word answer
        )

    # ── 3. Incomplete sentence (cut off mid-word or mid-clause) ───────────────
    if complexity > 0.20 and _INCOMPLETE_RE.search(r):
        return ValidationResult(
            ok=False,
            reason="incomplete sentence — response appears truncated",
            should_retry_4o=True,
        )

    # ── 4. Uncertainty markers ────────────────────────────────────────────────
    if _UNCERTAINTY_RE.search(r):
        return ValidationResult(
            ok=False,
            reason="uncertainty signal detected in response",
            should_retry_4o=True,
        )

    # ── 5. Capability refusal ─────────────────────────────────────────────────
    if _REFUSAL_RE.search(r):
        return ValidationResult(
            ok=False,
            reason="model refused or cited knowledge limitations",
            should_retry_4o=False,   # 4o has the same limits — don't waste a call
        )

    # ── 6. Generic boilerplate opener ────────────────────────────────────────
    if _GENERIC_RE.search(r[:60]):   # only check opening
        return ValidationResult(
            ok=False,
            reason="generic boilerplate opener detected",
            should_retry_4o=False,   # rewording style issue, not knowledge issue
        )

    # ── 7. Garbled / hallucinated text ────────────────────────────────────────
    if _GARBLED_RE.search(r):
        return ValidationResult(
            ok=False,
            reason="garbled or repeated characters detected",
            should_retry_4o=True,
        )

    return ValidationResult(ok=True, reason="", should_retry_4o=False)


def validate_and_retry(
    question:   str,
    response:   str,
    messages:   list[dict],
    complexity: float = 0.3,
) -> str:
    """
    Convenience wrapper: validate mini response, retry with gpt-4o if needed.

    Returns the best response available (original or retry).
    Imports openai_client inline to avoid circular imports.
    """
    result = validate(question, response, complexity)

    if result.ok or not result.should_retry_4o:
        return response

    import logging
    logging.getLogger(__name__).info(
        "[Validator] mini response failed (%s) — retrying with gpt-4o", result.reason
    )

    try:
        from api.services.openai_client import openai_client
        retry = openai_client.generate(messages, model="gpt-4o", max_tokens=200)
        if retry and retry.strip():
            return retry
    except Exception as exc:
        logging.getLogger(__name__).warning("[Validator] gpt-4o retry failed: %s", exc)

    return response  # best effort — return original even if it's not perfect
