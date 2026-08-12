"""
Automation workflow engine — loads JSON workflow definitions and executes multi-step sequences.

Workflows live in backend/workflows/*.json.
Each step dispatches to the tool registry (browser_*, desktop_*, etc.).
Steps are retried once on failure before the workflow aborts.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "workflows"


class AutomationWorkflowService:
    def __init__(self) -> None:
        self._workflows: Dict[str, dict] = {}
        self._load_all()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        if not WORKFLOWS_DIR.exists():
            logger.info("Workflows dir not found: %s", WORKFLOWS_DIR)
            return
        loaded = 0
        for f in sorted(WORKFLOWS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                name = data.get("name") or f.stem
                self._workflows[name] = data
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load workflow %s: %s", f.name, exc)
        logger.info("Loaded %d automation workflows", loaded)

    def reload(self) -> None:
        self._workflows.clear()
        self._load_all()

    def list_workflows(self) -> List[str]:
        return list(self._workflows.keys())

    # ── Trigger matching ──────────────────────────────────────────────────────

    def match_trigger(self, text: str) -> Optional[Tuple[dict, dict]]:
        """Return (workflow, extracted_vars) if text matches any trigger, else None."""
        lower = text.lower().strip()
        best_match: Optional[Tuple[dict, dict]] = None
        best_score = 0

        for workflow in self._workflows.values():
            for trigger in workflow.get("triggers", []):
                score, matched = self._score_trigger(lower, trigger.lower())
                if matched and score > best_score:
                    best_score = score
                    vars_ = self._extract_variables(lower, trigger.lower(), workflow)
                    best_match = (workflow, vars_)

        return best_match

    def _score_trigger(self, text: str, trigger: str) -> Tuple[int, bool]:
        if text == trigger:
            return 100, True
        if "{" in trigger:
            pattern = re.escape(trigger)
            pattern = re.sub(r'\\{[^}]+\\}', r'(.+)', pattern)
            if re.search(pattern, text, re.IGNORECASE):
                return 90, True
            prefix = trigger.split("{")[0].strip()
            if prefix and text.startswith(prefix):
                return 70, True
        if trigger in text:
            return 60 + len(trigger), True
        words = [w for w in re.sub(r'\{[^}]+\}', '', trigger).split() if len(w) > 2]
        if words and all(w in text for w in words):
            return 40, True
        return 0, False

    def _extract_variables(self, text: str, trigger: str, workflow: dict) -> dict:
        vars_: dict = {}
        if "{query}" in trigger:
            parts = trigger.split("{query}")
            prefix = parts[0].strip()
            suffix = parts[1].strip() if len(parts) > 1 else ""
            if prefix and text.startswith(prefix):
                raw = text[len(prefix):].strip()
                # Strip matching suffix (e.g. " on youtube" from trigger)
                if suffix and raw.endswith(suffix):
                    raw = raw[: -len(suffix)].strip()
                vars_["query"] = raw.rstrip(".,!?")
            else:
                words = [w for w in prefix.split() if len(w) > 2]
                if words:
                    last_kw = words[-1]
                    idx = text.rfind(last_kw)
                    if idx >= 0:
                        raw = text[idx + len(last_kw):].strip()
                        if suffix and raw.endswith(suffix):
                            raw = raw[: -len(suffix)].strip()
                        vars_["query"] = raw.rstrip(".,!?")
        if "{email}" in trigger:
            m = re.search(r'\b[\w.+\-]+@[\w.\-]+\b', text)
            if m:
                vars_["email"] = m.group(0)
        if "{name}" in trigger:
            m = re.search(r'\bto\s+([A-Za-z][A-Za-z\s]{0,30}?)(?:\s+about|\s+saying|\s+that|\s+with|$)', text)
            if m:
                vars_["name"] = m.group(1).strip().title()
        if "{subject}" in trigger:
            m = re.search(r'\babout\s+(.+?)(?:\s+saying|\s+with body|$)', text)
            if m:
                vars_["subject"] = m.group(1).strip()
        if "{location}" in trigger:
            m = re.search(r'\bto\s+(.+?)(?:\s+from|$)', text)
            if m:
                vars_["location"] = m.group(1).strip().rstrip(".,!?")
        return vars_

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(self, name: str, variables: dict, ctx: dict) -> dict:
        workflow = self._workflows.get(name)
        if not workflow:
            return {"success": False, "spoken": f"I don't have a workflow called '{name}'."}
        return self._run_steps(workflow, variables, ctx)

    def execute_workflow(self, workflow: dict, variables: dict, ctx: dict) -> dict:
        return self._run_steps(workflow, variables, ctx)

    def _run_steps(self, workflow: dict, variables: dict, ctx: dict) -> dict:
        steps = workflow.get("steps", [])
        if not steps:
            return {"success": False, "spoken": "Workflow has no steps."}

        wf_name = workflow.get("display_name", workflow.get("name", "workflow"))
        logger.info("[WORKFLOW] Starting '%s' vars=%r", wf_name, variables)

        for i, step in enumerate(steps):
            action = step.get("action", "")
            result = self._execute_step(action, step, variables, ctx)

            if not result.get("success", True):
                logger.warning("[WORKFLOW] Step %d failed (%s), retrying", i + 1, action)
                time.sleep(0.8)
                result = self._execute_step(action, step, variables, ctx)
                if not result.get("success", True):
                    logger.error("[WORKFLOW] Step %d permanently failed: %s", i + 1, action)
                    return {
                        "success": False,
                        "spoken": result.get("spoken", f"Step {i + 1} failed."),
                        "text": f"Workflow '{wf_name}' stopped at step {i + 1}: {action}",
                        "completed_steps": i,
                    }

            delay = float(step.get("delay", 0.6))
            if delay:
                time.sleep(delay)

            # Capture {project} after an open_app step. Live-measured that
            # window-title sniffing here is unreliable: open_app launches
            # VS Code bare (no folder argument), so its title reads generic
            # "Welcome - Visual Studio Code" — which _resolve_folder_name
            # then fuzzy-matched to an unrelated folder named "Welcome"
            # somewhere on disk. Use the backend's own repo_root instead —
            # deterministic, no timing dependency, and always correct for
            # what Xyron itself is actually running against.
            if action == "open_app" and result.get("success", True) and "project" not in variables:
                try:
                    from api.config import settings as _settings_wf
                    variables["project"] = _settings_wf.repo_root.name
                except Exception:
                    pass

        completion = workflow.get("completion_message", f"Done. {wf_name} completed.")

        # Context-aware completion (user feedback: completion speech should
        # reference what's actually being worked on — "VS Code and GitHub
        # are open, what are we building?" — not just "opened X"). {project}
        # was already captured in the loop above right after the open_app
        # step (before a later step could steal focus, e.g. work_mode's
        # browser open) — reusing it here rather than re-querying the
        # foreground window now, which would read the WRONG app.
        if "{project}" in completion:
            completion = completion.replace("{project}", variables.get("project", "your project"))

        logger.info("[WORKFLOW] '%s' completed (%d steps)", wf_name, len(steps))
        return {"success": True, "spoken": completion, "text": completion, "completed_steps": len(steps)}

    @staticmethod
    def _open_url_native(url: str) -> dict:
        """Open a URL in the user's real default Windows browser — never the
        Playwright automation browser. Delegates to the shared native-launch
        helper in system_tools.py (same mechanism as open_system_settings)
        so this isn't a second implementation of the same launch logic."""
        if not url:
            return {"success": False, "spoken": "No URL to open."}
        from api.tools.system_tools import open_url_native
        if not open_url_native(url):
            return {"success": False, "spoken": "Couldn't open that page."}
        label = url.replace("https://", "").replace("http://", "").split("/")[0]
        return {"success": True, "spoken": f"Opened {label}."}

    def _execute_step(self, action: str, step: dict, variables: dict, ctx: dict) -> dict:
        from api.tools import registry as tool_registry

        params = {
            k: self._substitute(v, variables)
            for k, v in step.items()
            if k not in ("action", "delay", "verify", "description")
        }

        if action == "browser.goto":
            r = tool_registry.execute("browser_navigate", {"url": params.get("url", "")}, ctx)
        elif action == "browser.click":
            r = tool_registry.execute("browser_click", {"selector": params.get("selector", ""), "text": params.get("text", "")}, ctx)
        elif action == "browser.fill":
            r = tool_registry.execute("browser_fill", {"selector": params.get("selector", ""), "label": params.get("label", ""), "value": params.get("value", "")}, ctx)
        elif action in ("browser.press", "browser.type"):
            # Reuses browser_workspace (the real, shared, CDP-controlled
            # browser) via the same main-loop bridge browser_tools.py uses —
            # _ensure_browser() no longer exists; it was a separate,
            # throwaway Playwright Chromium instance disconnected from every
            # other browser-aware part of this codebase.
            try:
                from api.agents.browser_agent.browser_workspace import browser_workspace
                from api.services.main_loop import run_coro_from_thread
                page = run_coro_from_thread(browser_workspace.get_or_create_page(), timeout=30.0)

                async def _do():
                    if action == "browser.press":
                        await page.keyboard.press(params.get("key", "Enter"))
                    else:
                        await page.keyboard.type(params.get("text", ""), delay=50)

                run_coro_from_thread(_do(), timeout=12.0)
                return {"success": True, "spoken": ""}
            except Exception as exc:
                return {"success": False, "spoken": str(exc)}
        elif action == "browser.wait":
            try:
                from api.agents.browser_agent.browser_workspace import browser_workspace
                from api.services.main_loop import run_coro_from_thread
                page = run_coro_from_thread(browser_workspace.get_or_create_page(), timeout=30.0)
                run_coro_from_thread(
                    page.wait_for_selector(params.get("selector", "body"), timeout=int(params.get("timeout", 8000))),
                    timeout=12.0,
                )
                return {"success": True, "spoken": ""}
            except Exception as exc:
                return {"success": False, "spoken": str(exc)}
        elif action == "browser.screenshot":
            r = tool_registry.execute("browser_screenshot", {}, ctx)
        elif action == "browser.close":
            r = tool_registry.execute("browser_close", {}, ctx)
        elif action == "desktop.open_url":
            # Distinct from browser.goto: that drives the Playwright
            # automation browser (headless=False Chromium on the Linux/WSL2
            # side — via WSLg this shows as a separate "Linux browser"
            # window, not the user's real Windows Chrome). browser.goto is
            # right when a later step needs to click/fill something in that
            # page; desktop.open_url is for "open this so the user can see
            # it in their actual browser" — same native `start` mechanism
            # open_system_settings already uses for ms-settings: URIs, which
            # opens in the user's real default Windows browser.
            return self._open_url_native(params.get("url", ""))
        elif action == "desktop.type":
            r = tool_registry.execute("desktop_type", {"text": params.get("text", "")}, ctx)
        elif action == "desktop.hotkey":
            r = tool_registry.execute("desktop_hotkey", {"keys": params.get("keys", "")}, ctx)
        elif action == "desktop.click":
            r = tool_registry.execute("desktop_click", {"x": params.get("x", 0), "y": params.get("y", 0), "button": params.get("button", "left")}, ctx)
        elif action == "desktop.scroll":
            r = tool_registry.execute("desktop_scroll", {"direction": params.get("direction", "down"), "amount": params.get("amount", 3)}, ctx)
        elif action == "desktop.focus":
            r = tool_registry.execute("desktop_focus_app", {"app_name": params.get("app_name", "")}, ctx)
        elif action == "desktop.screenshot":
            r = tool_registry.execute("desktop_screenshot", {}, ctx)
        elif action == "open_app":
            r = tool_registry.execute("open_application", {"app_name": params.get("app_name", "")}, ctx)
        elif action == "speak":
            return {"success": True, "spoken": self._substitute(params.get("text", ""), variables)}
        elif action == "wait":
            time.sleep(float(params.get("seconds", 1.0)))
            return {"success": True, "spoken": ""}
        else:
            logger.warning("[WORKFLOW] Unknown action: %s", action)
            return {"success": False, "spoken": f"Unknown step: {action}"}

        return {"success": r.success, "spoken": r.spoken or ""}

    @staticmethod
    def _substitute(value: Any, variables: dict) -> Any:
        if not isinstance(value, str):
            return value
        for k, v in variables.items():
            value = value.replace(f"{{{k}}}", str(v))
        return value


# Module-level singleton
automation_workflow_service = AutomationWorkflowService()
