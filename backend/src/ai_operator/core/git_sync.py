#!/usr/bin/env python3
"""
Git Sync Helpers — Platinum Tier
Shared utility for cloud and local vault synchronisation via Git.

Safety rules:
- Never logs or prints credential values
- Always masks remote URLs containing tokens before logging
- Treats missing remotes / no-op states gracefully
- All functions return (success: bool, message: str)
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    """Replace token-looking segments in a URL so they never appear in logs."""
    import re
    # Mask https://<token>@github.com patterns
    return re.sub(r"https://[^@]+@", "https://***@", url)


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a subprocess command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ},  # Inherit env (git credentials, SSH_AUTH_SOCK, etc.)
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError:
        return -1, "", "git not found in PATH"
    except Exception as exc:
        return -1, "", str(exc)


def git_pull(repo_root: Path, remote: str = "origin", branch: str = "main") -> Tuple[bool, str]:
    """
    Pull latest changes from remote.

    Returns:
        (True, message) on success or already-up-to-date
        (False, error)  on failure
    """
    repo_root = Path(repo_root)
    logger.info("git pull %s %s in %s", remote, branch, repo_root)

    rc, stdout, stderr = _run(["git", "pull", remote, branch], cwd=repo_root)

    if rc == 0:
        msg = stdout or "Already up to date."
        logger.info("git pull OK: %s", msg)
        return True, msg

    # Non-fatal: nothing to pull / already up to date worded differently
    if "Already up to date" in stderr or "Already up to date" in stdout:
        return True, "Already up to date."

    masked_err = _mask_url(stderr)
    logger.warning("git pull failed (rc=%d): %s", rc, masked_err)
    return False, masked_err


def git_status(repo_root: Path) -> Tuple[bool, str]:
    """
    Return short git status.

    Returns:
        (True, status_text) always (status never really fails if git is installed)
    """
    repo_root = Path(repo_root)
    rc, stdout, stderr = _run(["git", "status", "--short"], cwd=repo_root)
    if rc == 0:
        return True, stdout or "(clean)"
    return False, stderr


def git_commit_all(repo_root: Path, message: str) -> Tuple[bool, str]:
    """
    Stage all tracked + untracked changes and commit.

    Steps: git add -A → git commit -m <message>
    Treats "nothing to commit" as a success (no-op).

    Returns:
        (True, message) on success or no-op
        (False, error)  on failure
    """
    repo_root = Path(repo_root)

    # Stage all changes
    rc_add, _, stderr_add = _run(["git", "add", "-A"], cwd=repo_root)
    if rc_add != 0:
        logger.warning("git add failed: %s", stderr_add)
        return False, f"git add failed: {stderr_add}"

    # Commit
    rc_commit, stdout_commit, stderr_commit = _run(
        ["git", "commit", "-m", message], cwd=repo_root
    )

    if rc_commit == 0:
        logger.info("git commit OK: %s", stdout_commit)
        return True, stdout_commit

    # "nothing to commit" is not an error
    nothing_phrases = [
        "nothing to commit",
        "nothing added to commit",
        "no changes added",
    ]
    combined = (stdout_commit + stderr_commit).lower()
    if any(p in combined for p in nothing_phrases):
        logger.info("git commit: nothing to commit (no-op)")
        return True, "Nothing to commit."

    logger.warning("git commit failed (rc=%d): %s", rc_commit, stderr_commit)
    return False, stderr_commit


def git_push(repo_root: Path, remote: str = "origin", branch: str = "main") -> Tuple[bool, str]:
    """
    Push commits to remote.

    Returns:
        (True, message) on success or already-up-to-date
        (False, error)  on failure
    """
    repo_root = Path(repo_root)
    logger.info("git push %s %s in %s", remote, branch, repo_root)

    rc, stdout, stderr = _run(["git", "push", remote, branch], cwd=repo_root)

    if rc == 0:
        msg = stdout or stderr or "Push OK."
        logger.info("git push OK: %s", _mask_url(msg))
        return True, msg

    # "Everything up-to-date" is not an error
    if "Everything up-to-date" in stderr or "Everything up-to-date" in stdout:
        return True, "Everything up-to-date."

    masked_err = _mask_url(stderr)
    logger.warning("git push failed (rc=%d): %s", rc, masked_err)
    return False, masked_err
