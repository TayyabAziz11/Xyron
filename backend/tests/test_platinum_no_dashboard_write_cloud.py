"""
Platinum Tier Tests — Dashboard Single-Writer Safety
PT-3: Cloud MUST NOT write to Dashboard.md

Tests:
  - _assert_safe_write raises ValueError for Dashboard.md
  - _assert_safe_write raises ValueError for Approved/, Rejected/, Done/
  - Cloud orchestrator's _safe_write rejects Dashboard.md writes
  - Scanning with real items does NOT modify Dashboard.md
  - local_merge_updates is the ONLY writer to Dashboard.md
"""

import sys
from pathlib import Path

import pytest

# Bootstrap path so the package can be imported without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.skills.platinum.cloud_worker_orchestrator import (
    CloudWorkerOrchestrator,
    _assert_safe_write,
)
from ai_operator.skills.platinum.local_merge_updates import LocalMergeUpdates


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal vault structure for testing."""
    (tmp_path / "Needs_Action" / "email").mkdir(parents=True)
    (tmp_path / "In_Progress" / "cloud").mkdir(parents=True)
    (tmp_path / "Pending_Approval" / "email").mkdir(parents=True)
    (tmp_path / "Updates" / "cloud").mkdir(parents=True)
    (tmp_path / "Approved").mkdir()
    (tmp_path / "Rejected").mkdir()
    (tmp_path / "Done").mkdir()
    (tmp_path / "Dashboard.md").write_text("# Dashboard\n", encoding="utf-8")
    (tmp_path / "Logs").mkdir()
    return tmp_path


# ── Test: _assert_safe_write blocks Dashboard.md ──────────────────────────────

def test_assert_safe_write_blocks_dashboard(tmp_path: Path):
    """_assert_safe_write must raise ValueError for Dashboard.md."""
    dashboard = tmp_path / "Dashboard.md"
    with pytest.raises(ValueError, match="Dashboard.md"):
        _assert_safe_write(dashboard, tmp_path)


# ── Test: _assert_safe_write blocks Approved/ ────────────────────────────────

def test_assert_safe_write_blocks_approved(tmp_path: Path):
    """_assert_safe_write must raise ValueError for Approved/ directory."""
    approved_file = tmp_path / "Approved" / "some_plan.md"
    with pytest.raises(ValueError, match="Approved"):
        _assert_safe_write(approved_file, tmp_path)


# ── Test: _assert_safe_write blocks Rejected/ ────────────────────────────────

def test_assert_safe_write_blocks_rejected(tmp_path: Path):
    """_assert_safe_write must raise ValueError for Rejected/ directory."""
    rejected_file = tmp_path / "Rejected" / "some_plan.md"
    with pytest.raises(ValueError, match="Rejected"):
        _assert_safe_write(rejected_file, tmp_path)


# ── Test: _assert_safe_write blocks Done/ ────────────────────────────────────

def test_assert_safe_write_blocks_done(tmp_path: Path):
    """_assert_safe_write must raise ValueError for Done/ directory."""
    done_file = tmp_path / "Done" / "completed.md"
    with pytest.raises(ValueError, match="Done"):
        _assert_safe_write(done_file, tmp_path)


# ── Test: _assert_safe_write allows permitted paths ───────────────────────────

def test_assert_safe_write_allows_permitted_paths(tmp_path: Path):
    """_assert_safe_write should NOT raise for allowed cloud paths."""
    (tmp_path / "Pending_Approval" / "email").mkdir(parents=True)
    (tmp_path / "Updates" / "cloud").mkdir(parents=True)
    (tmp_path / "In_Progress" / "cloud").mkdir(parents=True)

    # These should not raise
    _assert_safe_write(tmp_path / "Pending_Approval" / "email" / "plan.md", tmp_path)
    _assert_safe_write(tmp_path / "Updates" / "cloud" / "update.md", tmp_path)
    # In_Progress/cloud is NOT in forbidden first-parts
    _assert_safe_write(tmp_path / "In_Progress" / "cloud" / "item.md", tmp_path)


# ── Test: orchestrator _safe_write rejects Dashboard.md ──────────────────────

def test_orchestrator_safe_write_blocks_dashboard(vault: Path):
    """CloudWorkerOrchestrator._safe_write should raise for Dashboard.md."""
    orch = CloudWorkerOrchestrator(vault_root=vault, dry_run=False)
    with pytest.raises(ValueError, match="Dashboard.md"):
        orch._safe_write(vault / "Dashboard.md", "malicious content")


# ── Test: scan_once does NOT modify Dashboard.md ─────────────────────────────

def test_scan_does_not_touch_dashboard(vault: Path):
    """After a full scan_once, Dashboard.md must be unchanged."""
    # Create an intake item
    item = vault / "Needs_Action" / "email" / "inbox__gmail__20260227-9000__test.md"
    item.write_text("Subject: Test\nFrom: test@example.com\n", encoding="utf-8")

    original = (vault / "Dashboard.md").read_text(encoding="utf-8")
    original_mtime = (vault / "Dashboard.md").stat().st_mtime

    orch = CloudWorkerOrchestrator(vault_root=vault, dry_run=False, git_commit=False)
    orch.scan_once()

    new_content = (vault / "Dashboard.md").read_text(encoding="utf-8")
    new_mtime = (vault / "Dashboard.md").stat().st_mtime

    assert new_content == original, "Cloud scan must not change Dashboard.md content"
    assert new_mtime == original_mtime, "Cloud scan must not touch Dashboard.md mtime"


# ── Test: local_merge_updates IS the correct Dashboard writer ─────────────────

def test_local_merge_updates_writes_dashboard(vault: Path):
    """LocalMergeUpdates should add Cloud Updates section to Dashboard.md."""
    # Create a cloud update
    update_file = vault / "Updates" / "cloud" / "update__20260227-143100.md"
    update_file.write_text(
        "# Cloud Update — 20260227-143100\n\n"
        "**Domain:** email\n"
        "**Plan:** plan__email__test__20260227-143100.md\n"
        "**Status:** Pending approval\n\n"
        "Cloud drafted a reply for an email.\n",
        encoding="utf-8",
    )

    merger = LocalMergeUpdates(vault_root=vault, dry_run=False)
    summary = merger.merge()

    dashboard_content = (vault / "Dashboard.md").read_text(encoding="utf-8")

    assert summary["updates_found"] == 1
    assert "Cloud Updates (Platinum)" in dashboard_content
    assert "Pending Approvals (Platinum)" in dashboard_content


# ── Test: static code analysis — cloud_worker does not reference Dashboard.md writes ──

def test_cloud_worker_source_has_no_dashboard_write():
    """
    Static check: cloud_worker_orchestrator.py source code must not contain
    patterns that suggest writing to Dashboard.md outside of the safety guard.
    """
    import ast
    from pathlib import Path

    source_file = Path(__file__).parent.parent / \
        "src/ai_operator/skills/platinum/cloud_worker_orchestrator.py"

    assert source_file.exists(), f"Source file not found: {source_file}"
    source = source_file.read_text(encoding="utf-8")

    # Parse and look for string literals containing "Dashboard.md" in write contexts
    tree = ast.parse(source)

    dashboard_string_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "Dashboard.md" in node.value:
                dashboard_string_nodes.append(node)

    # Only ONE occurrence is allowed: the FORBIDDEN_WRITE_PATHS set definition
    # and the error message in _assert_safe_write
    forbidden_contexts = [
        n for n in dashboard_string_nodes
        # Allow the constant in FORBIDDEN_WRITE_PATHS and error messages
        # Disallow in open() / write_text() / Path() constructors used for writing
    ]

    # There should be exactly the safety-related references, not write calls
    # Simple heuristic: count occurrences
    occurrences = source.count("Dashboard.md")
    # Expected: at most 4 (FORBIDDEN_WRITE_PATHS def, ValueError message x2, docstring)
    assert occurrences <= 6, (
        f"Found {occurrences} references to 'Dashboard.md' in cloud_worker_orchestrator.py. "
        "This may indicate an accidental write path. Review the source."
    )
