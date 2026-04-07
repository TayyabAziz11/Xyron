#!/usr/bin/env python3
"""
Personal AI Employee - MCP Helpers Tests
Gold Tier - G-M2: MCP Registry + Reliability Core

Purpose: Unit tests for mcp_helpers.py utilities
"""

import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_helpers import redact_pii, check_disk_space


def test_redact_pii_email():
    """Test email redaction."""
    test_cases = [
        ("Contact john@example.com", "Contact [EMAIL_REDACTED]"),
        ("Email test@test.org for info", "Email [EMAIL_REDACTED] for info"),
        ("alice.bob@company.co.uk is the address", "[EMAIL_REDACTED] is the address"),
    ]

    for input_text, expected in test_cases:
        result = redact_pii(input_text)
        assert result == expected, f"Failed: {input_text} -> {result} (expected: {expected})"

    print("✅ test_redact_pii_email passed")


def test_redact_pii_phone():
    """Test phone number redaction."""
    test_cases = [
        ("Call +1-555-123-4567", "Call [PHONE_REDACTED]"),
        ("Phone: (555) 123-4567", "Phone: [PHONE_REDACTED]"),
        ("Dial 555.123.4567 now", "Dial [PHONE_REDACTED] now"),
    ]

    for input_text, expected in test_cases:
        result = redact_pii(input_text)
        assert result == expected, f"Failed: {input_text} -> {result} (expected: {expected})"

    print("✅ test_redact_pii_phone passed")


def test_redact_pii_multiple():
    """Test multiple PII instances in one string."""
    input_text = "Email: test@test.com or call (555) 123-4567 or alice@example.org"
    result = redact_pii(input_text)

    # Should have 2 email redactions and 1 phone redaction
    assert result.count('[EMAIL_REDACTED]') == 2, "Should redact 2 emails"
    assert result.count('[PHONE_REDACTED]') == 1, "Should redact 1 phone"
    assert 'test@test.com' not in result, "Original email should be redacted"
    assert 'alice@example.org' not in result, "Second email should be redacted"
    assert '555' not in result, "Phone number should be redacted"

    print("✅ test_redact_pii_multiple passed")


def test_check_disk_space_sufficient():
    """Test disk space check with sufficient space (should always pass on real system)."""
    # Check for at least 10 MB (should always pass unless disk is critically full)
    result = check_disk_space(min_free_mb=10)
    assert result is True, "Should return True when sufficient space"

    print("✅ test_check_disk_space_sufficient passed")


def test_check_disk_space_edge_case():
    """Test disk space check with unrealistic requirement."""
    # Request 1 petabyte (1,000,000 GB) - should fail on normal systems
    result = check_disk_space(min_free_mb=1_000_000_000)
    assert result is False, "Should return False when insufficient space"

    print("✅ test_check_disk_space_edge_case passed")


def run_all_tests():
    """Run all mcp_helpers tests."""
    print("\n=== Running MCP Helpers Tests ===\n")

    try:
        test_redact_pii_email()
        test_redact_pii_phone()
        test_redact_pii_multiple()
        test_check_disk_space_sufficient()
        test_check_disk_space_edge_case()

        print("\n✅ All tests passed!\n")
        return 0

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
        return 1

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
