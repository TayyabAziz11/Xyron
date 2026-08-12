from __future__ import annotations

"""
PersonalityEngine — Xyron's voice and response personality system.

Manages mode switching, response polishing, and micro-reactions.
Exposes a module-level singleton `personality_engine` that other modules
can import directly:

    from api.agents.personality.personality_engine import personality_engine

Phase 3.4 agent entry point: run()

Log tags: [PERSONALITY_MODE_SET] [RESPONSE_POLISHED]
          [MICRO_REACTION_INSERTED] (emitted inside MicroReactionEngine)
          [HUMOR_GUARD_BLOCKED]    (emitted inside HumorGuard)
"""

import asyncio
import logging
import random
import re
import time
from enum import Enum
from typing import Any, Optional

from api.agents.agent_types import AgentStatus, AgentTask

logger = logging.getLogger("api.agents.personality.engine")


# ── Mode enum ─────────────────────────────────────────────────────────────────


class PersonalityMode(str, Enum):
    DEFAULT      = "default"
    PROFESSIONAL = "professional"
    FRIENDLY     = "friendly"
    JARVIS       = "jarvis"
    MINIMAL      = "minimal"
    FUNNY        = "funny"
    DEVELOPER    = "developer"
    CREATIVE     = "creative"
    RESEARCH     = "research"


# ── Voice-command → mode patterns ─────────────────────────────────────────────

_MODE_PATTERNS: list[tuple[re.Pattern, PersonalityMode]] = [
    (re.compile(r"\b(switch\s+to\s+jarvis|jarvis\s+mode)\b",          re.I), PersonalityMode.JARVIS),
    (re.compile(r"\b(switch\s+to\s+professional|professional\s+mode)\b", re.I), PersonalityMode.PROFESSIONAL),
    (re.compile(r"\b(switch\s+to\s+friendly|friendly\s+mode)\b",       re.I), PersonalityMode.FRIENDLY),
    (re.compile(r"\b(minimal\s+mode|switch\s+to\s+minimal)\b",         re.I), PersonalityMode.MINIMAL),
    (re.compile(r"\b(funny\s+mode|make\s+it\s+funny)\b",               re.I), PersonalityMode.FUNNY),
    (re.compile(r"\b(dev(?:eloper)?\s+mode)\b",                        re.I), PersonalityMode.DEVELOPER),
    (re.compile(r"\b(creative\s+mode)\b",                              re.I), PersonalityMode.CREATIVE),
    (re.compile(r"\b(research\s+mode)\b",                              re.I), PersonalityMode.RESEARCH),
    (re.compile(r"\b(default\s+mode|normal\s+mode|xyron\s+mode|reset\s+mode)\b", re.I), PersonalityMode.DEFAULT),
]


# ── Confirmation messages ─────────────────────────────────────────────────────

_CONFIRMATION: dict[PersonalityMode, str] = {
    PersonalityMode.JARVIS:       "Switching to Jarvis mode. How may I assist you, Sir?",
    PersonalityMode.PROFESSIONAL: "Professional mode activated. Ready to assist.",
    PersonalityMode.FRIENDLY:     "Sure thing! I'm in friendly mode now — let's get things done!",
    PersonalityMode.MINIMAL:      "Minimal mode.",
    PersonalityMode.FUNNY:        "Haha, switching to funny mode! Don't blame me for what comes next.",
    PersonalityMode.DEVELOPER:    "Developer mode on. I'll be verbose about the technical details.",
    PersonalityMode.CREATIVE:     "Creative mode engaged. Let's make something interesting.",
    PersonalityMode.RESEARCH:     "Research mode active. I'll be thorough and cite sources.",
    PersonalityMode.DEFAULT:      "Back to default Xyron mode. I'm ready.",
}


# ── LLM system addenda ────────────────────────────────────────────────────────

_SYSTEM_ADDENDA: dict[PersonalityMode, str] = {
    PersonalityMode.DEFAULT: (
        "You are Xyron, a warm and helpful AI assistant. "
        "Be concise but friendly. Keep voice responses under 150 characters."
    ),
    PersonalityMode.PROFESSIONAL: (
        "Respond formally and efficiently. Omit filler phrases. "
        "Be precise and action-oriented. No enthusiasm markers."
    ),
    PersonalityMode.FRIENDLY: (
        "Be enthusiastic, warm, and encouraging. "
        "Use casual conversational language. Show genuine interest."
    ),
    PersonalityMode.JARVIS: (
        "You are JARVIS — formal, precise, and deferential. "
        "Address the user as 'Sir'. Remain concise and highly competent. "
        "Never use casual language."
    ),
    PersonalityMode.MINIMAL: (
        "Respond with the absolute minimum words needed. "
        "No greetings, no filler, no preamble. Facts only."
    ),
    PersonalityMode.FUNNY: (
        "Be lightly humorous and playful. "
        "Brief, tasteful jokes are welcome. Stay helpful. Never offensive."
    ),
    PersonalityMode.DEVELOPER: (
        "Be technical and explicit. Narrate what you are doing step by step. "
        "Include relevant technical details, process names, and exit codes."
    ),
    PersonalityMode.CREATIVE: (
        "Be imaginative and descriptive. Use vivid language. "
        "Draw metaphors when explaining. Think outside the box."
    ),
    PersonalityMode.RESEARCH: (
        "Be thorough, structured, and academic. "
        "Cite sources when possible. Use numbered lists and clear sections."
    ),
}


# ── Main engine ───────────────────────────────────────────────────────────────


class PersonalityEngine:
    """Xyron personality mode manager — thread-safe for asyncio usage."""

    def __init__(self) -> None:
        self._mode: PersonalityMode = PersonalityMode.DEFAULT
        self._user_name: str = "Tayyab"

        # Sub-components lazy-loaded to avoid circular imports at module init
        self._polisher = None
        self._micro = None
        self._humor = None

        # Last variant spoken per (step, mode) — lets narrate_step() avoid
        # repeating the exact same line twice in a row when a step's
        # template is a list of natural variants instead of one fixed string.
        self._last_variant: dict[str, str] = {}

    # ── Lazy sub-component accessors ──────────────────────────────────────────

    def _get_polisher(self):
        if self._polisher is None:
            from api.agents.personality.response_polisher import ResponsePolisher
            self._polisher = ResponsePolisher()
        return self._polisher

    def _get_micro(self):
        if self._micro is None:
            from api.agents.personality.micro_reaction_engine import MicroReactionEngine
            self._micro = MicroReactionEngine()
        return self._micro

    def _get_humor(self):
        if self._humor is None:
            from api.agents.personality.humor_guard import HumorGuard
            self._humor = HumorGuard()
        return self._humor

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def mode(self) -> PersonalityMode:
        return self._mode

    @property
    def user_name(self) -> str:
        return self._user_name

    @user_name.setter
    def user_name(self, name: str) -> None:
        self._user_name = name

    # ── Mode management ───────────────────────────────────────────────────────

    def set_mode(self, mode: PersonalityMode) -> str:
        """
        Switch to *mode*.
        Returns the confirmation message in the NEW mode's style.
        """
        self._mode = mode
        logger.info("[PERSONALITY_MODE_SET] mode=%s", mode.value)
        return _CONFIRMATION.get(mode, "Mode updated.")

    def get_mode_from_voice(self, transcript: str) -> Optional[PersonalityMode]:
        """
        Detect a mode-switch intent in a voice transcript.
        Returns the matched PersonalityMode, or None if no match.
        """
        for pattern, mode in _MODE_PATTERNS:
            if pattern.search(transcript):
                return mode
        return None

    # ── Response polishing ────────────────────────────────────────────────────

    def polish_response(
        self,
        raw_response: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        Apply personality mode transforms to a raw response string.
        Must be instant (< 2 ms) — no I/O, no LLM calls, no awaits.

        *context* may contain:
          "action"  — what action is being performed (used by HumorGuard)
          "event"   — event type (success / error / thinking)
        """
        if not raw_response:
            return raw_response

        if context is None:
            context = {}

        chars_before = len(raw_response)

        # Humor guard for FUNNY mode
        if self._mode == PersonalityMode.FUNNY:
            humor_guard = self._get_humor()
            ctx_str = f"{context.get('action', '')} {context.get('event', '')}"
            result = humor_guard.sanitize(raw_response, ctx_str, self._mode.value)
        else:
            result = raw_response

        # Apply polish pipeline
        polisher = self._get_polisher()
        result = polisher.polish(result, self._mode.value)

        chars_after = len(result)
        logger.debug(
            "[RESPONSE_POLISHED] mode=%s chars_before=%d chars_after=%d",
            self._mode.value,
            chars_before,
            chars_after,
        )

        return result

    def get_system_addendum(self) -> str:
        """
        Return personality instructions to append to LLM system prompts.
        Safe to call at any time — no side effects.
        """
        return _SYSTEM_ADDENDA.get(self._mode, _SYSTEM_ADDENDA[PersonalityMode.DEFAULT])

    # ── Micro reactions ───────────────────────────────────────────────────────

    def get_reaction(self, event: str) -> str:
        """
        Return a text micro-reaction prefix for *event* (e.g. "success", "error").
        Returns "" if the current mode suppresses reactions for this event.
        """
        micro = self._get_micro()
        if micro.should_insert(event, self._mode.value):
            return micro.get_text_reaction(event, self._mode.value)
        return ""

    def narrate_step(
        self,
        step: str,
        context: "dict[str, Any] | None" = None,
    ) -> str:
        """Return a mode-appropriate narration for a well-known agent step.

        step:    A dotted key like ``"coding.creating_folder"`` or ``"browser.searching"``.
        context: Optional interpolation variables (project_name, url, port, …).

        Falls back to the raw step key if the step is unknown.
        """
        if context is None:
            context = {}

        step_map = _NARRATION_STEPS.get(step, {})
        template = step_map.get(self._mode.value) or step_map.get("default", step)

        # A step's template may be a single fixed string, or a list of
        # natural-sounding variants — pick one that isn't the same as the
        # last one spoken for this (step, mode), so routine narration
        # ("opening the browser", "searching now"...) doesn't read the
        # identical line back every single turn.
        if isinstance(template, list):
            variant_key = f"{step}:{self._mode.value}"
            last = self._last_variant.get(variant_key)
            choices = [t for t in template if t != last] or template
            template = random.choice(choices)
            self._last_variant[variant_key] = template

        try:
            text = template.format(**context)
            logger.info("[AGENT_NARRATION] step=%s mode=%s text=%r", step, self._mode.value, text[:100])
            return text
        except (KeyError, IndexError):
            return template


# ── Step narration templates ──────────────────────────────────────────────────

_NARRATION_STEPS: dict[str, dict[str, "str | list[str]"]] = {
    "coding.parsing": {
        "default":      "Let me understand what you're looking to build.",
        "jarvis":       "Parsing your project requirements, Sir.",
        "professional": "Analyzing project requirements.",
        "minimal":      "Parsing goal.",
        "friendly":     "Let me figure out exactly what you want to build!",
        "developer":    "[PARSE] Extracting goal tokens and feature keywords.",
        "creative":     "Reading between the lines of your creative vision.",
        "research":     "Decomposing the project goal into structured requirements.",
        "funny":        "Let me put my thinking cap on... beep boop!",
    },
    "coding.stack": {
        "default":      "Selecting the best technology stack for your project.",
        "jarvis":       "Selecting optimal technology stack, Sir.",
        "professional": "Stack selection in progress.",
        "minimal":      "Selecting stack.",
        "friendly":     "Picking the perfect tech stack for you!",
        "developer":    "StackSelector: evaluating frameworks by project type and feature set.",
        "creative":     "Choosing the right canvas for your project.",
        "research":     "Evaluating technology stacks against project requirements.",
        "funny":        "Time to pick tech. I'll try not to choose Fortran.",
    },
    "coding.researching": {
        "default":      "Researching current design trends for your project type.",
        "jarvis":       "Consulting current design intelligence, Sir.",
        "professional": "Gathering design intelligence.",
        "minimal":      "Researching trends.",
        "friendly":     "Checking what's trending in design right now!",
        "developer":    "DesignResearcher: querying LLM for current UX/UI conventions.",
        "creative":     "Drawing inspiration from the latest design world.",
        "research":     "Systematically reviewing contemporary design trends and best practices.",
        "funny":        "Browsing Dribbble in my head... I mean, researching trends.",
    },
    "coding.planning": {
        "default":      "Generating a detailed project structure and file plan.",
        "jarvis":       "Generating project blueprint, Sir.",
        "professional": "Generating project plan.",
        "minimal":      "Planning project.",
        "friendly":     "Building out the project blueprint — this is the exciting part!",
        "developer":    "[PLAN] Generating AST-aware file tree with dependencies.",
        "creative":     "Sketching the architecture of your vision.",
        "research":     "Constructing a systematic, structured project plan.",
        "funny":        "Drawing the blueprint. Not on a napkin, I promise.",
    },
    "coding.creating_folder": {
        "default":      "Creating a dedicated workspace for your project on the Desktop.",
        "jarvis":       "Creating project directory on your Desktop, Sir.",
        "professional": "Creating project directory.",
        "minimal":      "Creating folder.",
        "friendly":     "Setting up a fresh workspace on your Desktop — you'll see it appear!",
        "developer":    "mkdir C:\\Users\\Dell\\Desktop\\Xyron Projects\\{project_name}",
        "creative":     "Carving out a dedicated space for your new creation.",
        "research":     "Provisioning isolated project directory on Desktop.",
        "funny":        "Making a folder. Not exactly rocket science, but still important.",
    },
    "coding.opening_vscode": {
        "default":      "Opening Visual Studio Code so you can follow along.",
        "jarvis":       "Launching Visual Studio Code, Sir.",
        "professional": "Launching VS Code.",
        "minimal":      "Opening VS Code.",
        "friendly":     "Opening VS Code — watch it appear on your screen!",
        "developer":    "code.exe {windows_path} &disown",
        "creative":     "Opening your creative studio. VS Code incoming.",
        "research":     "Initializing development environment in VS Code.",
        "funny":        "Opening VS Code. Try not to get lost in the extensions marketplace.",
    },
    "coding.writing_files": {
        "default":      "Generating your project files — writing real code, not templates.",
        "jarvis":       "Generating project source files, Sir.",
        "professional": "Generating source files.",
        "minimal":      "Writing files.",
        "friendly":     "Writing all the code for you — this is the real thing!",
        "developer":    "[GEN] Writing {count} source files across pages, components, and config.",
        "creative":     "Painting your project into existence, file by file.",
        "research":     "Systematically generating all required source files.",
        "funny":        "Writing the code. I promise it's better than my poetry.",
    },
    "coding.installing": {
        "default":      "Installing the required packages. This will take a moment.",
        "jarvis":       "Installing dependencies, Sir. Please stand by.",
        "professional": "Installing dependencies.",
        "minimal":      "npm install.",
        "friendly":     "Installing packages — almost there!",
        "developer":    "npm install --save  [resolving dependency tree]",
        "creative":     "Gathering the ingredients for your project.",
        "research":     "Installing and resolving package dependency graph.",
        "funny":        "npm install... time for a coffee. ☕ No seriously, this takes a bit.",
    },
    "coding.starting_server": {
        "default":      "Starting the development server.",
        "jarvis":       "Initiating development server, Sir.",
        "professional": "Starting dev server.",
        "minimal":      "Starting server.",
        "friendly":     "Firing up the dev server!",
        "developer":    "npm run dev — binding to port {port}",
        "creative":     "Bringing your project to life on localhost.",
        "research":     "Launching development server for functional verification.",
        "funny":        "Server starting up... doing its morning stretches.",
    },
    "coding.opening_browser": {
        "default":      "Opening the browser to show you the result.",
        "jarvis":       "Opening browser preview, Sir.",
        "professional": "Launching browser preview.",
        "minimal":      "Opening browser.",
        "friendly":     "Opening the browser — take a look at what we built!",
        "developer":    "Start-Process '{url}'",
        "creative":     "Revealing your creation to the world.",
        "research":     "Initiating browser-based functional verification.",
        "funny":        "Browser go brrr. Opening in 3... 2... 1...",
    },
    "coding.verifying": {
        "default":      "Verifying everything works correctly.",
        "jarvis":       "Running final verification, Sir.",
        "professional": "Verifying project integrity.",
        "minimal":      "Verifying.",
        "friendly":     "Double-checking everything looks perfect!",
        "developer":    "[VERIFY] HTTP GET {url} — checking status and content.",
        "creative":     "Reviewing your masterpiece before the grand reveal.",
        "research":     "Conducting systematic functional verification.",
        "funny":        "Checking for bugs. Found zero. I'm suspicious.",
    },
    "coding.fixing": {
        "default":      "Found some issues — fixing them automatically.",
        "jarvis":       "Errors detected. Applying automatic corrections, Sir.",
        "professional": "Auto-correcting build errors.",
        "minimal":      "Fixing errors.",
        "friendly":     "Spotted a couple of issues — fixing them for you right now!",
        "developer":    "[AUTO_FIX] Patching {file} — attempt {attempt}/{max}",
        "creative":     "Refining the rough edges of your creation.",
        "research":     "Systematic error correction: analyzing root cause and applying fix.",
        "funny":        "There are bugs. Shocking. Absolutely shocking. Fixing now.",
    },
    "coding.complete": {
        "default":      "The website is running successfully. Your project is ready.",
        "jarvis":       "Project complete, Sir. The website is live and verified.",
        "professional": "Project build complete. Server running. Verification passed.",
        "minimal":      "Done. Live at {url}.",
        "friendly":     "Your website is live and looking great! Go check it out.",
        "developer":    "[DONE] Exit 0. Dev server at {url}. Project at {path}.",
        "creative":     "Your creation is alive — go take a look.",
        "research":     "Project successfully generated, built, and verified.",
        "funny":        "It works! I'm as surprised as you are. Go look!",
    },
    "browser.opening": {
        "default":      ["Let me pull that up.", "Checking that now.", "I've got it.", "Opening the browser now."],
        "jarvis":       "Launching browser, Sir.",
        "professional": "Launching browser.",
        "minimal":      "Opening browser.",
        "friendly":     "Opening the browser — let's explore the web!",
        "developer":    "chromium.launch(headless=False, viewport=1280x900)",
        "creative":     "Raising the curtain on the internet.",
        "research":     "Initializing web browser for research session.",
        "funny":        "Opening browser. Bracing myself for cookie banners.",
    },
    "browser.searching": {
        "default":      ["Searching for the best options.", "Let me check what's out there.", "Looking that up now."],
        "jarvis":       "Executing web search, Sir.",
        "professional": "Executing search query.",
        "minimal":      "Searching.",
        "friendly":     "Searching the web for you!",
        "developer":    "GET https://google.com/search?q={query}",
        "creative":     "Casting a wide net across the web.",
        "research":     "Executing structured web search across authoritative sources.",
        "funny":        "Googling it. Just like you'd do, but I read faster.",
    },
    "browser.reading": {
        "default":      ["Taking a look at the results.", "Going through what came back.", "Reading through this now."],
        "jarvis":       "Extracting intelligence from the page, Sir.",
        "professional": "Extracting page content.",
        "minimal":      "Reading page.",
        "friendly":     "Reading through the results — this is really interesting!",
        "developer":    "page.evaluate(): extracting visible text nodes",
        "creative":     "Absorbing the essence of the page.",
        "research":     "Systematically extracting and analyzing page content.",
        "funny":        "Reading... I don't skim. Unlike some people.",
    },
    "browser.comparing": {
        "default":      ["Comparing your options.", "Weighing these against each other.", "Let me see which one's best."],
        "jarvis":       "Running comparative analysis, Sir.",
        "professional": "Executing comparative analysis.",
        "minimal":      "Comparing.",
        "friendly":     "Comparing everything side by side!",
        "developer":    "[COMPARE] Cross-referencing {n} sources",
        "creative":     "Laying the options side by side for a clear view.",
        "research":     "Conducting systematic comparative analysis across sources.",
        "funny":        "Comparing... like a fast person with way too many tabs open.",
    },
    "automation.analyzing": {
        "default":      "I'm analysing your system. This will only take a moment.",
        "jarvis":       "Conducting full system analysis, Sir.",
        "professional": "Executing system analysis.",
        "minimal":      "Analysing.",
        "friendly":     "Looking at what's going on under the hood!",
        "developer":    "[SYS] Scanning filesystem, processes, memory, and disk.",
        "creative":     "Taking a deep look inside your machine.",
        "research":     "Performing systematic diagnostic analysis.",
        "funny":        "Analysing your system. Not as messy as I expected.",
    },
    "automation.found_junk": {
        "default":      "I've found {size} of files that can be safely cleaned up.",
        "jarvis":       "Analysis complete. Found {size} of recoverable space, Sir.",
        "professional": "Analysis complete. {size} recoverable.",
        "minimal":      "Found {size}.",
        "friendly":     "Found {size} of clutter — let's clean it up!",
        "developer":    "[SCAN] {count} items found, total_size={size}",
        "creative":     "Discovered {size} hiding in the shadows of your system.",
        "research":     "Quantified {size} of recoverable storage across {count} items.",
        "funny":        "Found {size} of stuff your PC doesn't need. Classic hoarder behaviour.",
    },
    "coordinator.planning": {
        "default":      "Let me look into that.",
        "jarvis":       "Formulating multi-step execution plan, Sir.",
        "professional": "Generating execution plan.",
        "minimal":      "Planning.",
        "friendly":     "Figuring out the best way to tackle this!",
        "developer":    "[COORD] Building delegation graph for: {goal}",
        "creative":     "Mapping out the journey ahead.",
        "research":     "Constructing structured multi-agent execution plan.",
        "funny":        "Making a plan. A good one. Probably.",
    },
    "coordinator.delegating": {
        "default":      "Give me a moment.",
        "jarvis":       "Dispatching to {agent} agent, Sir.",
        "professional": "Delegating to {agent}.",
        "minimal":      "{agent}.",
        "friendly":     "Passing this to the {agent} — they've totally got this!",
        "developer":    "[DELEGATE] agent={agent} node={node_id}",
        "creative":     "Passing the baton to {agent}.",
        "research":     "Delegating subtask to specialized {agent} agent.",
        "funny":        "Sending {agent} to do the heavy lifting. Teamwork!",
    },
    "coordinator.done_step": {
        "default":      "Finished: {step}. Moving on.",
        "jarvis":       "{step} complete, Sir. Proceeding.",
        "professional": "{step}: done.",
        "minimal":      "{step} done.",
        "friendly":     "{step} is done! Moving to the next step.",
        "developer":    "[NODE_DONE] {step}",
        "creative":     "{step} complete. Onwards.",
        "research":     "Phase complete: {step}. Initiating next phase.",
        "funny":        "{step} done! Still going strong.",
    },
    "coordinator.complete": {
        "default":      "All done! Everything completed successfully.",
        "jarvis":       "All tasks complete, Sir.",
        "professional": "Workflow complete.",
        "minimal":      "Done.",
        "friendly":     "All finished! Hope that's exactly what you needed!",
        "developer":    "[COORD_DONE] {n_nodes} nodes completed in {duration_s}s.",
        "creative":     "The symphony is complete.",
        "research":     "All workflow nodes executed and verified.",
        "funny":        "Done! High five! ...I can't high five, but the sentiment's real.",
    },
}


# ── Singleton ─────────────────────────────────────────────────────────────────

personality_engine = PersonalityEngine()
"""
Module-level singleton.  Import with:
    from api.agents.personality.personality_engine import personality_engine
"""


# ── Agent entry point ─────────────────────────────────────────────────────────


async def run(
    task: AgentTask,
    runtime: Any,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
) -> str:
    """
    Agent runtime entry point for personality mode switching.

    Reads task.metadata["mode"] (a PersonalityMode value string) or falls
    back to detecting mode from the goal text via get_mode_from_voice().
    """
    task.status = AgentStatus.RUNNING
    task.started_at = time.time()

    # 1. Try metadata["mode"] first
    mode_name: str = (task.metadata or {}).get("mode", "")
    mode: PersonalityMode | None = None

    if mode_name:
        try:
            mode = PersonalityMode(mode_name.lower())
        except ValueError:
            logger.warning(
                "[PERSONALITY_ENGINE] unknown mode value %r — falling back to voice detection",
                mode_name,
            )

    # 2. Try voice detection on the goal string
    if mode is None:
        mode = personality_engine.get_mode_from_voice(task.goal)

    # 3. Default fallback
    if mode is None:
        mode = PersonalityMode.DEFAULT

    confirmation = personality_engine.set_mode(mode)

    # Notify via WebSocket if connected
    if task.ws_send_fn is not None:
        try:
            await task.ws_send_fn({
                "type": "personality_mode_changed",
                "mode": mode.value,
                "message": confirmation,
            })
        except Exception as exc:
            logger.debug("[PERSONALITY_ENGINE] ws_send_fn error: %r", exc)

    task.status = AgentStatus.COMPLETED
    task.result_summary = confirmation
    task.completed_at = time.time()

    return confirmation
