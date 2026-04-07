"""
Unit tests for LinkedIn API header construction.

Validates that _build_headers() always produces the mandatory LinkedIn headers:
  - Authorization: Bearer <token>
  - LinkedIn-Version: YYYYMM
  - X-Restli-Protocol-Version: 2.0.0
  - Content-Type: application/json (only when content_type=True)

No real network calls are made.
"""

import json
import os
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_helper(tmp_path: Path, extra_creds: dict = None):
    """Build a LinkedInAPIHelper pointing at tmp_path with a fake token."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
    from datetime import datetime, timedelta, timezone

    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()

    # Fake non-expired token
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(days=60))
        .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    )
    token = {"access_token": "test-bearer-xyz", "expires_at": expires_at}
    token_file = secrets_dir / "linkedin_token.json"
    token_file.write_text(json.dumps(token))
    os.chmod(token_file, 0o600)

    # Credentials file (required for _load_credentials)
    creds = {
        "client_id": "fake_id",
        "client_secret": "fake_secret",
        "redirect_uri": "http://localhost:8080/callback",
    }
    if extra_creds:
        creds.update(extra_creds)
    creds_file = secrets_dir / "linkedin_credentials.json"
    creds_file.write_text(json.dumps(creds))
    os.chmod(creds_file, 0o600)

    return LinkedInAPIHelper(secrets_dir=secrets_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildHeaders:

    def test_authorization_header_present(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("my-token-abc")
        assert headers["Authorization"] == "Bearer my-token-abc"

    def test_linkedin_version_header_present(self, tmp_path):
        """LinkedIn-Version must always be present — missing it causes NO_VERSION errors."""
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("tok")
        assert "LinkedIn-Version" in headers
        version = headers["LinkedIn-Version"]
        # Must be a 6-digit YYYYMM string
        assert version.isdigit(), f"LinkedIn-Version must be numeric YYYYMM, got: {version}"
        assert len(version) == 6, f"LinkedIn-Version must be 6 chars, got: {version}"

    def test_restli_protocol_version_header_present(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("tok")
        assert headers["X-Restli-Protocol-Version"] == "2.0.0"

    def test_content_type_absent_by_default(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("tok", content_type=False)
        assert "Content-Type" not in headers

    def test_content_type_added_when_requested(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("tok", content_type=True)
        assert headers["Content-Type"] == "application/json"

    def test_default_linkedin_version_constant(self, tmp_path):
        """DEFAULT_LINKEDIN_VERSION must be a 6-digit YYYYMM string."""
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
        version = LinkedInAPIHelper.DEFAULT_LINKEDIN_VERSION
        assert isinstance(version, str)
        assert version.isdigit()
        assert len(version) == 6

    def test_restli_constant_value(self, tmp_path):
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
        assert LinkedInAPIHelper.RESTLI_PROTOCOL_VERSION == "2.0.0"

    def test_linkedin_version_overridable_via_credentials(self, tmp_path):
        """linkedin_version in credentials overrides DEFAULT_LINKEDIN_VERSION."""
        helper = _make_helper(tmp_path, extra_creds={"linkedin_version": "202501"})
        headers = helper._build_headers("tok")
        assert headers["LinkedIn-Version"] == "202501"

    def test_linkedin_version_uses_default_when_not_in_credentials(self, tmp_path):
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper
        helper = _make_helper(tmp_path)  # no linkedin_version in creds
        headers = helper._build_headers("tok")
        assert headers["LinkedIn-Version"] == LinkedInAPIHelper.DEFAULT_LINKEDIN_VERSION

    def test_all_three_required_headers_present_on_get(self, tmp_path):
        """GET request headers must have Authorization, LinkedIn-Version, X-Restli."""
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("get-token")
        for required in ("Authorization", "LinkedIn-Version", "X-Restli-Protocol-Version"):
            assert required in headers, f"Missing required header: {required}"

    def test_all_three_required_headers_present_on_post(self, tmp_path):
        """POST request headers (content_type=True) must also have all three required headers."""
        helper = _make_helper(tmp_path)
        headers = helper._build_headers("post-token", content_type=True)
        for required in ("Authorization", "LinkedIn-Version", "X-Restli-Protocol-Version"):
            assert required in headers, f"Missing required header: {required}"
        assert headers["Content-Type"] == "application/json"
