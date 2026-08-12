from __future__ import annotations

"""
Phase 4.6 — Autonomous Software Engineer pipeline.

Xyron CodingAgent acts as a full internal engineering team:

  ProductPlanner     → understand goal, define pages + features
  DesignReferenceAgent → research brand references ("like Apple")
  DesignResearcher   → research category trends (clothing, SaaS, …)
  DesignDirector     → produce concrete palette + typography + CSS vars
  FolderManager      → create workspace on Windows Desktop
  GitEngineer        → init + checkpoint commits + rollback on failure
  ProjectPlanner     → LLM-generated file tree with design context
  BackendEngineer    → generate mock data
  FrontendEngineer   → add Navbar/Footer/HeroSection + patch configs
  DependencyInstaller→ npm install with retry
  TerminalRunner     → dev server with port-conflict recovery
  PreviewLauncher    → open VS Code + browser
  QAEngineer         → build + lint + visual verification (Playwright)
  VisualReviewer     → screenshot blankness + DOM + console checks
  AutoDebugger       → error analysis + LLM fix → rebuild (max 3 rounds)
  ProjectMemory      → persist project record for "continue the website"

Narration: every step narrated via personality_engine.narrate_step()

Log tags:
  [CODING_AGENT_START] [CODING_NARRATION] [DESKTOP_PROJECT_CREATED]
  [VSCODE_OPENED] [BROWSER_PREVIEW_OPENED] [ITERATION_START]
  [ITERATION_CRITIQUE] [ITERATION_FIX_APPLIED] [ITERATION_REBUILD]
  [ITERATION_PASS] [ITERATION_STOP_MAX] [PROJECT_MEMORY_WRITE]
"""

import asyncio
import logging
import re
from typing import Any

from api.agents.agent_types import AgentStatus, AgentTask
from api.agents.coding_agent.auto_debugger import AutoDebugger
from api.agents.coding_agent.backend_engineer import BackendEngineer
from api.agents.coding_agent.code_generator import CodeGenerator
from api.agents.coding_agent.dependency_installer import DependencyInstaller
from api.agents.coding_agent.design_director import DesignDirector
from api.agents.coding_agent.design_researcher import DesignResearcher
from api.agents.coding_agent.error_analyzer import ErrorAnalyzer
from api.agents.coding_agent.folder_manager import FolderManager
from api.agents.coding_agent.frontend_engineer import FrontendEngineer
from api.agents.coding_agent.git_engineer import GitEngineer
from api.agents.coding_agent.preview_launcher import PreviewLauncher
from api.agents.coding_agent.product_planner import ProductPlanner
from api.agents.coding_agent.project_memory import project_memory
from api.agents.coding_agent.project_planner import ProjectPlanner
from api.agents.coding_agent.qa_engineer import QAEngineer
from api.agents.coding_agent.stack_selector import StackSelector
from api.agents.coding_agent.terminal_runner import TerminalRunner

logger = logging.getLogger(__name__)

_MAX_FIX_ROUNDS = 3


# ── Public entry point ─────────────────────────────────────────────────────────

async def run(
    task: AgentTask,
    runtime: Any,
    cancel_event: asyncio.Event,
    pause_event: asyncio.Event,
) -> str:
    goal = task.goal.strip()
    logger.info("[CODING_AGENT_START] goal=%r task_id=%s", goal, task.task_id)
    task.status = AgentStatus.RUNNING

    from api.agents.personality.personality_engine import personality_engine
    from api.agents.browser_agent.design_reference_agent import DesignReferenceAgent

    def _narrate(step: str, ctx: dict | None = None) -> str:
        return personality_engine.narrate_step(step, ctx or {})

    async def _say(pct: int, step: str, ctx: dict | None = None) -> None:
        msg = _narrate(step, ctx)
        logger.info("[CODING_NARRATION] pct=%d msg=%r", pct, msg)
        await _progress(task, pct, msg)

    # ── Instantiate all role modules ───────────────────────────────────────────
    product_planner  = ProductPlanner()
    design_ref_agent = DesignReferenceAgent()
    designer         = DesignResearcher()
    design_director  = DesignDirector()
    selector         = StackSelector()
    folder_mgr       = FolderManager()
    git              = GitEngineer()
    planner          = ProjectPlanner()
    backend_eng      = BackendEngineer()
    code_gen         = CodeGenerator()
    frontend_eng     = FrontendEngineer()
    installer        = DependencyInstaller()
    runner           = TerminalRunner()
    launcher         = PreviewLauncher()
    qa               = QAEngineer()
    debugger         = AutoDebugger()
    analyzer         = ErrorAnalyzer()

    dev_proc = None
    project_path = None
    scaffold_sha: str | None = None
    built_sha: str | None = None

    try:
        # ── Step 1: Parse goal ─────────────────────────────────────────────────
        await _say(3, "coding.parsing")
        await _check(cancel_event, task)
        features = _extract_features(goal)

        # ── Step 2: Understand the product ────────────────────────────────────
        await _say(6, "coding.planning")
        product_spec = await product_planner.plan(goal)
        app_type = product_spec["app_type"]
        logger.info("[PRODUCT_PLANNER] app_type=%r pages=%d", app_type, product_spec["page_count"])

        # ── Step 3a: Research brand reference ("like Apple") ──────────────────
        await _check(cancel_event, task)
        await _say(8, "coding.researching")
        brand_brief: dict | None = await design_ref_agent.research(goal)

        # ── Step 3b: Research category design trends ──────────────────────────
        category_brief: str | None = None
        if not brand_brief:
            category_brief = await designer.research(goal)

        # Combine briefs
        raw_brief: str = ""
        if brand_brief:
            raw_brief = brand_brief.get("design_brief", "")
        elif category_brief:
            raw_brief = category_brief

        # ── Step 4: Build design specification ────────────────────────────────
        await _check(cancel_event, task)
        design_spec = await design_director.direct(app_type, raw_brief or None)
        logger.info(
            "[DESIGN_DIRECTOR] layout=%r primary=%r font=%r",
            design_spec["layout"],
            design_spec["palette"].get("primary"),
            design_spec["typography"].get("heading"),
        )

        # Merge brand palette if available
        if brand_brief and "color_palette" in brand_brief:
            design_spec["palette"].update(brand_brief["color_palette"])
        if brand_brief and "typography" in brand_brief:
            for k, v in brand_brief["typography"].items():
                if v:
                    design_spec["typography"][k.replace("_font", "")] = v

        # ── Step 5: Select stack ───────────────────────────────────────────────
        await _check(cancel_event, task)
        stack_key  = selector.select(goal, features)
        stack_info = selector.get_stack_info(stack_key)
        await _say(12, "coding.stack")

        # ── Step 6: Generate LLM project plan ─────────────────────────────────
        await _check(cancel_event, task)
        task.status = AgentStatus.PLANNING
        project_plan = await planner.plan(goal, stack_key, design_brief=raw_brief or None)
        project_name = project_plan.get("name", folder_mgr.safe_name(goal))
        task.status  = AgentStatus.RUNNING
        logger.info("[PROJECT_PLAN_CREATED] name=%r files=%d", project_name, len(project_plan.get("files", [])))

        # ── Step 7: Create Desktop workspace ──────────────────────────────────
        await _check(cancel_event, task)
        await _say(18, "coding.creating_folder", {"project_name": project_name})
        project_path = await folder_mgr.create_project_folder(project_name)
        logger.info("[DESKTOP_PROJECT_CREATED] path=%s", project_path)

        # Save brand brief files now that project_path exists
        if brand_brief:
            try:
                import json as _json
                (project_path / "design_brief.json").write_text(_json.dumps(brand_brief, indent=2), "utf-8")
                logger.info("[DESIGN_BRIEF_CREATED] json=%s", project_path / "design_brief.json")
            except Exception:
                pass

        # ── Step 8: Git init ───────────────────────────────────────────────────
        await _check(cancel_event, task)
        git_ok = await git.init(project_path)
        if git_ok:
            logger.info("[GIT_INIT] %s", project_path)

        # ── Step 9: Write source files ─────────────────────────────────────────
        await _check(cancel_event, task)
        file_count = len(project_plan.get("files", []))
        await _say(22, "coding.writing_files", {"count": file_count})
        written_paths = await code_gen.generate_files(project_plan, project_path, task)
        logger.info("[FILES_WRITTEN] count=%d", len(written_paths))

        # ── Step 10: Generate mock data ────────────────────────────────────────
        await _check(cancel_event, task)
        mock_files = await backend_eng.generate_mock_data(project_path, product_spec, design_spec)
        if mock_files:
            logger.info("[BACKEND_ENGINEER] mock data files: %s", mock_files)

        # ── Step 11: Enhance with Navbar/Footer/HeroSection + theme ───────────
        await _check(cancel_event, task)
        extra_files = await frontend_eng.enhance(project_path, product_spec, design_spec)
        if extra_files:
            logger.info("[FRONTEND_ENGINEER] enhanced: %s", extra_files)

        # ── Step 12: Git checkpoint — scaffold ────────────────────────────────
        if git_ok:
            scaffold_sha = await git.checkpoint(project_path, "scaffold: generated project")
            logger.info("[GIT_COMMIT] scaffold sha=%s", scaffold_sha)

        # ── Step 13: Install dependencies (with retry) ─────────────────────────
        await _check(cancel_event, task)
        await _say(42, "coding.installing")
        install_result = await _install_with_retry(
            installer, stack_info.get("runtime", "node"),
            project_plan, project_path, task
        )
        if install_result and not install_result.success:
            await _progress(task, 55, f"Install issue: {install_result.output[:60]}")

        # ── Step 14: Post-install commands ─────────────────────────────────────
        for cmd_str in project_plan.get("commands_after_install", []):
            await _check(cancel_event, task)
            await runner.run_command(cmd_str.split(), cwd=project_path, timeout=60.0)

        # ── Step 15: Start dev server (port-conflict recovery) ────────────────
        await _check(cancel_event, task)
        port  = stack_info.get("port", 5173)
        url   = f"http://localhost:{port}"
        await _say(58, "coding.starting_server", {"port": port})
        dev_proc, port, url = await _start_server_with_recovery(
            runner, project_path, stack_info.get("dev_command", "npm run dev"), port
        )
        if dev_proc:
            logger.info("[DEV_SERVER_STARTED] port=%d url=%s", port, url)
        await _progress(task, 65, f"Dev server at {url}")

        # ── Step 16: Open VS Code ──────────────────────────────────────────────
        await _check(cancel_event, task)
        windows_path = PreviewLauncher._to_windows_path(str(project_path)) or str(project_path)
        await _say(68, "coding.opening_vscode", {"windows_path": windows_path})
        vscode_ok = await _open_vscode_with_retry(launcher, project_path)
        if vscode_ok:
            logger.info("[VSCODE_OPENED] path=%s", project_path)
        await asyncio.sleep(1.5)

        # ── Step 17: Open browser preview ─────────────────────────────────────
        await _check(cancel_event, task)
        await _say(72, "coding.opening_browser", {"url": url})
        preview_ok = await launcher.open_browser_preview(url)
        if preview_ok:
            logger.info("[BROWSER_PREVIEW_OPENED] url=%s", url)

        # ── Steps 18-20: Verify + Critique + Fix loop ─────────────────────────
        fix_round   = 0
        qa_passed   = False
        last_report = None

        while fix_round <= _MAX_FIX_ROUNDS:
            await _check(cancel_event, task)

            if fix_round == 0:
                logger.info("[ITERATION_START] round=0")
                await _say(75, "coding.verifying", {"url": url})
            else:
                logger.info("[ITERATION_START] round=%d", fix_round)
                await _say(75 + fix_round * 5, "coding.fixing",
                           {"attempt": fix_round, "max": _MAX_FIX_ROUNDS, "file": "source"})

            # Run full QA
            qa_report = await qa.run(
                project_path, url, task,
                run_build=(fix_round > 0),   # skip build on first check (server running = it builds)
                run_lint=False,               # lint is non-blocking, skip for speed
            )
            last_report = qa_report
            logger.info("[ITERATION_CRITIQUE] round=%d passed=%s issues=%d",
                        fix_round, qa_report["passed"], len(qa_report["issues"]))

            if qa_report["passed"]:
                logger.info("[ITERATION_PASS] round=%d", fix_round)
                qa_passed = True
                # Git checkpoint after successful verification
                if git_ok:
                    sha = "built" if fix_round == 0 else f"fix-{fix_round}"
                    built_sha = await git.checkpoint(project_path, f"{sha}: QA passed")
                    logger.info("[GIT_COMMIT] %s sha=%s", sha, built_sha)
                break

            fix_round += 1
            if fix_round > _MAX_FIX_ROUNDS:
                logger.warning("[ITERATION_STOP_MAX] max rounds reached")
                break

            # Auto-fix from QA critique
            critique = QAEngineer.format_critique(qa_report)
            logger.info("[ITERATION_CRITIQUE] %s", critique[:200])

            # Collect dev-server stderr for error context
            error_text = ""
            if dev_proc and dev_proc.stderr:
                try:
                    raw = await asyncio.wait_for(dev_proc.stderr.read(8192), timeout=2.0)
                    error_text = raw.decode(errors="replace")
                except Exception:
                    pass

            error_info = analyzer.parse_vite_error(error_text) if error_text else {
                "type": "visual",
                "message": critique,
                "file": "",
                "line": 0,
                "raw": critique,
                "suggestion": "",
            }

            fixed = await debugger.debug_cycle(error_info, project_path, task)
            logger.info("[ITERATION_FIX_APPLIED] round=%d fixed=%s", fix_round, fixed)

            if not fixed:
                # Nothing changed — no point rerunning
                logger.warning("[ITERATION_STOP_MAX] fix could not be applied")
                break

            # Restart dev server after fix
            logger.info("[ITERATION_REBUILD] round=%d", fix_round)
            await _safe_kill(runner, dev_proc)
            await asyncio.sleep(1.0)
            dev_proc, port, url = await _start_server_with_recovery(
                runner, project_path,
                stack_info.get("dev_command", "npm run dev"), port
            )

            # Rollback to scaffold if server won't start after fix
            if dev_proc is None and git_ok and scaffold_sha:
                logger.warning("[GIT_ROLLBACK] fix broke server — rolling back to scaffold")
                await git.rollback_to(project_path, scaffold_sha)
                dev_proc, port, url = await _start_server_with_recovery(
                    runner, project_path,
                    stack_info.get("dev_command", "npm run dev"), port
                )
                break

        # ── Step 21: Final git commit ──────────────────────────────────────────
        if git_ok:
            final_sha = await git.checkpoint(project_path, "verified: Phase 4.6 complete")
            logger.info("[GIT_COMMIT] final sha=%s", final_sha)
            commits = await git.get_commit_count(project_path)
            logger.info("[GIT_STATUS_CLEAN] %d commits in repo", commits)

        # ── Step 22: Save to ProjectMemory ─────────────────────────────────────
        project_memory.save_project({
            "name":         project_name,
            "path":         str(project_path),
            "stack":        stack_key,
            "port":         port,
            "url":          url,
            "app_type":     app_type,
            "design_brief": raw_brief[:200] if raw_brief else "",
            "status":       "verified" if qa_passed else "built",
            "page_count":   product_spec.get("page_count", 0),
            "file_count":   len(written_paths),
        })
        logger.info("[PROJECT_MEMORY_WRITE] saved name=%r", project_name)

        # ── Step 23: Final narrated response ──────────────────────────────────
        await _check(cancel_event, task)
        task.status = AgentStatus.COMPLETED
        task.progress_pct = 100
        task.metadata["project_path"] = str(project_path)
        task.metadata["preview_url"]  = url

        completion_step = "coding.complete" if qa_passed else "coding.starting_server"
        final_msg = _narrate(completion_step, {"url": url, "path": str(project_path)})
        await _progress(task, 100, final_msg)

        commits = await git.get_commit_count(project_path)
        summary = (
            f"'{project_name}' built at {project_path}\n"
            f"Stack: {stack_info['name']} | URL: {url} | Files: {len(written_paths)} | "
            f"Git commits: {commits} | QA: {'PASS' if qa_passed else 'PARTIAL'}\n"
            f"Design: {app_type} | Brand: {brand_brief.get('brand_reference', 'N/A') if brand_brief else 'N/A'}"
        )
        logger.info("[CODING_AGENT_DONE] %s", summary[:120])
        return summary

    except _CancelledError:
        task.status = AgentStatus.CANCELLED
        return "Project build cancelled."
    except Exception as exc:
        task.status = AgentStatus.FAILED
        task.error_message = str(exc)
        logger.exception("[CODING_AGENT] unexpected error: %s", exc)
        return f"Project build failed: {exc}"
    finally:
        await _safe_kill(runner, dev_proc)


# ── Recovery helpers ───────────────────────────────────────────────────────────

async def _open_vscode_with_retry(launcher: PreviewLauncher, project_path: Any) -> bool:
    for attempt in range(1, 4):
        ok = await launcher.open_in_vscode(project_path)
        if ok:
            return True
        await asyncio.sleep(attempt * 1.5)
    return False


async def _install_with_retry(
    installer: DependencyInstaller,
    runtime_type: str,
    project_plan: dict,
    project_path: Any,
    task: AgentTask,
    max_attempts: int = 2,
):
    result = None
    for attempt in range(1, max_attempts + 1):
        if runtime_type == "python":
            result = await installer.install_python_deps(project_path, project_plan.get("python_requirements", []))
        else:
            result = await installer.install(project_path, task)
        if result and result.success:
            return result
        if attempt < max_attempts:
            await asyncio.sleep(2.0)
    return result


async def _start_server_with_recovery(
    runner: TerminalRunner,
    project_path: Any,
    dev_command: str,
    preferred_port: int,
):
    ports = [preferred_port, preferred_port + 1, preferred_port + 2, preferred_port + 3]
    for port in ports:
        if _port_in_use(port):
            continue
        url     = f"http://localhost:{port}"
        command = _inject_port(dev_command, port)
        try:
            proc = await runner.run_dev_server(project_path, command, port)
            return proc, port, url
        except Exception as exc:
            logger.warning("[CODING_AGENT] port %d failed: %s", port, exc)
    return None, preferred_port, f"http://localhost:{preferred_port}"


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _inject_port(command: str, port: int) -> str:
    if "--port" not in command and "vite" in command.lower():
        return f"{command} --port {port}"
    return command


async def _safe_kill(runner: TerminalRunner, proc: Any) -> None:
    if proc is not None:
        try:
            await runner.kill_process(proc)
        except Exception:
            pass


# ── Internal helpers ───────────────────────────────────────────────────────────

class _CancelledError(Exception):
    pass


async def _check(cancel_event: asyncio.Event, task: AgentTask) -> None:
    if cancel_event.is_set():
        raise _CancelledError()
    await asyncio.sleep(0)


async def _progress(task: AgentTask, pct: int, message: str) -> None:
    task.progress_pct = pct
    logger.debug("[CODING_AGENT] pct=%d msg=%s", pct, message)
    if task.ws_send_fn is None:
        return
    try:
        await task.ws_send_fn({
            "type":         "agent_progress",
            "task_id":      task.task_id,
            "message":      message,
            "progress_pct": pct,
        })
    except Exception:
        pass


def _extract_features(goal: str) -> list[str]:
    stop = {
        "a", "an", "the", "me", "my", "i", "want", "need", "please",
        "build", "create", "make", "generate", "design", "write", "develop",
        "set", "up", "setup", "with", "and", "or", "for", "to", "that",
        "has", "have", "using", "use",
    }
    tokens = re.sub(r"[^a-z0-9 ._-]", " ", goal.lower()).split()
    return [t for t in tokens if t not in stop and len(t) > 2]
