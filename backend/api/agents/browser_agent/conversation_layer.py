from __future__ import annotations

"""
ConversationLayer (Phase 4.13 — Human Conversation & Runtime Experience).

Sits IN FRONT of `flight_narration.speak_stage()` — that module still owns
delivery mechanics (WS frontend-status events, duplicate-message
suppression, logging). This module owns *what to say*: it turns a
semantic event ("results found", "comparing", "recommendation ready")
into a natural-sounding line, decides whether the event is even worth
saying out loud, and remembers what's already been said this session so
the same phrase or the same fact isn't repeated verbatim.

Deliberately NOT a rewrite of flight_search_agent.py's extraction/ranking
logic, flight_conversation.py's filter/sort control code, or
BrowserWorkspace/FlightSessionState — this module only changes the
*words*, never invents a fact it wasn't handed (every composer here takes
already-verified data as input; it never guesses a price/airline/count).

Log tags: [CONVERSATION_EVENT] [CONVERSATION_NARRATE] [CONVERSATION_SKIPPED]
[CONVERSATION_MEMORY_RESET]
"""

import logging
import random
import time
from typing import Any, Optional

logger = logging.getLogger("api.agents.browser_agent.conversation_layer")


class ConversationEvent:
    ACKNOWLEDGE            = "acknowledge"
    OPENING_BROWSER        = "opening_browser"
    NOTICEABLE_DELAY       = "noticeable_delay"
    RESULTS_FOUND          = "results_found"
    COMPARISON_STARTED     = "comparison_started"
    RECOMMENDATION_READY   = "recommendation_ready"
    CLARIFICATION_NEEDED   = "clarification_needed"
    VERIFICATION_STARTED   = "verification_started"
    FILTER_APPLIED         = "filter_applied"
    SORT_APPLIED           = "sort_applied"
    COMPARE_TWO            = "compare_two"
    BAGGAGE_ANSWER         = "baggage_answer"
    CANCELLED              = "cancelled"

# Events that are pure implementation detail — Part 3's "bad moments" list.
# Never narrated regardless of context; callers should not even reach the
# conversation layer for these, but the gate is enforced here too so a
# stray call site can't accidentally start narrating DOM/click mechanics.
_SUPPRESSED_EVENTS = {
    "opening_page", "loading_dom", "finding_element", "clicking_button",
    "reading_grid", "waiting_generic", "done_generic",
}

# How long a delay has to be before it's worth mentioning at all (Part 3:
# "noticeable delay" is a good moment, routine sub-second waits are not).
_NOTICEABLE_DELAY_THRESHOLD_S = 1.5


class ConversationMemory:
    """Per-flight-session state — reset whenever a new flight search starts
    or the session is cancelled, so variety and callbacks never bleed
    across unrelated conversations."""

    def __init__(self) -> None:
        self.used_phrases: set[str] = set()
        self.mentioned_airlines: set[str] = set()
        self.mentioned_counts: set[int] = set()
        self.has_greeted_this_session = False
        self.last_event: Optional[str] = None
        self.last_narrated_at: float = 0.0


_memory = ConversationMemory()


def reset_conversation_memory() -> None:
    global _memory
    _memory = ConversationMemory()
    logger.info("[CONVERSATION_MEMORY_RESET]")


def _pick(pool: list[str]) -> str:
    """Random pick that avoids repeating a phrase already used this
    session when a fresh option exists — Part 2's "avoid repeating the
    same opening twice within a session", implemented as "prefer unused,
    fall back to any" rather than pure randomness (which could repeat by
    chance) or a fixed rotation (which would feel mechanical in its own
    way)."""
    fresh = [p for p in pool if p not in _memory.used_phrases]
    choice = random.choice(fresh or pool)
    _memory.used_phrases.add(choice)
    return choice


# ── Phrase pools ─────────────────────────────────────────────────────────────

_ACK_PHRASES = [
    "Sure.", "Absolutely.", "Let's see.", "One second.", "Got it.",
    "Okay.", "Let me check.",
]

_DELAY_PHRASES = [
    "This usually only takes a moment.",
    "Almost there.",
    "Google Flights is still loading.",
    "Just a moment longer.",
]

_OPENING_BROWSER_PHRASES = [
    "I'm opening Google Flights for you.",
    "Let's pull up Google Flights.",
    "Opening Google Flights now.",
]


def narrate(event: str, ctx: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Returns the natural-language line to speak for *event*, or None if
    this moment isn't worth narrating (Part 3's smart-narration gate).
    `ctx` carries whatever already-verified facts the composer for this
    event needs — counts, airline names, prices — never invented here."""
    ctx = ctx or {}

    if event in _SUPPRESSED_EVENTS:
        logger.info("[CONVERSATION_SKIPPED] event=%s reason=implementation_detail", event)
        return None

    if event == ConversationEvent.NOTICEABLE_DELAY:
        elapsed_s = ctx.get("elapsed_s", 0.0)
        if elapsed_s < _NOTICEABLE_DELAY_THRESHOLD_S:
            logger.info("[CONVERSATION_SKIPPED] event=%s reason=delay_not_noticeable elapsed_s=%.1f",
                        event, elapsed_s)
            return None
        line = _pick(_DELAY_PHRASES)

    elif event == ConversationEvent.ACKNOWLEDGE:
        line = _pick(_ACK_PHRASES)

    elif event == ConversationEvent.OPENING_BROWSER:
        line = _pick(_OPENING_BROWSER_PHRASES)

    elif event == ConversationEvent.RESULTS_FOUND:
        line = _compose_results_found(ctx)

    elif event == ConversationEvent.COMPARISON_STARTED:
        line = _compose_comparison_started(ctx)

    elif event == ConversationEvent.RECOMMENDATION_READY:
        line = _compose_recommendation(ctx)

    elif event == ConversationEvent.CLARIFICATION_NEEDED:
        line = ctx.get("question", "Could you clarify that?")

    elif event == ConversationEvent.VERIFICATION_STARTED:
        line = _compose_verification_started(ctx)

    elif event == ConversationEvent.FILTER_APPLIED:
        line = _compose_filter_applied(ctx)

    elif event == ConversationEvent.SORT_APPLIED:
        line = _compose_sort_applied(ctx)

    elif event == ConversationEvent.COMPARE_TWO:
        line = _compose_compare_two(ctx)

    elif event == ConversationEvent.BAGGAGE_ANSWER:
        line = ctx.get("text", "")

    elif event == ConversationEvent.CANCELLED:
        line = _pick([
            "Done — I've cancelled that.",
            "Okay, cancelled.",
            "No problem, I've stopped there.",
        ])

    else:
        logger.info("[CONVERSATION_SKIPPED] event=%s reason=unknown_event", event)
        return None

    if not line:
        return None

    _memory.last_event = event
    _memory.last_narrated_at = time.time()
    logger.info("[CONVERSATION_EVENT] event=%s", event)
    logger.info("[CONVERSATION_NARRATE] text=%r", line[:200])
    return line


# ── Composers — reasoning-style lines built from verified facts only ────────

def _compose_results_found(ctx: dict[str, Any]) -> str:
    count = ctx.get("count")
    if count is None:
        return "I've found a few options already."

    if count in _memory.mentioned_counts:
        # Already told the user this exact count earlier this session
        # (e.g. after a filter that didn't change the number) — vary the
        # phrasing rather than flatly repeating "I found N flights" again.
        return _pick([
            "Still the same set of options here.",
            "Same options as before.",
        ])

    _memory.mentioned_counts.add(count)
    opener = _pick([
        "Interesting —", "Good news —", "Alright,", "Okay,",
    ])
    body = _pick([
        f"there are quite a few good options here — {count} in total.",
        f"I've got {count} real options to work with.",
        f"I found {count} flights worth looking at.",
    ])
    tail = _pick([
        "Let me see which ones are actually worth recommending.",
        "Let me sort through these for you.",
        "Give me a second to compare them properly.",
    ])
    return f"{opener} {body} {tail}"


def _compose_comparison_started(ctx: dict[str, Any]) -> str:
    return _pick([
        "Let me see which one gives you the best balance between price and travel time.",
        "Let me weigh these against each other for you.",
        "I'm looking at price, duration, and stops together — not just the cheapest number.",
    ])


def _compose_recommendation(ctx: dict[str, Any]) -> str:
    """Proactive-consultant style: state the recommendation, add one
    genuinely useful contrast, then offer the next real choices — never a
    flat menu readout. Every fact here (airline, price, duration) must
    already be present in ctx — this never fabricates a comparison the
    caller didn't actually verify."""
    cheapest_airline = ctx.get("cheapest_airline")
    cheapest_price = ctx.get("cheapest_price")
    alt_airline = ctx.get("alt_airline")
    alt_reason = ctx.get("alt_reason")  # e.g. "a shorter journey"
    offer = ctx.get("offer", "Would you like me to compare them, or see only the cheapest?")

    if not cheapest_airline:
        return "I've compared what I could, but I couldn't confidently pick a top option from this page."

    _memory.mentioned_airlines.add(cheapest_airline.lower())
    price_part = f" at {cheapest_price}" if cheapest_price else ""
    lines = [f"The cheapest option is {cheapest_airline}{price_part}."]

    if alt_airline and alt_reason:
        if alt_airline.lower() in _memory.mentioned_airlines:
            lines.append(f"That {alt_airline} option I mentioned earlier is pricier, but {alt_reason}.")
        else:
            _memory.mentioned_airlines.add(alt_airline.lower())
            lines.append(f"{alt_airline} is more expensive, but {alt_reason}.")

    lines.append(offer)
    return " ".join(lines)


def _compose_verification_started(ctx: dict[str, Any]) -> str:
    airline = ctx.get("airline")
    if airline:
        return _pick([
            f"Let me check {airline}'s official site — one moment.",
            f"I'll double check that directly with {airline}.",
        ])
    return "Let me check the official site — one moment."


def _compose_filter_applied(ctx: dict[str, Any]) -> str:
    kind = ctx.get("kind", "that")   # "airline" | "stops" | "time"
    value = ctx.get("value", "")
    ok = ctx.get("ok", True)
    if not ok:
        return ctx.get("fail_text") or f"I couldn't find a way to filter by {value} on this page."
    if kind == "airline":
        return _pick([
            f"Filtering to {value} now.", f"Sure, narrowing this down to {value}.",
            f"Got it — {value} only.",
        ])
    if kind == "time":
        return _pick([f"Showing {value} flights now.", f"Sure, {value} flights only."])
    if kind == "stops":
        return _pick([f"Filtering for {value} now.", f"Sure, {value} it is."])
    return f"Filtering by {value} now."


def _compose_sort_applied(ctx: dict[str, Any]) -> str:
    key = ctx.get("key", "")
    ok = ctx.get("ok", True)
    if not ok:
        return ctx.get("fail_text") or f"I couldn't find a way to sort by {key} on this page."
    return _pick([f"Sorting by {key} now.", f"Let's sort by {key}.", f"One second — sorting by {key}."])


def _compose_compare_two(ctx: dict[str, Any]) -> str:
    a_name, a_desc = ctx.get("a_name"), ctx.get("a_desc")
    b_name, b_desc = ctx.get("b_name"), ctx.get("b_desc")
    if not (a_name and b_name):
        return ctx.get("fail_text", "I don't have both of those verified yet.")
    return f"{a_name}: {a_desc}. {b_name}: {b_desc}."
