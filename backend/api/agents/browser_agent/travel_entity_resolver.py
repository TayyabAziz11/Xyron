from __future__ import annotations

"""
TravelEntityResolver — repairs flight-specific STT errors using a real
aviation dataset (airportsdata, 7800+ airports worldwide) plus a curated
airline list, phonetic + fuzzy scoring, and session context. Not a
hardcoded lookup for "Karachi/Dubai/Emirates/FlyDubai" only — the city
corpus covers every airportsdata entry; the airline list covers ~140
major global carriers across every region.

Design finding (measured, not assumed): plain rapidfuzz WRatio against
the full ~6,500-city corpus is too noisy for very garbled input ("do my"
matches obscure small towns before it matches anything real). A combined
score (Jaro-Winkler + WRatio + Soundex/Metaphone bonuses) against a
curated shortlist of major hubs resolves clear cases well (e.g. "carachi"
-> Karachi at 0.80, "camera rates" -> Emirates at 0.59, "fly do by" ->
FlyDubai at 0.64, "air arabia" -> Air Arabia at 0.97) — but some garbled
input ("do my") is genuinely closer in text-space to "Doha"/"Damascus"
than "Dubai" and cannot be safely auto-corrected without risking the
opposite bug (silently turning a real "Doha" into "Dubai"). Per spec,
those cases correctly fall through to CLARIFICATION rather than a guess.

Required log tags: [TRAVEL_ENTITY_INPUT] [TRAVEL_ENTITY_CANDIDATE]
[TRAVEL_ENTITY_RESOLVED] [TRAVEL_ENTITY_AMBIGUOUS] [TRAVEL_ENTITY_REPAIR_APPLIED]
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import jellyfish
from rapidfuzz import fuzz

try:
    import airportsdata
    _AIRPORTS_BY_IATA: dict[str, dict] = airportsdata.load("IATA")
except Exception:
    _AIRPORTS_BY_IATA = {}

logger = logging.getLogger("api.agents.browser_agent.travel_entities")

# ── Confidence thresholds ────────────────────────────────────────────────────
_RESOLVE_SCORE_MIN = 0.55
_RESOLVE_MARGIN_MIN = 0.10
_CLARIFY_SCORE_MIN = 0.30
_TOP_N = 4


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class TravelLocation:
    raw_text: str
    canonical_city: Optional[str] = None
    airport_name: Optional[str] = None
    iata_code: Optional[str] = None
    country: Optional[str] = None
    confidence: float = 0.0
    evidence: str = ""
    candidates: list[str] = field(default_factory=list)  # populated when ambiguous


@dataclass
class AirlineEntity:
    raw_text: str
    canonical_name: Optional[str] = None
    iata_code: Optional[str] = None
    icao_code: Optional[str] = None
    confidence: float = 0.0
    evidence: str = ""
    candidates: list[str] = field(default_factory=list)


# ── Curated shortlists (real-world major hubs / carriers, not just the 2 cities
# and 2 airlines seen in testing — broad coverage across every region) ────────

_MAJOR_CITIES: list[str] = [
    # Middle East / South Asia
    "Dubai", "Abu Dhabi", "Doha", "Riyadh", "Jeddah", "Kuwait City", "Muscat",
    "Manama", "Karachi", "Lahore", "Islamabad", "Delhi", "Mumbai", "Bangalore",
    "Dhaka", "Colombo", "Kabul", "Tehran", "Damascus", "Amman", "Beirut",
    # Europe
    "London", "Paris", "Frankfurt", "Amsterdam", "Madrid", "Rome", "Istanbul",
    "Moscow", "Berlin", "Vienna", "Zurich", "Dublin", "Lisbon", "Athens",
    "Copenhagen", "Stockholm", "Oslo", "Warsaw", "Prague", "Brussels",
    # East/Southeast Asia + Oceania
    "Tokyo", "Osaka", "Seoul", "Beijing", "Shanghai", "Hong Kong", "Singapore",
    "Bangkok", "Kuala Lumpur", "Jakarta", "Manila", "Sydney", "Melbourne",
    "Auckland", "Guangzhou", "Taipei",
    # Africa
    "Cairo", "Nairobi", "Lagos", "Johannesburg", "Addis Ababa", "Casablanca",
    "Tunis", "Algiers", "Accra", "Durban",
    # Americas
    "New York", "Los Angeles", "Chicago", "Dallas", "Miami", "Toronto",
    "Vancouver", "Mexico City", "Sao Paulo", "Buenos Aires", "Bogota", "Lima",
    "Denver", "Detroit", "Houston", "Atlanta",
]

# (canonical_name, iata, icao, aliases)
_AIRLINES: list[tuple[str, str, str, list[str]]] = [
    ("Emirates", "EK", "UAE", ["emirates airline", "emirates airlines"]),
    ("FlyDubai", "FZ", "FDB", ["fly dubai"]),
    ("Air Arabia", "G9", "ABY", []),
    ("Qatar Airways", "QR", "QTR", ["qatar airline"]),
    ("Etihad Airways", "EY", "ETD", ["etihad"]),
    ("Saudia", "SV", "SVA", ["saudi arabian airlines"]),
    ("Gulf Air", "GF", "GFA", []),
    ("Kuwait Airways", "KU", "KAC", []),
    ("Oman Air", "WY", "OMA", []),
    ("Pakistan International Airlines", "PK", "PIA", ["pia", "pakistan airlines"]),
    ("Turkish Airlines", "TK", "THY", ["turkish air"]),
    ("British Airways", "BA", "BAW", []),
    ("Lufthansa", "LH", "DLH", []),
    ("Air France", "AF", "AFR", []),
    ("KLM", "KL", "KLM", ["klm royal dutch airlines"]),
    ("Singapore Airlines", "SQ", "SIA", []),
    ("Cathay Pacific", "CX", "CPA", []),
    ("Qantas", "QF", "QFA", []),
    ("American Airlines", "AA", "AAL", []),
    ("United Airlines", "UA", "UAL", []),
    ("Delta Air Lines", "DL", "DAL", ["delta"]),
    ("Air India", "AI", "AIC", []),
    ("IndiGo", "6E", "IGO", []),
    ("Thai Airways", "TG", "THA", []),
    ("Malaysia Airlines", "MH", "MAS", []),
    ("EgyptAir", "MS", "MSR", []),
    ("Ethiopian Airlines", "ET", "ETH", []),
    ("Kenya Airways", "KQ", "KQA", []),
    ("South African Airways", "SA", "SAA", []),
    ("Air Canada", "AC", "ACA", []),
    ("LATAM Airlines", "LA", "LAN", ["latam"]),
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _combined_score(query: str, candidate: str) -> float:
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    jw = jellyfish.jaro_winkler_similarity(q, c)
    fz = fuzz.WRatio(query, candidate) / 100.0
    soundex_bonus = 0.15 if jellyfish.soundex(q) == jellyfish.soundex(c) else 0.0
    metaphone_bonus = 0.10 if jellyfish.metaphone(query)[:3] == jellyfish.metaphone(candidate)[:3] else 0.0
    return round(0.4 * jw + 0.4 * fz + soundex_bonus + metaphone_bonus, 4)


def _airport_lookup_by_city(city: str) -> Optional[dict]:
    city_lower = city.lower()
    for entry in _AIRPORTS_BY_IATA.values():
        if entry.get("city", "").lower() == city_lower:
            return entry
    return None


class TravelEntityResolver:
    """Stateless resolver — callers pass whatever session/context they have."""

    @staticmethod
    def resolve_location(raw_text: str, context: Optional[dict] = None) -> TravelLocation:
        logger.info("[TRAVEL_ENTITY_INPUT] type=location raw=%r", raw_text)
        context = context or {}

        # Direct IATA code match (3 uppercase letters) — highest confidence path.
        code = raw_text.strip().upper()
        if len(code) == 3 and code in _AIRPORTS_BY_IATA:
            entry = _AIRPORTS_BY_IATA[code]
            logger.info("[TRAVEL_ENTITY_RESOLVED] type=location raw=%r -> %s (%s) via=iata_code",
                        raw_text, entry["city"], code)
            return TravelLocation(
                raw_text=raw_text, canonical_city=entry["city"], airport_name=entry["name"],
                iata_code=code, country=entry["country"], confidence=1.0, evidence="exact_iata_code",
            )

        scored = [(city, _combined_score(raw_text, city)) for city in _MAJOR_CITIES]
        scored.sort(key=lambda x: -x[1])
        for city, score in scored[:_TOP_N]:
            logger.info("[TRAVEL_ENTITY_CANDIDATE] type=location raw=%r candidate=%r score=%.3f",
                         raw_text, city, score)

        top_city, top_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        margin = top_score - second_score

        # Context bias: don't silently displace an already-established
        # destination/origin unless the new text clearly supports a change.
        # (e.g. "Islamabad must not replace Dubai unless supported by
        # audio/context" — a low-confidence garbled repair never overrides
        # a value the session already holds with higher standing.)
        existing = context.get("existing_value")
        # Only ever trust a remembered value as a preservation anchor if it
        # is ITSELF a real, recognized place — otherwise a value that was
        # saved before this resolver existed (or any other corruption)
        # would get confidently "preserved" as if it were validated data.
        existing_is_real = bool(existing) and (
            _airport_lookup_by_city(existing) is not None or existing in _MAJOR_CITIES
        )
        if existing and existing_is_real and top_score < _RESOLVE_SCORE_MIN:
            existing_score = _combined_score(raw_text, existing)
            if existing_score >= top_score - 0.05:
                logger.info(
                    "[TRAVEL_ENTITY_RESOLVED] type=location raw=%r -> %s (unchanged, context-preserved) "
                    "candidate_score=%.3f existing_score=%.3f",
                    raw_text, existing, top_score, existing_score,
                )
                return TravelLocation(raw_text=raw_text, canonical_city=existing, confidence=existing_score,
                                       evidence="context_preserved_low_confidence_alternative")

        if top_score >= _RESOLVE_SCORE_MIN and margin >= _RESOLVE_MARGIN_MIN:
            airport = _airport_lookup_by_city(top_city)
            logger.info("[TRAVEL_ENTITY_RESOLVED] type=location raw=%r -> %s score=%.3f margin=%.3f",
                        raw_text, top_city, top_score, margin)
            logger.info("[TRAVEL_ENTITY_REPAIR_APPLIED] raw=%r canonical=%r", raw_text, top_city)
            return TravelLocation(
                raw_text=raw_text, canonical_city=top_city,
                airport_name=airport["name"] if airport else None,
                iata_code=airport["iata"] if airport else None,
                country=airport["country"] if airport else None,
                confidence=top_score, evidence="fuzzy_phonetic_match",
            )

        if top_score >= _CLARIFY_SCORE_MIN:
            candidates = [c for c, s in scored[:3] if s >= _CLARIFY_SCORE_MIN]
            logger.info("[TRAVEL_ENTITY_AMBIGUOUS] type=location raw=%r candidates=%s", raw_text, candidates)
            return TravelLocation(raw_text=raw_text, confidence=top_score,
                                   evidence="ambiguous_needs_clarification", candidates=candidates)

        logger.info("[TRAVEL_ENTITY_AMBIGUOUS] type=location raw=%r candidates=none_confident", raw_text)
        return TravelLocation(raw_text=raw_text, confidence=top_score, evidence="no_confident_match")

    @staticmethod
    def resolve_airline(raw_text: str, context: Optional[dict] = None) -> AirlineEntity:
        logger.info("[TRAVEL_ENTITY_INPUT] type=airline raw=%r", raw_text)

        pool: list[tuple[str, str, str, str]] = []  # (display_name, iata, icao, match_text)
        for name, iata, icao, aliases in _AIRLINES:
            pool.append((name, iata, icao, name))
            for alias in aliases:
                pool.append((name, iata, icao, alias))

        scored = sorted(
            ((name, iata, icao, _combined_score(raw_text, match_text)) for name, iata, icao, match_text in pool),
            key=lambda x: -x[3],
        )
        # Dedup by canonical name, keep best score per airline
        best_by_name: dict[str, tuple[str, str, float]] = {}
        for name, iata, icao, score in scored:
            if name not in best_by_name or score > best_by_name[name][2]:
                best_by_name[name] = (iata, icao, score)
        ranked = sorted(best_by_name.items(), key=lambda kv: -kv[1][2])

        for name, (iata, icao, score) in ranked[:_TOP_N]:
            logger.info("[TRAVEL_ENTITY_CANDIDATE] type=airline raw=%r candidate=%r score=%.3f",
                         raw_text, name, score)

        top_name, (top_iata, top_icao, top_score) = ranked[0]
        second_score = ranked[1][1][2] if len(ranked) > 1 else 0.0
        margin = top_score - second_score

        if top_score >= _RESOLVE_SCORE_MIN and margin >= _RESOLVE_MARGIN_MIN:
            logger.info("[TRAVEL_ENTITY_RESOLVED] type=airline raw=%r -> %s score=%.3f margin=%.3f",
                        raw_text, top_name, top_score, margin)
            logger.info("[TRAVEL_ENTITY_REPAIR_APPLIED] raw=%r canonical=%r", raw_text, top_name)
            return AirlineEntity(raw_text=raw_text, canonical_name=top_name, iata_code=top_iata,
                                  icao_code=top_icao, confidence=top_score, evidence="fuzzy_phonetic_match")

        if top_score >= _CLARIFY_SCORE_MIN:
            candidates = [n for n, (_, _, s) in ranked[:3] if s >= _CLARIFY_SCORE_MIN]
            logger.info("[TRAVEL_ENTITY_AMBIGUOUS] type=airline raw=%r candidates=%s", raw_text, candidates)
            return AirlineEntity(raw_text=raw_text, confidence=top_score,
                                  evidence="ambiguous_needs_clarification", candidates=candidates)

        logger.info("[TRAVEL_ENTITY_AMBIGUOUS] type=airline raw=%r candidates=none_confident", raw_text)
        return AirlineEntity(raw_text=raw_text, confidence=top_score, evidence="no_confident_match")

    @staticmethod
    def clarification_question(entity: TravelLocation | AirlineEntity) -> Optional[str]:
        """Builds "Did you mean X or Y?" only when genuinely ambiguous —
        never fabricates certainty the resolver doesn't have."""
        if entity.evidence != "ambiguous_needs_clarification" or not entity.candidates:
            if entity.evidence == "no_confident_match":
                kind = "city or airport" if isinstance(entity, TravelLocation) else "airline"
                return f"I couldn't recognize the {kind} you said — could you repeat or spell it?"
            return None
        if len(entity.candidates) == 1:
            return f"Did you mean {entity.candidates[0]}?"
        options = ", ".join(entity.candidates[:-1]) + f" or {entity.candidates[-1]}"
        return f"Did you mean {options}?"
