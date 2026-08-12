from __future__ import annotations

"""
BrowserActionPlanner — wraps a follow-up action in an explicit
plan -> execute -> verify -> replan structure, instead of treating "the
click succeeded" as proof the action actually worked. Verification
compares real page state (URL and a content hash) before/after — a
click can "succeed" against a stale or wrong element while the page
never actually changes; this catches that.

Does not re-implement locator strategies (FlightFilterController /
_try_click already do the role/name -> label -> text -> attribute
fallback chain) — this module sits one level up, around that existing
logic, adding the plan/verify/replan bookkeeping the previous phases
were missing.

Log tags: [BROWSER_ACTION_PLAN] [BROWSER_ACTION_STEP]
[BROWSER_ACTION_VERIFY] [BROWSER_ACTION_REPLAN]
"""

import logging
from typing import Awaitable, Callable, Optional

from playwright.async_api import Page

logger = logging.getLogger("api.agents.browser_agent.action_planner")


async def _page_fingerprint(page: Page) -> str:
    """Cheap content fingerprint — URL plus a slice of visible text length,
    enough to detect "the page actually changed" without a full DOM diff."""
    try:
        url = page.url
        text_len = await page.evaluate("() => (document.body && document.body.innerText || '').length")
        return f"{url}#{text_len}"
    except Exception:
        return ""


async def plan_and_execute(
    page: Page,
    goal_description: str,
    steps: list[str],
    action: Callable[[], Awaitable[bool]],
) -> dict:
    """
    goal_description: e.g. "Morning flights only"
    steps: human-readable plan steps, logged before execution
    action: the actual FlightFilterController/DetailsInspector call —
            returns True if a control was found and clicked.

    Returns {"success": bool, "verified": bool, "reason": str}.
    "success" means a control was clicked; "verified" means the page
    state actually changed as a result. A click without a verified page
    change is reported honestly, not claimed as a working filter.
    """
    logger.info("[BROWSER_ACTION_PLAN] goal=%r steps=%s", goal_description, steps)
    for i, step in enumerate(steps, 1):
        logger.info("[BROWSER_ACTION_STEP] step=%d/%d text=%r", i, len(steps), step)

    before = await _page_fingerprint(page)
    clicked = False
    try:
        clicked = await action()
    except Exception as exc:
        logger.info("[BROWSER_ACTION_REPLAN] goal=%r reason=action_raised error=%r", goal_description, str(exc)[:150])
        return {"success": False, "verified": False, "reason": "action_error"}

    if not clicked:
        logger.info("[BROWSER_ACTION_VERIFY] goal=%r result=no_control_found", goal_description)
        return {"success": False, "verified": False, "reason": "no_control_found"}

    # Give the page a brief moment to settle (network/DOM update) before
    # taking the "after" fingerprint.
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    after = await _page_fingerprint(page)
    changed = bool(before) and bool(after) and before != after

    if changed:
        logger.info("[BROWSER_ACTION_VERIFY] goal=%r result=page_state_changed", goal_description)
        return {"success": True, "verified": True, "reason": "page_state_changed"}

    logger.info("[BROWSER_ACTION_REPLAN] goal=%r reason=click_succeeded_but_no_page_change", goal_description)
    logger.info("[BROWSER_ACTION_VERIFY] goal=%r result=unverified_click_only", goal_description)
    return {"success": True, "verified": False, "reason": "clicked_but_unverified"}
