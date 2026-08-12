"""
FlightSearchAgent — layered flight search, comparison, and recommendation.

Never books, never pays, never submits passenger/payment details. This
module only searches, extracts what it can, and presents options — the
approval gate before "continuing toward booking" is a real blocking wait
(see request_decision()), not just a notice.

Extraction layers (each only runs if the previous yielded zero options):
  1. Google Flights DOM scrape
  2. Visible page text regex extraction
  3. Screenshot capture for visual review (no OCR engine installed in this
     environment — logged honestly, not faked)
  4. Alternate sites (Kayak / Skyscanner / Expedia / Momondo) via search
  5. Honest fallback — summarize page state, ask user to review manually

Required log tags: [FLIGHT_SEARCH_INTENT] [FLIGHT_SEARCH_PARAMS]
[FLIGHT_SITE_SELECTED] [FLIGHT_RESULTS_PAGE_OPENED] [FLIGHT_EXTRACTION_LAYER]
[FLIGHT_RESULTS_FOUND] [FLIGHT_OPTION_COMPARE] [FLIGHT_RECOMMENDATION]
[FLIGHT_APPROVAL_REQUIRED] [VOICE_APPROVAL_DETECTED] [VOICE_APPROVAL_ACCEPTED]
[VOICE_APPROVAL_REJECTED] [VOICE_APPROVAL_CHOICE] [BOOKING_SAFETY_STOP]
[FLIGHT_NARRATION] [TRAVEL_CONSULTANT_REASONING] [FLIGHT_SCORE_CALCULATED]
[TRAVEL_MEMORY_READ] [TRAVEL_MEMORY_WRITE] [TRAVEL_CONTEXT_USED]
[TRAVEL_APPROVAL_REQUIRED] [TRAVEL_APPROVAL_ACCEPTED] [TRAVEL_APPROVAL_REJECTED]
[VOICE_APPROVAL_INTENT] [VOICE_APPROVAL_TARGET] [VOICE_APPROVAL_APPLIED]

Consultant-grade additions (Phase 4.7):
  TravelIntentParser    — origin/destination/date/trip-type/budget/airline/
                          baggage/time preferences from one utterance
  TravelPreferenceMemory — persists preferences + last route to
                          ~/.xyron/travel_memory.json across sessions
  FlightSourceManager   — narrated, ordered multi-site search
  FlightRankingEngine   — weighted price/duration/stops/timing/baggage/
                          source-reliability/confidence scoring
  FlightNarrator        — consultant-style comparative explanations
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import Page

from api.agents.agent_types import AgentTask
from api.agents.browser_agent.browser_navigator import BrowserNavigator
from api.agents.browser_agent.browser_reader import BrowserReader
from api.agents.browser_agent.browser_interactor import BrowserInteractor

logger = logging.getLogger("api.agents.browser_agent.flight_search")

_SCREENSHOT_DIR = Path.home() / ".xyron" / "flight_screenshots"

# Phase 4.11 Part 10 — defer OCR (real CPU work) rather than pile onto an
# already-strained machine. 1-minute load average > 2x core count is a
# reasonable "system is under real pressure" signal on this WSL2 host.
_OCR_LOAD_THRESHOLD = (os.cpu_count() or 4) * 2.0


def _system_load() -> tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return None, None, None

_ALT_SITE_DOMAINS = ("kayak.com", "skyscanner.net", "expedia.com", "momondo.com")


# ── Voice decision parsing ─────────────────────────────────────────────────────

_CHOICE_RE = re.compile(
    r"\b(?:choose|pick|select|go with)\s+(?:the\s+)?(cheapest|fastest|balanced)\b"
    r"|^\s*(cheapest|fastest|balanced)\s*$",
    re.IGNORECASE,
)
# General approval phrases ("yes"/"continue"/"proceed"/"do it"/"go ahead")
# are only meaningful here because callers gate this on
# task.metadata["awaiting_flight_decision"] — see request_decision() below.
_CONTINUE_RE = re.compile(
    r"\b(?:book\s+this|continue\s+(?:to\s+)?booking|yes[,]?\s+continue|proceed\s+with\s+booking"
    r"|proceed|do\s+it|go\s+ahead)\b"
    r"|^\s*(?:continue|yes)\s*$",
    re.IGNORECASE,
)
_MORE_RE = re.compile(r"\b(?:show\s+me\s+more|more\s+options|see\s+more)\b", re.IGNORECASE)
_SAVE_RE = re.compile(r"\b(?:save\s+this|save\s+it|remember\s+this\s+option)\b", re.IGNORECASE)
# Phase 4.8 tab policy: a second tab/source is only ever opened after this
# explicit approval — never automatically just because extraction failed.
_CHECK_OTHER_SITES_RE = re.compile(
    r"\bcheck\s+(?:other|another)\s+sites?\b|\btry\s+(?:another|a\s+different)\s+site\b"
    r"|\bcheck\s+(skyscanner|kayak|expedia|momondo)\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"\b(?:cancel|stop|don'?t\s+book|no\s+booking|never\s?mind|don'?t\s+do\s+it)\b"
    r"|^\s*no\s*$",
    re.IGNORECASE,
)


def parse_flight_decision(text: str) -> Optional[dict]:
    """
    Parse a voice utterance into a flight-decision action, or None if it
    doesn't match any known decision phrase. Stateless and context-free —
    callers should only invoke this while a flight task is actually waiting
    on a decision.
    """
    t = text.strip()

    m = _CHOICE_RE.search(t)
    if m:
        choice = (m.group(1) or m.group(2)).lower()
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=choose choice=%s", choice)
        return {"action": "choose", "choice": choice}
    m2 = _CHECK_OTHER_SITES_RE.search(t)
    if m2:
        site = next((g for g in m2.groups() if g), None)
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=check_other_sites site=%s", site)
        return {"action": "check_other_sites", "site": site}
    if _CANCEL_RE.search(t):
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=cancel")
        return {"action": "cancel"}
    if _CONTINUE_RE.search(t):
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=continue")
        return {"action": "continue"}
    if _MORE_RE.search(t):
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=more_options")
        return {"action": "more_options"}
    if _SAVE_RE.search(t):
        logger.info("[VOICE_APPROVAL_INTENT] target=flight intent=save")
        return {"action": "save"}
    return None


# ── TravelIntentParser — consultant-level parameter extraction ────────────────

_BUDGET_RE = re.compile(
    r"(?:under|below|less\s+than|budget\s+of|around|about|max(?:imum)?)\s*\$?\s*(\d{2,6})",
    re.IGNORECASE,
)
_AVOID_AIRLINE_RE = re.compile(r"\bavoid\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b")
_PREFER_AIRLINE_RE = re.compile(
    r"\b(?:prefer|fly)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+airlines?\b"
    r"|\bwith\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+airlines?\b"
)
_BAGGAGE_RE = re.compile(
    r"\b(checked\s*bag(?:gage)?|carry-?on\s*only|no\s*baggage|with\s*baggage|extra\s*bag(?:gage)?)\b",
    re.IGNORECASE,
)
_TIME_PREF_RE = re.compile(
    r"\b(morning|afternoon|evening|night|red-?eye)\s*flights?\b|\bprefer\s+(morning|afternoon|evening|night)\s*flights?\b",
    re.IGNORECASE,
)
_TRIP_TYPE_RE = re.compile(r"\b(one-?way|round-?trip|return\s*trip)\b", re.IGNORECASE)
_ALWAYS_PREFER_RE = re.compile(r"\balways\s+(?:prefer|choose|book|use|fly)\b", re.IGNORECASE)


class TravelIntentParser:
    """Extracts travel parameters + soft preferences from one utterance.

    Never invents values — every field is None unless the phrase actually
    contained something that maps to it.
    """

    @staticmethod
    def parse(goal: str) -> dict[str, Any]:
        g = goal.strip().rstrip(".!? ")

        budget_m = _BUDGET_RE.search(g)
        budget = int(budget_m.group(1)) if budget_m else None

        avoid_m = _AVOID_AIRLINE_RE.search(g)
        avoid_airline = avoid_m.group(1).strip() if avoid_m else None

        prefer_m = _PREFER_AIRLINE_RE.search(g)
        preferred_airline = None
        if prefer_m:
            preferred_airline = (prefer_m.group(1) or prefer_m.group(2) or "").strip() or None

        baggage_m = _BAGGAGE_RE.search(g)
        baggage_pref = baggage_m.group(1).lower() if baggage_m else None

        time_m = _TIME_PREF_RE.search(g)
        preferred_time = None
        if time_m:
            preferred_time = (time_m.group(1) or time_m.group(2) or "").lower() or None

        trip_m = _TRIP_TYPE_RE.search(g)
        trip_type = trip_m.group(1).lower().replace("-", "").replace(" ", "") if trip_m else None

        is_preference_only = bool(_ALWAYS_PREFER_RE.search(g))

        return {
            "budget": budget,
            "preferred_airline": preferred_airline,
            "avoid_airline": avoid_airline,
            "baggage_pref": baggage_pref,
            "preferred_time": preferred_time,
            "trip_type": trip_type,
            "is_preference_only": is_preference_only,
        }


# ── TravelPreferenceMemory — persisted across sessions ─────────────────────────

_TRAVEL_MEMORY_PATH = Path.home() / ".xyron" / "travel_memory.json"


class TravelPreferenceMemory:
    """Small JSON-backed store for travel preferences + last-searched route.

    Not a database — just enough state to answer "find me another Dubai
    flight" with a remembered default origin, or "always prefer morning
    flights" as a standalone instruction.
    """

    def load(self) -> dict[str, Any]:
        try:
            if _TRAVEL_MEMORY_PATH.exists():
                data = json.loads(_TRAVEL_MEMORY_PATH.read_text(encoding="utf-8"))
                logger.info("[TRAVEL_MEMORY_READ] keys=%s", sorted(data.keys()))
                return data
        except Exception as exc:
            logger.warning("[TRAVEL_MEMORY_READ] error=%r", str(exc))
        return {}

    def save(self, prefs: dict[str, Any]) -> None:
        try:
            _TRAVEL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TRAVEL_MEMORY_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
            logger.info("[TRAVEL_MEMORY_WRITE] keys=%s", sorted(prefs.keys()))
        except Exception as exc:
            logger.warning("[TRAVEL_MEMORY_WRITE] error=%r", str(exc))

    def update(self, intent: dict[str, Any], origin: str = "", destination: str = "") -> dict[str, Any]:
        """Merge non-empty *intent* fields (+ origin/destination if given)
        into stored preferences, persist, and return the merged dict."""
        prefs = self.load()
        for key in ("budget", "preferred_airline", "avoid_airline", "baggage_pref", "preferred_time", "trip_type"):
            val = intent.get(key)
            if val:
                prefs[key] = val
        if origin:
            prefs["default_origin"] = origin
        if destination:
            prefs["last_destination"] = destination
        prefs["last_searched_at"] = time.time()
        self.save(prefs)
        return prefs

    def get_context(self) -> dict[str, Any]:
        """Read stored preferences for use as fallback context. Logs
        [TRAVEL_CONTEXT_USED] since this is specifically the "did we fill
        in a gap from memory" signal, distinct from a plain load()."""
        prefs = self.load()
        if prefs:
            logger.info("[TRAVEL_CONTEXT_USED] fields=%s", sorted(prefs.keys()))
        return prefs


# ── Duration / comparison helpers ──────────────────────────────────────────────

def _duration_minutes(duration_str: str) -> Optional[int]:
    """Parse '5h 30m' / '5 hr 30 min' / '5:30' style strings → minutes."""
    if not duration_str:
        return None
    m = re.search(r"(\d+)\s*h(?:r|rs)?\b.*?(?:(\d+)\s*m(?:in)?)?", duration_str, re.IGNORECASE)
    if m:
        hours = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        return hours * 60 + mins
    m = re.match(r"(\d+):(\d+)", duration_str)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _price_value(price_str: str) -> Optional[float]:
    if not price_str:
        return None
    cleaned = re.sub(r"[^0-9.]", "", price_str)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


# Placeholder/failed-extraction values that must never be spoken as if
# they were a real airline name — "Unknown is both the cheapest and the
# best overall balance" was reported live and is exactly the bug this
# guards against. `_scrape_dom`'s own JS default was also changed from
# the literal string 'Unknown' to '' so a fresh extraction never even
# produces one of these in the first place; this set is the second,
# independent safety net for anything already carrying an old-style
# placeholder (Layer 2's "See site", stale cached options, etc).
_UNRESOLVED_AIRLINE_LABELS = {"", "unknown", "see site", "n/a", "none"}

# Longest-name-first so a substring recovery attempt matches "Air Arabia"
# before the shorter, less specific "Arabia" would (there is no such
# shorter entry today, but this ordering rule is what keeps future
# additions safe). Sourced from the same curated set already used for
# official-site baggage lookups — reusing it here means one list to
# maintain, not two divergent "airlines we know about" sets.
def _known_airline_names() -> list[str]:
    from api.agents.browser_agent.airline_knowledge_service import _GENERAL_BAGGAGE_POLICY
    return sorted(_GENERAL_BAGGAGE_POLICY.keys(), key=len, reverse=True)


def _is_airline_resolved(name: Optional[str]) -> bool:
    return bool(name) and name.strip().lower() not in _UNRESOLVED_AIRLINE_LABELS


def _display_airline(opt: dict) -> str:
    """The single place every spoken/written airline mention goes
    through — returns the real name when extraction succeeded, or an
    honest "an unidentified airline" instead of ever surfacing a
    placeholder like "Unknown" as if it were a real answer."""
    name = (opt or {}).get("airline")
    return name if _is_airline_resolved(name) else "an unidentified airline"


def _recover_airline_name(raw_text: str) -> Optional[str]:
    """Second-chance airline recovery: the DOM regex that produces the
    per-card `airline` field is deliberately simple (a single leading-
    capitalized-line pattern) and can miss real airline names embedded
    elsewhere in the card's text — this checks the same real, already-
    extracted card text against the full curated airline-name list
    instead of just the one regex shape. Never invents a name: returns
    None (honest "still unresolved") if nothing in the curated list
    actually appears in the text."""
    if not raw_text:
        return None
    lowered = raw_text.lower()
    for name in _known_airline_names():
        if name.lower() in lowered:
            return name
    return None


def compare_and_recommend(options: list[dict], prefs: Optional[dict] = None) -> dict:
    """
    Rank *options* and pick cheapest / fastest / balanced.
    Returns {"cheapest": opt|None, "fastest": opt|None, "balanced": opt|None}.
    Never fabricates data — options missing price/duration just aren't
    eligible for that particular ranking.

    When both price and duration are available for at least one option,
    "balanced" is chosen by FlightRankingEngine's weighted score (price +
    duration + stops + source reliability + confidence + preference fit)
    rather than a plain rank-sum, so preferences (budget/airline/time)
    actually move the recommendation.
    """
    prefs = prefs or {}
    priced = [o for o in options if _price_value(o.get("price", "")) is not None]
    timed = [o for o in options if _duration_minutes(o.get("duration", "")) is not None]

    cheapest = min(priced, key=lambda o: _price_value(o["price"])) if priced else None
    fastest = min(timed, key=lambda o: _duration_minutes(o["duration"])) if timed else None

    balanced = None
    both = [o for o in options if o in priced and o in timed]
    if both:
        ranked = FlightRankingEngine.rank(both, prefs)
        balanced = ranked[0] if ranked else None
    elif priced:
        balanced = cheapest

    # Best-baggage-value / best-refundable only when at least one option
    # actually carries verified data for that field — never invented.
    with_baggage = [o for o in options if o.get("baggage_kg")]
    best_baggage = max(with_baggage, key=lambda o: o["baggage_kg"]) if with_baggage else None
    refundable_opts = [o for o in options if o.get("refundable") is True]
    best_refundable = (min(refundable_opts, key=lambda o: _price_value(o.get("price", "")) or 1e9)
                       if refundable_opts else None)

    logger.info(
        "[FLIGHT_OPTION_COMPARE] total=%d priced=%d timed=%d cheapest=%r fastest=%r balanced=%r",
        len(options), len(priced), len(timed),
        (cheapest or {}).get("price"), (fastest or {}).get("duration"), (balanced or {}).get("price"),
    )

    recommendation = "balanced" if balanced else ("cheapest" if cheapest else "fastest")
    logger.info("[FLIGHT_RECOMMENDATION] pick=%s", recommendation)

    top_pick = balanced or cheapest or fastest
    if top_pick is not None and not _is_airline_resolved(top_pick.get("airline")):
        logger.info(
            "[FLIGHT_RECOMMENDATION_DOWNGRADED] reason=top_pick_airline_unresolved price=%s duration=%s",
            top_pick.get("price"), top_pick.get("duration"),
        )

    return {
        "cheapest": cheapest,
        "fastest": fastest,
        "balanced": balanced,
        "best_baggage_value": best_baggage,
        "best_refundable_option": best_refundable,
        "recommendation": recommendation,
    }


# ── FlightRankingEngine — weighted multi-factor scoring ────────────────────────

class FlightRankingEngine:
    """
    Scores flight options on price, duration, stops, departure/arrival
    timing, source reliability, and extraction confidence — then nudges
    the score with soft preferences (preferred/avoided airline, budget,
    preferred time of day). Lower score = better. Never used to invent
    data: options missing price/duration simply score using whatever
    normalized signal is actually present.
    """

    _SOURCE_RELIABILITY = {
        "google_flights": 1.0, "kayak": 0.9, "skyscanner": 0.9,
        "expedia": 0.85, "momondo": 0.8, "unknown": 0.6,
    }
    _CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.85, "low": 0.6}

    # Configurable component weights (Part 8 v2) — tunable without
    # touching the scoring logic itself.
    WEIGHTS = {
        "price": 1.0, "duration": 1.0, "stops": 0.3,
        "reliability_penalty": 1.0, "confidence_penalty": 1.0,
        "airline_preference": 0.15, "airline_avoid": 0.3, "budget_penalty": 0.2,
        "time_preference": 0.1, "baggage_bonus": 0.15, "refundable_bonus": 0.1,
    }

    @classmethod
    def score(cls, opt: dict, prefs: dict) -> float:
        w = cls.WEIGHTS
        price = _price_value(opt.get("price", ""))
        duration = _duration_minutes(opt.get("duration", ""))
        stops_str = (opt.get("stops") or "").lower()
        stops_val = 0
        if stops_str and "nonstop" not in stops_str:
            m = re.search(r"(\d+)", stops_str)
            stops_val = int(m.group(1)) if m else 1

        norm_price = ((price / 1000.0) if price is not None else 1.0) * w["price"]
        norm_duration = ((duration / 600.0) if duration is not None else 1.0) * w["duration"]
        norm_stops = stops_val * w["stops"]

        reliability = cls._SOURCE_RELIABILITY.get(opt.get("source", "unknown"), 0.6)
        confidence = cls._CONFIDENCE_WEIGHT.get(opt.get("confidence", "low"), 0.6)

        raw = norm_price + norm_duration + norm_stops
        reliability_factor = 1 + (1 - reliability) * w["reliability_penalty"]
        confidence_factor = 1 + (1 - confidence) * w["confidence_penalty"]
        weighted = raw * reliability_factor * confidence_factor
        components = {
            "price": norm_price, "duration": norm_duration, "stops": norm_stops,
            "reliability_factor": round(reliability_factor, 3), "confidence_factor": round(confidence_factor, 3),
        }

        airline = (opt.get("airline") or "").lower()
        if prefs.get("preferred_airline") and prefs["preferred_airline"].lower() in airline:
            weighted *= (1 - w["airline_preference"])
            components["airline_preference_bonus"] = -w["airline_preference"]
        if prefs.get("avoid_airline") and prefs["avoid_airline"].lower() in airline:
            weighted *= (1 + w["airline_avoid"])
            components["airline_avoid_penalty"] = w["airline_avoid"]
        if prefs.get("budget") and price is not None and price > prefs["budget"]:
            weighted *= (1 + w["budget_penalty"])
            components["budget_penalty"] = w["budget_penalty"]
        if prefs.get("preferred_time"):
            dep = (opt.get("departure") or "").lower()
            wants_am = prefs["preferred_time"] == "morning"
            wants_pm = prefs["preferred_time"] in ("afternoon", "evening", "night")
            if (wants_am and "am" in dep) or (wants_pm and "pm" in dep):
                weighted *= (1 - w["time_preference"])
                components["time_preference_bonus"] = -w["time_preference"]
        baggage_kg = opt.get("baggage_kg")
        if baggage_kg and prefs.get("baggage_kg") and baggage_kg >= prefs["baggage_kg"]:
            weighted *= (1 - w["baggage_bonus"])
            components["baggage_bonus"] = -w["baggage_bonus"]
        if opt.get("refundable") is True:
            weighted *= (1 - w["refundable_bonus"])
            components["refundable_bonus"] = -w["refundable_bonus"]

        logger.info("[FLIGHT_SCORE_COMPONENT] airline=%s components=%s", opt.get("airline"), components)
        return round(weighted, 4)

    @classmethod
    def rank(cls, options: list[dict], prefs: dict) -> list[dict]:
        for opt in options:
            s = cls.score(opt, prefs)
            opt["score"] = s
            logger.info(
                "[FLIGHT_SCORE_CALCULATED] airline=%s price=%s duration=%s stops=%s source=%s score=%.3f",
                opt.get("airline"), opt.get("price"), opt.get("duration"), opt.get("stops"),
                opt.get("source", "unknown"), s,
            )
        ranked = sorted(options, key=lambda o: o.get("score", 999))
        logger.info("[FLIGHT_RANKING_COMPLETE] count=%d", len(ranked))
        return ranked


# ── FlightNarrator — consultant-style comparative reasoning ───────────────────

class FlightNarrator:
    """Turns a ranking into plain-English reasoning a travel consultant
    would say out loud, instead of just reciting numbers."""

    @staticmethod
    def explain(ranking: dict) -> str:
        cheapest = ranking.get("cheapest")
        fastest = ranking.get("fastest")
        balanced = ranking.get("balanced")
        lines: list[str] = []

        if cheapest and fastest and cheapest is not fastest:
            c_price = _price_value(cheapest.get("price", ""))
            f_price = _price_value(fastest.get("price", ""))
            c_dur = _duration_minutes(cheapest.get("duration", ""))
            f_dur = _duration_minutes(fastest.get("duration", ""))
            if c_price is not None and f_price is not None and c_price < f_price:
                line = f"I found a cheaper option with {_display_airline(cheapest)}, but "
                if c_dur and f_dur and c_dur > f_dur:
                    line += "it arrives much later."
                else:
                    line += "it's worth double-checking the schedule works for you."
                lines.append(line)
            if _is_airline_resolved(fastest.get("airline")):
                lines.append(f"{fastest.get('airline')} costs more, but the timing is better.")

        if balanced is not None and balanced not in (cheapest, fastest):
            lines.append(
                f"{_display_airline(balanced)} is the balanced choice — it keeps the "
                f"price reasonable and avoids a long layover."
            )
        elif balanced is not None and balanced is cheapest:
            lines.append(f"{_display_airline(balanced)} is both the cheapest and the best overall balance.")

        # Evidence-qualified baggage mention — never implies certainty
        # above what was actually extracted from the page.
        top_pick = balanced or cheapest or fastest
        evidence_items: list[str] = []
        if top_pick is not None:
            if top_pick.get("baggage_kg"):
                lines.append(f"The fare information shows {top_pick['baggage_kg']} kilograms of checked baggage.")
                evidence_items.append("baggage_kg")
            else:
                lines.append("I can verify the timing and price, but not the fare-specific baggage allowance yet.")
            if top_pick.get("price"):
                evidence_items.append("price")
            if top_pick.get("duration"):
                evidence_items.append("duration")

        text = " ".join(lines) if lines else "Here's what I found — let me know which one you'd like."
        logger.info("[TRAVEL_CONSULTANT_REASONING] text=%r", text[:200])
        logger.info("[TRAVEL_RECOMMENDATION_EVIDENCE] verified_fields=%s", evidence_items)
        confidence_label = "high" if len(evidence_items) >= 2 else ("medium" if evidence_items else "low")
        logger.info("[TRAVEL_RECOMMENDATION_CONFIDENCE] level=%s verified_field_count=%d",
                    confidence_label, len(evidence_items))
        return text


def _field(opt: dict, key: str, unit: str = "") -> str:
    val = opt.get(key)
    if not val:
        return "Not available"
    return f"{val}{unit}"


def format_comparison(options: list[dict], ranking: dict) -> str:
    """Build the required structured comparison text. Never invents values —
    anything not actually extracted is shown as 'Not available'."""
    if not options:
        return ""

    labels = {
        "cheapest": ("Cheapest", "Cheapest option."),
        "fastest": ("Fastest", "Shortest travel time."),
        "balanced": ("Balanced", "Good balance of price and timing."),
    }
    seen_ids: set[int] = set()
    lines: list[str] = []
    n = 0
    for key in ("cheapest", "fastest", "balanced"):
        opt = ranking.get(key)
        if not opt or id(opt) in seen_ids:
            continue
        seen_ids.add(id(opt))
        n += 1
        title, why = labels[key]
        lines.append(f"Option {n} — {title}")
        lines.append(_display_airline(opt).capitalize())
        lines.append(f"Price: {_field(opt, 'price')}")
        lines.append(f"Duration: {_field(opt, 'duration')}")
        lines.append(f"Stops: {_field(opt, 'stops')}")
        lines.append(f"Departure: {_field(opt, 'departure')}")
        lines.append(f"Arrival: {_field(opt, 'arrival')}")
        lines.append(f"Why: {why}")
        lines.append("")
    return "\n".join(lines).strip()


# ── DOM / text extraction (layers 1-2) ─────────────────────────────────────────

async def _scrape_dom(page: Page) -> list[dict]:
    return await page.evaluate(
        """
        () => {
            const cards = document.querySelectorAll('[data-gs], li[class*="pIav2d"], .yR1fYc');
            const results = [];
            const seen = new Set();
            cards.forEach(card => {
                const text = card.innerText || '';
                // Prices render as either a currency symbol ($1,234) or a
                // currency code (PKR 132,300) depending on the account/
                // browser locale -- Google Flights defaults to the latter
                // for many non-US locales, which the symbol-only pattern
                // silently missed entirely (0 options extracted despite a
                // fully-rendered results page). The whitespace between
                // code and digits is deliberately restricted to a plain
                // space/NBSP (not \\s, which also matches newlines) --
                // otherwise an airport code like "IST" immediately
                // followed by a "1 stop" line below it false-matches as
                // a price.
                const priceMatch = text.match(/[$\u20ac\u00a3\u00a5\u20b9][\\d,]+|\\b[A-Z]{3}[ \u00a0][\\d,]+/);
                const timeMatch = text.match(/\\d{1,2}:\\d{2}\\s?(AM|PM)/gi);
                // Layover annotations ("3 hr 5 min DXB") match the same
                // pattern as the flight's total duration -- exclude any
                // match immediately followed by a 3-letter airport code.
                const durMatch = text.match(/\\d+\\s?hr?\\s?\\d*\\s?min?(?! [A-Z]{3}\\b)/i);
                const stopsMatch = text.match(/nonstop|\\d+\\s?stop/i);
                // The selector list matches overlapping/nested elements for
                // the same visual card on the current Google Flights markup
                // (parent + child both match different alternatives), so
                // the airline name isn't reliably at the very start of
                // every matched node's text -- search the whole card
                // instead of anchoring to its first line.
                const airlineMatch = text.match(/\\n([A-Z][a-zA-Z]+(?:[A-Z][a-zA-Z]+)*)\\n\\d+\\s?hr/);
                if (priceMatch) {
                    const times = timeMatch || [];
                    // A matched node that carries a price but no departure
                    // time and no duration is a fragment of a larger card
                    // (the overlapping selector list matches nested
                    // elements for the same visual card) or the standalone
                    // "cheapest fare" summary badge -- not a distinct
                    // flight option, so skip it rather than report a
                    // near-empty duplicate.
                    if (!times[0] && !durMatch) return;
                    const dedupeKey = priceMatch[0] + '|' + (times[0] || '') + '|' + (times[1] || '');
                    if (seen.has(dedupeKey)) return;
                    seen.add(dedupeKey);
                    results.push({
                        price: priceMatch[0],
                        departure: times[0] || '',
                        arrival: times[1] || '',
                        duration: durMatch ? durMatch[0] : '',
                        stops: stopsMatch ? stopsMatch[0] : '',
                        // '' (never the literal string 'Unknown') when the
                        // regex doesn't find a name -- Python-side code
                        // treats a real, honest "not resolved" value very
                        // differently from a string that reads like an
                        // actual airline called "Unknown".
                        airline: airlineMatch ? airlineMatch[1].trim() : '',
                        // Kept only long enough for a second-chance airline
                        // recovery attempt against the full curated airline
                        // list (_recover_airline_name) -- stripped back out
                        // before these options are stored/returned.
                        _raw_text: text.slice(0, 300),
                    });
                }
            });
            return results.slice(0, 8);
        }
        """
    )


def _parse_text(text: str) -> list[dict]:
    # Matches either a currency symbol ($1,234.56) or a currency code
    # (PKR 132,300) — Google Flights renders the latter for many
    # non-US-locale accounts, which this regex previously missed entirely.
    price_re = re.compile(r"[$€£¥₹][\d,]+(?:\.\d{2})?|\b[A-Z]{3}\s?[\d,]+(?:\.\d{2})?\b")
    time_re = re.compile(r"\d{1,2}:\d{2}\s?(?:AM|PM)", re.IGNORECASE)
    dur_re = re.compile(r"\d+\s?hr?\s?\d*\s?min?", re.IGNORECASE)
    stops_re = re.compile(r"nonstop|\d+\s?stop[s]?", re.IGNORECASE)

    prices = price_re.findall(text)
    times = time_re.findall(text)
    durations = dur_re.findall(text)
    stops = stops_re.findall(text)

    results: list[dict] = []
    for i, price in enumerate(prices[:8]):
        results.append({
            "airline": "See site",
            "price": price,
            "departure": times[2 * i] if 2 * i < len(times) else "",
            "arrival": times[2 * i + 1] if 2 * i + 1 < len(times) else "",
            "duration": durations[i] if i < len(durations) else "",
            "stops": stops[i] if i < len(stops) else "",
        })
    return results


# ── Public entry point ─────────────────────────────────────────────────────────

# Ordered alternate sources tried when the primary source (Google Flights)
# doesn't yield at least 3 options. Each gets its own narration line so
# there is no long silent period while the agent works.
# Alternate sources are NEVER auto-tried (Phase 4.8 tab policy) — a
# second tab only opens if the user explicitly approves checking another
# source, and then exactly one, via request_decision()'s "check_other_sites"
# action + BrowserWorkspace.new_tab_if_approved(). This dict just maps a
# spoken site name to a site-search domain for that one approved attempt.
ALT_SOURCE_DOMAINS: dict[str, str] = {
    "skyscanner": "skyscanner.net", "kayak": "kayak.com",
    "expedia": "expedia.com", "momondo": "momondo.com",
}


async def search_and_compare(
    page: Page,
    navigator: BrowserNavigator,
    origin: str,
    destination: str,
    date: str,
    task: AgentTask,
    prefs: Optional[dict] = None,
) -> dict:
    """
    Runs the full layered, multi-source search → compare → recommend
    pipeline, narrating like a travel consultant instead of going silent
    while it works. Never hallucinates a price — anything not actually
    extracted is left out or marked "Not available".

    Returns {"options": [...], "ranking": {...}, "spoken": str, "layer_used": int,
             "sources_searched": [...], "urls_opened": [...]}.
    """
    reader = BrowserReader()
    interactor = BrowserInteractor()
    prefs = prefs or {}

    from api.agents.browser_agent.flight_narration import FlightStage, speak_stage

    def _narrate(msg: str, stage: str = FlightStage.SEARCHING_FLIGHTS) -> None:
        """Speaks *msg* live while the browser automation keeps running.
        Fire-and-forget: `task.ws_send_fn` is scheduled as its own task
        rather than awaited here, so a slow TTS turnaround never blocks
        the Playwright actions below it (Phase 4.11.2 continuous
        narration — the previous version only logged, so the user heard
        nothing until the whole search finished). Delegates to the
        canonical flight_narration.speak_stage so the log trail, the
        frontend status, and the spoken line are always one event
        (Phase 4.11.3)."""
        logger.info("[FLIGHT_NARRATION] text=%r", msg)
        speak_stage(stage, msg, task=task)

    logger.info("[FLIGHT_SEARCH_INTENT] origin=%r destination=%r date=%r", origin, destination, date)
    logger.info("[FLIGHT_SEARCH_PARAMS] origin=%r destination=%r date=%r", origin, destination, date)

    options: list[dict] = []
    layer_used = 0
    sources_searched: list[str] = []
    urls_opened: list[str] = []

    # Phase 4.11: never build/navigate to a malformed query. If either
    # end of the route is missing, stay on the (already-visible) Google
    # Flights home page and ask honestly instead of guessing or showing
    # an empty/garbage search result.
    if not origin or not destination:
        missing = "destination" if not destination else "origin"
        logger.info("[EMPTY_QUERY_BLOCKED] missing=%s origin=%r destination=%r", missing, origin, destination)
        question = "What destination should I use?" if missing == "destination" else "Which city are you flying from?"
        return {
            "options": [], "ranking": {}, "spoken": question, "layer_used": 0,
            "sources_searched": [], "urls_opened": [], "origin": origin, "destination": destination, "date": date,
        }

    query = f"flights from {origin} to {destination} on {date}".strip()
    logger.info("[FLIGHT_URL_BUILT] query=%r", query)

    # ── Layer 1: Google Flights DOM ─────────────────────────────────────────
    from api.agents.browser_agent.conversation_layer import narrate as _cl_narrate, ConversationEvent as _CE
    _route_line = (
        f"I'm checking flights from {origin} to {destination} on Google Flights first."
        if origin and destination else "I'm checking Google Flights first."
    )
    _narrate(_route_line, stage=FlightStage.SEARCHING_FLIGHTS)
    logger.info("[FLIGHT_SITE_SELECTED] site=google_flights")
    sources_searched.append("google_flights")
    gf_url = "https://www.google.com/travel/flights?q=" + query.replace(" ", "+")
    logger.info("[FLIGHT_URL_VALIDATED] url=%s", gf_url)
    logger.info("[FLIGHT_NAVIGATION_STARTED] url=%s", gf_url)
    logger.info("[BROWSER_ACTION] action=navigate url=%s", gf_url)
    # mirror=False: this page IS the one visible, controllable browser
    # window (BrowserWorkspace/WSLg Chromium) — no separate real-Chrome
    # mirror needed or wanted, that would just be a second, divergent tab.
    _nav_t0 = time.time()
    ok = await navigator.go_to(gf_url, mirror=False)
    if ok:
        logger.info("[FLIGHT_NAVIGATION_COMPLETE] url=%s", gf_url)
        urls_opened.append(gf_url)
        logger.info("[FLIGHT_RESULTS_PAGE_OPENED] url=%s", gf_url)
        # Phase 4.13: this used to unconditionally say "I'm waiting for the
        # page to finish loading" — factually backwards (navigation had
        # already succeeded by this point) and robotic regardless. Now
        # only narrated when the navigation itself genuinely took a
        # noticeable while — a real signal this particular load is slow,
        # not routine filler for every search.
        _delay_line = _cl_narrate(_CE.NOTICEABLE_DELAY, {"elapsed_s": time.time() - _nav_t0})
        if _delay_line:
            _narrate(_delay_line, stage=FlightStage.LOADING_RESULTS)
        _mp_cookie_t0 = time.time()
        await navigator.handle_cookie_banner()
        logger.info("[MICRO_PROFILE] op=handle_cookie_banner_call total_ms=%.1f",
                    (time.time() - _mp_cookie_t0) * 1000)
        # Phase 4.15: this was a flat, unconditional 1.5s sleep regardless of
        # whether results had already rendered — an artificial delay Part 14
        # asked to remove. Wait for the actual results-card selector Layer 1
        # is about to query (same selector, so this can never wait for the
        # "wrong" thing) and proceed the moment it appears; 1.5s remains the
        # worst case, not the typical case.
        _mp_wait_t0 = time.time()
        _mp_wait_selector = '[data-gs], li[class*="pIav2d"], .yR1fYc'
        _mp_wait_timeout = 1500
        try:
            await page.wait_for_selector(_mp_wait_selector, timeout=_mp_wait_timeout)
            logger.info(
                "[MICRO_PROFILE] op=wait_for_selector selector=%r timeout_ms=%d "
                "outcome=found wait_ms=%.1f",
                _mp_wait_selector, _mp_wait_timeout, (time.time() - _mp_wait_t0) * 1000,
            )
        except Exception:
            logger.info(
                "[MICRO_PROFILE] op=wait_for_selector selector=%r timeout_ms=%d "
                "outcome=timed_out wait_ms=%.1f",
                _mp_wait_selector, _mp_wait_timeout, (time.time() - _mp_wait_t0) * 1000,
            )
            pass  # not there yet — Layer 1/2/3 fallbacks handle a truly empty page
        logger.info("[FLIGHT_EXTRACTION_LAYER] layer=1 method=dom site=google_flights")
        logger.info("[BROWSER_ACTION] action=read_results_grid")
        try:
            _mp_scrape_t0 = time.time()
            options = await _scrape_dom(page)
            logger.info("[MICRO_PROFILE] op=scrape_dom_evaluate scrape_ms=%.1f options_found=%d",
                        (time.time() - _mp_scrape_t0) * 1000, len(options))
            for o in options:
                o.update(source="google_flights", booking_url=gf_url, confidence="high",
                          baggage_notes="Not specified", refund_notes="Not specified")
                if not _is_airline_resolved(o.get("airline")):
                    logger.info("[FLIGHT_AIRLINE_UNKNOWN] price=%s duration=%s", o.get("price"), o.get("duration"))
                    logger.info("[FLIGHT_AIRLINE_RECOVERY_ATTEMPT] method=curated_name_substring_match")
                    recovered = _recover_airline_name(o.get("_raw_text", ""))
                    if recovered:
                        o["airline"] = recovered
                        logger.info("[FLIGHT_AIRLINE_RECOVERY_SUCCESS] airline=%s", recovered)
                    else:
                        o["airline"] = ""
                        o["confidence"] = "low"
                        logger.info("[FLIGHT_AIRLINE_RECOVERY_FAILED] confidence_downgraded=true")
                o.pop("_raw_text", None)
            if options:
                _found_line = _cl_narrate(_CE.RESULTS_FOUND, {"count": len(options)})
                if _found_line:
                    _narrate(_found_line, stage=FlightStage.EXTRACTING_OPTIONS)
                _ux_t0_results = task.metadata.get("_ux_t0")
                if _ux_t0_results:
                    logger.info("[PERCEIVED_LATENCY] stage=results_displayed speech_to_results_ms=%.0f",
                                (time.time() - _ux_t0_results) * 1000)
        except Exception as exc:
            logger.warning("[FLIGHT_EXTRACTION_LAYER] layer=1 error=%r", str(exc))
        if options:
            layer_used = 1

    # ── Layer 2: visible page text (same Google Flights page) ──────────────
    if not options:
        _narrate("I'm still pulling these up.", stage=FlightStage.EXTRACTING_OPTIONS)
        logger.info("[FLIGHT_EXTRACTION_LAYER] layer=2 method=text site=google_flights")
        try:
            text = await reader.summarize_page(page, max_chars=2000)
            options = _parse_text(text)
            for o in options:
                o.update(source="google_flights", booking_url=gf_url, confidence="medium",
                          baggage_notes="Not specified", refund_notes="Not specified")
        except Exception as exc:
            logger.warning("[FLIGHT_EXTRACTION_LAYER] layer=2 error=%r", str(exc))
        if options:
            layer_used = 2

    # ── Layer 3: screenshot + OCR fallback (Phase 4.10) ─────────────────────
    screenshot_path: Optional[str] = None
    if not options:
        # Phase 4.15: this used to claim "I found a few options" at the
        # exact point where zero options exist (that's why Layer 3 runs at
        # all) — narration must be truthful to current, verified state.
        _narrate("This page is trickier than usual — let me take a closer look.",
                 stage=FlightStage.EXTRACTING_OPTIONS)
        logger.info("[FLIGHT_EXTRACTION_LAYER] layer=3 method=screenshot_ocr")
        try:
            _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(_SCREENSHOT_DIR / f"{task.task_id}_flights.png")
            await interactor.take_screenshot(page, screenshot_path)
            logger.info("[FLIGHT_EXTRACTION_LAYER] layer=3 note=screenshot_saved path=%s", screenshot_path)

            # Phase 4.11 Part 10: OCR (easyocr) is real CPU work — defer it
            # under high system load rather than piling more work onto an
            # already-strained machine. The screenshot is still saved for
            # later/manual review either way.
            ocr_options: list = []
            _load1, _, _ = _system_load()
            if _load1 is not None and _load1 > _OCR_LOAD_THRESHOLD:
                logger.info("[SYSTEM_PRESSURE_HIGH] load1=%.2f threshold=%.1f", _load1, _OCR_LOAD_THRESHOLD)
                logger.info("[BACKGROUND_TASK_DEFERRED] task=ocr_extraction reason=high_system_load")
            else:
                from api.agents.browser_agent.flight_option_extractor import ocr_extract_options
                ocr_options = ocr_extract_options(screenshot_path, source="google_flights", booking_url=gf_url)
            for o in ocr_options:
                o_dict = o.to_dict()
                # Normalize field names to the departure/arrival convention
                # the rest of this module (format_comparison, ranking) uses.
                o_dict["departure"] = o_dict.pop("departure_time", None) or ""
                o_dict["arrival"] = o_dict.pop("arrival_time", None) or ""
                o_dict.setdefault("baggage_notes", "Not specified")
                o_dict.setdefault("refund_notes", "Not specified")
                o_dict["confidence"] = "low"
                options.append(o_dict)
            if options:
                layer_used = 3
        except Exception as exc:
            logger.warning("[FLIGHT_EXTRACTION_LAYER] layer=3 error=%r", str(exc))

    # ── Layer 4: NOT auto-tried (Phase 4.8 tab policy) ──────────────────────
    # Alternate sites (Skyscanner/Kayak/Expedia/Momondo) are never opened
    # automatically just because Google Flights extraction failed — that
    # was exactly the "opens multiple tabs, searches on Google" behavior
    # flagged as unwanted. Offer it as a question instead; request_decision()
    # below handles "check other sites" as one more approval action, and
    # opens exactly one additional approved tab if the user says yes.
    if not options:
        logger.info("[FLIGHT_EXTRACTION_LAYER] layer=4 method=alt_site_search status=deferred_pending_approval")

    # ── Layer 5: honest fallback ─────────────────────────────────────────────
    if not options:
        logger.info("[FLIGHT_EXTRACTION_LAYER] layer=5 method=honest_fallback")
        layer_used = 5

    logger.info("[FLIGHT_RESULTS_FOUND] count=%d layer=%d", len(options), layer_used)

    if options:
        # Persist so follow-up turns (e.g. "open the first one" ->
        # "do they allow 20kg baggage" -> "yes, check the official site")
        # have a real booking_url to work with. Previously nothing wrote
        # to FlightSessionState.last_verified_options at all, so
        # `selected_flight` could never be populated and the official-site
        # verification path was structurally unreachable.
        from api.agents.browser_agent import flight_session_state as _fss
        _fss.update(last_verified_options=options)

    ranking: dict = {}
    if options:
        _compare_line = _cl_narrate(_CE.COMPARISON_STARTED)
        if _compare_line:
            _narrate(_compare_line, stage=FlightStage.COMPARING_PRICE)
        ranking = compare_and_recommend(options, prefs)
        comparison_text = format_comparison(options, ranking)
        reasoning = FlightNarrator.explain(ranking)

        # Phase 4.13: proactive-consultant framing instead of a flat
        # "here's a table + here's a menu" dump. Every fact fed to the
        # composer comes straight from `ranking` (already-verified
        # extraction/ranking data) — nothing here is invented.
        cheapest = ranking.get("cheapest")
        fastest = ranking.get("fastest")
        rec_ctx: dict = {}
        if cheapest is not None:
            rec_ctx["cheapest_airline"] = _display_airline(cheapest)
            rec_ctx["cheapest_price"] = cheapest.get("price") or None
            if fastest is not None and fastest is not cheapest:
                rec_ctx["alt_airline"] = _display_airline(fastest)
                rec_ctx["alt_reason"] = "it's a faster route"
            rec_ctx["offer"] = (
                "Would you like me to compare them, see only the cheapest, "
                "or filter by a specific airline or baggage allowance?"
            )
        _lead = _cl_narrate(_CE.RECOMMENDATION_READY, rec_ctx) if rec_ctx else None
        spoken = _lead + f"\n\n{reasoning}" if _lead else (
            f"I found {len(options)} option{'s' if len(options) != 1 else ''}. {reasoning}"
        )
        # State-only (speak=False): the full spoken text above is already
        # delivered via the normal final-turn-response path (the caller
        # TTS's the returned "spoken" string) — this just makes the
        # frontend-facing stage/log trail reflect WAITING_FOR_PREFERENCE
        # without triggering a second, duplicate TTS utterance.
        speak_stage(FlightStage.WAITING_FOR_PREFERENCE, spoken, task=task, speak=False)
    else:
        spoken = (
            "I couldn't reliably extract exact prices from Google Flights. I won't guess at a price "
            "I didn't actually see — I can still show you the page as-is, or check one more source "
            "like Skyscanner or Kayak if you'd like — just say \"check other sites\"."
        )
        if screenshot_path:
            spoken += f" I saved a screenshot for you to review at {screenshot_path}."

    approval_msg = " Before I go any further toward booking, just say the word to confirm."
    spoken += approval_msg
    logger.info("[FLIGHT_APPROVAL_REQUIRED] task=%s options_found=%d", task.task_id, len(options))
    logger.info("[TRAVEL_APPROVAL_REQUIRED] task=%s reason=pre_booking_confirmation", task.task_id)

    payload = {
        "type": "options_found",
        "task_id": task.task_id,
        "options": options,
        "ranking": {k: v for k, v in ranking.items() if k != "recommendation"},
        "recommendation": ranking.get("recommendation"),
        "spoken_summary": spoken,
        "sources_searched": sources_searched,
        "urls_opened": urls_opened,
    }
    if task.ws_send_fn is not None:
        try:
            await task.ws_send_fn(payload)
        except Exception as exc:
            logger.warning("[BROWSER_WS_SEND_ERROR] error=%r", str(exc))

    return {
        "options": options,
        "ranking": ranking,
        "spoken": spoken,
        "layer_used": layer_used,
        "sources_searched": sources_searched,
        "urls_opened": urls_opened,
        "origin": origin,
        "destination": destination,
        "date": date,
    }


# ── Voice-driven decision gate (real block, not just a notice) ────────────────

async def request_decision(
    task: AgentTask, result: dict, cancel_event: asyncio.Event,
    timeout_s: float = 120.0, round_timeout_s: float = 60.0,
) -> str:
    """
    Block waiting for real voice decisions on the presented flight options —
    a genuine multi-round conversation, not a single-shot gate. "choose
    cheapest/fastest/balanced", "show me more", and "save this" all narrate
    a response and then keep waiting; only "cancel"/"stop"/"don't book" or
    "continue"/"book this" end the wait (both end in a safety stop — this
    agent never books or pays, ever).

    Sets task.metadata["awaiting_flight_decision"] so voice_ws.py knows to
    route the next utterance here via parse_flight_decision(), and reads
    task.metadata["flight_decision"] once voice_ws.py sets it.
    """
    from api.agents.agent_types import AgentStatus

    task.status = AgentStatus.WAITING_APPROVAL
    total_waited = 0.0
    responses: list[str] = []

    while total_waited < timeout_s:
        task.metadata["awaiting_flight_decision"] = True
        waited = 0.0
        decision: Optional[dict] = None
        while waited < round_timeout_s and total_waited < timeout_s:
            if cancel_event.is_set():
                task.metadata["awaiting_flight_decision"] = False
                task.status = AgentStatus.RUNNING
                logger.info("[VOICE_APPROVAL_REJECTED] task=%s reason=cancelled", task.task_id)
                return " ".join(responses + ["Cancelled. No booking was started."])
            pending = task.metadata.get("flight_decision")
            if pending:
                decision = pending
                task.metadata["flight_decision"] = None
                break
            await asyncio.sleep(0.5)
            waited += 0.5
            total_waited += 0.5

        task.metadata["awaiting_flight_decision"] = False

        if decision is None:
            task.status = AgentStatus.RUNNING
            return " ".join(responses + ["No response received in time — stopping here safely. Nothing was booked."])

        action = decision.get("action")
        logger.info("[VOICE_APPROVAL_DETECTED] task=%s action=%s", task.task_id, action)

        logger.info("[VOICE_APPROVAL_TARGET] task=%s target=flight", task.task_id)

        if action == "cancel":
            task.status = AgentStatus.RUNNING
            logger.info("[VOICE_APPROVAL_REJECTED] task=%s", task.task_id)
            logger.info("[TRAVEL_APPROVAL_REJECTED] task=%s", task.task_id)
            logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=cancel", task.task_id)
            return " ".join(responses + ["Cancelled. No booking was started."])

        if action == "continue":
            task.status = AgentStatus.RUNNING
            logger.info("[VOICE_APPROVAL_ACCEPTED] task=%s", task.task_id)
            logger.info("[TRAVEL_APPROVAL_ACCEPTED] task=%s", task.task_id)
            logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=continue", task.task_id)
            logger.info("[BOOKING_SAFETY_STOP] task=%s reason=no_booking_capability", task.task_id)
            return " ".join(responses + [
                "You've approved continuing, but I don't complete bookings or handle payments — "
                "that step needs to happen on the airline or travel site directly. I'll stop here safely."
            ])

        if action == "choose":
            choice = decision.get("choice", "")
            opt = result.get("ranking", {}).get(choice)
            logger.info("[VOICE_APPROVAL_CHOICE] task=%s choice=%s option=%r", task.task_id, choice, opt)
            logger.info("[VOICE_APPROVAL_APPLIED] task=%s action=choose choice=%s", task.task_id, choice)
            logger.info("[BOOKING_SAFETY_STOP] task=%s reason=no_booking_capability", task.task_id)
            if opt:
                responses.append(
                    f"You've selected the {choice} option: {opt.get('airline', 'that flight')} "
                    f"at {_field(opt, 'price')}. This is where I stop — I don't complete bookings "
                    f"or handle payments. Let me know if you'd like anything else, or say cancel."
                )
            else:
                responses.append(
                    f"Noted — you'd like the {choice} option, but I don't have enough data to point "
                    f"to a specific flight for it. I still won't book or pay."
                )
            continue  # keep waiting — a choice doesn't end the conversation

        if action == "more_options":
            options = result.get("options", [])
            rest = options[3:8]
            if rest:
                lines = [f"- {o.get('airline','Unknown')}: {_field(o,'price')}, {_field(o,'duration')}" for o in rest]
                responses.append("Here are more options:\n" + "\n".join(lines))
            else:
                responses.append("That's all the options I found.")
            continue

        if action == "save":
            responses.append("Saved this option for later. I still won't book or pay without your explicit go-ahead.")
            continue

        if action == "check_other_sites":
            from api.agents.browser_agent.browser_workspace import browser_workspace
            site = decision.get("site") or next(iter(ALT_SOURCE_DOMAINS), "skyscanner")
            domain = ALT_SOURCE_DOMAINS.get(site, ALT_SOURCE_DOMAINS["skyscanner"])
            origin = result.get("origin", "")
            destination = result.get("destination", "")
            date = result.get("date", "")
            logger.info("[BROWSER_NEW_TAB_APPROVAL_REQUIRED] site=%s domain=%s task=%s", site, domain, task.task_id)
            alt_url = f"https://www.google.com/search?q=site:{domain}+flights+{origin}+to+{destination}+{date}".replace(" ", "+")
            alt_page = await browser_workspace.new_tab_if_approved(alt_url, approved=True, reason=f"user_approved_check_{site}")
            if alt_page is not None:
                try:
                    from api.agents.browser_agent.browser_reader import BrowserReader as _BR
                    text = await _BR().summarize_page(alt_page, max_chars=1500)
                    new_opts = _parse_text(text)
                    for o in new_opts:
                        o.update(source=site, booking_url=alt_page.url, confidence="medium",
                                  baggage_notes="Not specified", refund_notes="Not specified")
                except Exception:
                    new_opts = []
                main_page = await browser_workspace.get_or_create_page()
                await browser_workspace.close_extra_tabs(keep=main_page)
                if new_opts:
                    result.setdefault("options", []).extend(new_opts)
                    responses.append(f"I checked {site} and found {len(new_opts)} more option(s). I'm still not booking anything.")
                else:
                    responses.append(f"I checked {site}, but couldn't reliably extract prices there either.")
            else:
                responses.append(f"I wasn't able to open {site}.")
            continue

        responses.append("I didn't catch a clear decision.")
        continue

    task.status = AgentStatus.RUNNING
    return " ".join(responses + ["Timed out waiting for a final decision — stopping here safely. Nothing was booked."])
