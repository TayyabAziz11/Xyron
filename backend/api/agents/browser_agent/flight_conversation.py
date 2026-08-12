from __future__ import annotations

"""
Flight follow-up conversation layer (Phase 4.8) — operates on the ONE
persistent page from BrowserWorkspace instead of restarting a search or
opening a new tab for every filter/sort/detail request.

FlightFollowUpResolver   — detects which of the ~20 follow-up phrase
                           categories a transcript belongs to.
FlightFilterController   — applies filters/sort/date/passengers/cabin on
                           the live page via a layered locator strategy.
FlightDetailsInspector   — opens/reads flight details, cancellation
                           policy, baggage text; supports "go back".
FlightConversationManager — orchestrates resolver + controller +
                           inspector + FlightSessionState + narration for
                           one follow-up turn.

Never fabricates a successful filter application or a baggage/refund
answer it didn't actually verify on the page — every action honestly
reports success or failure.

Log tags: [FLIGHT_FOLLOWUP_DETECTED] [FLIGHT_FOLLOWUP_INTENT]
[FLIGHT_FILTER_APPLIED] [FLIGHT_DATE_CHANGED] [FLIGHT_PASSENGERS_CHANGED]
[FLIGHT_DETAILS_OPENED] [FLIGHT_RESULTS_RESTORED] [FLIGHT_CONTROL_ACTION]
[FLIGHT_LOCATOR_STRATEGY] [FLIGHT_CONTROL_SUCCESS] [FLIGHT_CONTROL_FAILED]
[FLIGHT_CONTROL_RECOVERY] [BAGGAGE_CHECK_START] [BAGGAGE_INFO_FOUND]
[BAGGAGE_INFO_UNAVAILABLE] [OFFICIAL_AIRLINE_CHECK_REQUIRED]
[OFFICIAL_AIRLINE_INFO_VERIFIED] [TRAVEL_NARRATION] [TRAVEL_PROGRESS_UPDATE]
[TRAVEL_NARRATION_SKIPPED_DUPLICATE]
"""

import asyncio
import logging
import re
from typing import Any, Optional

from playwright.async_api import Page

from api.agents.browser_agent import flight_session_state as fss
from api.agents.browser_agent.browser_workspace import browser_workspace
from api.agents.browser_agent.conversation_layer import narrate as _cl_narrate, ConversationEvent as _CE

logger = logging.getLogger("api.agents.browser_agent.flight_conversation")

_WORD_NUM = {
    "a": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5,
}

# Maps a FlightFollowUpResolver intent to the canonical FlightStage it
# represents — set once per `handle_followup` call (see
# `_current_followup_stage` below) so the ~20 existing `narrate(...)`
# call sites throughout this file don't each need to be individually
# rewritten to pass a stage explicitly.
_INTENT_TO_STAGE: dict[str, str] = {
    "airline_only": "FILTERING_AIRLINE", "airline_include": "FILTERING_AIRLINE", "airline_exclude": "FILTERING_AIRLINE",
    "stops_nonstop": "FILTERING_STOPS", "stops_one": "FILTERING_STOPS",
    "time_morning": "FILTERING_TIME", "time_evening": "FILTERING_TIME",
    "sort_cheapest": "COMPARING_PRICE", "sort_fastest": "COMPARING_DURATION",
    "baggage_query": "CHECKING_BAGGAGE", "official_site_approved": "CHECKING_BAGGAGE",
    "compare_with": "COMPARING_PRICE", "recommend": "RECOMMENDATION_READY",
    "cancel": "CANCELLED",
}
_current_followup_stage: str = "FILTERING_AIRLINE"


def narrate(text: str, stage: Optional[str] = None) -> str:
    """Sync frontend status + log trail for *text* via the canonical
    flight_narration.speak_stage() (speak=False — every one of this
    file's `return narrate(...)` call sites returns the same text to
    voice_ws.py's Tier 0f1 handler, which speaks it once through the
    normal turn-response TTS path; queuing a second narration audio
    event here would speak it twice). This used to be an independent
    reimplementation of speak_stage()'s dedup/WS-emission logic with its
    own separate "last spoken" tracker — collapsed into one so a phrase
    just spoken by a background agent task and a phrase about to be
    spoken by a follow-up turn share the same dedup state instead of two
    that could never see each other."""
    from api.agents.browser_agent.flight_narration import speak_stage
    _stage = stage or _current_followup_stage
    speak_stage(_stage, text, task=None, speak=False)
    return text


def progress(step: str, pct: int) -> None:
    logger.info("[TRAVEL_PROGRESS_UPDATE] step=%s pct=%d", step, pct)


# ── FlightFollowUpResolver ──────────────────────────────────────────────────────

# Order matters: specific filter/sort/detail phrases are checked before
# the broad "check/show only <name>" airline patterns, so e.g. "show
# only direct flights" resolves to stops_nonstop, not an airline named
# "Direct".
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("official_site_approved", re.compile(r"\byes\W*(?:check|verify|go\s+ahead)\b.*official|\bcheck\s+the\s+(?:airline'?s?\s+)?official\s+(?:website|site)\b", re.IGNORECASE)),
    # Both word orders — "show only direct flights" and "only show direct
    # flights" are both natural phrasings; the latter previously fell
    # through to airline_only's "only X" match and treated "Direct" as a
    # fake airline name.
    ("stops_nonstop", re.compile(
        r"\bshow\s+only\s+direct\s+flights?\b|\bonly\s+show\s+direct\s+flights?\b"
        r"|\bdirect\s+flights?\s+only\b|\bonly\s+direct\s+flights?\b"
        r"|\bnonstop\s+only\b|\bonly\s+nonstop\b"
        r"|^\s*direct\s+only\s*$|^\s*only\s+direct\s*$",
        re.IGNORECASE,
    )),
    ("stops_one", re.compile(r"\ballow\s+one\s+stop\b", re.IGNORECASE)),
    ("time_morning", re.compile(r"\bmorning\s+flights?\s+only\b", re.IGNORECASE)),
    ("time_evening", re.compile(r"\bshow\s+evening\s+flights?\b", re.IGNORECASE)),
    # "by"/"my" is a common STT confusion ("sort my cheapest" heard for
    # "sort by cheapest") — tolerate either. Also accept the natural
    # "find (me) the cheapest (one)" phrasing — observed live to fall
    # through to the generic intent router instead, spawning a redundant
    # second coordinator task that then failed on browser resource
    # contention with the still-running first one.
    ("sort_cheapest", re.compile(r"\bsort\s+(?:by|my)\s+cheapest\b|\bfind\s+(?:me\s+)?the\s+cheapest\b(?:\s+one)?", re.IGNORECASE)),
    ("sort_fastest", re.compile(r"\bsort\s+(?:by|my)\s+fastest\b|\bfind\s+(?:me\s+)?the\s+fastest\b(?:\s+one)?", re.IGNORECASE)),
    ("cabin_economy", re.compile(r"\beconomy\s+class\b", re.IGNORECASE)),
    ("cabin_business", re.compile(r"\bbusiness\s+class\b", re.IGNORECASE)),
    ("baggage_query", re.compile(
        r"\b(?:which\s+flights?|does\s+(?:it|this)\s+(?:flight|fare)|allow).{0,40}?"
        r"(\d+)\s*k(?:g|ilograms?)\b.*(?:baggage|luggage)?"
        r"|(\d+)\s*k(?:g|ilograms?)\s*(?:of\s+)?(?:baggage|luggage)"
        # Open-ended baggage questions with no number ("how much baggage
        # do I get", "what's the baggage allowance") — same intent, just
        # no specific kg requirement to check against (params["kg"] stays
        # None; the dispatch handler already falls back to general
        # airline policy + offers to check the official site for this).
        r"|\bhow\s+(?:much|many)\s+(?:baggage|luggage|bags?)\b"
        r"|\bbaggage\s+allowance\b|\bluggage\s+allowance\b"
        r"|\bwhat.?s\s+the\s+baggage\b",
        re.IGNORECASE,
    )),
    ("open_details", re.compile(r"\bopen\s+the\s+first\s+(?:one|option)\b|\bopen\s+that\s+(?:one|option)\b|\bshow\s+details\b", re.IGNORECASE)),
    ("cancellation_policy", re.compile(r"\b(?:show\s+(?:me\s+)?(?:the\s+)?)?cancellation\s+policy\b|\brefund\s+policy\b", re.IGNORECASE)),
    ("go_back", re.compile(r"^\s*go\s+back\s*$", re.IGNORECASE)),
    # "will"/"would"/"should" are all natural phrasings of the same
    # question — the original pattern only matched "do you recommend"
    # and fell through to the generic LLM path (producing ungrounded,
    # generic airline trivia instead of a real answer from verified data)
    # for "which one WILL you recommend".
    ("recommend", re.compile(
        r"\bwhich\s+one\s+(?:do|will|would|should)\s+you\s+recommend\b"
        r"|\bwhat\s+(?:do|would)\s+you\s+recommend\b|^\s*recommend\s*$",
        re.IGNORECASE,
    )),
    ("show_more", re.compile(r"\bshow\s+me\s+more\b", re.IGNORECASE)),
    ("cancel", re.compile(r"\bcancel\b|\bstop\b|\bnever\s?mind\b", re.IGNORECASE)),
    ("date_change", re.compile(r"\bchange\s+(?:it|the\s+date)\s+to\s+(.+?)\s*$", re.IGNORECASE)),
    ("make_return", re.compile(r"\bmake\s+it\s+a\s+return\s+trip\b", re.IGNORECASE)),
    ("passengers", re.compile(
        r"\b(\w+)\s+adults?\s*(?:and\s+(\w+)\s+child(?:ren)?)?\b", re.IGNORECASE)),
    ("compare_with", re.compile(
        r"\bcompare\s+this\s+with\s+([A-Za-z][A-Za-z ]{2,20})\b"
        r"|\bcompare\s+([A-Za-z][A-Za-z ]{2,20}?)\s+(?:with|versus|vs\.?)\s+([A-Za-z][A-Za-z ]{2,20})\b",
        re.IGNORECASE,
    )),
    ("airline_include", re.compile(r"\balso\s+include\s+([A-Za-z][A-Za-z ]{2,20})\b", re.IGNORECASE)),
    ("airline_exclude", re.compile(r"\bhide\s+([A-Za-z][A-Za-z ]{2,20})\b", re.IGNORECASE)),
    # "Only Emirates." (bare, no "show") is a natural, common phrasing —
    # observed live to fall through entirely since the pattern previously
    # required "only show X" / "show only X". Checked after every more
    # specific filter pattern above, so "only show direct flights"/
    # "morning flights only" etc. are never mis-captured here.
    ("airline_only", re.compile(r"\b(?:check|only\s+show|show\s+only|only)\s+([A-Za-z][A-Za-z ]{2,20})\b", re.IGNORECASE)),
]

_AIRLINE_TRAILING_STRIP_RE = re.compile(r"\s+flights?$", re.IGNORECASE)


def _clean_airline_name(raw: str) -> str:
    return _AIRLINE_TRAILING_STRIP_RE.sub("", raw.strip()).strip().title()


class FlightFollowUpResolver:
    """Stateless detector — maps a transcript to a follow-up intent, or
    None if it doesn't match anything flight-related."""

    @staticmethod
    def detect(text: str) -> Optional[dict[str, Any]]:
        t = text.strip().rstrip(".!? ")
        for name, pat in _PATTERNS:
            m = pat.search(t)
            if not m:
                continue
            params: dict[str, Any] = {}
            if name in ("airline_only", "airline_include", "airline_exclude"):
                params["airline"] = _clean_airline_name(m.group(1))
            elif name == "compare_with":
                if m.group(1):
                    params["airline"] = _clean_airline_name(m.group(1))
                else:
                    params["airline_a"] = _clean_airline_name(m.group(2))
                    params["airline_b"] = _clean_airline_name(m.group(3))
            elif name == "date_change":
                params["date_phrase"] = m.group(1).strip()
            elif name == "passengers":
                a = _WORD_NUM.get(m.group(1).lower(), None) if m.group(1) else None
                if a is None:
                    continue  # not actually a passengers phrase (e.g. random text with "and")
                c = _WORD_NUM.get((m.group(2) or "").lower(), 0)
                params["adults"] = a
                params["children"] = c
            elif name == "baggage_query":
                kg = m.group(1) or m.group(2)
                params["kg"] = int(kg) if kg else None

            logger.info("[FLIGHT_FOLLOWUP_DETECTED] text=%r intent=%s", t[:100], name)
            logger.info("[FLIGHT_FOLLOWUP_INTENT] intent=%s params=%r", name, params)
            return {"intent": name, "params": params}
        return None


# ── Layered locator strategy ─────────────────────────────────────────────────────

async def _try_click(page: Page, candidates: list, action_desc: str) -> bool:
    """candidates: list of (strategy_name, locator_factory) pairs, tried in
    order. Returns True and logs success on the first that actually finds
    and clicks a visible element; logs failure honestly otherwise."""
    logger.info("[FLIGHT_CONTROL_ACTION] action=%r", action_desc)
    for strategy_name, make_locator in candidates:
        try:
            loc = make_locator()
            count = await loc.count()
            if count > 0:
                el = loc.first
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(timeout=5000)
                logger.info("[FLIGHT_LOCATOR_STRATEGY] strategy=%s action=%r", strategy_name, action_desc)
                logger.info("[FLIGHT_CONTROL_SUCCESS] action=%r strategy=%s", action_desc, strategy_name)
                return True
        except Exception as exc:
            logger.info("[FLIGHT_CONTROL_RECOVERY] action=%r strategy=%s error=%r", action_desc, strategy_name, str(exc)[:120])
            continue
    logger.info("[FLIGHT_CONTROL_FAILED] action=%r reason=no_locator_matched", action_desc)
    return False


# ── FlightFilterController ──────────────────────────────────────────────────────

class FlightFilterController:
    """Applies filters/sort/date/passengers/cabin class on the live page.
    Every method tries role/name → label → visible text → stable
    attribute, in that order, and honestly reports failure rather than
    pretending the filter was applied."""

    @staticmethod
    async def filter_airline(page: Page, airline: str, mode: str) -> bool:
        candidates = [
            ("role_checkbox_name", lambda: page.get_by_role("checkbox", name=re.compile(airline, re.IGNORECASE))),
            ("role_button_name", lambda: page.get_by_role("button", name=re.compile(airline, re.IGNORECASE))),
            ("label_text", lambda: page.locator(f"label:has-text('{airline}')")),
            ("visible_text", lambda: page.get_by_text(re.compile(airline, re.IGNORECASE))),
        ]
        ok = await _try_click(page, candidates, f"filter_airline({airline},{mode})")
        if ok:
            logger.info("[FLIGHT_FILTER_APPLIED] filter=airline value=%s mode=%s", airline, mode)
        return ok

    @staticmethod
    async def filter_stops(page: Page, mode: str) -> bool:
        name_pat = re.compile(r"stops", re.IGNORECASE)
        candidates = [
            ("role_button_stops", lambda: page.get_by_role("button", name=name_pat)),
            ("label_stops", lambda: page.locator("[aria-label*='stops' i]")),
            ("text_nonstop", lambda: page.get_by_text(re.compile("nonstop", re.IGNORECASE)) if mode == "nonstop"
                else page.get_by_text(re.compile("1 stop", re.IGNORECASE))),
        ]
        ok = await _try_click(page, candidates, f"filter_stops({mode})")
        if ok:
            logger.info("[FLIGHT_FILTER_APPLIED] filter=stops value=%s", mode)
        return ok

    @staticmethod
    async def filter_time(page: Page, which: str, window: str) -> bool:
        candidates = [
            ("role_button_time", lambda: page.get_by_role("button", name=re.compile(f"{which} time|departure|arrival", re.IGNORECASE))),
            ("label_time", lambda: page.locator(f"[aria-label*='{which}' i]")),
            ("text_window", lambda: page.get_by_text(re.compile(window, re.IGNORECASE))),
        ]
        ok = await _try_click(page, candidates, f"filter_time({which},{window})")
        if ok:
            logger.info("[FLIGHT_FILTER_APPLIED] filter=%s_time value=%s", which, window)
        return ok

    @staticmethod
    async def apply_sort(page: Page, sort_key: str) -> bool:
        """Two-step control, same root cause as `change_cabin_class`: the
        sort button's accessible name is its CURRENT state ("Sorted by
        top flights, Change sort order."), not the word "sort" or the
        target option's label — `get_by_role("button", name=/Price/i)`
        could never match. Confirmed live via CDP: the button is
        `<button aria-label="Sorted by ..., Change sort order.">`, and
        an aria-haspopup wrapper element intercepts every Playwright
        `.click()` action even after locating the right element (the
        same overlay-interception pattern hit while fixing the cabin
        class control) — a plain DOM `element.click()` via
        `page.evaluate()` avoids that entirely. The dropdown's real
        options carry `role="menuitemradio"` with exact text "Top
        flights" / "Price" / "Departure time" / "Arrival time" /
        "Duration" / "Emissions" (verified live, not guessed)."""
        clicked = await page.evaluate(
            "() => { const b = document.querySelector(\"button[aria-label*='sort' i]\"); "
            "if (!b) return false; b.click(); return true; }"
        )
        if not clicked:
            logger.info("[FLIGHT_CONTROL_FAILED] action=apply_sort(%s) reason=sort_button_not_found", sort_key)
            return False
        logger.info("[FLIGHT_LOCATOR_STRATEGY] strategy=native_js_click action=apply_sort(%s)", sort_key)

        label = {"cheapest": "Price", "fastest": "Duration", "best": "Top flights"}.get(sort_key, sort_key.title())
        option = page.get_by_role("menuitemradio", name=label, exact=True)
        try:
            # `.count()` does NOT auto-wait/retry the way `.click()` does —
            # checking it immediately after the click (as an earlier
            # version of this code did) could return 0 before the menu had
            # even finished rendering under real system load, aborting
            # before ever giving `.click()`'s own auto-retry a chance.
            # `.click()`'s built-in polling (up to `timeout`) is what
            # should absorb that render delay, not an unconditional
            # pre-check.
            await option.first.click(timeout=5000)
            logger.info("[FLIGHT_CONTROL_SUCCESS] action=apply_sort(%s) strategy=native_js_click", sort_key)
            logger.info("[FLIGHT_FILTER_APPLIED] filter=sort value=%s", sort_key)
            return True
        except Exception as exc:
            logger.info("[FLIGHT_CONTROL_FAILED] action=apply_sort(%s) reason=%r", sort_key, str(exc)[:120])
            return False

    @staticmethod
    async def change_date(page: Page, date_phrase: str) -> bool:
        candidates = [
            ("role_button_date", lambda: page.get_by_role("button", name=re.compile("departure|date", re.IGNORECASE))),
            ("label_date", lambda: page.locator("[aria-label*='Departure' i]")),
        ]
        ok = await _try_click(page, candidates, f"change_date({date_phrase})")
        logger.info("[FLIGHT_DATE_CHANGED] date_phrase=%r applied=%s", date_phrase, ok)
        return ok

    @staticmethod
    async def change_passengers(page: Page, adults: int, children: int) -> bool:
        candidates = [
            ("role_button_passengers", lambda: page.get_by_role("button", name=re.compile("passenger", re.IGNORECASE))),
        ]
        ok = await _try_click(page, candidates, f"change_passengers({adults},{children})")
        logger.info("[FLIGHT_PASSENGERS_CHANGED] adults=%d children=%d applied=%s", adults, children, ok)
        return ok

    @staticmethod
    async def change_cabin_class(page: Page, cabin: str) -> bool:
        """Two-step control: unlike every other filter here, Google
        Flights' cabin-class combobox has no accessible name containing
        "class" at all — its accessible name IS the currently-selected
        value ("Economy", "Premium economy", "Business", or "First").
        Confirmed live via CDP DOM inspection: `<div role="combobox">`
        with innerText "Economy"/"Business" was the only combobox-role
        element near the passenger selector — the previous locator
        (`name=/class/i`) could never match and always silently failed.

        Two locator pitfalls found and fixed while verifying live:
        1. `get_by_text(label, exact=True)` resolves to an inner `<span>`
           rather than the actual clickable combobox container, which a
           transparent overlay `<div>` then intercepts — every click
           timed out. `get_by_role("combobox", name=regex)` targets the
           real container and clicks cleanly.
        2. Labels must be tried longest-first ("Premium economy" before
           "Economy") — an unanchored "Economy" search would otherwise
           false-match "Premium economy" first and open the wrong item
           (an anchored `^Economy$` regex against the combobox's
           accessible name returned 0 matches for unrelated reasons, so
           ordering is the correct fix here, not anchoring).
        Verified live in both directions: combobox read back "Business"
        after selecting it from "Economy", then "Economy" again after
        switching back."""
        _CABIN_LABELS_LONGEST_FIRST = ["Premium economy", "Business", "First", "Economy"]
        target_label = {"economy": "Economy", "business": "Business",
                         "premium economy": "Premium economy", "first": "First"}.get(cabin.lower(), cabin.title())

        opened = False
        for label in _CABIN_LABELS_LONGEST_FIRST:
            combobox = page.get_by_role("combobox", name=re.compile(re.escape(label), re.IGNORECASE))
            try:
                if await combobox.count() > 0:
                    await combobox.first.click(timeout=3000)
                    logger.info("[FLIGHT_LOCATOR_STRATEGY] strategy=cabin_combobox_by_role_name action=change_cabin_class(%s) opened_from=%r", cabin, label)
                    opened = True
                    break
            except Exception as exc:
                logger.info("[FLIGHT_CONTROL_RECOVERY] action=change_cabin_class(%s) strategy=cabin_combobox_by_role_name error=%r", cabin, str(exc)[:120])

        if not opened:
            logger.info("[FLIGHT_CONTROL_FAILED] action=change_cabin_class(%s) reason=combobox_not_found", cabin)
            return False

        option = page.get_by_role("option", name=re.compile(f"^{re.escape(target_label)}$", re.IGNORECASE))
        try:
            if await option.count() == 0:
                logger.info("[FLIGHT_CONTROL_FAILED] action=change_cabin_class(%s) reason=option_not_found", cabin)
                return False
            await option.first.click(timeout=3000)
            logger.info("[FLIGHT_CONTROL_SUCCESS] action=change_cabin_class(%s) strategy=cabin_combobox_by_role_name", cabin)
            logger.info("[FLIGHT_FILTER_APPLIED] filter=cabin_class value=%s", cabin)
            return True
        except Exception as exc:
            logger.info("[FLIGHT_CONTROL_FAILED] action=change_cabin_class(%s) reason=%r", cabin, str(exc)[:120])
            return False


# ── FlightDetailsInspector ───────────────────────────────────────────────────────

class FlightDetailsInspector:
    """Opens/reads flight detail panels — never invents baggage/refund
    text that isn't actually visible on the page."""

    @staticmethod
    async def open_first_result(page: Page) -> bool:
        candidates = [
            ("role_button_details", lambda: page.get_by_role("button", name=re.compile("flight details|select flight", re.IGNORECASE))),
            ("generic_result_row", lambda: page.locator("li, div[role='listitem']").first),
        ]
        ok = await _try_click(page, candidates, "open_first_result")
        logger.info("[FLIGHT_DETAILS_OPENED] applied=%s", ok)
        return ok

    @staticmethod
    async def read_cancellation_policy(page: Page) -> str:
        try:
            text = await page.inner_text("body")
        except Exception:
            text = ""
        m = re.search(r"(non[-\s]?refundable|refundable|cancellation[^\n.]{0,200})", text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:300]
        return ""

    @staticmethod
    async def go_back(page: Page) -> bool:
        try:
            await page.go_back(timeout=10_000)
            logger.info("[FLIGHT_RESULTS_RESTORED] method=browser_back")
            return True
        except Exception as exc:
            logger.info("[FLIGHT_CONTROL_FAILED] action=go_back error=%r", str(exc)[:120])
            return False


# ── Baggage / official-site verification ────────────────────────────────────────

async def check_baggage(page: Page, kg: Optional[int]) -> dict:
    """Inspect the current page for baggage information. Never guesses —
    returns found=False if the text simply isn't visible, and callers
    should then ask approval before checking an official airline site."""
    logger.info("[BAGGAGE_CHECK_START] kg=%s", kg)
    logger.info("[FLIGHT_STAGE_CHANGED] stage=CHECKING_BAGGAGE")
    logger.info("[FRONTEND_FLIGHT_STATUS_SENT] state=CHECKING_BAGGAGE message=%r", "Checking baggage...")
    logger.info("[BROWSER_ACTION] action=read_page_text purpose=baggage_check")
    try:
        text = await page.inner_text("body")
    except Exception:
        text = ""

    pattern = re.compile(r"(\d+)\s*kg\b[^.\n]{0,60}(?:baggage|luggage|checked bag)", re.IGNORECASE)
    matches = pattern.findall(text)
    if matches:
        logger.info("[BAGGAGE_INFO_FOUND] matches=%s", matches[:5])
        return {"found": True, "matches": matches[:5]}

    logger.info("[BAGGAGE_INFO_UNAVAILABLE] reason=not_visible_on_current_page")
    return {"found": False, "matches": []}


async def verify_baggage_on_official_site(booking_url: str, kg: Optional[int]) -> dict:
    """Opens exactly one approved second tab on the airline's own URL,
    reads visible text for baggage info, then closes it again (restoring
    the one-tab default). Only called after explicit user approval."""
    logger.info("[OFFICIAL_AIRLINE_CHECK_REQUIRED] url=%s kg=%s", booking_url, kg)
    logger.info("[FLIGHT_STAGE_CHANGED] stage=CHECKING_BAGGAGE")
    logger.info("[FRONTEND_FLIGHT_STATUS_SENT] state=CHECKING_BAGGAGE message=%r", "Verifying on the airline's site...")
    logger.info("[BROWSER_ACTION] action=open_temp_tab url=%s", booking_url)
    page = await browser_workspace.new_tab_if_approved(booking_url, approved=True, reason="baggage_verification_approved")
    if page is None:
        return {"found": False, "matches": [], "verified": False}
    result = await check_baggage(page, kg)
    result["verified"] = result["found"]
    if result["found"]:
        logger.info("[OFFICIAL_AIRLINE_INFO_VERIFIED] url=%s matches=%s", booking_url, result["matches"])
    main_page = await browser_workspace.get_or_create_page()
    await browser_workspace.close_extra_tabs(keep=main_page)
    return result


# ── FlightConversationManager ────────────────────────────────────────────────────

class FlightConversationManager:
    """Top-level orchestrator for one follow-up turn: resolve intent →
    act on the persistent page → update FlightSessionState → narrate."""

    @staticmethod
    async def handle_followup(text: str) -> Optional[str]:
        detected = FlightFollowUpResolver.detect(text)
        if detected is None:
            return None

        session = fss.get_active()
        if session is None:
            return None  # nothing active — let generic routing take over

        page = await browser_workspace.get_or_create_page()
        intent = detected["intent"]
        params = detected["params"]
        global _current_followup_stage
        _current_followup_stage = _INTENT_TO_STAGE.get(intent, "FILTERING_AIRLINE")
        logger.info("[FLIGHT_STAGE_CHANGED] stage=%s intent=%s", _current_followup_stage, intent)
        logger.info("[BROWSER_ACTION] action=followup intent=%s params=%r", intent, params)

        # Phase 4.10: repair airline-name STT errors ("camera rates" ->
        # Emirates) before acting on them. An ambiguous repair asks for
        # clarification instead of silently filtering on garbage text.
        if "airline" in params and params["airline"]:
            from api.agents.browser_agent.travel_entity_resolver import TravelEntityResolver
            resolved = TravelEntityResolver.resolve_airline(params["airline"])
            if resolved.evidence == "ambiguous_needs_clarification" and resolved.candidates:
                return narrate(TravelEntityResolver.clarification_question(resolved))
            if resolved.canonical_name:
                params["airline"] = resolved.canonical_name

        if intent in ("airline_only", "airline_include"):
            airline = params["airline"]
            mode = "only" if intent == "airline_only" else "include"
            ok = await FlightFilterController.filter_airline(page, airline, mode)
            if mode == "only":
                fss.update_filter_state(preferred_airlines=[airline], excluded_airlines=[])
            else:
                prefs = list(session.preferred_airlines) + [airline]
                fss.update_filter_state(preferred_airlines=prefs)
            return narrate(_cl_narrate(_CE.FILTER_APPLIED, {
                "kind": "airline", "value": airline, "ok": ok,
                "fail_text": f"I couldn't find an {airline} filter control on this page — the option may not be available here.",
            }))

        if intent == "airline_exclude":
            airline = params["airline"]
            ok = await FlightFilterController.filter_airline(page, airline, "exclude")
            excl = list(session.excluded_airlines) + [airline]
            fss.update_filter_state(excluded_airlines=excl)
            return narrate(
                f"Hiding {airline} results." if ok else
                f"I couldn't find a way to hide {airline} on this page."
            )

        if intent == "stops_nonstop":
            from api.agents.browser_agent.browser_action_planner import plan_and_execute
            result = await plan_and_execute(
                page, "Show only direct flights",
                ["Inspect current Google Flights state", "Locate the stops filter",
                 "Select nonstop", "Verify results actually updated"],
                lambda: FlightFilterController.filter_stops(page, "nonstop"),
            )
            fss.update_filter_state(stops_filter="nonstop")
            if result["verified"]:
                return narrate(_cl_narrate(_CE.FILTER_APPLIED, {"kind": "stops", "value": "direct flights only"}))
            if result["success"]:
                return narrate("I clicked the direct-flights filter, but I can't confirm the results actually changed.")
            return narrate("I couldn't find a stops filter on this page.")

        if intent == "stops_one":
            ok = await FlightFilterController.filter_stops(page, "1_stop")
            fss.update_filter_state(stops_filter="1_stop")
            return narrate(_cl_narrate(_CE.FILTER_APPLIED, {
                "kind": "stops", "value": "up to one stop", "ok": ok,
                "fail_text": "I couldn't find a stops filter on this page.",
            }))

        if intent == "time_morning":
            ok = await FlightFilterController.filter_time(page, "departure", "morning")
            fss.update_filter_state(departure_time_filter="morning")
            return narrate(_cl_narrate(_CE.FILTER_APPLIED, {
                "kind": "time", "value": "morning", "ok": ok,
                "fail_text": "I couldn't find a departure-time filter on this page.",
            }))

        if intent == "time_evening":
            ok = await FlightFilterController.filter_time(page, "departure", "evening")
            fss.update_filter_state(departure_time_filter="evening")
            return narrate(_cl_narrate(_CE.FILTER_APPLIED, {
                "kind": "time", "value": "evening", "ok": ok,
                "fail_text": "I couldn't find a departure-time filter on this page.",
            }))

        if intent == "date_change":
            date_phrase = params["date_phrase"]
            ok = await FlightFilterController.change_date(page, date_phrase)
            fss.update(departure_date=date_phrase)
            return narrate(f"Updating the date to {date_phrase}." if ok else f"I couldn't open the date picker to change it to {date_phrase}.")

        if intent == "make_return":
            fss.update(one_way_or_return="return")
            return narrate("Switching this to a return trip.")

        if intent == "passengers":
            adults, children = params["adults"], params["children"]
            ok = await FlightFilterController.change_passengers(page, adults, children)
            fss.update(passengers={"adults": adults, "children": children, "infants": 0})
            return narrate(
                f"Updating passengers to {adults} adult(s) and {children} child(ren)." if ok else
                "I couldn't open the passenger selector on this page."
            )

        if intent in ("cabin_economy", "cabin_business"):
            cabin = "economy" if intent == "cabin_economy" else "business"
            ok = await FlightFilterController.change_cabin_class(page, cabin)
            fss.update(cabin_class=cabin)
            return narrate(f"Switching to {cabin} class." if ok else f"I couldn't find a cabin class control for {cabin} on this page.")

        if intent in ("sort_cheapest", "sort_fastest"):
            key = "cheapest" if intent == "sort_cheapest" else "fastest"
            ok = await FlightFilterController.apply_sort(page, key)
            fss.update_filter_state(current_sort=key)
            return narrate(_cl_narrate(_CE.SORT_APPLIED, {
                "key": key, "ok": ok,
                "fail_text": f"I couldn't find a sort control for {key} on this page.",
            }))

        if intent == "baggage_query":
            kg = params.get("kg")
            result = await check_baggage(page, kg)
            if result["found"]:
                return narrate(f"I can see baggage details on this page: {'; '.join(result['matches'][:3])}.")

            # Fare-specific data isn't on the page — offer general airline
            # policy as context (clearly labeled, never conflated with a
            # verified fare-specific fact) before asking to check further.
            candidate_airline = (session.preferred_airlines[0] if session.preferred_airlines else
                                  ((session.selected_flight or {}).get("airline")))
            if candidate_airline:
                from api.agents.browser_agent.airline_knowledge_service import explain_general_vs_fare_specific
                general = explain_general_vs_fare_specific(candidate_airline, kg)
                return narrate(f"{general} Would you like me to check the airline's official website?")

            return narrate(
                "I can't verify the baggage allowance from this page. "
                "Would you like me to check the airline's official website?"
            )

        if intent == "official_site_approved":
            selected = session.selected_flight or {}
            url = selected.get("booking_url", "")
            if not url and session.last_verified_options:
                # Nothing was explicitly "opened"/"selected" — the natural
                # conversation flow ("do they allow 20kg baggage?" -> "yes,
                # check the official site") never requires that step, so
                # fall back to whichever verified option matches the
                # current airline filter (or just the top result).
                candidates = session.last_verified_options
                if session.preferred_airlines:
                    preferred_lower = [a.lower() for a in session.preferred_airlines]
                    matched = [o for o in candidates if str(o.get("airline", "")).lower() in preferred_lower]
                    candidates = matched or candidates
                selected = candidates[0]
                url = selected.get("booking_url", "")
                if url:
                    fss.select_option(selected)
            if not url:
                return narrate("I don't have a specific airline page to check yet — choose a flight first.")

            # `url` here is `booking_url`, which for every extraction layer
            # is the Google Flights search results page itself, not the
            # airline's own site — re-opening it can never surface
            # fare-specific baggage text. Resolve to the real, curated
            # baggage-policy page for this airline when we have one
            # (airline_knowledge_service._GENERAL_BAGGAGE_POLICY already
            # carries a `source_url` per airline for exactly this reason);
            # otherwise honestly say we can't check further rather than
            # opening an unrelated page and pretending it was "the airline's
            # official website".
            airline_name = str(selected.get("airline") or "").strip()
            from api.agents.browser_agent.airline_knowledge_service import get_general_policy
            policy = get_general_policy(airline_name) if airline_name else None
            official_url = policy["source_url"] if policy else None
            if not official_url:
                return narrate(
                    f"I don't have {airline_name or 'this airline'}'s official baggage page on file to check."
                    if airline_name else
                    "I don't know which airline to check the official site for yet."
                )

            _wait_msg = f"Let me check {airline_name}'s official baggage page — one moment."
            narrate(_wait_msg)
            try:
                from api.agents.agent_runtime import agent_runtime as _art_narr
                _active_task = _art_narr.get_active()
                if _active_task is not None and _active_task.ws_send_fn is not None:
                    asyncio.create_task(_active_task.ws_send_fn({
                        "type": "narration", "task_id": _active_task.task_id, "message": _wait_msg,
                    }))
            except Exception:
                pass
            result = await verify_baggage_on_official_site(official_url, session.baggage_requirement and int(re.sub(r"\D", "", session.baggage_requirement) or 0) or None)
            if result.get("found"):
                return narrate(f"Verified on {airline_name}'s official site: {'; '.join(result['matches'][:3])}.")
            return narrate(f"I checked {airline_name}'s official site, but couldn't find clear baggage information there either.")

        if intent == "open_details":
            ok = await FlightDetailsInspector.open_first_result(page)
            if ok and session.last_verified_options:
                fss.select_option(session.last_verified_options[0])
            return narrate("Opening that flight's details." if ok else "I couldn't open details for that result on this page.")

        if intent == "cancellation_policy":
            text_found = await FlightDetailsInspector.read_cancellation_policy(page)
            return narrate(f"Here's what I can see about cancellation: {text_found}" if text_found else
                            "I can't find cancellation policy text on this page.")

        if intent == "go_back":
            ok = await FlightDetailsInspector.go_back(page)
            return narrate("Back to the results." if ok else "I couldn't go back on this page.")

        if intent == "compare_with":
            options = session.last_verified_options or []

            def _find(name: str) -> Optional[dict]:
                return next((o for o in options if name.lower() in (o.get("airline") or "").lower()), None)

            def _describe(o: dict) -> str:
                return f"{o.get('price') or 'price not available'}, {o.get('duration') or 'duration not available'}"

            if "airline_a" in params:
                a_name, b_name = params["airline_a"], params["airline_b"]
                match_a, match_b = _find(a_name), _find(b_name)
                if match_a and match_b:
                    return narrate(_cl_narrate(_CE.COMPARE_TWO, {
                        "a_name": a_name, "a_desc": _describe(match_a),
                        "b_name": b_name, "b_desc": _describe(match_b),
                    }))
                missing = a_name if not match_a else b_name
                return narrate(f"I don't have a verified {missing} option to compare yet on this page.")

            airline = params["airline"]
            match = _find(airline)
            if match:
                return narrate(f"Comparing against {airline}: {_describe(match)}.")
            return narrate(f"I don't have a verified {airline} option to compare yet on this page.")

        if intent == "recommend":
            from api.agents.browser_agent.flight_search_agent import compare_and_recommend, FlightNarrator
            options = session.last_verified_options or []
            if not options:
                return narrate("I don't have verified flight options yet to recommend from.")
            ranking = compare_and_recommend(options, {})
            reasoning = FlightNarrator.explain(ranking)
            return narrate(reasoning)

        if intent == "show_more":
            options = session.last_verified_options or []
            rest = options[3:6]
            if not rest:
                return narrate("That's all the verified options I have from this page.")
            from api.agents.browser_agent.flight_search_agent import _display_airline
            lines = [f"{_display_airline(o)}: {o.get('price') or 'price not available'}" for o in rest]
            return narrate("More options: " + "; ".join(lines))

        if intent == "cancel":
            logger.info("[VOICE_APPROVAL_CANCELLED] task=%s reason=user_cancel", session.task_id)
            fss.clear()
            from api.agents.browser_agent.conversation_layer import reset_conversation_memory as _cl_reset
            _cancel_line = _cl_narrate(_CE.CANCELLED) or "Cancelled."
            _cl_reset()
            return narrate(f"{_cancel_line} The browser stays open, but I've cleared the active flight search.")

        return None

    @classmethod
    async def handle_followup_logged(cls, text: str) -> Optional[str]:
        """Same as handle_followup, but also emits the
        [VOICE_APPROVAL_RESOLVED]/[VOICE_APPROVAL_APPLIED] pair the voice
        pipeline is required to log once a follow-up actually resolves."""
        reply = await cls.handle_followup(text)
        if reply is not None:
            logger.info("[VOICE_APPROVAL_RESOLVED] text=%r", text[:100])
            logger.info("[VOICE_APPROVAL_APPLIED] text=%r reply=%r", text[:100], reply[:100])
        return reply
