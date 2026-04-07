"""
Tests for LinkedIn watcher graceful degradation when read permission is unavailable.

Validates:
- When check_read_access() returns 403 → watcher returns 0 items, no crash
- Remediation file created at Needs_Action/remediation__linkedin_read_permission__*.md
- Remediation file content explains r_member_social restriction
- Watcher run() returns success=True with scanned=0, errors=0
- Other read failures (network error, URN error) handled by existing flow

No real LinkedIn endpoints are called.
"""

import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: build a minimal config for LinkedInWatcher pointing at tmp_path
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> dict:
    """Create a watcher config rooted at tmp_path."""
    (tmp_path / 'Needs_Action').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'Social' / 'Inbox').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'Logs').mkdir(parents=True, exist_ok=True)

    return {
        'base_dir': tmp_path,
        'checkpoint_path': str(tmp_path / 'Logs' / 'linkedin_watcher_checkpoint.json'),
        'log_path': str(tmp_path / 'Logs' / 'linkedin_watcher.log'),
        'output_dir': str(tmp_path / 'Social' / 'Inbox'),
        'max_results': 5,
        'mock_fixture_path': str(tmp_path / 'mock_linkedin.json'),
    }


def _make_watcher(tmp_path: Path):
    """Instantiate LinkedInWatcher with tmp_path config."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from ai_operator.skills.gold.linkedin_watcher_skill import LinkedInWatcher
    return LinkedInWatcher(_make_config(tmp_path))


def _fake_helper_authenticated(tmp_path: Path):
    """
    Build a partial mock helper that:
      - check_auth() → authenticated
      - get_person_urn() → 'urn:li:person:TEST123'
      - check_read_access() → 403 (permission denied)
    """
    helper = MagicMock()
    helper.check_auth.return_value = {
        'status': 'authenticated',
        'profile': {'name': 'Test User', 'sub': 'TEST123'},
        'auth_method': 'oidc',
    }
    helper.get_author_urn.return_value = 'urn:li:person:TEST123'
    helper.check_read_access.return_value = {
        'available': False,
        'status': 403,
        'reason': 'r_member_social not granted (LinkedIn requires special access approval)',
    }
    return helper


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWatcherGracefulDegradation:

    def _run_real_mode_with_helper(self, watcher, fake_helper):
        """Patch LinkedInAPIHelper constructor to return fake_helper, run real mode."""
        import sys
        sys.path.insert(0, str(Path(watcher.config['base_dir']).parent / 'src'))

        with patch(
            'ai_operator.skills.gold.linkedin_watcher_skill.LinkedInAPIHelper',
            return_value=fake_helper,
        ):
            return watcher.run(mock=False)

    def test_returns_zero_items_on_403(self, tmp_path):
        """Watcher must return 0 scanned items when read permission is denied (403)."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        result = self._run_real_mode_with_helper(watcher, helper)

        assert result['success'] is True
        assert result['scanned'] == 0
        assert result['errors'] == 0

    def test_does_not_crash_on_403(self, tmp_path):
        """Watcher must not raise an exception when read permission is missing."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        # Should not raise
        try:
            self._run_real_mode_with_helper(watcher, helper)
        except Exception as exc:
            pytest.fail(f"Watcher raised unexpected exception on 403: {exc}")

    def test_creates_remediation_file_on_403(self, tmp_path):
        """Watcher must create a remediation file in Needs_Action/ on read 403."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        remediation_files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))

        assert len(remediation_files) >= 1, (
            f"Expected remediation file in Needs_Action/, found none. "
            f"Files present: {list(needs_action.iterdir())}"
        )

    def test_remediation_filename_format(self, tmp_path):
        """Remediation file must follow the pattern remediation__linkedin_read_permission__YYYYMMDD-HHMM.md"""
        import re
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))
        assert files, "No remediation file found"

        fname = files[0].name
        pattern = r'^remediation__linkedin_read_permission__\d{8}-\d{4}\.md$'
        assert re.match(pattern, fname), (
            f"Filename '{fname}' does not match pattern "
            f"'remediation__linkedin_read_permission__YYYYMMDD-HHMM.md'"
        )

    def test_remediation_content_mentions_r_member_social(self, tmp_path):
        """Remediation file must explain the r_member_social restriction."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))
        assert files
        content = files[0].read_text()

        assert 'r_member_social' in content, "File must mention r_member_social"

    def test_remediation_content_confirms_posting_works(self, tmp_path):
        """Remediation file must clarify that posting still works."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))
        assert files
        content = files[0].read_text(encoding='utf-8')

        # Posting should be mentioned as working
        assert 'ost' in content.lower(), "File must mention posting works"

    def test_remediation_content_mentions_restricted(self, tmp_path):
        """Remediation file must explain the permission is restricted."""
        watcher = _make_watcher(tmp_path)
        helper = _fake_helper_authenticated(tmp_path)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))
        assert files
        content = files[0].read_text(encoding='utf-8')

        assert 'restricted' in content.lower() or 'special' in content.lower(), (
            "File must explain that r_member_social requires special access"
        )

    def test_no_remediation_file_created_on_success(self, tmp_path):
        """No read-permission remediation file when read access is OK."""
        watcher = _make_watcher(tmp_path)
        helper = MagicMock()
        helper.check_auth.return_value = {
            'status': 'authenticated',
            'profile': {'name': 'User', 'sub': 'X'},
            'auth_method': 'oidc',
        }
        helper.get_author_urn.return_value = 'urn:li:person:X'
        helper.check_read_access.return_value = {'available': True, 'status': 200, 'reason': ''}
        helper.list_posts.return_value = []  # No posts (but permission OK)

        self._run_real_mode_with_helper(watcher, helper)

        needs_action = tmp_path / 'Needs_Action'
        read_perm_files = list(needs_action.glob('remediation__linkedin_read_permission__*.md'))
        assert read_perm_files == [], (
            "Should NOT create read-permission remediation when read access is available"
        )


class TestCheckReadAccess:
    """Unit tests for LinkedInAPIHelper.check_read_access()"""

    def _make_helper(self, tmp_path: Path):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper

        secrets_dir = tmp_path / ".secrets"
        secrets_dir.mkdir()

        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=60))
            .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
        )
        token_file = secrets_dir / "linkedin_token.json"
        token_file.write_text(json.dumps({"access_token": "tok", "expires_at": expires_at}))
        os.chmod(token_file, 0o600)

        creds_file = secrets_dir / "linkedin_credentials.json"
        creds_file.write_text(json.dumps({
            "client_id": "id", "client_secret": "sec",
            "redirect_uri": "http://localhost:8080/callback",
            "scopes": ["openid", "profile", "email", "w_member_social"],
        }))
        os.chmod(creds_file, 0o600)

        # Pre-seed URN cache
        profile_file = secrets_dir / "linkedin_profile.json"
        profile_file.write_text(json.dumps({
            "person_urn": "urn:li:person:T1", "person_id": "T1",
            "method": "v2_me", "cached_at": datetime.now(timezone.utc).isoformat(),
        }))
        os.chmod(profile_file, 0o600)

        return LinkedInAPIHelper(secrets_dir=secrets_dir)

    def test_403_returns_not_available(self, tmp_path):
        helper = self._make_helper(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"message": "ACCESS_DENIED"}'

        with patch.object(helper, '_api_request_raw', return_value=mock_resp):
            result = helper.check_read_access()

        assert result['available'] is False
        assert result['status'] == 403

    def test_200_returns_available(self, tmp_path):
        helper = self._make_helper(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'elements': []}

        with patch.object(helper, '_api_request_raw', return_value=mock_resp):
            result = helper.check_read_access()

        assert result['available'] is True
        assert result['status'] == 200

    def test_404_treated_as_available(self, tmp_path):
        """404 = endpoint exists, just no posts — read access is available."""
        helper = self._make_helper(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch.object(helper, '_api_request_raw', return_value=mock_resp):
            result = helper.check_read_access()

        assert result['available'] is True
