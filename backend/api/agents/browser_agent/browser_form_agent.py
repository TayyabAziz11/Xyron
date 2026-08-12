"""
BrowserFormAgent — safe form detection, filling, and gated submission.

SAFETY CONTRACT
===============
NEVER_AUTO_SUBMIT = True

submit_if_approved() is the ONLY path to form submission, and it checks
task.metadata['approved'] == True. All other submission attempts are blocked.
request_submission_approval() always returns False — it sends the approval
event via WebSocket and the human must explicitly approve.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from playwright.async_api import Page

from api.agents.agent_types import AgentTask

logger = logging.getLogger("api.agents.browser_agent.form_agent")

# JS that finds all visible, interactive form fields on the page.
_DETECT_FORMS_JS = """
() => {
    const fields = [];
    const inputs = document.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]),' +
        'textarea, select'
    );
    inputs.forEach(el => {
        if (!el.offsetParent) return; // skip hidden
        // Find associated label
        let label = '';
        if (el.id) {
            const labelEl = document.querySelector('label[for="' + el.id + '"]');
            if (labelEl) label = labelEl.innerText.trim();
        }
        if (!label && el.placeholder) label = el.placeholder;
        if (!label && el.name) label = el.name;
        if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
        fields.push({
            name: el.name || el.id || '',
            type: el.type || el.tagName.toLowerCase(),
            label: label,
            required: el.required || false,
            selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : ''),
        });
    });
    return fields;
}
"""


class BrowserFormAgent:
    """Handles form detection and filling. Submission ALWAYS requires approval."""

    NEVER_AUTO_SUBMIT: bool = True  # Hard-coded safety constant

    # ── Form detection ─────────────────────────────────────────────────────────

    async def detect_forms(self, page: Page) -> list[dict]:
        """
        Detect all visible, interactive form fields on the current page.

        Returns: [{name, type, label, required, selector}]
        """
        try:
            fields: list[dict] = await page.evaluate(_DETECT_FORMS_JS)
            logger.info(
                "[BROWSER_FORMS_DETECTED] url=%s fields=%d",
                page.url,
                len(fields),
            )
            return fields
        except Exception as exc:
            logger.warning("[BROWSER_FORM_DETECT_ERROR] error=%r", str(exc))
            return []

    # ── Form filling ───────────────────────────────────────────────────────────

    async def fill_form_fields(self, page: Page, form_data: dict) -> bool:
        """
        Fill form fields WITHOUT submitting.

        *form_data*: {field_selector_or_name: value_to_fill}
        Returns True if all provided fields were filled successfully.
        """
        all_ok = True
        for field_key, value in form_data.items():
            selector = field_key if field_key.startswith(
                ("#", ".", "[", "input", "select", "textarea")
            ) else f'[name="{field_key}"], [id="{field_key}"], [placeholder*="{field_key}" i]'

            filled = await self._fill_one_field(page, selector, value)
            if not filled:
                # Try the key directly as a CSS selector
                filled = await self._fill_one_field(page, field_key, value)
            if not filled:
                logger.warning(
                    "[BROWSER_FORM_FILL_MISS] field=%r value=%r", field_key, value
                )
                all_ok = False

        return all_ok

    async def _fill_one_field(
        self, page: Page, selector: str, value: Any
    ) -> bool:
        """Internal: fill a single field. Returns True on success."""
        try:
            # For compound selectors (a, b, c) query each variant
            variants = [s.strip() for s in selector.split(",")]
            for sel in variants:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    field_type = await el.get_attribute("type") or ""

                    if tag == "select":
                        await el.select_option(str(value))
                    elif field_type in ("checkbox", "radio"):
                        checked = await el.is_checked()
                        if bool(value) != checked:
                            await el.click()
                    else:
                        await el.triple_click()
                        await el.fill(str(value))

                    logger.info(
                        "[BROWSER_ACTION_FILL] field=%r value=%r",
                        sel,
                        str(value)[:40],
                    )
                    await asyncio.sleep(0.15)
                    return True
        except Exception as exc:
            logger.debug(
                "[BROWSER_FIELD_FILL_ERROR] selector=%r error=%r",
                selector,
                str(exc),
            )
        return False

    # ── Approval gate ──────────────────────────────────────────────────────────

    async def request_submission_approval(
        self, task: AgentTask, form_summary: str
    ) -> bool:
        """
        Send an approval request to the user via WebSocket.

        ALWAYS returns False — submission is blocked until the user approves.
        The caller must poll task.metadata.get('approved') after this returns.
        """
        logger.info(
            "[BROWSER_APPROVAL_REQUIRED] action=form_submit summary=%r",
            form_summary[:200],
        )

        payload = {
            "type": "approval_required",
            "action": "form_submit",
            "task_id": task.task_id,
            "summary": form_summary,
            "message": (
                f"Xyron wants to submit a form. Here's what will be sent:\n\n"
                f"{form_summary}\n\n"
                f"Reply 'approve' to proceed or 'cancel' to abort."
            ),
        }

        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn(payload)
            except Exception as exc:
                logger.warning("[BROWSER_WS_SEND_ERROR] error=%r", str(exc))

        # Mark task as waiting for approval so the runtime pauses
        task.metadata["waiting_approval"] = True
        task.metadata["approved"] = False

        # Always return False — submission requires explicit approval
        return False

    # ── Gated submission ───────────────────────────────────────────────────────

    async def submit_if_approved(self, page: Page, task: AgentTask) -> bool:
        """
        Submit the form ONLY if task.metadata.get('approved') is True.

        If not approved, logs the block and returns False.
        """
        if not task.metadata.get("approved", False):
            logger.warning(
                "[BROWSER_SUBMIT_BLOCKED] task=%s — approval not granted",
                task.task_id,
            )
            return False

        try:
            # Try common submit button selectors
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Submit')",
                "button:has-text('Send')",
                "button:has-text('Continue')",
                "button:has-text('Apply')",
                "[role='button']:has-text('Submit')",
            ]
            for sel in submit_selectors:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    logger.info(
                        "[BROWSER_ACTION_CLICK] selector=%r text=submit_approved",
                        sel,
                    )
                    await el.click(timeout=10_000)
                    await asyncio.sleep(1.0)
                    logger.info(
                        "[BROWSER_FORM_SUBMITTED] task=%s url=%s",
                        task.task_id,
                        page.url,
                    )
                    # Clear approval flag after use
                    task.metadata["approved"] = False
                    task.metadata["waiting_approval"] = False
                    return True

            logger.warning(
                "[BROWSER_SUBMIT_NO_BUTTON] url=%s — no submit button found",
                page.url,
            )
            return False
        except Exception as exc:
            logger.error("[BROWSER_SUBMIT_ERROR] error=%r", str(exc))
            return False
