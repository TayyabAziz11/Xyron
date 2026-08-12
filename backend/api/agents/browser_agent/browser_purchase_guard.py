"""
BrowserPurchaseGuard — safety wrapper for all purchase and booking flows.

Any action in BLOCKED_ACTIONS is intercepted and routed through human
approval. The guard also has helpers to search flights without booking.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from playwright.async_api import Page

from api.agents.agent_types import AgentTask
from api.agents.browser_agent.browser_navigator import BrowserNavigator
from api.agents.browser_agent.browser_reader import BrowserReader

logger = logging.getLogger("api.agents.browser_agent.purchase_guard")


class BrowserPurchaseGuard:
    """Guards purchase / booking actions behind mandatory human approval."""

    BLOCKED_ACTIONS: frozenset[str] = frozenset(
        {
            "checkout",
            "confirm_booking",
            "place_order",
            "submit_payment",
            "book_now",
            "buy_now",
            "purchase",
            "pay_now",
            "complete_order",
            "confirm_purchase",
            "confirm_payment",
        }
    )

    # Selector hints for flight search inputs (Google Flights / Kayak / Skyscanner)
    _FLIGHT_PRICE_SELECTORS: list[str] = [
        "[class*='price']",
        "[class*='fare']",
        "[class*='amount']",
        "[data-price]",
        "[itemprop='price']",
    ]

    # ── Action gate ────────────────────────────────────────────────────────────

    async def check_action(
        self, action: str, details: dict, task: AgentTask
    ) -> bool:
        """
        Evaluate whether *action* is safe to execute autonomously.

        Returns True if the action is safe (caller may proceed).
        Returns False if the action requires human approval (caller must halt
        and wait for task.metadata['approved'] == True).
        """
        action_lower = action.lower().replace(" ", "_").replace("-", "_")

        if action_lower in self.BLOCKED_ACTIONS:
            logger.info(
                "[BROWSER_APPROVAL_REQUIRED] action=%r task=%s",
                action,
                task.task_id,
            )
            await self._send_approval_request(task, action, details)
            return False  # Blocked — wait for approval

        # Any action mentioning money / payment keywords is also blocked
        money_kw = re.compile(
            r"\b(pay|payment|card|credit|debit|billing|checkout|order|purchase|book)\b",
            re.IGNORECASE,
        )
        if money_kw.search(action):
            logger.info(
                "[BROWSER_APPROVAL_REQUIRED] action=%r (money keyword) task=%s",
                action,
                task.task_id,
            )
            await self._send_approval_request(task, action, details)
            return False

        return True  # Action is safe

    async def _send_approval_request(
        self, task: AgentTask, action: str, details: dict
    ) -> None:
        """Send approval payload via WebSocket and mark task as waiting."""
        detail_lines = "\n".join(f"  • {k}: {v}" for k, v in details.items())
        payload = {
            "type": "approval_required",
            "action": action,
            "task_id": task.task_id,
            "details": details,
            "message": (
                f"Xyron wants to perform: **{action}**\n\n"
                f"Details:\n{detail_lines}\n\n"
                f"Reply 'approve' to proceed or 'cancel' to abort."
            ),
        }
        task.metadata["waiting_approval"] = True
        task.metadata["approved"] = False

        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn(payload)
            except Exception as exc:
                logger.warning("[BROWSER_WS_SEND_ERROR] error=%r", str(exc))

    # ── Flight search (no booking) ─────────────────────────────────────────────

    async def find_flight_options(
        self,
        page: Page,
        navigator: BrowserNavigator,
        origin: str,
        destination: str,
        date: str,
    ) -> list[dict]:
        """
        Search for flights using Google Flights. Does NOT book anything.

        Returns: [{airline, price, departure, duration, url}]
        """
        query = f"flights from {origin} to {destination} on {date}"
        url = (
            "https://www.google.com/travel/flights?q="
            + query.replace(" ", "+")
        )
        logger.info(
            "[BROWSER_FLIGHT_SEARCH] origin=%r dest=%r date=%r",
            origin,
            destination,
            date,
        )

        ok = await navigator.go_to(url)
        if not ok:
            return []

        await navigator.handle_cookie_banner()
        await asyncio.sleep(1.5)  # Let the React flight grid render

        results: list[dict] = []
        try:
            flight_results = await self._scrape_google_flights(page)
            results = flight_results
        except Exception as exc:
            logger.warning("[BROWSER_FLIGHT_SCRAPE_ERROR] error=%r", str(exc))

        # Fallback: extract price + text from page body
        if not results:
            reader = BrowserReader()
            text = await reader.summarize_page(page, max_chars=1500)
            results = self._parse_flight_text(text, origin, destination, date)

        logger.info(
            "[BROWSER_FLIGHT_SEARCH_DONE] options_found=%d", len(results)
        )
        logger.info("[FLIGHT_RESULTS_FOUND] count=%d", len(results))
        return results

    async def _scrape_google_flights(self, page: Page) -> list[dict]:
        """Parse Google Flights result cards from the DOM."""
        return await page.evaluate(
            """
            () => {
                const cards = document.querySelectorAll('[data-gs], li[class*="pIav2d"], .yR1fYc');
                const results = [];
                cards.forEach(card => {
                    const text = card.innerText || '';
                    // Very rough extraction — GF obfuscates class names
                    const priceMatch = text.match(/[$€£¥₹][\\d,]+/);
                    const timeMatch = text.match(/\\d{1,2}:\\d{2}\\s?(AM|PM)/i);
                    const durMatch = text.match(/(\\d+)\\s?hr?\\s?(\\d+)?\\s?min?/i);
                    const airlineMatch = text.match(/^([A-Z][a-zA-Z ]+)\\n/);
                    if (priceMatch) {
                        results.push({
                            price: priceMatch[0],
                            departure: timeMatch ? timeMatch[0] : '',
                            duration: durMatch ? durMatch[0] : '',
                            airline: airlineMatch ? airlineMatch[1].trim() : 'Unknown',
                        });
                    }
                });
                return results.slice(0, 8);
            }
            """
        )

    def _parse_flight_text(
        self, text: str, origin: str, destination: str, date: str
    ) -> list[dict]:
        """Fallback: extract price patterns from plain page text."""
        price_re = re.compile(r"[$€£¥₹][\d,]+(?:\.\d{2})?")
        time_re = re.compile(r"\d{1,2}:\d{2}\s?(?:AM|PM)", re.IGNORECASE)
        dur_re = re.compile(r"\d+\s?hr?\s?\d*\s?min?", re.IGNORECASE)

        prices = price_re.findall(text)
        times = time_re.findall(text)
        durations = dur_re.findall(text)

        results: list[dict] = []
        for i, price in enumerate(prices[:8]):
            results.append(
                {
                    "airline": "See site",
                    "price": price,
                    "departure": times[i] if i < len(times) else "",
                    "duration": durations[i] if i < len(durations) else "",
                }
            )
        return results

    # ── Present options ────────────────────────────────────────────────────────

    async def present_options(self, options: list[dict], task: AgentTask) -> str:
        """
        Send flight / product options to the user via WebSocket.
        Returns a short spoken summary string.

        Search-only: never books or submits anything. If options were found,
        the summary ends with an explicit request for confirmation before
        proceeding any further (booking/payment is out of scope for this
        function and this agent never performs it).
        """
        if not options:
            spoken = "I could not find any options. Please check the site directly."
        else:
            top = options[:5]
            spoken = f"I found {len(options)} options. "
            cheapest = min(top, key=lambda o: self._parse_price(o.get("price", "$99999")))
            spoken += (
                f"The cheapest is {cheapest.get('airline', 'an airline')} "
                f"at {cheapest.get('price', 'unknown price')}"
            )
            if cheapest.get("departure"):
                spoken += f", departing at {cheapest['departure']}"
            spoken += "."

            # Compare cheapest vs. next-best option (e.g. earlier/better timing)
            others = [o for o in top if o is not cheapest]
            if others:
                alt = others[0]
                logger.info(
                    "[FLIGHT_OPTION_COMPARE] cheapest=%r alt=%r",
                    cheapest.get("price"), alt.get("price"),
                )
                if alt.get("departure") and cheapest.get("departure"):
                    compare_line = (
                        f" I found a cheaper option, but the timing is {cheapest.get('departure')}. "
                        f"This one is {alt.get('price', 'more expensive')}, but has a departure at "
                        f"{alt.get('departure')}."
                    )
                    spoken += compare_line
                    logger.info("[AGENT_NARRATION] step=flight.compare_options text=%r", compare_line.strip())

            spoken += " Before I continue to booking, I need your confirmation."

        payload = {
            "type": "options_found",
            "task_id": task.task_id,
            "options": options,
            "spoken_summary": spoken,
        }

        if task.ws_send_fn is not None:
            try:
                await task.ws_send_fn(payload)
            except Exception as exc:
                logger.warning("[BROWSER_WS_SEND_ERROR] error=%r", str(exc))

        if options:
            logger.info("[BROWSER_APPROVAL_REQUIRED] action=continue_to_booking task=%s", task.task_id)

        return spoken

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """Convert a price string like '$1,234' to a float."""
        cleaned = re.sub(r"[^0-9.]", "", price_str)
        try:
            return float(cleaned)
        except ValueError:
            return float("inf")
