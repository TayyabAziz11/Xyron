#!/usr/bin/env python3
"""
Personal AI Employee - Gold Tier E2E Smoke Tests

Purpose: Automated smoke tests for Gold Tier features (G-M1 through G-M8)
Mode: Mock only (no real credentials required)
Framework: pytest
Run: python -m pytest tests/test_gold_e2e_smoke.py -v

Test Coverage:
- Watchers (WhatsApp, LinkedIn, Twitter, Odoo)
- MCP registry refresh
- Odoo queries
- CEO briefing generation
- Accounting audit generation
- Ralph loop orchestrator
- Social executor (dry-run only)
- Path resolution and imports
"""

import pytest
import subprocess
import sys
from pathlib import Path
import json

# Get repo root
REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

def run_skill(script_name: str, args: list[str] = None) -> tuple[int, str, str]:
    """
    Run a skill script and capture output.

    Args:
        script_name: Name of script in scripts/ directory
        args: List of command-line arguments

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    cmd = [sys.executable, str(SCRIPTS_DIR / script_name)]
    if args:
        cmd.extend(args)

    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr


class TestWatchers:
    """Test perception layer (watchers)"""

    def test_whatsapp_watcher_mock(self):
        """WhatsApp watcher runs in mock mode"""
        returncode, stdout, stderr = run_skill(
            "whatsapp_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "2"]
        )
        assert returncode == 0, f"WhatsApp watcher failed: {stderr}"
        assert "WhatsApp watcher complete" in stdout or "WhatsApp watcher complete" in stderr

    def test_linkedin_watcher_mock(self):
        """LinkedIn watcher runs in mock mode"""
        returncode, stdout, stderr = run_skill(
            "linkedin_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "2"]
        )
        assert returncode == 0, f"LinkedIn watcher failed: {stderr}"
        assert "LinkedIn watcher complete" in stdout or "LinkedIn watcher complete" in stderr

    def test_twitter_watcher_mock(self):
        """Twitter watcher runs in mock mode"""
        returncode, stdout, stderr = run_skill(
            "twitter_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "2"]
        )
        assert returncode == 0, f"Twitter watcher failed: {stderr}"
        assert "Twitter watcher complete" in stdout or "Twitter watcher complete" in stderr

    def test_odoo_watcher_mock(self):
        """Odoo watcher runs in mock mode"""
        returncode, stdout, stderr = run_skill(
            "odoo_watcher_skill.py",
            ["--mode", "mock", "--once", "--max-results", "2"]
        )
        assert returncode == 0, f"Odoo watcher failed: {stderr}"
        assert "Odoo watcher complete" in stdout or "Odoo watcher complete" in stderr


class TestMCPIntegration:
    """Test MCP server integration"""

    def test_mcp_registry_refresh_mock(self):
        """MCP registry refresh discovers tools"""
        returncode, stdout, stderr = run_skill(
            "brain_mcp_registry_refresh_skill.py",
            ["--mock", "--once"]
        )
        assert returncode == 0, f"MCP registry refresh failed: {stderr}"
        assert "MCP Registry Refresh Complete" in stdout or "MCP Registry Refresh Complete" in stderr
        assert "Servers queried: 4" in stdout or "Servers queried: 4" in stderr


class TestOdooQueries:
    """Test Odoo accounting queries"""

    def test_odoo_revenue_summary_mock(self):
        """Odoo revenue summary query works"""
        returncode, stdout, stderr = run_skill(
            "brain_odoo_query_with_mcp_skill.py",
            ["--operation", "revenue_summary", "--mode", "mock", "--report"]
        )
        assert returncode == 0, f"Odoo query failed: {stderr}"
        assert "Query successful: revenue_summary" in stdout or "Query successful: revenue_summary" in stderr
        assert "total_invoiced" in stdout or "total_invoiced" in stderr

    def test_odoo_query_creates_report(self):
        """Odoo query generates report file"""
        returncode, stdout, stderr = run_skill(
            "brain_odoo_query_with_mcp_skill.py",
            ["--operation", "revenue_summary", "--mode", "mock", "--report"]
        )
        assert returncode == 0
        # Check that report directory exists
        reports_dir = REPO_ROOT / "Business" / "Accounting" / "Reports"
        assert reports_dir.exists(), "Reports directory should exist"
        # Check that at least one report file was created
        report_files = list(reports_dir.glob("odoo_query__revenue_summary__*.md"))
        assert len(report_files) > 0, "Revenue summary report should be created"


class TestExecutiveReporting:
    """Test CEO briefing and accounting audit"""

    def test_ceo_briefing_generation_mock(self):
        """CEO briefing generates successfully"""
        returncode, stdout, stderr = run_skill(
            "brain_generate_weekly_ceo_briefing_skill.py",
            ["--mode", "mock"]
        )
        assert returncode == 0, f"CEO briefing generation failed: {stderr}"
        assert "Weekly CEO briefing generated successfully" in stdout or "Weekly CEO briefing generated successfully" in stderr

    def test_ceo_briefing_creates_file(self):
        """CEO briefing creates report file"""
        returncode, stdout, stderr = run_skill(
            "brain_generate_weekly_ceo_briefing_skill.py",
            ["--mode", "mock"]
        )
        assert returncode == 0
        briefings_dir = REPO_ROOT / "Business" / "Briefings"
        assert briefings_dir.exists(), "Briefings directory should exist"
        briefing_files = list(briefings_dir.glob("CEO_Briefing__*.md"))
        assert len(briefing_files) > 0, "CEO briefing file should be created"

    def test_accounting_audit_generation_mock(self):
        """Accounting audit generates successfully"""
        returncode, stdout, stderr = run_skill(
            "brain_generate_accounting_audit_skill.py",
            ["--mode", "mock"]
        )
        assert returncode == 0, f"Accounting audit generation failed: {stderr}"
        assert "Accounting audit generated successfully" in stdout or "Accounting audit generated successfully" in stderr


class TestRalphLoopOrchestrator:
    """Test autonomous orchestration (Ralph loop)"""

    def test_ralph_loop_dry_run(self):
        """Ralph loop runs in dry-run mode"""
        returncode, stdout, stderr = run_skill(
            "brain_ralph_loop_orchestrator_skill.py",
            ["--dry-run", "--max-iterations", "2", "--max-plans", "2"]
        )
        assert returncode == 0, f"Ralph loop failed: {stderr}"
        assert "Ralph Loop Summary" in stdout or "Ralph Loop Summary" in stderr

    def test_ralph_loop_respects_max_iterations(self):
        """Ralph loop respects max iterations bound"""
        returncode, stdout, stderr = run_skill(
            "brain_ralph_loop_orchestrator_skill.py",
            ["--dry-run", "--max-iterations", "2"]
        )
        assert returncode == 0
        output = stdout + stderr
        assert "Iterations Completed: 2/2" in output, "Should complete exactly 2 iterations"

    def test_ralph_loop_never_executes_in_dry_run(self):
        """Ralph loop never executes actions in dry-run mode"""
        returncode, stdout, stderr = run_skill(
            "brain_ralph_loop_orchestrator_skill.py",
            ["--dry-run", "--max-iterations", "1"]
        )
        assert returncode == 0
        output = stdout + stderr
        assert "DRY-RUN" in output, "Should indicate dry-run mode"
        assert "Plans Created: 0" in output, "Should not create any plans in dry-run"


class TestExecutionLayer:
    """Test action executors (dry-run only)"""

    def test_social_executor_dry_run(self):
        """Social executor runs in dry-run mode"""
        returncode, stdout, stderr = run_skill(
            "brain_execute_social_with_mcp_skill.py",
            ["--dry-run"]
        )
        # Expected behavior: fails because no approved plan exists
        # But it should fail gracefully and create remediation task
        output = stdout + stderr
        assert "DRY-RUN MODE" in output, "Should indicate dry-run mode"
        assert "No real actions will be taken" in output or "No approved plan found" in output

    def test_odoo_executor_dry_run_mock(self):
        """Odoo executor runs in dry-run + mock mode"""
        returncode, stdout, stderr = run_skill(
            "brain_execute_odoo_with_mcp_skill.py",
            ["--dry-run", "--mode", "mock"]
        )
        output = stdout + stderr
        # Should either run dry-run or indicate no approved plan
        assert "DRY-RUN" in output or "No approved plan" in output


class TestPathResolution:
    """Test that skills can find repo root correctly"""

    def test_scripts_are_wrappers(self):
        """Scripts directory contains wrapper files"""
        assert SCRIPTS_DIR.exists(), "scripts/ directory should exist"

        # Check that key wrappers exist
        wrappers = [
            "whatsapp_watcher_skill.py",
            "linkedin_watcher_skill.py",
            "twitter_watcher_skill.py",
            "brain_ralph_loop_orchestrator_skill.py",
            "brain_generate_weekly_ceo_briefing_skill.py"
        ]
        for wrapper in wrappers:
            wrapper_path = SCRIPTS_DIR / wrapper
            assert wrapper_path.exists(), f"{wrapper} should exist in scripts/"

    def test_src_package_structure(self):
        """src/ package structure is correct"""
        src_dir = REPO_ROOT / "src" / "ai_operator"
        assert src_dir.exists(), "src/ai_operator/ should exist"

        # Check core modules
        core_dir = src_dir / "core"
        assert core_dir.exists(), "core/ should exist"
        assert (core_dir / "__init__.py").exists(), "core/__init__.py should exist"
        assert (core_dir / "mcp_helpers.py").exists(), "mcp_helpers.py should exist"

        # Check skills directories
        skills_dir = src_dir / "skills"
        assert skills_dir.exists(), "skills/ should exist"
        assert (skills_dir / "silver").exists(), "skills/silver/ should exist"
        assert (skills_dir / "gold").exists(), "skills/gold/ should exist"


class TestSilverTierRegression:
    """Ensure Silver tier features still work"""

    def test_gmail_watcher_mock(self):
        """Gmail watcher (Silver tier) still works"""
        returncode, stdout, stderr = run_skill(
            "gmail_watcher_skill.py",
            ["--mock", "--once"]
        )
        assert returncode == 0, f"Gmail watcher failed: {stderr}"
        # Should complete without errors

    def test_plan_creation_help(self):
        """Plan creation skill (Silver tier) shows help"""
        returncode, stdout, stderr = run_skill(
            "brain_create_plan_skill.py",
            ["--help"]
        )
        assert returncode == 0, "Plan creation help should work"
        assert "--task" in stdout or "--task" in stderr, "Help should show --task option"


class TestSecurityCompliance:
    """Security validation tests"""

    def test_no_secrets_in_git(self):
        """No secrets committed to git"""
        # Check .gitignore includes .secrets/
        gitignore = REPO_ROOT / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            assert ".secrets" in content or "secrets/" in content, ".secrets should be gitignored"

    def test_pii_redaction_module_exists(self):
        """PII redaction module is available"""
        # Import should succeed
        sys.path.insert(0, str(REPO_ROOT / "src"))
        try:
            from ai_operator.core.mcp_helpers import redact_pii
            # Test basic redaction
            text = "Contact me at test@example.com or +1-234-567-8900"
            redacted = redact_pii(text)
            assert "@" not in redacted or "[REDACTED]" in redacted, "Email should be redacted"
        except ImportError as e:
            pytest.fail(f"Failed to import redact_pii: {e}")


if __name__ == "__main__":
    # Allow running directly: python tests/test_gold_e2e_smoke.py
    pytest.main([__file__, "-v", "--tb=short"])
