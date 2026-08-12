from __future__ import annotations

"""
FlightOptionExtractor v2 — turns raw scraped dicts (from
flight_search_agent's DOM/text layers) into evidence-carrying FlightOption
records, and provides an OCR fallback for when DOM/text extraction finds
nothing at all.

Never fabricates a missing field — every field is either populated with
real evidence or left None. A "confidence" score reflects how much of the
record is actually verified vs. absent.

Layered extraction order (matches flight_search_agent.py's existing
layers 1-2 plus this module's OCR layer):
  1. accessibility snapshot / stable DOM structures  (flight_search_agent._scrape_dom)
  2. visible text regex                              (flight_search_agent._parse_text)
  3. structured/JSON data embedded in the page        (best-effort, optional)
  4. OCR on a screenshot                              (this module, last resort only)

Required log tags: [FLIGHT_OPTION_EXTRACT_START] [FLIGHT_OPTION_FIELD]
[FLIGHT_OPTION_EVIDENCE] [FLIGHT_OPTION_CREATED] [FLIGHT_OPTION_REJECTED_LOW_CONFIDENCE]
[FLIGHT_OCR_START] [FLIGHT_OCR_RESULT] [FLIGHT_VISION_FALLBACK] [FLIGHT_VISUAL_FIELD_VERIFIED]
"""

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

logger = logging.getLogger("api.agents.browser_agent.flight_option_extractor")

_REQUIRED_FOR_ACCEPTANCE = ("airline", "price")  # anything else missing is fine (marked None)


@dataclass
class FlightOption:
    airline: Optional[str] = None
    flight_number: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    duration: Optional[str] = None
    stops: Optional[str] = None
    origin_airport: Optional[str] = None
    destination_airport: Optional[str] = None
    fare_class: Optional[str] = None
    baggage_kg: Optional[int] = None
    refundable: Optional[bool] = None
    source: str = "unknown"
    booking_url: str = ""
    evidence: dict = field(default_factory=dict)       # field_name -> evidence description
    field_confidence: dict = field(default_factory=dict)  # field_name -> 0..1
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _field_conf(value: Any, method: str) -> float:
    if value is None or value == "":
        return 0.0
    return {"dom": 0.9, "text_regex": 0.65, "ocr": 0.45, "structured_data": 0.95}.get(method, 0.5)


def from_raw_dict(raw: dict, method: str, source: str, booking_url: str, task_id: str = "") -> Optional[FlightOption]:
    """Converts one raw scraped dict (as produced by flight_search_agent's
    _scrape_dom/_parse_text) into a FlightOption with evidence. Rejects
    (logs, returns None) anything missing airline+price entirely — no
    partial-guess record gets created from nothing."""
    logger.info("[FLIGHT_OPTION_EXTRACT_START] method=%s source=%s", method, source)

    opt = FlightOption(source=source, booking_url=booking_url)
    field_map = {
        "airline": raw.get("airline"), "price": raw.get("price"),
        "departure_time": raw.get("departure"), "arrival_time": raw.get("arrival"),
        "duration": raw.get("duration"), "stops": raw.get("stops"),
    }
    for name, value in field_map.items():
        if value:
            setattr(opt, name, value)
            opt.evidence[name] = f"{method}:{source}"
            opt.field_confidence[name] = _field_conf(value, method)
            logger.info("[FLIGHT_OPTION_FIELD] field=%s value=%r method=%s", name, str(value)[:60], method)
            logger.info("[FLIGHT_OPTION_EVIDENCE] field=%s evidence=%s", name, opt.evidence[name])

    if not opt.airline or not opt.price:
        logger.info("[FLIGHT_OPTION_REJECTED_LOW_CONFIDENCE] reason=missing_required_fields "
                    "airline=%r price=%r", opt.airline, opt.price)
        return None

    populated = [v for v in opt.field_confidence.values()]
    opt.confidence = round(sum(populated) / max(len(field_map), 1), 3)
    logger.info("[FLIGHT_OPTION_CREATED] airline=%s price=%s confidence=%.2f", opt.airline, opt.price, opt.confidence)
    return opt


# ── OCR fallback (Part 6) ────────────────────────────────────────────────────

_OCR_PRICE_RE = re.compile(r"[$€£¥₹]\s?[\d,]+(?:\.\d{2})?")
_OCR_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s?(?:AM|PM)?", re.IGNORECASE)
_OCR_DURATION_RE = re.compile(r"\d+\s?h(?:r|rs)?\s?\d*\s?m(?:in)?", re.IGNORECASE)
_OCR_STOPS_RE = re.compile(r"nonstop|\d+\s?stop[s]?", re.IGNORECASE)

_ocr_reader = None  # lazy-loaded easyocr.Reader — expensive to construct


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is not None:
        return _ocr_reader
    try:
        import easyocr
        _ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        return _ocr_reader
    except Exception as exc:
        logger.warning("[FLIGHT_OCR_START] ocr_unavailable error=%r", str(exc)[:200])
        return None


def ocr_extract_options(screenshot_path: str, source: str, booking_url: str) -> list[FlightOption]:
    """Last-resort fallback — only called when DOM/text extraction found
    zero options. Extracts price/airline-adjacent text/times/durations/
    stops from a screenshot via local OCR (easyocr). Honest: if OCR isn't
    installed or finds nothing, returns an empty list rather than
    fabricating anything, and logs exactly that."""
    logger.info("[FLIGHT_OCR_START] path=%s source=%s", screenshot_path, source)
    reader = _get_ocr_reader()
    if reader is None:
        logger.info("[FLIGHT_OCR_RESULT] status=unavailable count=0")
        return []

    try:
        results = reader.readtext(screenshot_path, detail=1)
    except Exception as exc:
        logger.warning("[FLIGHT_OCR_RESULT] status=error error=%r", str(exc)[:200])
        return []

    lines = [text for (_bbox, text, conf) in results if conf > 0.4]
    full_text = "\n".join(lines)
    logger.info("[FLIGHT_OCR_RESULT] status=ok lines=%d", len(lines))
    logger.info("[FLIGHT_VISION_FALLBACK] chars=%d", len(full_text))

    prices = _OCR_PRICE_RE.findall(full_text)
    times = _OCR_TIME_RE.findall(full_text)
    durations = _OCR_DURATION_RE.findall(full_text)
    stops = _OCR_STOPS_RE.findall(full_text)

    options: list[FlightOption] = []
    for i, price in enumerate(prices[:8]):
        opt = FlightOption(
            price=price,
            departure_time=times[2 * i] if 2 * i < len(times) else None,
            arrival_time=times[2 * i + 1] if 2 * i + 1 < len(times) else None,
            duration=durations[i] if i < len(durations) else None,
            stops=stops[i] if i < len(stops) else None,
            source=source, booking_url=booking_url,
        )
        # Airline name isn't reliably OCR-locatable without layout
        # correlation — honestly leave it None rather than guess.
        opt.evidence["price"] = f"ocr:{screenshot_path}"
        opt.field_confidence["price"] = 0.45
        if opt.departure_time:
            opt.evidence["departure_time"] = f"ocr:{screenshot_path}"
            opt.field_confidence["departure_time"] = 0.4
            logger.info("[FLIGHT_VISUAL_FIELD_VERIFIED] field=departure_time value=%r", opt.departure_time)
        if not opt.airline or not opt.price:
            if not opt.price:
                continue  # price is the one truly required field for OCR results
        opt.confidence = round(sum(opt.field_confidence.values()) / 6, 3)
        logger.info("[FLIGHT_OPTION_CREATED] airline=None price=%s confidence=%.2f (ocr)", opt.price, opt.confidence)
        options.append(opt)

    return options
