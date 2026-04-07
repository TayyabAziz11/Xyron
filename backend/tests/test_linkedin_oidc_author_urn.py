"""
Tests for get_author_urn() OIDC sub fallback + _get_granted_scopes() scope parsing.

Validates:
- get_author_urn() returns disk-cached URN (any method)
- get_author_urn() calls /v2/me and returns urn:li:person:<id>
- get_author_urn() falls back to OIDC sub when /v2/me returns 403
- get_author_urn() raises LinkedInAuthError when all sources fail
- _get_granted_scopes() reads from token['granted_scopes'] first
- _get_granted_scopes() falls back to credentials['scopes']
- check_can_post() returns True with w_member_social (no network call)
- check_can_post() returns False when w_member_social missing (no network call)

No real LinkedIn endpoints are called.
"""

import json
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import():
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from ai_operator.core.linkedin_api_helper import (
        LinkedInAPIHelper, LinkedInAuthError,
    )
    return LinkedInAPIHelper, LinkedInAuthError


def _make_helper(tmp_path: Path, scopes=None, granted_scopes=None) -> "LinkedInAPIHelper":
    LinkedInAPIHelper, _ = _import()

    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir(exist_ok=True)

    expires_at = (
        (datetime.now(timezone.utc) + timedelta(days=60))
        .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    )
    token_data: dict = {"access_token": "tok", "expires_at": expires_at}
    if granted_scopes is not None:
        token_data["granted_scopes"] = granted_scopes

    token_file = secrets_dir / "linkedin_token.json"
    token_file.write_text(json.dumps(token_data))
    os.chmod(token_file, 0o600)

    if scopes is None:
        scopes = ["openid", "profile", "email", "w_member_social"]

    creds_file = secrets_dir / "linkedin_credentials.json"
    creds_file.write_text(json.dumps({
        "client_id": "id", "client_secret": "sec",
        "redirect_uri": "http://localhost:8080/callback",
        "scopes": scopes,
    }))
    os.chmod(creds_file, 0o600)

    return LinkedInAPIHelper(secrets_dir=secrets_dir)


# ---------------------------------------------------------------------------
# Tests: _get_granted_scopes()
# ---------------------------------------------------------------------------

class TestGetGrantedScopes:

    def test_returns_granted_scopes_from_token(self, tmp_path):
        """token['granted_scopes'] takes priority over credentials['scopes']."""
        helper = _make_helper(
            tmp_path,
            scopes=["openid", "profile"],
            granted_scopes=["openid", "profile", "email", "w_member_social"],
        )
        scopes = helper._get_granted_scopes()
        assert "w_member_social" in scopes

    def test_falls_back_to_credentials_scopes(self, tmp_path):
        """When token has no granted_scopes, use credentials scopes."""
        helper = _make_helper(
            tmp_path,
            scopes=["openid", "profile", "email", "w_member_social"],
            granted_scopes=None,  # no granted_scopes in token
        )
        scopes = helper._get_granted_scopes()
        assert "w_member_social" in scopes

    def test_returns_empty_list_when_no_sources(self, tmp_path):
        """Returns [] gracefully when token and credentials both lack scopes."""
        helper = _make_helper(tmp_path, scopes=[], granted_scopes=None)
        scopes = helper._get_granted_scopes()
        assert isinstance(scopes, list)

    def test_reads_raw_scope_field_when_granted_scopes_missing(self, tmp_path):
        """Falls back to raw token['scope'] string when granted_scopes is absent."""
        LinkedInAPIHelper, _ = _import()

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir(exist_ok=True)

        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        # Token has raw 'scope' field but NO 'granted_scopes' (pre-parse token)
        token_data = {
            "access_token": "tok",
            "expires_at": expires_at,
            "scope": "email,openid,profile,w_member_social",
        }
        token_file = secrets_dir / "linkedin_token.json"
        token_file.write_text(json.dumps(token_data))
        os.chmod(token_file, 0o600)

        # Credentials file has NO 'scopes' field
        creds_file = secrets_dir / "linkedin_credentials.json"
        creds_file.write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
        }))
        os.chmod(creds_file, 0o600)

        helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
        scopes = helper._get_granted_scopes()

        assert "w_member_social" in scopes, (
            f"Expected w_member_social in scopes parsed from raw 'scope' field, got: {scopes}"
        )


# ---------------------------------------------------------------------------
# Tests: check_can_post() — scope-only check, no network
# ---------------------------------------------------------------------------

class TestCheckCanPost:

    def test_true_when_w_member_social_in_granted_scopes(self, tmp_path):
        helper = _make_helper(
            tmp_path,
            granted_scopes=["openid", "profile", "w_member_social"],
        )
        result = helper.check_can_post()
        assert result["available"] is True
        assert result.get("reason", "") == ""

    def test_true_when_w_member_social_in_credentials_scopes(self, tmp_path):
        helper = _make_helper(
            tmp_path,
            scopes=["openid", "profile", "email", "w_member_social"],
            granted_scopes=None,
        )
        result = helper.check_can_post()
        assert result["available"] is True

    def test_false_when_scope_missing(self, tmp_path):
        helper = _make_helper(
            tmp_path,
            scopes=["openid", "profile", "email"],
            granted_scopes=None,
        )
        result = helper.check_can_post()
        assert result["available"] is False
        assert "w_member_social" in result.get("reason", "")

    def test_no_network_call_made(self, tmp_path):
        """check_can_post() must never make a network request."""
        helper = _make_helper(tmp_path)
        with patch("requests.get") as mock_get, patch("requests.request") as mock_req:
            helper.check_can_post()
            mock_get.assert_not_called()
            mock_req.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: get_author_urn()
# ---------------------------------------------------------------------------

class TestGetAuthorUrn:

    def test_returns_cached_urn_from_disk(self, tmp_path):
        """Disk-cached URN (any method) is returned immediately."""
        LinkedInAPIHelper, _ = _import()
        helper = _make_helper(tmp_path)

        # Pre-seed disk cache as oidc_sub (not v2_me)
        profile_file = helper.secrets_dir / "linkedin_profile.json"
        profile_file.write_text(json.dumps({
            "person_urn": "urn:li:person:CACHED123",
            "person_id": "CACHED123",
            "method": "oidc_sub",
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }))
        os.chmod(profile_file, 0o600)

        urn = helper.get_author_urn()
        assert urn == "urn:li:person:CACHED123"

    def test_falls_back_to_oidc_sub_when_v2_me_fails(self, tmp_path):
        """When /v2/me returns None, OIDC sub is used."""
        helper = _make_helper(tmp_path)

        with patch.object(helper, "get_person_id_v2_me", return_value=None):
            with patch.object(helper, "check_auth", return_value={
                "status": "authenticated",
                "profile": {"sub": "OIDCSUB99"},
                "auth_method": "oidc",
            }):
                urn = helper.get_author_urn()

        assert urn == "urn:li:person:OIDCSUB99"

    def test_v2_me_success_returns_numeric_urn(self, tmp_path):
        """When /v2/me returns a member_id, that takes priority over OIDC sub."""
        helper = _make_helper(tmp_path)

        with patch.object(helper, "get_person_id_v2_me", return_value="12345678"):
            urn = helper.get_author_urn()

        assert urn == "urn:li:person:12345678"

    def test_raises_when_all_sources_fail(self, tmp_path):
        """get_author_urn() raises LinkedInAuthError when no URN can be resolved."""
        _, LinkedInAuthError = _import()
        helper = _make_helper(tmp_path)

        with patch.object(helper, "get_person_id_v2_me", return_value=None):
            with patch.object(helper, "check_auth", return_value={
                "status": "authenticated",
                "profile": {},  # no 'sub'
                "auth_method": "oidc",
            }):
                with pytest.raises(LinkedInAuthError):
                    helper.get_author_urn()

    def test_oidc_sub_caches_to_disk(self, tmp_path):
        """After using OIDC sub fallback, profile is cached to disk."""
        helper = _make_helper(tmp_path)

        with patch.object(helper, "get_person_id_v2_me", return_value=None):
            with patch.object(helper, "check_auth", return_value={
                "status": "authenticated",
                "profile": {"sub": "SUBXYZ"},
                "auth_method": "oidc",
            }):
                helper.get_author_urn()

        profile_file = helper.secrets_dir / "linkedin_profile.json"
        assert profile_file.exists()
        data = json.loads(profile_file.read_text())
        assert data["person_urn"] == "urn:li:person:SUBXYZ"
        assert data["method"] == "oidc_sub"

    def test_second_call_uses_memory_cache(self, tmp_path):
        """Second call to get_author_urn() must not re-invoke get_person_id_v2_me."""
        helper = _make_helper(tmp_path)

        with patch.object(helper, "get_person_id_v2_me", return_value=None) as mock_v2me:
            with patch.object(helper, "check_auth", return_value={
                "status": "authenticated",
                "profile": {"sub": "SUBCACHE"},
                "auth_method": "oidc",
            }):
                helper.get_author_urn()
                helper.get_author_urn()  # second call

        # get_person_id_v2_me should only be called once
        assert mock_v2me.call_count == 1


# ---------------------------------------------------------------------------
# Tests: granted_scopes parsed during exchange_code_for_token()
# ---------------------------------------------------------------------------

class TestGrantedScopesParsing:

    def test_scope_field_parsed_into_granted_scopes(self, tmp_path):
        """exchange_code_for_token() parses scope string into granted_scopes list."""
        LinkedInAPIHelper, _ = _import()
        helper = _make_helper(tmp_path)

        token_response = {
            "access_token": "newtoken",
            "expires_in": 5184000,
            "scope": "openid profile email w_member_social",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = token_response
        mock_resp.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_resp):
            result = helper.exchange_code_for_token("authcode123")

        assert "granted_scopes" in result
        assert "w_member_social" in result["granted_scopes"]

    def test_comma_separated_scope_parsed(self, tmp_path):
        """Comma-separated scope field is also handled."""
        LinkedInAPIHelper, _ = _import()
        helper = _make_helper(tmp_path)

        token_response = {
            "access_token": "tok2",
            "expires_in": 5184000,
            "scope": "openid,profile,email,w_member_social",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = token_response
        mock_resp.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_resp):
            result = helper.exchange_code_for_token("authcode456")

        assert "w_member_social" in result["granted_scopes"]


# ---------------------------------------------------------------------------
# Tests: _build_headers() version override — "api_version" and "linkedin_version" keys
# ---------------------------------------------------------------------------

class TestBuildHeadersVersionOverride:

    def test_linkedin_version_key_overrides_default(self, tmp_path):
        """'linkedin_version' in credentials overrides DEFAULT_LINKEDIN_VERSION."""
        LinkedInAPIHelper, _ = _import()

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()

        from datetime import timedelta
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        (secrets_dir / "linkedin_token.json").write_text(
            json.dumps({"access_token": "tok", "expires_at": expires_at})
        )
        os.chmod(secrets_dir / "linkedin_token.json", 0o600)

        (secrets_dir / "linkedin_credentials.json").write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "w_member_social"],
            "linkedin_version": "202401",
        }))
        os.chmod(secrets_dir / "linkedin_credentials.json", 0o600)

        helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
        headers = helper._build_headers("testtoken")
        assert headers["LinkedIn-Version"] == "202401"

    def test_api_version_key_overrides_default(self, tmp_path):
        """'api_version' in credentials is an accepted alias for linkedin_version."""
        LinkedInAPIHelper, _ = _import()

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()

        from datetime import timedelta
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        (secrets_dir / "linkedin_token.json").write_text(
            json.dumps({"access_token": "tok", "expires_at": expires_at})
        )
        os.chmod(secrets_dir / "linkedin_token.json", 0o600)

        (secrets_dir / "linkedin_credentials.json").write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "w_member_social"],
            "api_version": "202401",
        }))
        os.chmod(secrets_dir / "linkedin_credentials.json", 0o600)

        helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
        headers = helper._build_headers("testtoken")
        assert headers["LinkedIn-Version"] == "202401"

    def test_linkedin_version_takes_priority_over_api_version(self, tmp_path):
        """When both keys exist, 'linkedin_version' wins."""
        LinkedInAPIHelper, _ = _import()

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()

        from datetime import timedelta
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        (secrets_dir / "linkedin_token.json").write_text(
            json.dumps({"access_token": "tok", "expires_at": expires_at})
        )
        os.chmod(secrets_dir / "linkedin_token.json", 0o600)

        (secrets_dir / "linkedin_credentials.json").write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "w_member_social"],
            "linkedin_version": "202502",
            "api_version": "202401",   # linkedin_version should win
        }))
        os.chmod(secrets_dir / "linkedin_credentials.json", 0o600)

        helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
        headers = helper._build_headers("testtoken")
        assert headers["LinkedIn-Version"] == "202502"


# ---------------------------------------------------------------------------
# Tests: _normalize_linkedin_version() — YYYYMMDD → YYYYMM truncation
# ---------------------------------------------------------------------------

class TestNormalizeLinkedinVersion:

    def test_yyyymmdd_truncated_to_yyyymm(self):
        """8-digit YYYYMMDD is silently truncated to 6-digit YYYYMM."""
        LinkedInAPIHelper, _ = _import()
        assert LinkedInAPIHelper._normalize_linkedin_version("20250201") == "202502"

    def test_yyyymm_unchanged(self):
        """6-digit YYYYMM is returned as-is."""
        LinkedInAPIHelper, _ = _import()
        assert LinkedInAPIHelper._normalize_linkedin_version("202502") == "202502"

    def test_yyyymm_jan_unchanged(self):
        """6-digit YYYYMM for January is returned as-is."""
        LinkedInAPIHelper, _ = _import()
        assert LinkedInAPIHelper._normalize_linkedin_version("202501") == "202501"

    def test_build_headers_normalizes_yyyymmdd_api_version(self, tmp_path):
        """api_version: '20250201' in credentials is normalised to '202502' in header."""
        LinkedInAPIHelper, _ = _import()

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()

        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        (secrets_dir / "linkedin_token.json").write_text(
            json.dumps({"access_token": "tok", "expires_at": expires_at})
        )
        os.chmod(secrets_dir / "linkedin_token.json", 0o600)

        # Simulates user who set api_version to YYYYMMDD format
        (secrets_dir / "linkedin_credentials.json").write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "w_member_social"],
            "api_version": "20250201",   # 8-digit — should be normalised to 202502
        }))
        os.chmod(secrets_dir / "linkedin_credentials.json", 0o600)

        helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
        headers = helper._build_headers("testtoken")
        assert headers["LinkedIn-Version"] == "202502", (
            f"Expected normalised '202502', got: {headers['LinkedIn-Version']}"
        )
