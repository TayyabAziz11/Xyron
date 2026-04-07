"""
Tests for LinkedIn capability diagnostics (--capabilities / show_capabilities()).

Validates:
- Output includes Authenticated, Can Post, Can Read Posts lines
- YES/NO values are correct for each scenario
- show_capabilities() never raises (even when auth fails or read is denied)
- check_can_post() returns correct result based on scopes + URN availability
- check_read_access() is used for Can Read Posts result

No real LinkedIn endpoints are called.
"""

import json
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import StringIO


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_helpers():
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from ai_operator.core.linkedin_api_helper import (
        LinkedInAPIHelper, show_capabilities
    )
    return LinkedInAPIHelper, show_capabilities


def _make_helper(tmp_path: Path, scopes=None):
    LinkedInAPIHelper, _ = _import_helpers()

    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir(exist_ok=True)

    expires_at = (
        (datetime.now(timezone.utc) + timedelta(days=60))
        .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    )
    token_file = secrets_dir / "linkedin_token.json"
    token_file.write_text(json.dumps({"access_token": "tok", "expires_at": expires_at}))
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

    profile_file = secrets_dir / "linkedin_profile.json"
    profile_file.write_text(json.dumps({
        "person_urn": "urn:li:person:CAPS1", "person_id": "CAPS1",
        "method": "v2_me", "cached_at": datetime.now(timezone.utc).isoformat(),
    }))
    os.chmod(profile_file, 0o600)

    return LinkedInAPIHelper(secrets_dir=secrets_dir)


# ---------------------------------------------------------------------------
# Tests: check_can_post()
# ---------------------------------------------------------------------------

class TestCheckCanPost:

    def test_can_post_true_when_scope_present(self, tmp_path):
        helper = _make_helper(tmp_path, scopes=["openid", "profile", "email", "w_member_social"])
        result = helper.check_can_post()
        assert result['available'] is True

    def test_can_post_false_when_scope_missing(self, tmp_path):
        helper = _make_helper(tmp_path, scopes=["openid", "profile", "email"])
        result = helper.check_can_post()
        assert result['available'] is False
        assert 'w_member_social' in result['reason']

    def test_can_post_includes_reason_on_failure(self, tmp_path):
        helper = _make_helper(tmp_path, scopes=["openid"])
        result = helper.check_can_post()
        assert 'reason' in result
        assert result['reason'] != ''

    def test_can_post_true_reason_empty(self, tmp_path):
        helper = _make_helper(tmp_path)
        result = helper.check_can_post()
        assert result['available'] is True
        assert result.get('reason', '') == ''


# ---------------------------------------------------------------------------
# Tests: show_capabilities() output formatting
# ---------------------------------------------------------------------------

class TestShowCapabilitiesOutput:

    def _run_show_capabilities(self, auth_result, can_post_result, read_result):
        """
        Run show_capabilities() with mocked helper methods.
        Returns captured stdout as a string.
        """
        _, show_capabilities = _import_helpers()

        mock_helper = MagicMock()
        mock_helper.check_auth.return_value = auth_result
        mock_helper.check_can_post.return_value = can_post_result
        mock_helper.check_read_access.return_value = read_result

        output = StringIO()
        with patch(
            'ai_operator.core.linkedin_api_helper.LinkedInAPIHelper',
            return_value=mock_helper,
        ):
            with patch('sys.stdout', output):
                show_capabilities()

        return output.getvalue()

    def test_authenticated_yes_when_ok(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={'available': True, 'reason': ''},
            read_result={'available': True, 'status': 200, 'reason': ''},
        )
        assert 'Authenticated' in out
        assert 'YES' in out

    def test_can_post_yes_shown(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={'available': True, 'reason': ''},
            read_result={'available': True, 'status': 200, 'reason': ''},
        )
        assert 'Can Post' in out
        # Should contain YES for Can Post
        lines = [l for l in out.splitlines() if 'Can Post' in l]
        assert lines and 'YES' in lines[0]

    def test_can_read_no_with_reason(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={'available': True, 'reason': ''},
            read_result={
                'available': False, 'status': 403,
                'reason': 'r_member_social not granted',
            },
        )
        assert 'Can Read Posts' in out
        lines = [l for l in out.splitlines() if 'Can Read Posts' in l]
        assert lines
        line = lines[0]
        assert 'NO' in line
        assert 'r_member_social' in line

    def test_not_authenticated_shows_no(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'unauthenticated', 'error': 'no token'},
            can_post_result={'available': False, 'reason': 'not authenticated'},
            read_result={'available': False, 'status': -1, 'reason': 'not authenticated'},
        )
        assert 'Authenticated' in out
        lines = [l for l in out.splitlines() if 'Authenticated' in l]
        assert lines and 'NO' in lines[0]

    def test_does_not_crash_when_not_authenticated(self):
        """show_capabilities() must not raise even when unauthenticated."""
        try:
            self._run_show_capabilities(
                auth_result={'status': 'unauthenticated', 'error': 'no token'},
                can_post_result={'available': False, 'reason': ''},
                read_result={'available': False, 'status': -1, 'reason': ''},
            )
        except Exception as exc:
            pytest.fail(f"show_capabilities() raised unexpectedly: {exc}")

    def test_can_post_no_shown_when_scope_missing(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={
                'available': False,
                'reason': 'w_member_social scope not in configured scopes',
            },
            read_result={'available': True, 'status': 200, 'reason': ''},
        )
        lines = [l for l in out.splitlines() if 'Can Post' in l]
        assert lines and 'NO' in lines[0]

    def test_output_contains_separator(self):
        """Output must include a visual separator (dashes)."""
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={'available': True, 'reason': ''},
            read_result={'available': True, 'status': 200, 'reason': ''},
        )
        assert '---' in out or '═' in out or 'Capabilities' in out

    def test_all_three_capability_lines_present(self):
        out = self._run_show_capabilities(
            auth_result={'status': 'authenticated', 'profile': {}},
            can_post_result={'available': True, 'reason': ''},
            read_result={'available': False, 'status': 403, 'reason': 'r_member_social not granted'},
        )
        assert 'Authenticated' in out
        assert 'Can Post' in out
        assert 'Can Read Posts' in out
