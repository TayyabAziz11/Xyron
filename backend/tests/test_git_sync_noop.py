"""
Platinum Tier Tests — Git Sync No-op Handling
Tests that git_sync helpers handle no-op states gracefully (mocked subprocess).

Tests:
  - git_pull succeeds when already up-to-date
  - git_pull returns (False, msg) on error without raising
  - git_commit_all returns (True, "Nothing to commit.") when no changes
  - git_push returns (True, ...) when everything up-to-date
  - git_pull does not log credential values (mock URL with token)
  - Timeout returns (False, error_msg) gracefully
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Bootstrap path so the package can be imported without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.core.git_sync import (
    git_commit_all,
    git_pull,
    git_push,
    git_status,
    _mask_url,
)


# ── Helper: create mock subprocess result ─────────────────────────────────────

def _mock_result(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── git_pull tests ────────────────────────────────────────────────────────────

def test_git_pull_already_up_to_date(tmp_path: Path):
    """git_pull returns (True, ...) when already up to date."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(0, "Already up to date.", "")
        ok, msg = git_pull(tmp_path)
    assert ok is True
    assert "up to date" in msg.lower()


def test_git_pull_success_with_changes(tmp_path: Path):
    """git_pull returns (True, ...) when new commits are pulled."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(
            0,
            "Updating abc1234..def5678\nFast-forward\n 1 file changed",
            "",
        )
        ok, msg = git_pull(tmp_path)
    assert ok is True


def test_git_pull_failure_returns_false(tmp_path: Path):
    """git_pull returns (False, error_msg) on non-zero exit without raising."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(1, "", "ERROR: Connection refused")
        ok, msg = git_pull(tmp_path)
    assert ok is False
    assert len(msg) > 0


def test_git_pull_timeout_returns_false(tmp_path: Path):
    """git_pull returns (False, ...) when subprocess times out."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60)):
        ok, msg = git_pull(tmp_path)
    assert ok is False
    assert "timed out" in msg.lower()


def test_git_pull_git_not_found_returns_false(tmp_path: Path):
    """git_pull returns (False, ...) when git binary is not found."""
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        ok, msg = git_pull(tmp_path)
    assert ok is False


# ── git_commit_all tests ──────────────────────────────────────────────────────

def test_git_commit_all_nothing_to_commit(tmp_path: Path):
    """git_commit_all returns (True, 'Nothing to commit.') when nothing has changed."""
    with patch("subprocess.run") as mock_run:
        # git add succeeds
        mock_run.side_effect = [
            _mock_result(0, "", ""),                           # git add
            _mock_result(1, "nothing to commit, working tree clean", ""),  # git commit
        ]
        ok, msg = git_commit_all(tmp_path, "test commit")
    assert ok is True
    assert "nothing" in msg.lower()


def test_git_commit_all_success(tmp_path: Path):
    """git_commit_all returns (True, ...) on successful commit."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _mock_result(0, "", ""),           # git add
            _mock_result(0, "[main abc1234] test commit\n 2 files changed", ""),  # git commit
        ]
        ok, msg = git_commit_all(tmp_path, "test commit")
    assert ok is True
    assert "test commit" in msg or "abc1234" in msg


def test_git_commit_all_add_failure(tmp_path: Path):
    """git_commit_all returns (False, ...) if git add fails."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(1, "", "fatal: not a git repository")
        ok, msg = git_commit_all(tmp_path, "test commit")
    assert ok is False


def test_git_commit_all_no_changes_added_phrase(tmp_path: Path):
    """Handles 'no changes added to commit' phrasing as no-op."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _mock_result(0, "", ""),                               # git add
            _mock_result(1, "", "no changes added to commit"),    # git commit
        ]
        ok, msg = git_commit_all(tmp_path, "test commit")
    assert ok is True


# ── git_push tests ────────────────────────────────────────────────────────────

def test_git_push_success(tmp_path: Path):
    """git_push returns (True, ...) on success."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(0, "", "To github.com:user/repo.git\n   abc..def  main -> main")
        ok, msg = git_push(tmp_path)
    assert ok is True


def test_git_push_everything_up_to_date(tmp_path: Path):
    """git_push returns (True, ...) when everything is already up-to-date."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(0, "", "Everything up-to-date")
        ok, msg = git_push(tmp_path)
    assert ok is True


def test_git_push_failure_returns_false(tmp_path: Path):
    """git_push returns (False, error_msg) on failure."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(1, "", "ERROR: Permission denied (publickey)")
        ok, msg = git_push(tmp_path)
    assert ok is False


# ── git_status tests ──────────────────────────────────────────────────────────

def test_git_status_clean(tmp_path: Path):
    """git_status returns (True, '(clean)') on clean repo."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(0, "", "")
        ok, status = git_status(tmp_path)
    assert ok is True
    assert "(clean)" in status


def test_git_status_with_changes(tmp_path: Path):
    """git_status returns (True, status_text) with pending changes."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_result(0, " M Dashboard.md\n?? new_file.md", "")
        ok, status = git_status(tmp_path)
    assert ok is True
    assert "Dashboard.md" in status


# ── Security: _mask_url never leaks token ────────────────────────────────────

def test_mask_url_removes_token():
    """_mask_url must mask https://<token>@github.com patterns."""
    url = "https://ghp_secretTokenABCDEF123456@github.com/user/repo.git"
    masked = _mask_url(url)
    assert "ghp_secretTokenABCDEF123456" not in masked
    assert "***" in masked
    assert "github.com" in masked


def test_mask_url_passes_clean_url():
    """_mask_url should leave clean URLs intact."""
    url = "https://github.com/user/repo.git"
    masked = _mask_url(url)
    assert masked == url


def test_mask_url_handles_non_url():
    """_mask_url should not modify non-URL strings."""
    plain = "Already up to date."
    assert _mask_url(plain) == plain


# ── Integration: no real subprocess called when mocked ───────────────────────

def test_no_real_subprocess_in_git_pull(tmp_path: Path):
    """Verify subprocess.run is properly intercepted (no real git calls in tests)."""
    calls = []
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = lambda *a, **kw: (calls.append(a), _mock_result(0, "Already up to date.", ""))[1]
        git_pull(tmp_path)

    assert len(calls) == 1, "Expected exactly one subprocess call for git_pull"
    cmd = calls[0][0]
    assert cmd[0] == "git"
    assert cmd[1] == "pull"
