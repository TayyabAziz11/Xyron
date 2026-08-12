"""
Xyron self-introduction demo — cinematic showcase for social media videos.

Structure: Claim → Demonstration → Result, for every step.
Each narration blocks until TTS playback is fully complete before any action fires.

Trigger phrases:
  - "introduce yourself"
  - "show me what you can do"
  - "showcase yourself"
  - "social media demo"
  - "demo yourself"
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ── Trigger detection ──────────────────────────────────────────────────────────
INTRO_DEMO_RE = re.compile(
    r'\b(?:'
    r'introduce\s+yourself(?:\s+again)?\b'
    r'|do\s+your\s+introduction(?:\s+again)?\b'
    r'|show\s+(?:me\s+)?what\s+you\s+can\s+do\b'
    r'|demo(?:nstrate)?\s+yourself\b'
    r'|do\s+your\s+demo\b'
    r'|showcase\s+yourself\b'
    r'|social\s+media\s+demo\b'
    r')\b',
    re.IGNORECASE,
)

# ── Pacing constants ───────────────────────────────────────────────────────────
_POST_ACTION_PAUSE  = 1.2   # after action completes, before confirmation narration
_POST_CONFIRM_PAUSE = 0.5   # after confirmation, before next claim
_BETWEEN_APPS_PAUSE = 0.8   # between apps in the multi-app sequence


async def run_intro_demo(
    tts_fn: Callable[[str], Awaitable[bool]],
    run_tool_fn: Callable[..., Awaitable[str]],
    memory,
    log: logging.Logger | None = None,
    demo_mode_setter: Optional[Callable[[bool], None]] = None,
) -> None:
    """
    Run the full self-introduction cinematic demo.

    tts_fn:           async (text) -> bool(interrupted). Must block until client
                      finishes playing — pass _tts_await_playback from voice_ws.py,
                      NOT _tts_with_fallback (which returns after transmission only).
    run_tool_fn:      async (tool_name, params, goal) -> str.
    memory:           MemoryManager instance.
    log:              optional logger override.
    demo_mode_setter: callback to set demo_mode flag (disables VAD during demo).
    """
    _log = log or logger
    _log.info("[INTRO_SELF_START] beginning showcase demo")

    if demo_mode_setter:
        demo_mode_setter(True)
        _log.info("[DEMO_MODE_ENABLED] VAD disabled during showcase")

    # ── Primitives ─────────────────────────────────────────────────────────────

    async def _speak(text: str) -> bool:
        """Speak text and block until client playback is complete. Returns True if interrupted."""
        _log.info("[INTRO_TTS] chars=%d text=%r", len(text), text[:60])
        return bool(await tts_fn(text))

    async def _act(tool: str, params: dict, label: str) -> bool:
        """Execute tool and return True on success."""
        _log.info("[INTRO_ACTION] step=%s tool=%s params=%s", label, tool, params)
        try:
            result = await run_tool_fn(tool, params, "intro_demo")
            _log.info("[INTRO_VERIFY] step=%s result=%r", label, (result or "")[:80])
            return True
        except Exception as exc:
            _log.warning("[INTRO_FAIL] step=%s tool=%s error=%s", label, tool, exc)
            return False

    # ── Core showcase primitive ────────────────────────────────────────────────

    async def run_showcase_step(
        narration: str,
        tool: str,
        params: dict,
        label: str,
        confirm_ok: str = "Done.",
        confirm_fail: str = "Couldn't do that. Moving on.",
        post_delay: float = _POST_ACTION_PAUSE,
    ) -> bool:
        """
        Guaranteed per-step sequence:
          1. Narrate  — blocks until full playback complete
          2. Execute action
          3. Pause so viewer sees the result
          4. Confirm  — blocks until full playback complete
          5. Brief pause before next step
          Returns True if interrupted (caller should stop the demo).
        """
        if await _speak(narration):
            return True
        ok = await _act(tool, params, label)
        await asyncio.sleep(post_delay)
        if await _speak(confirm_ok if ok else confirm_fail):
            return True
        await asyncio.sleep(_POST_CONFIRM_PAUSE)
        return False

    try:
        # ══════════════════════════════════════════════════════════════════════
        # OPENING — who Xyron is
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] opening")
        if await _speak(
            "Hey. I'm Xyron. An AI Operator. "
            "I don't just answer questions. "
            "I operate your computer."
        ):
            return
        await asyncio.sleep(0.8)

        # ══════════════════════════════════════════════════════════════════════
        # STEP 1 — Applications: VS Code
        # Claim → Demo → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_1_vscode")
        if await _speak("I work with applications. Let me prove it."):
            return
        if await run_showcase_step(
            narration    = "Launching Visual Studio Code.",
            tool         = "open_application",
            params       = {"app_name": "vscode"},
            label        = "1",
            confirm_ok   = "This is software development.",
            confirm_fail = "Couldn't launch that. Continuing.",
        ):
            return

        # ══════════════════════════════════════════════════════════════════════
        # STEP 2 — File management: create folder
        # Claim → Demo → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_2_folder")
        if await _speak("I manage files for you."):
            return
        if await run_showcase_step(
            narration    = "Creating a folder directly on this machine.",
            tool         = "create_folder",
            params       = {"name": "Xyron Demo", "path": "C:\\"},
            label        = "2",
            confirm_ok   = "Done.",
            confirm_fail = "Folder may already exist. Continuing.",
        ):
            return

        # ══════════════════════════════════════════════════════════════════════
        # STEP 3 — Software installation: Microsoft Store
        # Claim → Demo → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_3_store")
        if await _speak(
            "I install software. "
            "No searching. No clicking. "
            "Just tell me what you need."
        ):
            return
        if await run_showcase_step(
            narration    = "Opening the Microsoft Store. Installing Instagram now.",
            tool         = "install_store_app",
            params       = {"app_name": "Instagram"},
            label        = "3",
            confirm_ok   = "Application page is ready. Say the word and I'll install it.",
            confirm_fail = "Store didn't open. Moving on.",
            post_delay   = 1.5,
        ):
            return

        # ══════════════════════════════════════════════════════════════════════
        # STEP 4 — Research: open Chrome, run search
        # Claim → Demo (two actions) → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_4_research")
        if await _speak("I research information in real time. Watch this."):
            return

        if await _speak("Opening Chrome."):
            return
        chrome_ok = await _act("open_application", {"app_name": "chrome"}, "4a")
        await asyncio.sleep(_POST_ACTION_PAUSE)

        if chrome_ok:
            if await _speak("Searching for the latest AI trends."):
                return
            await _act("search_web_browser", {"query": "latest AI developments 2026"}, "4b")
            await asyncio.sleep(_POST_ACTION_PAUSE)
            if await _speak("Research complete."):
                return
        else:
            if await _speak("Chrome unavailable. Moving on."):
                return
        await asyncio.sleep(_POST_CONFIRM_PAUSE)

        # ══════════════════════════════════════════════════════════════════════
        # STEP 5 — Work organisation: notes file
        # Claim → Demo → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_5_notes")
        if await _speak("I organize your work."):
            return
        if await run_showcase_step(
            narration    = "Creating a notes file on this machine.",
            tool         = "write_file",
            params       = {
                "path":    "C:\\Xyron Demo\\notes.txt",
                "content": (
                    "Generated by Xyron - Your AI Operator\n\n"
                    "Task: Demo completed successfully.\n"
                    "Timestamp: " + time.strftime("%Y-%m-%d %H:%M:%S")
                ),
            },
            label        = "5",
            confirm_ok   = "Done.",
            confirm_fail = "Couldn't write the file. Continuing.",
        ):
            return

        # ══════════════════════════════════════════════════════════════════════
        # STEP 6 — Automation: three apps, one command
        # Claim → Demo (three actions) → Result
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] step_6_automation")
        if await _speak("And I can automate repetitive tasks. Watch."):
            return

        if await _speak("Opening Calculator."):
            return
        await _act("open_application", {"app_name": "calculator"}, "6a")
        await asyncio.sleep(_BETWEEN_APPS_PAUSE)

        if await _speak("Opening Notepad."):
            return
        await _act("open_application", {"app_name": "notepad"}, "6b")
        await asyncio.sleep(_BETWEEN_APPS_PAUSE)

        if await _speak("Opening File Explorer."):
            return
        await _act("open_application", {"app_name": "explorer"}, "6c")
        await asyncio.sleep(_POST_ACTION_PAUSE)

        if await _speak("Three applications. One command."):
            return
        await asyncio.sleep(_POST_CONFIRM_PAUSE)

        # ══════════════════════════════════════════════════════════════════════
        # CLOSING — identity statement
        # ══════════════════════════════════════════════════════════════════════
        _log.info("[INTRO_PHASE] closing")
        if await _speak(
            "And this is only the beginning. "
            "I'm Xyron. "
            "Your AI Operator."
        ):
            return

        try:
            from api.services.memory_service import memory_service as _ms
            _ts = time.strftime("%Y-%m-%d %H:%M:%S")
            _ms.set_fact("last_demo_run", "introduce_self")
            _ms.set_fact("last_demo_time", _ts)
            _log.info("[INTRO_SELF_COMPLETE] last_demo_time=%s", _ts)
        except Exception as exc:
            _log.warning("[INTRO_SELF_COMPLETE] memory save failed: %s", exc)
            _log.info("[INTRO_SELF_COMPLETE]")

    finally:
        if demo_mode_setter:
            demo_mode_setter(False)
            _log.info("[DEMO_MODE_DISABLED] VAD re-enabled after showcase")
