"""
Browser Tools — Feature #9
Playwright-powered browser control: navigate, click, fill, read page.
Registered in the tool registry so voice commands can trigger them.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)


# ── Playwright lazy init ──────────────────────────────────────────────────────

_browser = None
_page    = None
_lock    = None


def _get_lock():
    global _lock
    if _lock is None:
        import asyncio, threading
        _lock = threading.Lock()
    return _lock


def _ensure_browser():
    """Start a persistent Chromium browser (reused across calls)."""
    global _browser, _page
    if _page is not None:
        try:
            _page.title()   # cheap liveness check
            return _page
        except Exception:
            _browser = None
            _page    = None
    from playwright.sync_api import sync_playwright
    _pw      = sync_playwright().__enter__()
    _browser = _pw.chromium.launch(headless=False, args=["--no-sandbox"])
    _page    = _browser.new_page()
    logger.info("Playwright browser launched")
    return _page


# ── Tools ─────────────────────────────────────────────────────────────────────

def _exec_browser_navigate(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Navigate the browser to a URL."""
    url = params.get("url", "")
    if not url:
        return ToolResult(success=False, text="URL required.", spoken="I need a URL to navigate to.")
    if not url.startswith("http"):
        url = "https://" + url
    try:
        page = _ensure_browser()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = page.title() or url
        return ToolResult(
            success=True, text=f"Navigated to: {title}",
            spoken=f"Done — I've opened {title}.",
            action_url=url,
        )
    except Exception as exc:
        logger.warning("browser_navigate failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I had trouble opening that page.")


def _exec_browser_click(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Click an element described by a CSS selector or text."""
    selector = params.get("selector", "")
    text     = params.get("text", "")
    try:
        page = _ensure_browser()
        if text:
            page.get_by_text(text, exact=False).first.click(timeout=8000)
            return ToolResult(success=True, text=f"Clicked '{text}'", spoken=f"Clicked '{text}'.")
        elif selector:
            page.click(selector, timeout=8000)
            return ToolResult(success=True, text=f"Clicked {selector}", spoken=f"Done, clicked it.")
        return ToolResult(success=False, text="Need selector or text.", spoken="What should I click?")
    except Exception as exc:
        logger.warning("browser_click failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I couldn't find that element to click.")


def _exec_browser_fill(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Fill a form field (by label, placeholder, or selector)."""
    selector = params.get("selector", "")
    label    = params.get("label", "")
    value    = params.get("value", "")
    if not value:
        return ToolResult(success=False, text="Value required.", spoken="What should I fill in?")
    try:
        page = _ensure_browser()
        if label:
            page.get_by_label(label).fill(value, timeout=8000)
        elif selector:
            page.fill(selector, value, timeout=8000)
        else:
            # Fallback: fill the focused/active input
            page.keyboard.type(value)
        return ToolResult(success=True, text=f"Filled '{value}'", spoken=f"I've filled in {value}.")
    except Exception as exc:
        logger.warning("browser_fill failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I had trouble filling that field.")


def _exec_browser_read(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Extract visible text from the current page."""
    selector = params.get("selector", "body")
    try:
        page  = _ensure_browser()
        title = page.title()
        text  = page.inner_text(selector or "body")
        # Trim for speech
        trimmed = " ".join(text.split())[:600]
        return ToolResult(
            success=True,
            text=f"Page: {title}\n\n{text}",
            spoken=f"The page says: {trimmed}",
        )
    except Exception as exc:
        logger.warning("browser_read failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I couldn't read that page.")


def _exec_browser_screenshot(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Take a screenshot of the current browser page."""
    try:
        import base64, tempfile
        page = _ensure_browser()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        page.screenshot(path=path, full_page=False)
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        import os; os.unlink(path)
        return ToolResult(success=True, text=f"data:image/png;base64,{b64}", spoken="Screenshot taken.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Screenshot failed.")


def _exec_browser_close(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Close the controlled browser."""
    global _browser, _page
    try:
        if _browser:
            _browser.close()
            _browser = None
            _page    = None
        return ToolResult(success=True, text="Browser closed.", spoken="Browser closed.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Trouble closing the browser.")


# ── Registration ──────────────────────────────────────────────────────────────

def _make_def(name: str, description: str, properties: dict, required: list | None = None) -> dict:
    d: dict = {"type": "object", "properties": properties}
    if required:
        d["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": d}}


def register_browser_tools() -> None:
    registry.register(
        name="browser_navigate",
        definition=_make_def(
            "browser_navigate",
            "Open a URL in the controlled Playwright browser. Use for 'go to X', 'open X in browser', 'navigate to X'.",
            {"url": {"type": "string", "description": "Full URL to navigate to"}},
            required=["url"],
        ),
        executor=_exec_browser_navigate,
        risk="low",
        category="browser",
    )
    registry.register(
        name="browser_click",
        definition=_make_def(
            "browser_click",
            "Click an element on the current browser page by visible text or CSS selector.",
            {
                "text":     {"type": "string", "description": "Visible text of element to click"},
                "selector": {"type": "string", "description": "CSS selector fallback"},
            },
        ),
        executor=_exec_browser_click,
        risk="low",
        category="browser",
    )
    registry.register(
        name="browser_fill",
        definition=_make_def(
            "browser_fill",
            "Fill a form field on the current browser page.",
            {
                "label":    {"type": "string", "description": "Form field label"},
                "selector": {"type": "string", "description": "CSS selector"},
                "value":    {"type": "string", "description": "Value to fill in"},
            },
            required=["value"],
        ),
        executor=_exec_browser_fill,
        risk="low",
        category="browser",
    )
    registry.register(
        name="browser_read",
        definition=_make_def(
            "browser_read",
            "Read the visible text from the current browser page.",
            {"selector": {"type": "string", "description": "CSS selector (default: body)"}},
        ),
        executor=_exec_browser_read,
        risk="low",
        category="browser",
    )
    registry.register(
        name="browser_screenshot",
        definition=_make_def("browser_screenshot", "Take a screenshot of the current browser page.", {}),
        executor=_exec_browser_screenshot,
        risk="low",
        category="browser",
    )
    registry.register(
        name="browser_close",
        definition=_make_def("browser_close", "Close the Playwright browser window.", {}),
        executor=_exec_browser_close,
        risk="low",
        category="browser",
    )


register_browser_tools()
