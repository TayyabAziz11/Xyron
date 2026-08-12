"""
Browser Tools — Feature #9
Playwright-powered browser control: navigate, click, fill, read page.
Registered in the tool registry so voice commands can trigger them.

These executors reuse browser_workspace (api/agents/browser_agent/browser_workspace.py)
— the SAME persistent, CDP-controlled, real-Chrome browser every other
browser-aware module in this codebase already shares (flight agent,
screen_context_agent, perception's browser_perception.py). This module used
to launch its own separate, throwaway Playwright-managed Chromium instance
(`sync_playwright().chromium.launch(headless=False)`) — a second, disconnected
"browser" that voice commands like "click X" would silently act on while the
user's real interactions went through browser_workspace's Chrome. That was an
accidental duplicate browser pipeline, not an intentional second system —
fixed by routing everything here through browser_workspace instead.

Threading note: browser_workspace's Page is an async-API Playwright object
bound to the app's main event loop (see main_loop.py's docstring for why).
These tool executors run synchronously in a worker thread (registry.execute()
is called via asyncio.to_thread from voice_ws.py), so every operation is
bridged onto the main loop via main_loop.run_coro_from_thread() rather than
awaited directly.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .registry import ToolResult, registry

logger = logging.getLogger(__name__)


def _get_page():
    """Bridge onto the main loop to fetch/create browser_workspace's page.
    Raises CDPUnavailableError (from browser_workspace) if Chrome control
    cannot be established — callers catch this and return a clear failure."""
    from api.agents.browser_agent.browser_workspace import browser_workspace
    from api.services.main_loop import run_coro_from_thread
    return run_coro_from_thread(browser_workspace.get_or_create_page(), timeout=30.0)


# ── Tools ─────────────────────────────────────────────────────────────────────

def _exec_browser_navigate(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Navigate the browser to a URL."""
    from api.services.main_loop import run_coro_from_thread

    url = params.get("url", "")
    if not url:
        return ToolResult(success=False, text="URL required.", spoken="I need a URL to navigate to.")
    if not url.startswith("http"):
        url = "https://" + url
    try:
        page = _get_page()

        async def _goto():
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return await page.title()

        title = run_coro_from_thread(_goto(), timeout=20.0) or url
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
    from api.services.main_loop import run_coro_from_thread

    selector = params.get("selector", "")
    text     = params.get("text", "")
    if not text and not selector:
        return ToolResult(success=False, text="Need selector or text.", spoken="What should I click?")
    try:
        page = _get_page()

        async def _click():
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
            else:
                await page.click(selector, timeout=8000)

        run_coro_from_thread(_click(), timeout=12.0)
        clicked = text or selector
        return ToolResult(success=True, text=f"Clicked '{clicked}'", spoken=f"Clicked '{clicked}'.")
    except Exception as exc:
        logger.warning("browser_click failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I couldn't find that element to click.")


def _exec_browser_fill(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Fill a form field (by label, placeholder, or selector)."""
    from api.services.main_loop import run_coro_from_thread

    selector = params.get("selector", "")
    label    = params.get("label", "")
    value    = params.get("value", "")
    if not value:
        return ToolResult(success=False, text="Value required.", spoken="What should I fill in?")
    try:
        page = _get_page()

        async def _fill():
            if label:
                await page.get_by_label(label).fill(value, timeout=8000)
            elif selector:
                await page.fill(selector, value, timeout=8000)
            else:
                await page.keyboard.type(value)

        run_coro_from_thread(_fill(), timeout=12.0)
        return ToolResult(success=True, text=f"Filled '{value}'", spoken=f"I've filled in {value}.")
    except Exception as exc:
        logger.warning("browser_fill failed: %s", exc)
        return ToolResult(success=False, text=str(exc), spoken="I had trouble filling that field.")


def _exec_browser_read(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Extract visible text from the current page."""
    from api.services.main_loop import run_coro_from_thread

    selector = params.get("selector", "body")
    try:
        page = _get_page()

        async def _read():
            title = await page.title()
            text = await page.inner_text(selector or "body")
            return title, text

        title, text = run_coro_from_thread(_read(), timeout=12.0)
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
    from api.services.main_loop import run_coro_from_thread

    try:
        import base64, tempfile, os
        page = _get_page()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name

        async def _shot():
            await page.screenshot(path=path, full_page=False)

        run_coro_from_thread(_shot(), timeout=12.0)
        b64 = base64.b64encode(open(path, "rb").read()).decode()
        os.unlink(path)
        return ToolResult(success=True, text=f"data:image/png;base64,{b64}", spoken="Screenshot taken.")
    except Exception as exc:
        return ToolResult(success=False, text=str(exc), spoken="Screenshot failed.")


def _exec_browser_close(params: Dict[str, Any], ctx: Dict[str, Any]) -> ToolResult:
    """Close the controlled browser (drops the CDP connection — never
    terminates the user's own Chrome process, see BrowserWorkspace.close())."""
    from api.services.main_loop import run_coro_from_thread

    try:
        from api.agents.browser_agent.browser_workspace import browser_workspace
        run_coro_from_thread(browser_workspace.close(), timeout=10.0)
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
