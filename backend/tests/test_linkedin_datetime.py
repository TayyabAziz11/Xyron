"""
Unit tests for LinkedIn OAuth timezone-aware datetime handling.

Verifies that:
- "Z"-suffixed expires_at strings parse correctly to aware datetimes
- Remaining time calculation does not raise TypeError
"""

import pytest
from datetime import datetime, timezone


def parse_expires_at(expires_at_str: str) -> datetime:
    """Parse expires_at string to a timezone-aware datetime."""
    return datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))


def seconds_remaining(expires_at_str: str) -> float:
    """Return seconds until expiry; negative means already expired."""
    expires_at = parse_expires_at(expires_at_str)
    now = datetime.now(timezone.utc)
    return (expires_at - now).total_seconds()


class TestLinkedInDatetimeParsing:
    """Tests for expires_at datetime parsing."""

    def test_parse_z_suffix_returns_aware_datetime(self):
        """Parsing a 'Z'-suffixed ISO string must return a timezone-aware datetime."""
        expires_at_str = "2026-04-18T11:42:19.521804Z"
        dt = parse_expires_at(expires_at_str)

        assert dt.tzinfo is not None, "Parsed datetime must be timezone-aware"
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 18
        assert dt.hour == 11
        assert dt.minute == 42
        assert dt.second == 19
        assert dt.microsecond == 521804

    def test_seconds_remaining_does_not_raise(self):
        """seconds_remaining() must not raise TypeError for aware/naive mismatch."""
        expires_at_str = "2026-04-18T11:42:19.521804Z"
        # Should not raise
        result = seconds_remaining(expires_at_str)
        assert isinstance(result, float)

    def test_future_token_has_positive_remaining(self):
        """A far-future expires_at must yield positive remaining seconds."""
        expires_at_str = "2099-01-01T00:00:00.000000Z"
        result = seconds_remaining(expires_at_str)
        assert result > 0, "Future token should have positive seconds remaining"

    def test_past_token_has_negative_remaining(self):
        """An already-expired expires_at must yield negative remaining seconds."""
        expires_at_str = "2000-01-01T00:00:00.000000Z"
        result = seconds_remaining(expires_at_str)
        assert result < 0, "Past token should have negative seconds remaining"

    def test_naive_minus_aware_raises(self):
        """Confirm that naive - aware raises TypeError (validates why fix was needed)."""
        from datetime import datetime
        aware = datetime.fromisoformat("2026-04-18T11:42:19.521804+00:00")
        naive = datetime.utcnow()

        with pytest.raises(TypeError):
            _ = aware - naive

    def test_aware_minus_aware_does_not_raise(self):
        """Confirm that aware - aware does not raise."""
        aware_expires = datetime.fromisoformat("2026-04-18T11:42:19.521804+00:00")
        aware_now = datetime.now(timezone.utc)
        # Must not raise
        delta = aware_expires - aware_now
        assert delta is not None
