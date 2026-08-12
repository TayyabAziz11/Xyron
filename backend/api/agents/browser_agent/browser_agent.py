"""
Phase 3.1 — BrowserAgent entry point.

Called by AgentRuntime via:
    result_summary = await run(task, runtime, cancel_event, pause_event)

Routing logic
─────────────
Goal keyword            → Sub-agent
──────────────────────────────────────────────────────────────────────────
research / summarize    → BrowserResearchAgent.research()
compare / vs / price    → BrowserReader.compare_products()
book / flight / hotel   → FlightSearchAgent.search_and_compare() (layered extraction)
download / invoice      → BrowserDownloadAgent.download_file()
apply / fill form       → BrowserFormAgent (detect + fill, gated submit)
(default)               → BrowserNavigator.go_to() + BrowserReader.summarize()

One BrowserContext is shared across the whole task lifetime and closed on exit.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

from api.agents.agent_types import AgentTask, AgentStatus, StepResult
from api.agents.browser_agent.browser_navigator import BrowserNavigator
from api.agents.browser_agent.browser_reader import BrowserReader
from api.agents.browser_agent.browser_interactor import BrowserInteractor
from api.agents.browser_agent.browser_verifier import BrowserVerifier
from api.agents.browser_agent.browser_recovery import BrowserRecovery
from api.agents.browser_agent.browser_form_agent import BrowserFormAgent
from api.agents.browser_agent.browser_research_agent import BrowserResearchAgent
from api.agents.browser_agent.browser_download_agent import BrowserDownloadAgent

logger = logging.getLogger("api.agents.browser_agent")

# ── Routing keyword sets ───────────────────────────────────────────────────────

_RESEARCH_KW = re.compile(
    r"\b(research|summarize|find information|look up|what is|tell me about"
    r"|search for|explain|overview|who is|history of)\b",
    re.IGNORECASE,
)
_COMPARE_KW = re.compile(
    r"\b(compare|vs\.?|versus|price|cheapest|best deal|which is better"
    r"|difference between|cost of)\b",
    re.IGNORECASE,
)
_BOOK_KW = re.compile(
    r"\b(book|flights?|hotel|ticket|reserve|reservation|buy ticket)\b",
    re.IGNORECASE,
)
_DOWNLOAD_KW = re.compile(
    r"\b(download|invoice|receipt|statement|report|file|pdf|export)\b",
    re.IGNORECASE,
)
_FORM_KW = re.compile(
    r"\b(apply|fill|submit|application|job application|form|register|sign up)\b",
    re.IGNORECASE,
)

# Default save directory for downloads
_DOWNLOAD_DIR = Path.home() / "Downloads" / "xyron"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _route(goal: str) -> str:
    """
    Return the routing key for *goal*.
    Order matters — more specific checks come first.
    """
    if _FORM_KW.search(goal):
        return "form"
    if _DOWNLOAD_KW.search(goal):
        return "download"
    if _BOOK_KW.search(goal):
        return "book"
    if _COMPARE_KW.search(goal):
        return "compare"
    if _RESEARCH_KW.search(goal):
        return "research"
    return "navigate"


async def _send(task: AgentTask, payload: dict) -> None:
    """Fire-and-forget WebSocket send — never raises."""
    if task.ws_send_fn is not None:
        try:
            await task.ws_send_fn(payload)
        except Exception:
            pass


async def _progress(task: AgentTask, message: str, pct: int) -> None:
    """Canonical progress event — the one function every route uses to
    narrate a step, so UI/TTS/logs all see the same text instead of
    separately hardcoded messages for each destination (Phase 4.11 Part 7).
    Suppresses exact-duplicate consecutive messages so a slow step doesn't
    spam the same line twice."""
    last = task.metadata.get("_last_progress_msg")
    if last == message:
        logger.info("[PROGRESS_DUPLICATE_SUPPRESSED] task=%s message=%r", task.task_id, message[:80])
        return
    task.metadata["_last_progress_msg"] = message

    task.progress_pct = pct
    logger.info("[PROGRESS_EVENT_CREATED] task=%s pct=%d message=%r", task.task_id, pct, message[:80])
    logger.info("[PROGRESS_UPDATE_SENT] task=%s pct=%d message=%r", task.task_id, pct, message[:80])
    await _send(task, {
        "type": "progress",
        "task_id": task.task_id,
        "message": message,
        "progress_pct": pct,
    })
    if task.ws_send_fn is not None:
        logger.info("[PROGRESS_UI_SENT] task=%s pct=%d", task.task_id, pct)
        logger.info("[PROGRESS_SYNC_OK] task=%s narration=%r ui_message=%r", task.task_id, message[:80], message[:80])


async def _get_page_with_repair(task: AgentTask) -> Page:
    """Phase 4.11.1 — self-healing entry point for controlled browser
    tasks. `browser_workspace.get_or_create_page()` no longer falls back
    to an uncontrolled browser on its own; if it raises
    `CDPUnavailableError`, this runs the CDP Environment Doctor's
    diagnose-then-repair flow (one Windows elevation prompt) and retries
    exactly once before giving up honestly."""
    from api.agents.browser_agent.browser_workspace import browser_workspace, CDPUnavailableError
    from api.services.cdp_environment_doctor import doctor as cdp_doctor

    try:
        page = await browser_workspace.get_or_create_page()
        if browser_workspace.last_tab_match == "exact_session_url":
            await _progress(task, "Reusing your flight search", 4)
        else:
            await _progress(task, "Connecting to Chrome", 4)
        return page
    except CDPUnavailableError:
        pass

    await _progress(task, "Repairing the Windows Chrome bridge", 4)
    diagnosis = await cdp_doctor.diagnose()
    if not diagnosis.repair_required:
        # Doctor sees no fixable issue (e.g. Chrome isn't installed at
        # all) — retrying would just fail the same way.
        raise CDPUnavailableError("CDP unreachable and no repairable cause found.")

    repair_result = await cdp_doctor.repair(diagnosis)
    if not repair_result.get("success"):
        raise CDPUnavailableError(
            f"Elevated repair failed or was denied: {repair_result.get('errors')}",
        )

    await _progress(task, "Connecting to Chrome", 5)
    page = await browser_workspace.get_or_create_page()
    return page


# ── Public entry point ─────────────────────────────────────────────────────────

async def run(
    task: AgentTask,
    runtime: Any,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
) -> str:
    """
    Entry point called by AgentRuntime.

    Launches Playwright (headless Chromium), routes to the correct sub-agent,
    and returns a plain-English summary of what was accomplished.
    """
    from api.agents.personality.personality_engine import personality_engine

    goal = task.goal
    logger.info("[BROWSER_LAZY_INIT_TRIGGERED] task_id=%s reason=browser_command", task.task_id)
    logger.info("[BROWSER_AGENT_START] goal=%r task=%s", goal, task.task_id)
    logger.info("[BROWSER_START] task=%s", task.task_id)

    route = _route(goal)
    logger.info("[BROWSER_AGENT_ROUTE] route=%s", route)

    await _progress(task, personality_engine.narrate_step("browser.opening"), 2)

    summary: str = ""

    if route == "book":
        # Persistent workspace: one visible, controllable browser reused
        # across this task AND every later follow-up voice turn — never
        # closed in a per-task `finally` (that was the bug that made
        # follow-ups like "check Emirates" impossible; see
        # browser_workspace.py for the full architecture rationale).
        from api.agents.browser_agent.browser_workspace import browser_workspace, CDPUnavailableError
        # Phase 4.14 Part 8: anchor timing to the moment the user actually
        # stopped speaking (voice_ws.py's _turn_t0, passed through via
        # context["turn_started_at"]), not to whenever this coroutine
        # happened to start — so BROWSER_VISIBLE_MS/PAGE_READY_MS reflect
        # true perceived latency instead of an agent-internal relative clock.
        _ux_t0 = task.metadata.get("context", {}).get("turn_started_at") or time.time()
        task.metadata["_ux_t0"] = _ux_t0
        logger.info("[BROWSER_LAUNCH_PARALLEL_START] task=%s speech_to_launch_ms=%.1f",
                    task.task_id, (time.time() - _ux_t0) * 1000)
        try:
            await _progress(task, "Checking Chrome connection", 3)
            page = await _get_page_with_repair(task)
            task.metadata["_ux_browser_visible_ms"] = round((time.time() - _ux_t0) * 1000, 1)
            logger.info("[BROWSER_VISIBLE_MS] ms=%.1f", task.metadata["_ux_browser_visible_ms"])
            logger.info("[BROWSER_VISIBLE] ms=%.1f task=%s", task.metadata["_ux_browser_visible_ms"], task.task_id)
            if task.metadata["_ux_browser_visible_ms"] > 2000:
                logger.warning("[SLOW_STAGE] stage=browser_visible ms=%.1f budget=2000", task.metadata["_ux_browser_visible_ms"])
            navigator = BrowserNavigator(page)
            if cancel_event.is_set():
                return "Task was cancelled before browser work started."
            summary = await _dispatch(
                route=route, goal=goal, task=task, page=page, ctx=None,
                navigator=navigator, reader=BrowserReader(), interactor=BrowserInteractor(),
                verifier=BrowserVerifier(), recovery=BrowserRecovery(),
                cancel_event=cancel_event, pause_event=pause_event,
            )
        except asyncio.CancelledError:
            logger.info("[BROWSER_AGENT_CANCELLED] task=%s", task.task_id)
            summary = "Browser task was cancelled."
        except CDPUnavailableError:
            logger.warning("[CDP_UNAVAILABLE_USER_NOTIFIED] task=%s", task.task_id)
            await _progress(task, "Chrome control unavailable", 100)
            summary = "I couldn't establish control of Chrome. I haven't started the flight search."
        except Exception as exc:
            logger.error("[BROWSER_AGENT_ERROR] task=%s error=%r", task.task_id, str(exc))
            summary = f"Browser agent encountered an error: {exc}"
        logger.info("[BROWSER_AGENT_DONE] task=%s summary_chars=%d", task.task_id, len(summary))
        await _send(task, {"type": "agent_done", "task_id": task.task_id, "summary": summary})
        return summary

    # ── Every other route keeps the original per-task headless browser,
    # launched fresh and closed on exit — only the flight/travel workflow
    # needs cross-turn persistence.
    browser: Optional[Browser] = None
    ctx: Optional[BrowserContext] = None
    page: Optional[Page] = None

    try:
        async with async_playwright() as pw:
            # Headless — this instance only drives/reads pages for automation.
            # The user sees the real Windows Chrome instead: BrowserNavigator
            # opens every visited URL there too via `cmd.exe /c start`
            # (see browser_navigator._open_in_real_chrome), the same WSL
            # interop mechanism system_tools.py already uses to launch apps.
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                ],
            )
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = await ctx.new_page()
            logger.info("[BROWSER_PAGE_OPENED] url=about:blank")

            # Instantiate helpers
            navigator = BrowserNavigator(page)
            reader = BrowserReader()
            interactor = BrowserInteractor()
            verifier = BrowserVerifier()
            recovery = BrowserRecovery()

            # Check for cancellation before doing any real work
            if cancel_event.is_set():
                return "Task was cancelled before browser work started."

            summary = await _dispatch(
                route=route,
                goal=goal,
                task=task,
                page=page,
                ctx=ctx,
                navigator=navigator,
                reader=reader,
                interactor=interactor,
                verifier=verifier,
                recovery=recovery,
                cancel_event=cancel_event,
                pause_event=pause_event,
            )

    except asyncio.CancelledError:
        logger.info("[BROWSER_AGENT_CANCELLED] task=%s", task.task_id)
        summary = "Browser task was cancelled."
    except Exception as exc:
        logger.error(
            "[BROWSER_AGENT_ERROR] task=%s error=%r", task.task_id, str(exc)
        )
        summary = f"Browser agent encountered an error: {exc}"
    finally:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    logger.info(
        "[BROWSER_AGENT_DONE] task=%s summary_chars=%d",
        task.task_id,
        len(summary),
    )
    await _send(task, {
        "type": "agent_done",
        "task_id": task.task_id,
        "summary": summary,
    })
    return summary


# ── Dispatcher ─────────────────────────────────────────────────────────────────

async def _dispatch(
    route: str,
    goal: str,
    task: AgentTask,
    page: Page,
    ctx: BrowserContext,
    navigator: BrowserNavigator,
    reader: BrowserReader,
    interactor: BrowserInteractor,
    verifier: BrowserVerifier,
    recovery: BrowserRecovery,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
) -> str:
    """Route to the correct sub-agent handler and return a summary string."""

    from api.agents.personality.personality_engine import personality_engine

    # ── Research ───────────────────────────────────────────────────────────────
    if route == "research":
        await _progress(task, personality_engine.narrate_step("browser.searching"), 8)
        agent = BrowserResearchAgent()
        return await agent.research(
            goal=goal,
            context=ctx,
            navigator=navigator,
            reader=reader,
            task=task,
        )

    # ── Comparison ─────────────────────────────────────────────────────────────
    if route == "compare":
        await _progress(task, personality_engine.narrate_step("browser.comparing"), 10)
        query = re.sub(r"^(compare|find|search for)\s+", "", goal, flags=re.IGNORECASE)
        search_results = await navigator.search_google(query)

        from api.agents.browser_agent.browser_navigator import _open_in_real_chrome

        pages_to_compare: list[Page] = []
        for r in search_results[:4]:
            url = r.get("url", "")
            if not url.startswith("http"):
                continue
            try:
                _open_in_real_chrome(url)  # mirror to the visible Windows Chrome too
                p = await ctx.new_page()
                await p.goto(url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(0.5)
                pages_to_compare.append(p)
            except Exception:
                continue

        if not pages_to_compare:
            return f"Could not open any pages for comparison: {goal}"

        await _progress(task, "Extracting comparison data…", 70)
        result = await reader.compare_products(pages_to_compare, goal)

        for p in pages_to_compare:
            try:
                await p.close()
            except Exception:
                pass

        return result

    # ── Booking / flights ──────────────────────────────────────────────────────
    if route == "book":
        from api.agents.browser_agent.flight_search_agent import (
            search_and_compare, request_decision,
            TravelIntentParser, TravelPreferenceMemory,
        )
        from api.agents.browser_agent.flight_narration import FlightStage, speak_stage
        from api.agents.browser_agent.conversation_layer import narrate as _cl_narrate, ConversationEvent as _CE

        _ack_line = _cl_narrate(_CE.ACKNOWLEDGE) or "Sure."
        speak_stage(FlightStage.UNDERSTANDING_FLIGHT_REQUEST, _ack_line, task=task, speak=False)

        # Phase 4.11: show a REAL page immediately, before entity
        # resolution/TravelGoal building even runs — never leave Chrome
        # on a blank page while that (fast, local, but non-zero) work
        # happens. If we're already mid-conversation on a flights page,
        # leave it alone instead of re-navigating for no reason.
        _flights_home = "https://www.google.com/travel/flights"
        if not (page.url or "").startswith("https://www.google.com/travel/flights"):
            logger.info("[BROWSER_LAUNCH_REQUESTED] target=%s", _flights_home)
            _opening_msg = _cl_narrate(_CE.OPENING_BROWSER) or "I'm opening Google Flights."
            logger.info("[AGENT_NARRATION] step=flight.opening text=%r", _opening_msg)
            await _progress(task, _opening_msg, 5)
            speak_stage(FlightStage.OPENING_GOOGLE_FLIGHTS, _opening_msg, task=task)
            logger.info("[BROWSER_ACTION] action=navigate url=%s", _flights_home)
            ok_home = await navigator.go_to(_flights_home, mirror=False)
            if "_ux_t0" in task.metadata:
                task.metadata["_ux_page_ready_ms"] = round((time.time() - task.metadata["_ux_t0"]) * 1000, 1)
                logger.info("[BROWSER_PAGE_READY_MS] ms=%.1f", task.metadata["_ux_page_ready_ms"])
            if ok_home:
                logger.info("[BROWSER_VISIBLE_MS] event=flights_home_loaded")
            else:
                logger.warning("[BLANK_PAGE_BLOCKED] reason=home_nav_failed url=%s", _flights_home)

        # Attempt to parse origin/destination/date from the goal, plus
        # consultant-level preferences (budget/airline/baggage/time/trip-type).
        origin, destination, date = _parse_travel_intent(goal)
        intent = TravelIntentParser.parse(goal)
        travel_memory = TravelPreferenceMemory()

        # "Always prefer morning flights" — a standalone preference
        # statement, not a search request. Save it and stop here; no
        # browser search runs for a bare preference update.
        if intent["is_preference_only"] and not (origin and destination):
            travel_memory.update(intent)
            ack = "Got it — I'll keep that in mind for future flight searches."
            logger.info("[AGENT_NARRATION] step=flight.preference_saved text=%r", ack)
            return ack

        # Bare "<city> flight" phrasing without "from X to Y", e.g. "find me
        # another Dubai flight" — grab the destination word directly.
        if not destination:
            bare_dest_m = _bare_destination_match(goal)
            if bare_dest_m:
                destination = bare_dest_m.group(1).strip()

        # Phase 4.10: repair STT errors ("do my"/"carachi") through the
        # travel entity resolver + real aviation dataset BEFORE building
        # anything context/memory/browser-related touches these values.
        # A garbled destination that resolves ambiguously (e.g. "do my"
        # could be Doha/Damascus/etc.) triggers a clarification question
        # instead of silently searching for the wrong city.
        from api.agents.browser_agent.travel_goal import build_travel_goal
        _ux_plan_t0 = time.time()
        mem_ctx = travel_memory.get_context()
        travel_goal = build_travel_goal(goal, origin_raw=origin, destination_raw=destination, memory_context=mem_ctx)
        logger.info("[TRAVEL_PLAN_PARALLEL_MS] ms=%.1f", (time.time() - _ux_plan_t0) * 1000)
        if travel_goal.needs_clarification:
            logger.info("[AGENT_NARRATION] step=flight.clarification_needed text=%r", travel_goal.needs_clarification)
            return travel_goal.needs_clarification

        origin = travel_goal.origin or origin
        destination = travel_goal.destination or destination
        date = travel_goal.departure_date or date

        prefs = travel_memory.update(intent, origin=origin, destination=destination)

        if origin and destination:
            check_msg = f"I'm checking flights from {origin} to {destination}."
        else:
            check_msg = "I'm checking flights for you now."
        logger.info("[AGENT_NARRATION] step=flight.checking text=%r", check_msg)
        await _progress(task, check_msg, 10)

        from api.agents.browser_agent import flight_session_state as fss
        existing_session = fss.get_active()
        if existing_session is not None:
            fss.update(task_id=task.task_id, origin=origin or existing_session.origin,
                       destination=destination or existing_session.destination,
                       departure_date=date or existing_session.departure_date)
        else:
            fss.create(task.task_id, origin=origin, destination=destination, departure_date=date)
            from api.agents.browser_agent.conversation_layer import reset_conversation_memory
            reset_conversation_memory()

        _ux_search_t0 = time.time()
        result = await search_and_compare(page, navigator, origin, destination, date, task, prefs)
        first_search_action_ms = round((time.time() - _ux_search_t0) * 1000, 1)

        if "_ux_t0" in task.metadata:
            total_ms = round((time.time() - task.metadata["_ux_t0"]) * 1000, 1)
            browser_visible_ms = task.metadata.get("_ux_browser_visible_ms", 0.0)
            page_ready_ms = task.metadata.get("_ux_page_ready_ms", 0.0)
            logger.info(
                "[UX_LATENCY] browser_launch_request_ms=%.1f browser_visible_ms=%.1f "
                "page_ready_ms=%.1f first_search_action_ms=%.1f total_ms=%.1f",
                browser_visible_ms, browser_visible_ms, page_ready_ms, first_search_action_ms, total_ms,
            )
            stages = {
                "browser_launch": browser_visible_ms,
                "page_to_ready": max(0.0, page_ready_ms - browser_visible_ms),
                "search_action": first_search_action_ms,
            }
            bottleneck = max(stages, key=stages.get)
            logger.info("[UX_LATENCY_BOTTLENECK] stage=%s ms=%.1f", bottleneck, stages[bottleneck])

        compare_msg = "I'm comparing price, time, and stops."
        logger.info("[AGENT_NARRATION] step=flight.comparing text=%r", compare_msg)
        await _progress(task, compare_msg, 60)
        await _progress(task, result["spoken"], 80)

        fss.update(
            last_verified_options=result.get("options", []),
            current_page_url=page.url,
            confidence="high" if result.get("options") else "low",
        )

        decision_summary = await request_decision(task, result, cancel_event)
        return f"{result['spoken']}\n\n{decision_summary}"

    # ── Download ───────────────────────────────────────────────────────────────
    if route == "download":
        await _progress(task, "Looking for download link…", 10)
        dl_agent = BrowserDownloadAgent()

        # Try to find a URL in the goal first
        url_match = re.search(r"https?://\S+", goal)
        download_url: Optional[str] = url_match.group(0) if url_match else None

        if not download_url:
            # Navigate to a relevant page and find the link
            query = re.sub(
                r"^(download|get|fetch)\s+", "", goal, flags=re.IGNORECASE
            )
            results = await navigator.search_google(query)
            if results:
                best = results[0]
                await navigator.go_to(best["url"])
                await navigator.handle_cookie_banner()
                download_url = await dl_agent.find_download_link(page, goal)

        if not download_url:
            return f"Could not find a download link for: {goal}"

        filename = _guess_filename(goal, download_url)
        save_path = _DOWNLOAD_DIR / filename
        result: StepResult = await dl_agent.download_file(page, download_url, save_path, task)
        return result.output

    # ── Form / apply ───────────────────────────────────────────────────────────
    if route == "form":
        await _progress(task, "Opening page and detecting form fields…", 10)
        form_agent = BrowserFormAgent()

        # Extract URL if present in goal
        url_match = re.search(r"https?://\S+", goal)
        if url_match:
            await navigator.go_to(url_match.group(0))

        await navigator.handle_cookie_banner()
        fields = await form_agent.detect_forms(page)

        if not fields:
            return f"No form fields found on this page for: {goal}"

        field_names = [f.get("label") or f.get("name") for f in fields if f.get("label") or f.get("name")]
        form_summary = (
            f"Form with {len(fields)} field(s) detected:\n"
            + "\n".join(f"  • {n}" for n in field_names[:10])
        )

        await _progress(task, f"Form detected: {len(fields)} fields. Requesting approval before filling.", 50)

        # Always request approval before submitting
        await form_agent.request_submission_approval(task, form_summary)
        # Return early — runtime must wait for approval event
        return (
            f"I found a form with {len(fields)} fields. "
            f"Please review and approve before I fill and submit it.\n\n{form_summary}"
        )

    # ── Default: navigate + read ───────────────────────────────────────────────
    await _progress(task, personality_engine.narrate_step("browser.reading"), 10)

    # Extract URL if present
    url_match = re.search(r"https?://\S+", goal)
    if url_match:
        url = url_match.group(0)
    else:
        # Fall back to Google search + open first result
        query = re.sub(
            r"^(open|go to|navigate to|visit|show me)\s+", "", goal, flags=re.IGNORECASE
        )
        results = await navigator.search_google(query)
        if not results:
            return f"Could not find a relevant page for: {goal}"
        url = results[0]["url"]

    ok = await navigator.go_to(url)
    if not ok:
        return f"Failed to open {url}"

    await navigator.handle_cookie_banner()
    await navigator.handle_popup()
    await navigator.wait_for_load()

    loaded = await verifier.verify_page_loaded(page)
    if not loaded:
        recovered = await recovery.recover_from_error(page, "page not loaded", _dummy_step())
        if not recovered:
            return f"Page failed to load properly: {url}"

    await _progress(task, personality_engine.narrate_step("browser.reading"), 60)
    summary_text = await reader.summarize_page(page, max_chars=1500)
    title = await navigator.get_page_title()
    logger.info("[BROWSER_PAGE_READ] url=%s chars=%d", page.url, len(summary_text))

    return f"**{title}** ({page.url})\n\n{summary_text}"


# ── Utilities ──────────────────────────────────────────────────────────────────

_DATE_PHRASE_RE = re.compile(
    r"\b(next\s+month|next\s+week|tomorrow|today|this\s+weekend|"
    r"in\s+\d+\s+days?|on\s+[A-Za-z0-9,\s]+?)$",
    re.IGNORECASE,
)

# Catches bare "<city> flight" phrasing with no "from X to Y" structure,
# e.g. "find me another Dubai flight" — used only as a fallback when the
# richer from/to parse below finds nothing.
_BARE_DEST_STOPWORDS = {
    "a", "the", "my", "another", "next", "new", "one", "return", "direct",
    "cheap", "cheapest", "this", "that", "first", "business", "economy",
    "international", "domestic", "morning", "afternoon", "evening", "night",
}
_BARE_DEST_RE = re.compile(r"\b([A-Za-z]+)\s+flights?\b", re.IGNORECASE)


def _bare_destination_match(goal: str) -> Optional[re.Match]:
    for m in _BARE_DEST_RE.finditer(goal):
        if m.group(1).lower() not in _BARE_DEST_STOPWORDS:
            return m
    return None


def _parse_travel_intent(goal: str) -> tuple[str, str, str]:
    """
    Very lightweight extraction of origin, destination, date from a travel goal.
    Returns ('', '', '') for anything we cannot parse — callers show a search UI.
    """
    # Pull a trailing relative/absolute date phrase off first so it isn't
    # swallowed into the destination capture, e.g. "...to Dubai next month".
    goal = goal.strip().rstrip(".!? ")
    date = ""
    goal_wo_date = goal
    date_match = _DATE_PHRASE_RE.search(goal)
    if date_match:
        date = date_match.group(0).strip()
        goal_wo_date = goal[: date_match.start()].strip()

    # "book flight from NYC to London" (date already stripped above, if present)
    from_match = re.search(
        r"from\s+([A-Za-z\s]+?)\s+to\s+([A-Za-z\s]+?)$",
        goal_wo_date,
        re.IGNORECASE,
    )
    if from_match:
        origin = from_match.group(1).strip()
        destination = from_match.group(2).strip()
        return origin, destination, date
    return "", "", date


def _guess_filename(goal: str, url: str) -> str:
    """Derive a safe filename from the goal or URL."""
    # Try URL path segment
    path_part = url.split("?")[0].rstrip("/").split("/")[-1]
    if "." in path_part and len(path_part) < 100:
        return path_part
    # Derive from goal
    slug = re.sub(r"[^a-z0-9]+", "_", goal.lower()).strip("_")[:40]
    return slug + ".bin"


def _dummy_step() -> Any:
    """Create a minimal AgentStep stub for recovery calls."""
    from api.agents.agent_types import AgentStep, RiskLevel
    return AgentStep(index=0, description="navigate", risk=RiskLevel.LOW)
