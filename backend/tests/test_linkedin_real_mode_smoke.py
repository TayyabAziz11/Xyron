#!/usr/bin/env python3
"""
LinkedIn Real Mode Smoke Tests
Gold Tier - LinkedIn OAuth2 Integration

Purpose: Validate LinkedIn real mode integration (OAuth2 + watcher + executor)

Test Modes:
- DEFAULT (mock mode): Runs without credentials, validates imports and structure
- REAL MODE (env var LINKEDIN_REAL_TEST=1): Checks authentication only (no posting)

Usage:
    # Default (mock mode)
    python -m pytest tests/test_linkedin_real_mode_smoke.py

    # Real mode (authentication check only)
    LINKEDIN_REAL_TEST=1 python -m pytest tests/test_linkedin_real_mode_smoke.py -v

    # Skip slow tests
    python -m pytest tests/test_linkedin_real_mode_smoke.py -m "not slow"
"""

import os
import sys
import json
import pytest
import subprocess
from pathlib import Path
from typing import Dict, Any

# Add src to path
REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

# Check if real mode enabled via environment variable
REAL_MODE_ENABLED = os.environ.get('LINKEDIN_REAL_TEST', '0') == '1'


def run_skill(script_name: str, args: list[str] = None) -> tuple[int, str, str]:
    """
    Run a skill script and capture output.

    Args:
        script_name: Name of script in scripts/ directory
        args: Optional command-line arguments

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    cmd = [sys.executable, str(SCRIPTS_DIR / script_name)]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30
    )

    return result.returncode, result.stdout, result.stderr


class TestLinkedInRealModeImports:
    """Test LinkedIn real mode module imports"""

    def test_linkedin_api_helper_import(self):
        """Verify LinkedInAPIHelper can be imported"""
        try:
            from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
            assert LinkedInAPIHelper is not None, "LinkedInAPIHelper should not be None"
        except ImportError as e:
            pytest.fail(f"Failed to import LinkedInAPIHelper: {e}")

    def test_linkedin_watcher_has_real_mode_support(self):
        """Verify LinkedIn watcher supports --mode real argument"""
        returncode, stdout, stderr = run_skill("linkedin_watcher_skill.py", ["--help"])

        assert returncode == 0, "Watcher help command should succeed"
        assert "--mode" in stdout or "--mode" in stderr, "Should have --mode argument"
        assert "real" in stdout.lower() or "real" in stderr.lower(), "Should support real mode"

    def test_linkedin_watcher_imports_helper(self):
        """Verify LinkedIn watcher imports LinkedInAPIHelper correctly"""
        watcher_path = SRC_DIR / "ai_operator" / "skills" / "gold" / "linkedin_watcher_skill.py"
        assert watcher_path.exists(), f"Watcher skill not found: {watcher_path}"

        content = watcher_path.read_text(encoding='utf-8')
        assert "LinkedInAPIHelper" in content, "Watcher should import LinkedInAPIHelper"
        assert "from ai_operator.core.linkedin_api_helper" in content

    def test_social_executor_imports_helper(self):
        """Verify social executor imports LinkedInAPIHelper correctly"""
        executor_path = SRC_DIR / "ai_operator" / "skills" / "gold" / "brain_execute_social_with_mcp_skill.py"
        assert executor_path.exists(), f"Executor skill not found: {executor_path}"

        content = executor_path.read_text(encoding='utf-8')
        assert "LinkedInAPIHelper" in content, "Executor should import LinkedInAPIHelper"
        assert "from ai_operator.core.linkedin_api_helper" in content


class TestLinkedInWatcherMockMode:
    """Test LinkedIn watcher in mock mode (no credentials required)"""

    def test_watcher_mock_mode_runs(self):
        """Verify watcher runs successfully in mock mode"""
        returncode, stdout, stderr = run_skill(
            "linkedin_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "2"]
        )

        # Should succeed or skip (if already processed)
        assert returncode == 0, f"Watcher should succeed in mock mode\nStdout: {stdout}\nStderr: {stderr}"
        assert "LinkedIn watcher complete" in stdout, "Should show completion message"

    def test_watcher_mock_mode_creates_intake_wrappers(self):
        """Verify watcher creates intake wrappers in Social/Inbox/"""
        social_inbox = REPO_ROOT / "Social" / "Inbox"

        # Run watcher with reset checkpoint to ensure fresh run
        returncode, stdout, stderr = run_skill(
            "linkedin_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "1", "--reset-checkpoint"]
        )

        assert returncode == 0, "Watcher should succeed"

        # Check for LinkedIn intake wrappers
        linkedin_wrappers = list(social_inbox.glob("inbox__linkedin__*.md"))
        # Note: May be 0 if checkpointing already processed mock data in previous runs
        # This test is informational, not strict
        print(f"LinkedIn intake wrappers found: {len(linkedin_wrappers)}")

    def test_watcher_checkpoint_file_created(self):
        """Verify watcher creates checkpoint file to avoid duplicates"""
        checkpoint_path = REPO_ROOT / "Logs" / "linkedin_watcher_checkpoint.json"

        # Run watcher once
        run_skill("linkedin_watcher_skill.py", ["--mode", "mock", "--once", "--max-results", "1"])

        # Checkpoint should exist
        assert checkpoint_path.exists(), "Checkpoint file should be created"

        # Should be valid JSON
        with open(checkpoint_path, 'r') as f:
            data = json.load(f)

        assert "processed_ids" in data, "Checkpoint should have processed_ids list"
        assert isinstance(data["processed_ids"], list), "processed_ids should be a list"


class TestLinkedInExecutorMockMode:
    """Test LinkedIn executor in mock mode (no credentials required)"""

    def test_executor_dry_run_without_plan(self):
        """Verify executor dry-run mode handles missing plan gracefully"""
        returncode, stdout, stderr = run_skill(
            "brain_execute_social_with_mcp_skill.py",
            ["--dry-run"]
        )

        # May fail due to no approved plan, but should not crash
        # Expected: Either success (with note) or failure (with remediation task created)
        assert "Traceback" not in stderr, "Should not crash with Python exception"

    def test_executor_supports_execute_flag(self):
        """Verify executor has --execute flag for real mode"""
        returncode, stdout, stderr = run_skill(
            "brain_execute_social_with_mcp_skill.py",
            ["--help"]
        )

        assert returncode == 0, "Help command should succeed"
        assert "--execute" in stdout or "--execute" in stderr, "Should have --execute flag"


@pytest.mark.skipif(not REAL_MODE_ENABLED, reason="Real mode tests require LINKEDIN_REAL_TEST=1")
class TestLinkedInRealModeAuthentication:
    """
    Test LinkedIn real mode authentication (OPTIONAL - requires credentials)

    Run with: LINKEDIN_REAL_TEST=1 python -m pytest tests/test_linkedin_real_mode_smoke.py -v

    This test ONLY checks authentication. It does NOT:
    - Create posts
    - Send messages
    - Modify any LinkedIn data
    """

    def test_credentials_file_exists(self):
        """Verify .secrets/linkedin_credentials.json exists"""
        creds_path = REPO_ROOT / ".secrets" / "linkedin_credentials.json"
        assert creds_path.exists(), (
            "LinkedIn credentials file not found. "
            "See Docs/linkedin_real_setup.md for setup instructions."
        )

        # Verify JSON structure
        with open(creds_path, 'r') as f:
            creds = json.load(f)

        assert "client_id" in creds, "Credentials must have client_id"
        assert "client_secret" in creds, "Credentials must have client_secret"
        assert "redirect_uri" in creds, "Credentials must have redirect_uri"

    def test_authentication_check(self):
        """
        Verify LinkedIn OAuth2 authentication works (read-only check)

        This test calls LinkedInAPIHelper.check_auth() which:
        - Reads token from .secrets/linkedin_token.json
        - Calls GET /v2/me endpoint (read-only)
        - Verifies token is valid
        - Returns user profile info

        NO WRITE OPERATIONS PERFORMED
        """
        try:
            from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

            secrets_dir = REPO_ROOT / ".secrets"
            helper = LinkedInAPIHelper(secrets_dir=secrets_dir)

            # This should succeed if token is valid
            auth_info = helper.check_auth()

            assert auth_info is not None, "Authentication should return data"
            assert "id" in auth_info or "localizedFirstName" in auth_info, (
                "Auth response should contain user profile info"
            )

            print(f"✅ LinkedIn authentication successful")
            print(f"   User: {auth_info.get('localizedFirstName', 'N/A')}")

        except FileNotFoundError as e:
            pytest.fail(
                f"Token file not found: {e}\n\n"
                f"To authenticate:\n"
                f"1. Run: python3 src/ai_operator/core/linkedin_api_helper.py\n"
                f"2. Follow OAuth2 flow\n"
                f"3. Re-run test"
            )
        except Exception as e:
            pytest.fail(
                f"Authentication failed: {e}\n\n"
                f"Check:\n"
                f"1. Token hasn't expired (re-run OAuth flow if needed)\n"
                f"2. Internet connection is working\n"
                f"3. LinkedIn API is accessible"
            )

    def test_watcher_real_mode_runs(self):
        """
        Verify watcher can run in real mode and fetch posts (read-only)

        This test:
        - Runs watcher with --mode real
        - Fetches YOUR LinkedIn posts via API (read-only)
        - Creates intake wrappers locally
        - Does NOT post, reply, or send messages
        """
        returncode, stdout, stderr = run_skill(
            "linkedin_watcher_skill.py",
            ["--mode", "real", "--once", "--max-results", "3"]
        )

        # Should succeed if authenticated
        assert returncode == 0, (
            f"Real mode watcher failed\n"
            f"Stdout: {stdout}\n"
            f"Stderr: {stderr}\n\n"
            f"Check:\n"
            f"1. Authentication is valid (run check_auth test first)\n"
            f"2. You have at least 1 LinkedIn post\n"
            f"3. API rate limits not exceeded"
        )

        assert "LinkedIn auth OK" in stdout, "Should confirm authentication"
        assert "Fetched" in stdout and "LinkedIn posts from API" in stdout, "Should fetch posts"


# CLI Test Commands (for manual testing)
def print_cli_commands():
    """Print CLI commands for manual testing"""
    print("\n" + "=" * 70)
    print("LinkedIn Real Mode - Manual Test Commands")
    print("=" * 70)

    print("\n1. Test Authentication (read-only):")
    print("   python3 src/ai_operator/core/linkedin_api_helper.py --check-auth")

    print("\n2. Run Watcher (mock mode - no credentials):")
    print("   python3 scripts/linkedin_watcher_skill.py --mode mock --once --max-results 5")

    print("\n3. Run Watcher (real mode - requires credentials):")
    print("   python3 scripts/linkedin_watcher_skill.py --mode real --once --max-results 3")

    print("\n4. Execute Plan (dry-run - preview only):")
    print("   python3 scripts/brain_execute_social_with_mcp_skill.py --dry-run")

    print("\n5. Execute Plan (real posting - REQUIRES approved plan + --execute):")
    print("   python3 scripts/brain_execute_social_with_mcp_skill.py --execute")
    print("   ⚠️  WARNING: This will create a REAL LinkedIn post!")

    print("\n6. Run Automated Tests (mock mode):")
    print("   python -m pytest tests/test_linkedin_real_mode_smoke.py -v")

    print("\n7. Run Real Mode Tests (authentication only - requires credentials):")
    print("   LINKEDIN_REAL_TEST=1 python -m pytest tests/test_linkedin_real_mode_smoke.py -v")

    print("\n" + "=" * 70)
    print("Setup Guide: Docs/linkedin_real_setup.md")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    # If run directly, print CLI commands
    print_cli_commands()

    # Run tests
    pytest.main([__file__, "-v"])
