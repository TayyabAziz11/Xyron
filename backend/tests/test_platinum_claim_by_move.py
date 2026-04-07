"""
Platinum Tier Tests — Claim-by-Move Rule
PT-3: Delegation via Synced Vault

Tests:
  - Claim-by-move succeeds for first agent (file moved from Needs_Action to In_Progress/cloud)
  - Second attempt to claim the same file raises FileNotFoundError (already claimed)
  - Dry-run mode does not actually move files
  - Items in In_Progress/cloud are not processed again
  - Flood halt: orchestrator halts when max_pending_approvals reached
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Bootstrap path so the package can be imported without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.skills.platinum.cloud_worker_orchestrator import (
    CloudWorkerOrchestrator,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Create a minimal vault structure for testing."""
    (tmp_path / "Needs_Action" / "email").mkdir(parents=True)
    (tmp_path / "Needs_Action" / "social").mkdir(parents=True)
    (tmp_path / "Needs_Action" / "accounting").mkdir(parents=True)
    (tmp_path / "In_Progress" / "cloud").mkdir(parents=True)
    (tmp_path / "In_Progress" / "local").mkdir(parents=True)
    (tmp_path / "Pending_Approval" / "email").mkdir(parents=True)
    (tmp_path / "Pending_Approval" / "social").mkdir(parents=True)
    (tmp_path / "Pending_Approval" / "accounting").mkdir(parents=True)
    (tmp_path / "Updates" / "cloud").mkdir(parents=True)
    (tmp_path / "Approved").mkdir()
    (tmp_path / "Rejected").mkdir()
    (tmp_path / "Done").mkdir()
    (tmp_path / "Dashboard.md").write_text("# Dashboard\n", encoding="utf-8")
    (tmp_path / "Logs").mkdir()
    return tmp_path


def _make_intake_item(vault: Path, domain: str, name: str) -> Path:
    """Create a test intake item in Needs_Action/<domain>/."""
    item = vault / "Needs_Action" / domain / name
    item.write_text(
        f"# Test Item\nDomain: {domain}\nFrom: test@example.com\nSubject: Test\n",
        encoding="utf-8",
    )
    return item


def _make_orchestrator(vault: Path, **kwargs) -> CloudWorkerOrchestrator:
    """Create an orchestrator with sensible test defaults."""
    defaults = dict(
        vault_root=vault,
        dry_run=False,
        max_items=5,
        max_pending_approvals=10,
        git_commit=False,
        git_push=False,
    )
    defaults.update(kwargs)
    return CloudWorkerOrchestrator(**defaults)


# ── Test: basic claim succeeds ─────────────────────────────────────────────────

def test_claim_moves_file(vault: Path):
    """Claiming an item moves it from Needs_Action/email/ to In_Progress/cloud/."""
    item = _make_intake_item(vault, "email", "inbox__gmail__20260227-1000__test.md")
    orch = _make_orchestrator(vault)

    claimed = orch._claim_item(item)

    assert claimed is not None
    assert claimed.exists(), "Claimed path should exist after move"
    assert not item.exists(), "Original file should be gone (moved)"
    assert claimed.parent == vault / "In_Progress" / "cloud"


# ── Test: second claim on same file returns None ───────────────────────────────

def test_double_claim_returns_none(vault: Path):
    """Second claim attempt on same file returns None (file already moved)."""
    item = _make_intake_item(vault, "email", "inbox__gmail__20260227-1001__test.md")
    orch = _make_orchestrator(vault)

    # First claim
    claimed = orch._claim_item(item)
    assert claimed is not None

    # Second claim attempt — original file is gone; should return None
    claimed_again = orch._claim_item(item)
    assert claimed_again is None, "Second claim should return None (file already moved)"


# ── Test: dry-run does not move files ─────────────────────────────────────────

def test_dry_run_does_not_move_file(vault: Path):
    """In dry-run mode, claim_item should not move the file."""
    item = _make_intake_item(vault, "email", "inbox__gmail__20260227-1002__dryrun.md")
    orch = _make_orchestrator(vault, dry_run=True)

    claimed = orch._claim_item(item)

    # In dry-run, returns a path but does NOT move the file
    assert claimed is not None, "dry-run claim should return a path"
    assert item.exists(), "Original file should still exist in dry-run mode"


# ── Test: scan_once claims and creates plan ────────────────────────────────────

def test_scan_once_creates_plan(vault: Path):
    """scan_once should claim an item and create a plan file."""
    _make_intake_item(vault, "email", "inbox__gmail__20260227-1003__scan.md")
    orch = _make_orchestrator(vault)

    count = orch.scan_once()

    assert count == 1, f"Expected 1 item processed, got {count}"
    plans = list((vault / "Pending_Approval" / "email").glob("plan__email__*.md"))
    assert len(plans) == 1, f"Expected 1 plan file, got {len(plans)}"


# ── Test: plan file contains required markers ──────────────────────────────────

def test_plan_file_contains_draft_marker(vault: Path):
    """Plan files must contain the DRAFT CREATED BY CLOUD marker."""
    _make_intake_item(vault, "email", "inbox__gmail__20260227-1004__marker.md")
    orch = _make_orchestrator(vault)
    orch.scan_once()

    plans = list((vault / "Pending_Approval" / "email").glob("plan__email__*.md"))
    assert plans, "No plan file created"
    content = plans[0].read_text(encoding="utf-8")
    assert "DRAFT CREATED BY CLOUD" in content
    assert "REQUIRES LOCAL APPROVAL" in content


# ── Test: flood halt ───────────────────────────────────────────────────────────

def test_flood_halt_when_max_pending_reached(vault: Path):
    """Orchestrator should halt when In_Progress/cloud/ has >= max_pending_approvals items."""
    # Pre-fill In_Progress/cloud/ up to the limit
    in_progress_cloud = vault / "In_Progress" / "cloud"
    for i in range(3):  # limit is 3 for this test
        (in_progress_cloud / f"existing_item_{i}.md").write_text("existing\n", encoding="utf-8")

    # Create a new item to process
    _make_intake_item(vault, "email", "inbox__gmail__20260227-1005__flood.md")

    # Set max_pending_approvals to 3 (already reached)
    orch = _make_orchestrator(vault, max_pending_approvals=3)

    count = orch.scan_once()
    assert count == 0, "Should process 0 items when flood halt is triggered"


# ── Test: scan respects max_items ──────────────────────────────────────────────

def test_max_items_respected(vault: Path):
    """scan_once should not process more than max_items items per cycle."""
    for i in range(5):
        _make_intake_item(vault, "email", f"inbox__gmail__20260227-100{i}__item{i}.md")

    orch = _make_orchestrator(vault, max_items=2)
    count = orch.scan_once()

    assert count == 2, f"Expected max 2 items, got {count}"


# ── Test: items already in In_Progress are ignored ────────────────────────────

def test_items_in_in_progress_not_reprocessed(vault: Path):
    """Items in In_Progress/cloud/ should not appear in Needs_Action/ and are not reprocessed."""
    # Put an item directly into In_Progress/cloud/
    in_progress = vault / "In_Progress" / "cloud"
    (in_progress / "inbox__gmail__20260227-1010__already.md").write_text(
        "already claimed\n", encoding="utf-8"
    )

    orch = _make_orchestrator(vault)
    count = orch.scan_once()

    # No new items in Needs_Action/email/, so nothing should be processed
    assert count == 0, "Pre-existing In_Progress items should not be reprocessed"
