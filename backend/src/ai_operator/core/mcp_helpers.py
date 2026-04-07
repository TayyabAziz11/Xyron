#!/usr/bin/env python3
"""
Personal AI Employee - MCP Helpers
Gold Tier - G-M2: MCP Registry + Reliability Core

Purpose: Utility functions for MCP operations (PII redaction, rate limiting, disk checks)
Tier: Gold
Module ID: G-M2

Functions:
- redact_pii(text): Redact emails and phone numbers from text
- rate_limit_and_backoff(func, max_retries): Decorator for HTTP 429 handling
- check_disk_space(min_free_mb): Verify sufficient disk space
"""

import re
import time
import shutil
import logging
from functools import wraps
from typing import Callable, Any
from pathlib import Path


# Configure logging
logger = logging.getLogger(__name__)


def get_repo_root() -> Path:
    """
    Get the repository root directory.

    This function finds the repo root by locating the directory containing
    system_log.md, which is a required file in the root of the repo.

    Returns:
        Path to repository root directory
    """
    # Start from this file and go up until we find system_log.md
    current = Path(__file__).parent
    while current != current.parent:
        if (current / 'system_log.md').exists():
            return current
        current = current.parent

    # Fallback: assume we're in src/ai_operator/core and go up 3 levels
    return Path(__file__).parent.parent.parent.parent


def redact_pii(text: str) -> str:
    """
    Redact personally identifiable information (PII) from text.

    Redacts:
    - Email addresses → [EMAIL_REDACTED]
    - Phone numbers → [PHONE_REDACTED]

    Args:
        text: Input text that may contain PII

    Returns:
        Text with PII redacted

    Examples:
        >>> redact_pii("Contact john@example.com")
        'Contact [EMAIL_REDACTED]'

        >>> redact_pii("Call +1-555-123-4567")
        'Call [PHONE_REDACTED]'

        >>> redact_pii("Email: test@test.com or call (555) 123-4567")
        'Email: [EMAIL_REDACTED] or call [PHONE_REDACTED]'
    """
    if not text:
        return text

    # Email pattern: captures most common email formats
    # Pattern: username@domain.tld
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    redacted_text = re.sub(email_pattern, '[EMAIL_REDACTED]', text)

    # Phone number pattern: handles various formats
    # Supports: +1-555-123-4567, (555) 123-4567, 555.123.4567, etc.
    # Pattern matches: optional +, optional country code, area code (with or without parens), main number
    phone_pattern = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    redacted_text = re.sub(phone_pattern, '[PHONE_REDACTED]', redacted_text)

    return redacted_text


def rate_limit_and_backoff(max_retries: int = 4) -> Callable:
    """
    Decorator for HTTP 429 rate limit handling with exponential backoff.

    Implements exponential backoff strategy:
    - Retry 1: 1 second delay
    - Retry 2: 2 seconds delay
    - Retry 3: 4 seconds delay
    - Retry 4: 8 seconds delay

    After max_retries, raises the original exception.

    Args:
        max_retries: Maximum number of retry attempts (default: 4)

    Returns:
        Decorator function

    Raises:
        Exception: Original exception after max retries exceeded

    Example:
        @rate_limit_and_backoff(max_retries=4)
        def call_api():
            # API call that may return 429
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            retry_count = 0

            while retry_count <= max_retries:
                try:
                    return func(*args, **kwargs)

                except Exception as e:
                    # Check if it's a rate limit error (HTTP 429)
                    # Handle both requests library and generic exceptions
                    is_rate_limit = False

                    if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                        is_rate_limit = e.response.status_code == 429
                    elif '429' in str(e) or 'rate limit' in str(e).lower():
                        is_rate_limit = True

                    if not is_rate_limit or retry_count >= max_retries:
                        logger.error(f"Request failed after {retry_count} retries: {e}")
                        raise

                    # Calculate exponential backoff delay
                    delay = 2 ** retry_count  # 1s, 2s, 4s, 8s
                    retry_count += 1

                    logger.warning(
                        f"Rate limit hit (HTTP 429). "
                        f"Retry {retry_count}/{max_retries} after {delay}s delay"
                    )

                    time.sleep(delay)

            # Should not reach here, but just in case
            return func(*args, **kwargs)

        return wrapper
    return decorator


def check_disk_space(min_free_mb: int = 100, path: str = '.') -> bool:
    """
    Check if sufficient disk space is available.

    Args:
        min_free_mb: Minimum required free space in megabytes (default: 100 MB)
        path: Path to check disk space for (default: current directory)

    Returns:
        True if free space >= min_free_mb, False otherwise

    Examples:
        >>> check_disk_space(100)  # Check if at least 100 MB free
        True

        >>> check_disk_space(1000000)  # Check if 1 TB free
        False
    """
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free / (1024 * 1024)  # Convert bytes to MB

        if free_mb < min_free_mb:
            logger.warning(
                f"Low disk space: {free_mb:.2f} MB free "
                f"(minimum required: {min_free_mb} MB)"
            )
            return False

        logger.debug(f"Disk space check passed: {free_mb:.2f} MB free")
        return True

    except Exception as e:
        logger.error(f"Failed to check disk space: {e}")
        return False


if __name__ == "__main__":
    # Quick smoke tests
    logging.basicConfig(level=logging.INFO)

    print("=== PII Redaction Tests ===")
    test_cases = [
        "Contact john@example.com",
        "Call +1-555-123-4567",
        "Email: test@test.com or call (555) 123-4567",
        "Multiple: alice@test.com, bob@example.org, +1-800-555-0199"
    ]

    for test in test_cases:
        redacted = redact_pii(test)
        print(f"Original: {test}")
        print(f"Redacted: {redacted}")
        print()

    print("=== Disk Space Check ===")
    result = check_disk_space(100)
    print(f"Disk space check (100 MB): {result}")

    print("\n✅ mcp_helpers.py smoke tests complete")
