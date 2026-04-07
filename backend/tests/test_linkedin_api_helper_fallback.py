"""
Tests for LinkedInAPIHelper fallback logic.

Covers:
- get_person_id_v2_me(): /v2/me 200 → numeric member id; 403 → None
- get_person_urn() strict mode:
    - /v2/me 200 → urn:li:person:<numeric>    (method=v2_me)
    - /v2/me 403 → raises LinkedInAuthError   (NO oidc_sub fallback)
    - /v2/me 401 → raises LinkedInAuthError
    - cache method=v2_me → trusted (no network)
    - cache method=oidc_sub → bypassed, /v2/me called fresh
- list_posts():
    - ugcPosts 200 → normalized posts returned
    - ugcPosts 404 → shares 200 → fallback succeeds
    - ugcPosts 403 → shares 200 → fallback succeeds
    - both fail → empty list returned
    - ugcPosts 200 empty → warning logged
- _normalize_post():
    - ugcPosts raw dict normalizes correctly
    - shares raw dict normalizes correctly
"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers to build the helper under test with a fake token
# ---------------------------------------------------------------------------

def _make_helper(tmp_path: Path):
    """Create a LinkedInAPIHelper pointing at tmp_path/.secrets with a valid fake token."""
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()

    # Write a fake token that is not expired
    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc) + timedelta(days=60)).strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    token = {
        "access_token": "fake-token-abc123",
        "expires_at": expires_at,
    }
    token_file = secrets_dir / "linkedin_token.json"
    token_file.write_text(json.dumps(token))
    os.chmod(token_file, 0o600)

    helper = LinkedInAPIHelper(secrets_dir=secrets_dir)
    return helper


def _mock_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# A: get_person_id_v2_me
# ---------------------------------------------------------------------------

class TestGetPersonIdV2Me:

    def test_v2_me_200_returns_member_id(self, tmp_path):
        helper = _make_helper(tmp_path)

        me_body = {"id": "ABC123", "localizedFirstName": "Jane", "localizedLastName": "Doe"}

        with patch("requests.get", return_value=_mock_response(200, me_body)):
            result = helper.get_person_id_v2_me()

        assert result == "ABC123"

    def test_v2_me_403_returns_none(self, tmp_path):
        helper = _make_helper(tmp_path)

        with patch("requests.get", return_value=_mock_response(403, {"message": "ACCESS_DENIED"})):
            result = helper.get_person_id_v2_me()

        assert result is None

    def test_v2_me_200_caches_profile_json(self, tmp_path):
        helper = _make_helper(tmp_path)
        me_body = {"id": "NUM42", "localizedFirstName": "Test"}

        with patch("requests.get", return_value=_mock_response(200, me_body)):
            helper.get_person_id_v2_me()

        profile_file = tmp_path / ".secrets" / "linkedin_profile.json"
        assert profile_file.exists()
        cached = json.loads(profile_file.read_text())
        assert cached["person_urn"] == "urn:li:person:NUM42"
        assert cached["method"] == "v2_me"
        assert cached["raw_me"] == me_body


# ---------------------------------------------------------------------------
# B: get_person_urn
# ---------------------------------------------------------------------------

class TestGetPersonUrn:

    def test_v2_me_200_produces_numeric_urn(self, tmp_path):
        helper = _make_helper(tmp_path)
        me_body = {"id": "NUM99"}

        with patch("requests.get", return_value=_mock_response(200, me_body)):
            urn = helper.get_person_urn()

        assert urn == "urn:li:person:NUM99"

    def test_v2_me_403_raises_auth_error(self, tmp_path):
        """
        Strict mode: /v2/me 403 must raise LinkedInAuthError, NOT fall back to OIDC sub.
        OIDC sub is unreliable for sharing/UGC endpoints.
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ai_operator.core.linkedin_api_helper import LinkedInAuthError

        helper = _make_helper(tmp_path)
        me_403 = _mock_response(403, {"message": "ACCESS_DENIED"})

        with patch("requests.get", return_value=me_403):
            with pytest.raises(LinkedInAuthError) as exc_info:
                helper.get_person_urn()

        assert "--init" in str(exc_info.value)  # actionable message present

    def test_v2_me_401_raises_auth_error(self, tmp_path):
        """401 from /v2/me must also raise LinkedInAuthError."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ai_operator.core.linkedin_api_helper import LinkedInAuthError

        helper = _make_helper(tmp_path)
        me_401 = _mock_response(401, {"message": "Unauthorized"})

        with patch("requests.get", return_value=me_401):
            with pytest.raises(LinkedInAuthError):
                helper.get_person_urn()

    def test_cached_v2_me_urn_returned_without_network(self, tmp_path):
        """Cache with method=v2_me is trusted — no network call needed."""
        helper = _make_helper(tmp_path)

        profile_file = tmp_path / ".secrets" / "linkedin_profile.json"
        profile_file.write_text(json.dumps({
            "person_urn": "urn:li:person:CACHED_NUM",
            "method": "v2_me",
        }))

        with patch("requests.get", side_effect=Exception("should not be called")):
            urn = helper.get_person_urn()

        assert urn == "urn:li:person:CACHED_NUM"

    def test_cached_oidc_sub_is_bypassed(self, tmp_path):
        """Cache with method=oidc_sub must be bypassed; /v2/me is called fresh."""
        helper = _make_helper(tmp_path)

        profile_file = tmp_path / ".secrets" / "linkedin_profile.json"
        profile_file.write_text(json.dumps({
            "person_urn": "urn:li:person:OLD_OIDC_SUB",
            "method": "oidc_sub",
        }))

        me_200 = _mock_response(200, {"id": "FRESH_NUM_ID"})
        with patch("requests.get", return_value=me_200):
            urn = helper.get_person_urn()

        # Must return the fresh /v2/me result, not the stale oidc_sub URN
        assert urn == "urn:li:person:FRESH_NUM_ID"


# ---------------------------------------------------------------------------
# C: _normalize_post
# ---------------------------------------------------------------------------

class TestNormalizePost:

    def test_ugcposts_normalization(self, tmp_path):
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

        raw = {
            "id": "urn:li:ugcPost:111",
            "author": "urn:li:person:ABC",
            "created": {"time": 1700000000000},
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": "Hello world"},
                    "shareMediaCategory": "NONE",
                }
            },
        }
        result = LinkedInAPIHelper._normalize_post(raw, "ugcPosts")
        assert result["id"] == "urn:li:ugcPost:111"
        assert result["text"] == "Hello world"
        assert result["author_urn"] == "urn:li:person:ABC"
        assert result["created_ms"] == 1700000000000
        assert result["source_endpoint"] == "ugcPosts"

    def test_shares_normalization(self, tmp_path):
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

        raw = {
            "id": "urn:li:share:222",
            "owner": "urn:li:person:DEF",
            "created": {"time": 1700000001000},
            "text": {"text": "Share content"},
        }
        result = LinkedInAPIHelper._normalize_post(raw, "shares")
        assert result["id"] == "urn:li:share:222"
        assert result["text"] == "Share content"
        assert result["author_urn"] == "urn:li:person:DEF"
        assert result["source_endpoint"] == "shares"


# ---------------------------------------------------------------------------
# D: list_posts fallback
# ---------------------------------------------------------------------------

class TestListPostsFallback:

    def _ugc_200(self):
        elements = [
            {
                "id": "urn:li:ugcPost:1",
                "author": "urn:li:person:X",
                "created": {"time": 1700000000000},
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": "UGC post text"},
                        "shareMediaCategory": "NONE",
                    }
                },
            }
        ]
        return _mock_response(200, {"elements": elements})

    def _shares_200(self):
        elements = [
            {
                "id": "urn:li:share:9",
                "owner": "urn:li:person:X",
                "created": {"time": 1700000002000},
                "text": {"text": "Share text"},
            }
        ]
        return _mock_response(200, {"elements": elements})

    def test_ugcposts_200_returns_normalized_posts(self, tmp_path):
        helper = _make_helper(tmp_path)

        with patch.object(helper, "_api_request_raw", return_value=self._ugc_200()):
            posts = helper.list_posts("urn:li:person:X", count=3)

        assert len(posts) == 1
        assert posts[0]["source_endpoint"] == "ugcPosts"
        assert posts[0]["text"] == "UGC post text"

    def test_ugcposts_404_falls_back_to_shares_200(self, tmp_path):
        helper = _make_helper(tmp_path)

        ugc_404 = _mock_response(404, {"message": "Not Found"})
        shares_200 = self._shares_200()

        with patch.object(helper, "_api_request_raw", side_effect=[ugc_404, shares_200]):
            posts = helper.list_posts("urn:li:person:X", count=3)

        assert len(posts) == 1
        assert posts[0]["source_endpoint"] == "shares"
        assert posts[0]["text"] == "Share text"

    def test_ugcposts_403_falls_back_to_shares_200(self, tmp_path):
        helper = _make_helper(tmp_path)

        ugc_403 = _mock_response(403, {"message": "Forbidden"})
        shares_200 = self._shares_200()

        with patch.object(helper, "_api_request_raw", side_effect=[ugc_403, shares_200]):
            posts = helper.list_posts("urn:li:person:X", count=3)

        assert len(posts) == 1
        assert posts[0]["source_endpoint"] == "shares"

    def test_both_endpoints_fail_returns_empty(self, tmp_path):
        helper = _make_helper(tmp_path)

        ugc_404 = _mock_response(404, {"message": "Not Found"})
        shares_404 = _mock_response(404, {"message": "Not Found"})

        with patch.object(helper, "_api_request_raw", side_effect=[ugc_404, shares_404]):
            posts = helper.list_posts("urn:li:person:X", count=3)

        assert posts == []

    def test_ugcposts_200_empty_elements_logs_warning(self, tmp_path, caplog):
        helper = _make_helper(tmp_path)
        empty_resp = _mock_response(200, {"elements": []})

        import logging
        with patch.object(helper, "_api_request_raw", return_value=empty_resp):
            with caplog.at_level(logging.WARNING, logger="ai_operator.core.linkedin_api_helper"):
                posts = helper.list_posts("urn:li:person:X", count=3)

        assert posts == []
        assert any("0 posts" in r.message for r in caplog.records)
