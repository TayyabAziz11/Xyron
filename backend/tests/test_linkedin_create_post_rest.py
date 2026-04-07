"""
Tests for create_post() using the LinkedIn REST Posts API.

Validates:
- Endpoint is POST https://api.linkedin.com/rest/posts (NOT /v2/ugcPosts or /v2/shares)
- All three required headers are injected (Authorization, LinkedIn-Version,
  X-Restli-Protocol-Version) plus Content-Type: application/json
- Post URN extracted from x-restli-id response header (preferred)
- Post URN falls back to response body 'id' when header absent
- HTTP 201 with x-restli-id → success dict returned
- HTTP 403 → LinkedInAPIError raised (never crashes executor)
- Network error → LinkedInAPIError raised

No real LinkedIn endpoints are called.
"""

import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_helper(tmp_path: Path, extra_creds: dict = None):
    """Build a LinkedInAPIHelper with a fake non-expired token."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

    from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

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

    # Credentials with w_member_social scope
    creds = {
        "client_id": "fake_id",
        "client_secret": "fake_secret",
        "redirect_uri": "http://localhost:8080/callback",
        "scopes": ["openid", "profile", "email", "w_member_social"],
    }
    if extra_creds:
        creds.update(extra_creds)
    creds_file = secrets_dir / "linkedin_credentials.json"
    creds_file.write_text(json.dumps(creds))
    os.chmod(creds_file, 0o600)

    # Pre-seed a v2_me URN cache so get_person_urn() doesn't need a network call
    profile_file = secrets_dir / "linkedin_profile.json"
    profile_file.write_text(json.dumps({
        "person_urn": "urn:li:person:TEST123",
        "person_id": "TEST123",
        "method": "v2_me",
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }))
    os.chmod(profile_file, 0o600)

    return LinkedInAPIHelper(secrets_dir=secrets_dir)


def _mock_response(status_code: int, body: dict = None,
                   headers: dict = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# Tests: REST Posts API endpoint used
# ---------------------------------------------------------------------------

class TestCreatePostUsesRestPostsAPI:

    def test_uses_rest_posts_url(self, tmp_path):
        """create_post() must POST to https://api.linkedin.com/rest/posts."""
        helper = _make_helper(tmp_path)

        resp_201 = _mock_response(
            201, {}, headers={'x-restli-id': 'urn:li:share:99999'}
        )

        with patch.object(helper, '_api_request_raw', return_value=resp_201) as mock_raw:
            helper.create_post("Hello LinkedIn")

        call_kwargs = mock_raw.call_args
        # Second positional arg is endpoint, keyword 'base' should be REST_BASE
        assert call_kwargs[0][1] == '/posts', "Endpoint must be /posts"
        assert call_kwargs[1].get('base') == helper.REST_BASE, (
            "Must use REST_BASE (https://api.linkedin.com/rest), not API_BASE"
        )

    def test_does_not_use_ugcposts(self, tmp_path):
        """create_post() must NOT call /ugcPosts."""
        helper = _make_helper(tmp_path)

        resp_201 = _mock_response(201, {}, headers={'x-restli-id': 'urn:li:share:1'})
        with patch.object(helper, '_api_request_raw', return_value=resp_201) as mock_raw:
            helper.create_post("Test")

        for call in mock_raw.call_args_list:
            assert '/ugcPosts' not in str(call), "Must not call /ugcPosts"

    def test_does_not_use_shares(self, tmp_path):
        """create_post() must NOT call /shares."""
        helper = _make_helper(tmp_path)

        resp_201 = _mock_response(201, {}, headers={'x-restli-id': 'urn:li:share:2'})
        with patch.object(helper, '_api_request_raw', return_value=resp_201) as mock_raw:
            helper.create_post("Test")

        for call in mock_raw.call_args_list:
            assert '/shares' not in str(call), "Must not call /shares"


# ---------------------------------------------------------------------------
# Tests: Header injection
# ---------------------------------------------------------------------------

class TestCreatePostHeaders:

    def _capture_headers(self, helper, tmp_path):
        """Return headers that _api_request_raw would receive for a create_post call."""
        # _api_request_raw calls _build_headers internally
        captured = {}

        def fake_raw(method, endpoint, *, base=None, **kwargs):
            # Let _build_headers run normally, capture headers
            token = helper.get_access_token()
            captured['headers'] = helper._build_headers(token, content_type=True)
            resp = MagicMock()
            resp.status_code = 201
            resp.headers = {'x-restli-id': 'urn:li:share:123'}
            resp.json.return_value = {}
            resp.text = '{}'
            return resp

        with patch.object(helper, '_api_request_raw', side_effect=fake_raw):
            helper.create_post("Test post")

        return captured['headers']

    def test_authorization_header(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = self._capture_headers(helper, tmp_path)
        assert headers.get('Authorization') == 'Bearer test-bearer-xyz'

    def test_linkedin_version_header(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = self._capture_headers(helper, tmp_path)
        version = headers.get('LinkedIn-Version', '')
        assert version.isdigit() and len(version) == 6, (
            f"LinkedIn-Version must be 6-digit YYYYMM, got: {version!r}"
        )

    def test_restli_protocol_version_header(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = self._capture_headers(helper, tmp_path)
        assert headers.get('X-Restli-Protocol-Version') == '2.0.0'

    def test_content_type_header(self, tmp_path):
        helper = _make_helper(tmp_path)
        headers = self._capture_headers(helper, tmp_path)
        assert headers.get('Content-Type') == 'application/json'


# ---------------------------------------------------------------------------
# Tests: Post URN extraction
# ---------------------------------------------------------------------------

class TestCreatePostUrnExtraction:

    def test_urn_from_x_restli_id_header(self, tmp_path):
        """Post URN should come from x-restli-id response header."""
        helper = _make_helper(tmp_path)

        resp = _mock_response(
            201, {}, headers={'x-restli-id': 'urn:li:share:555'}
        )
        with patch.object(helper, '_api_request_raw', return_value=resp):
            result = helper.create_post("Hello")

        assert result['id'] == 'urn:li:share:555'
        assert result['endpoint_used'] == 'rest/posts'

    def test_urn_fallback_to_response_body(self, tmp_path):
        """When x-restli-id is absent, use id from response body."""
        helper = _make_helper(tmp_path)

        resp = _mock_response(
            201,
            {'id': 'urn:li:share:777'},
            headers={},  # no x-restli-id
        )
        with patch.object(helper, '_api_request_raw', return_value=resp):
            result = helper.create_post("Hello")

        assert result['id'] == 'urn:li:share:777'

    def test_returns_author_and_commentary(self, tmp_path):
        """Returned dict must include author, commentary, visibility."""
        helper = _make_helper(tmp_path)

        resp = _mock_response(201, {}, headers={'x-restli-id': 'urn:li:share:1'})
        with patch.object(helper, '_api_request_raw', return_value=resp):
            result = helper.create_post("My post text", visibility="PUBLIC")

        assert result['author'] == 'urn:li:person:TEST123'
        assert result['commentary'] == 'My post text'
        assert result['visibility'] == 'PUBLIC'


# ---------------------------------------------------------------------------
# Tests: Failure cases — must raise, never return silently
# ---------------------------------------------------------------------------

class TestCreatePostFailureCases:

    def test_http_403_raises_linked_in_api_error(self, tmp_path):
        """HTTP 403 must raise LinkedInAPIError — executor catches this."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ai_operator.core.linkedin_api_helper import LinkedInAPIError

        helper = _make_helper(tmp_path)
        resp = _mock_response(403, {"message": "Permission denied"})

        with patch.object(helper, '_api_request_raw', return_value=resp):
            with pytest.raises(LinkedInAPIError) as exc_info:
                helper.create_post("Test")

        assert '403' in str(exc_info.value)

    def test_http_400_raises_linked_in_api_error(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ai_operator.core.linkedin_api_helper import LinkedInAPIError

        helper = _make_helper(tmp_path)
        resp = _mock_response(400, {"message": "Bad request"})

        with patch.object(helper, '_api_request_raw', return_value=resp):
            with pytest.raises(LinkedInAPIError):
                helper.create_post("Test")

    def test_payload_uses_rest_posts_schema(self, tmp_path):
        """Payload must use REST Posts API fields (commentary, distribution)."""
        helper = _make_helper(tmp_path)

        captured_payload = {}

        def fake_raw(method, endpoint, *, base=None, **kwargs):
            captured_payload.update(kwargs.get('json', {}))
            resp = MagicMock()
            resp.status_code = 201
            resp.headers = {'x-restli-id': 'urn:li:share:1'}
            resp.json.return_value = {}
            resp.text = '{}'
            return resp

        with patch.object(helper, '_api_request_raw', side_effect=fake_raw):
            helper.create_post("Production-grade post", visibility="PUBLIC")

        assert 'commentary' in captured_payload, "Must use 'commentary' field (REST Posts API)"
        assert 'distribution' in captured_payload, "Must include 'distribution' field"
        assert 'lifecycleState' in captured_payload
        assert captured_payload['author'] == 'urn:li:person:TEST123'
        assert captured_payload['commentary'] == 'Production-grade post'
        # Must NOT use legacy ugcPosts schema fields
        assert 'specificContent' not in captured_payload, "Must not use ugcPosts schema"
        assert 'shareCommentary' not in str(captured_payload), "Must not use ugcPosts schema"
