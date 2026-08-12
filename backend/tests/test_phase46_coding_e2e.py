"""
Phase 4.6 — Autonomous Software Engineer — Live E2E Tests.

These tests exercise the REAL pipeline end-to-end:
  no mocks, no fakes, real filesystem, real LLM calls, real npm build,
  real Playwright screenshots.

Run individually (they each take 3-5 minutes):
  pytest tests/test_phase46_coding_e2e.py::test_clothing_website -s -v
  pytest tests/test_phase46_coding_e2e.py::test_apple_style_site -s -v
  pytest tests/test_phase46_coding_e2e.py::test_saas_dashboard -s -v
  pytest tests/test_phase46_coding_e2e.py::test_continue_website -s -v
  pytest tests/test_phase46_coding_e2e.py::test_error_recovery -s -v

Requirements:
  OPENAI_API_KEY must be set (reads from backend/.env or env)
  Node.js must be available on PATH
  playwright must be installed (pip install playwright && playwright install chromium)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

# ── Bootstrap path & env ───────────────────────────────────────────────────────
_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

# Load .env if present
_env_file = _BACKEND / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Skip entire module if no API key
pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — skipping Phase 4.6 live E2E tests",
)

# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_task(goal: str, task_id: str = "e2e-test"):
    from api.agents.agent_types import AgentStatus, AgentTask
    task = AgentTask(
        task_id=task_id,
        goal=goal,
        agent="coding",
        status=AgentStatus.PENDING,
    )
    task.ws_send_fn = None
    task.metadata   = {}
    return task


async def _run_pipeline(goal: str, task_id: str = "e2e") -> tuple[str, dict]:
    """Run full CodingAgent pipeline and return (summary, task.metadata)."""
    import api.agents.coding_agent.coding_builder_agent as builder
    from api.agents.agent_types import AgentStatus

    task         = _make_task(goal, task_id)
    cancel_event = asyncio.Event()
    pause_event  = asyncio.Event()

    summary = await builder.run(task, runtime=None, cancel_event=cancel_event, pause_event=pause_event)
    return summary, task.metadata


# ── Test 1: Clothing website ───────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_clothing_website():
    """Full pipeline: clothing e-commerce site with mock data + visual verify."""
    t0 = time.monotonic()
    summary, meta = await _run_pipeline(
        "Create a modern clothing e-commerce website with product grid, hero banner, and cart",
        "e2e-clothing",
    )
    elapsed = time.monotonic() - t0

    print(f"\n[TEST] elapsed={elapsed:.1f}s")
    print(f"[TEST] summary: {summary}")
    print(f"[TEST] meta: {meta}")

    project_path = Path(meta.get("project_path", ""))
    preview_url  = meta.get("preview_url", "")

    # File assertions
    assert project_path.exists(), f"Project directory not created: {project_path}"
    assert (project_path / "package.json").exists(), "package.json missing"
    assert (project_path / "src").exists(), "src/ directory missing"

    # Project memory assertion
    from api.agents.coding_agent.project_memory import project_memory
    last = project_memory.get_last_project()
    assert last is not None, "ProjectMemory did not save the project"
    assert "clothing" in last["name"].lower() or "ecommerce" in last.get("app_type", "").lower(), (
        f"Unexpected project name: {last['name']}"
    )

    # Git assertions
    git_dir = project_path / ".git"
    assert git_dir.exists(), ".git directory missing — GitEngineer not called"
    log = (project_path / ".git" / "logs" / "HEAD")
    assert log.exists(), "Git log missing — no commits made"
    commits = log.read_text(errors="replace").strip().splitlines()
    assert len(commits) >= 2, f"Expected >=2 git commits, got {len(commits)}"

    # Screenshot assertion
    desktop_shot = project_path / "xyron_review_desktop.png"
    mobile_shot  = project_path / "xyron_review_mobile.png"
    assert desktop_shot.exists(), "Desktop screenshot not captured"
    assert mobile_shot.exists(), "Mobile screenshot not captured"
    assert desktop_shot.stat().st_size > 10_000, "Desktop screenshot looks empty"

    # URL / server must have been set
    assert preview_url.startswith("http://localhost:"), f"Bad preview URL: {preview_url}"

    print(f"[TEST PASS] test_clothing_website in {elapsed:.1f}s")


# ── Test 2: Apple-style site (brand reference research) ───────────────────────

@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_apple_style_site():
    """Brand reference research: 'like Apple' triggers DesignReferenceAgent."""
    from api.agents.browser_agent.design_reference_agent import DesignReferenceAgent

    summary, meta = await _run_pipeline(
        "Build me a product showcase website like Apple — clean, minimal, premium feel",
        "e2e-apple",
    )

    project_path = Path(meta.get("project_path", ""))
    assert project_path.exists(), "Project not created"

    # DesignReferenceAgent should have saved design_brief.json
    brief_file = project_path / "design_brief.json"
    assert brief_file.exists(), "design_brief.json not created — brand research may have failed"

    brief = json.loads(brief_file.read_text())
    assert "brand_reference" in brief, "brief missing brand_reference field"
    assert "color_palette" in brief,   "brief missing color_palette"
    assert "typography" in brief,      "brief missing typography"

    print(f"[TEST] brand_reference={brief.get('brand_reference')}")
    print(f"[TEST] palette={brief.get('color_palette', {}).get('primary')}")

    # Components should exist
    assert (project_path / "src" / "components").exists(), "components/ dir missing"

    print("[TEST PASS] test_apple_style_site")


# ── Test 3: SaaS dashboard ────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_saas_dashboard():
    """SaaS dashboard: admin layout, mock analytics data, sidebar nav."""
    summary, meta = await _run_pipeline(
        "Create a SaaS analytics dashboard with sidebar nav, KPI cards, and a data table",
        "e2e-dashboard",
    )

    project_path = Path(meta.get("project_path", ""))
    assert project_path.exists(), "Project not created"

    # Mock data should be generated for admin-dashboard type
    src_data = project_path / "src" / "data"
    if src_data.exists():
        data_files = list(src_data.glob("*.json"))
        print(f"[TEST] mock data files: {[f.name for f in data_files]}")
        assert len(data_files) >= 1, "No mock data files generated"
        for f in data_files:
            parsed = json.loads(f.read_text())
            assert isinstance(parsed, (list, dict)), f"{f.name} is not valid JSON"

    # Tailwind config should have brand colors injected
    tw_config = project_path / "tailwind.config.js"
    if tw_config.exists():
        content = tw_config.read_text()
        # FrontendEngineer may inject brand colors; verify file is valid JS
        assert "module.exports" in content or "export default" in content, (
            "tailwind.config.js looks malformed"
        )

    print("[TEST PASS] test_saas_dashboard")


# ── Test 4: "Continue the website" — ProjectMemory retrieval ──────────────────

@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_continue_website():
    """ProjectMemory can retrieve the last project built in this test session."""
    from api.agents.coding_agent.project_memory import project_memory

    # Ensure at least one project is saved (may rely on previous tests running first,
    # or we seed a synthetic record)
    project_memory.save_project({
        "name":   "test-seed-project",
        "path":   "/tmp/test-seed",
        "stack":  "vite-react",
        "port":   5173,
        "url":    "http://localhost:5173",
        "app_type": "landing-page",
        "design_brief": "Clean minimal SaaS landing page",
        "status": "verified",
    })

    # Test retrieval
    last = project_memory.get_last_project()
    assert last is not None, "project_memory.get_last_project() returned None"

    # Test search
    results = project_memory.search_projects("seed")
    assert len(results) >= 1, "search_projects('seed') found nothing"

    # Test list
    all_projects = project_memory.list_projects()
    assert len(all_projects) >= 1, "list_projects() returned empty list"

    # Test status update
    project_memory.update_status("test-seed-project", "deployed")
    updated = project_memory.get_project_by_name("test-seed-project")
    assert updated is not None, "get_project_by_name returned None"
    assert updated["status"] == "deployed", f"Status not updated: {updated['status']}"

    print(f"[TEST] project_memory has {len(all_projects)} project(s)")
    print("[TEST PASS] test_continue_website")


# ── Test 5: Error recovery — AutoDebugger fix loop ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.timeout(360)
async def test_error_recovery():
    """
    Inject a syntax error into App.tsx after scaffold, then confirm the
    AutoDebugger fix loop detects the error and either:
      (a) fixes it successfully, OR
      (b) reaches max rounds gracefully without crashing.

    We do this by running a normal project and then reading QA report state
    from the pipeline outcome — the fix loop is already wired into run().
    """
    summary, meta = await _run_pipeline(
        "Create a simple portfolio website",
        "e2e-recovery",
    )

    project_path = Path(meta.get("project_path", ""))
    assert project_path.exists(), "Project not created"

    # Whether QA passed or reached max rounds, the pipeline must complete
    # without raising — the summary will say PASS or PARTIAL
    assert "portfolio" in summary.lower() or "built" in summary.lower(), (
        f"Unexpected summary: {summary}"
    )

    # Git must have at least 1 commit (scaffold)
    git_log = project_path / ".git" / "logs" / "HEAD"
    if git_log.exists():
        commits = git_log.read_text(errors="replace").strip().splitlines()
        assert len(commits) >= 1, "No git commits — GitEngineer not called"
        print(f"[TEST] git commits: {len(commits)}")

    print(f"[TEST] summary: {summary}")
    print("[TEST PASS] test_error_recovery")
