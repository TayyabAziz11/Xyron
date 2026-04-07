"""
tests/test_instagram_helper.py
Unit tests for InstagramAPIHelper — covers credential loading, error classes,
request retry logic, and the two-step post creation (container → publish).
Uses mock patching only; no real HTTP calls.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Bootstrap path so the helper can be imported without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_operator.core.instagram_api_helper import (
    InstagramAPIHelper,
    InstagramAPIError,
    InstagramAuthError,
    InstagramPermissionError,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

GOOD_CREDS = {"access_token": "EAA_test_token", "ig_user_id": "123456789"}


def _make_helper(tmp_path: Path, creds: dict = GOOD_CREDS) -> InstagramAPIHelper:
    secrets = tmp_path / ".secrets"
    secrets.mkdir(exist_ok=True)
    (secrets / "instagram_credentials.json").write_text(json.dumps(creds))
    return InstagramAPIHelper(secrets_dir=secrets)


# ── Credential tests ───────────────────────────────────────────────────────────

class TestCredentialLoading(unittest.TestCase):
    """Credentials are loaded lazily on first property access."""

    def test_loads_token_via_property(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            # _token is a property that loads credentials lazily
            self.assertEqual(helper._token, "EAA_test_token")

    def test_loads_ig_user_id_via_property(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            self.assertEqual(helper._ig_user_id, "123456789")

    def test_raises_auth_error_when_no_credentials_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            secrets = Path(td) / ".secrets"
            secrets.mkdir()
            helper = InstagramAPIHelper(secrets_dir=secrets)
            # Error raised on first access, not in constructor
            with self.assertRaises(InstagramAuthError):
                _ = helper._token

    def test_raises_auth_error_when_missing_token(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            secrets = Path(td) / ".secrets"
            secrets.mkdir()
            (secrets / "instagram_credentials.json").write_text(
                json.dumps({"ig_user_id": "123"})
            )
            helper = InstagramAPIHelper(secrets_dir=secrets)
            with self.assertRaises(InstagramAuthError):
                _ = helper._token

    def test_raises_auth_error_when_missing_user_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            secrets = Path(td) / ".secrets"
            secrets.mkdir()
            (secrets / "instagram_credentials.json").write_text(
                json.dumps({"access_token": "EAA_tok"})
            )
            helper = InstagramAPIHelper(secrets_dir=secrets)
            with self.assertRaises(InstagramAuthError):
                _ = helper._token


# ── check_auth tests ───────────────────────────────────────────────────────────

class TestCheckAuth(unittest.TestCase):

    def test_check_auth_success_returns_valid_flag(self):
        """check_auth returns {"valid": True, "user_id": ..., "name": ...}"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            # _request returns the raw /me response; check_auth wraps it
            me_response = {"id": "123456789", "name": "Test User"}
            with patch.object(helper, "_request", return_value=me_response):
                result = helper.check_auth()
            self.assertTrue(result["valid"])
            self.assertEqual(result["user_id"], "123456789")
            self.assertEqual(result["name"], "Test User")

    def test_check_auth_propagates_auth_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            with patch.object(helper, "_request", side_effect=InstagramAuthError("expired")):
                with self.assertRaises(InstagramAuthError):
                    helper.check_auth()


# ── Two-step post creation tests ───────────────────────────────────────────────

class TestCreatePostWithImage(unittest.TestCase):

    def test_dry_run_calls_container_endpoint_but_not_publish(self):
        """
        dry_run=True: creates container (1 request) but skips media_publish.
        The helper's dry_run check happens *after* container creation.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            with patch.object(helper, "_request", return_value={"id": "container_001"}) as mock_req:
                result = helper.create_post_with_image(
                    image_url="https://example.com/img.jpg",
                    caption="Test caption",
                    dry_run=True,
                )
            # Exactly 1 call: the container creation
            self.assertEqual(mock_req.call_count, 1)
            self.assertTrue(result.get("dry_run"))
            self.assertEqual(result.get("creation_id"), "container_001")

    def test_real_post_makes_three_requests(self):
        """
        Real mode: container → sleep(2) → publish → permalink fetch = 3 requests.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            responses = [
                {"id": "container_001"},                          # 1. create container
                {"id": "post_001"},                               # 2. media_publish
                {"id": "post_001", "permalink": "https://ig/p"}, # 3. fetch permalink
            ]
            mock_req = MagicMock(side_effect=responses)
            with patch("time.sleep"):  # skip the 2-second pause
                with patch.object(helper, "_request", mock_req):
                    result = helper.create_post_with_image(
                        image_url="https://example.com/img.jpg",
                        caption="Hello Instagram",
                        dry_run=False,
                    )
            self.assertEqual(mock_req.call_count, 3)
            self.assertEqual(result.get("media_id"), "post_001")
            self.assertFalse(result.get("dry_run"))

    def test_create_post_raises_on_permission_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            with patch.object(helper, "_request",
                               side_effect=InstagramPermissionError("insufficient scope")):
                with self.assertRaises(InstagramPermissionError):
                    helper.create_post_with_image(
                        image_url="https://example.com/img.jpg",
                        caption="caption",
                        dry_run=False,
                    )

    def test_create_post_raises_if_no_creation_id_returned(self):
        """If the container endpoint returns no id, raises InstagramAPIError."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            # Return empty dict — no 'id' key
            with patch.object(helper, "_request", return_value={}):
                with self.assertRaises(InstagramAPIError):
                    helper.create_post_with_image(
                        image_url="https://example.com/img.jpg",
                        caption="caption",
                        dry_run=True,
                    )


# ── list_recent_media tests ────────────────────────────────────────────────────

class TestListRecentMedia(unittest.TestCase):

    def test_returns_list_of_media(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            mock_data = {
                "data": [
                    {"id": "m1", "media_type": "IMAGE", "caption": "Post 1", "timestamp": "2026-02-22T10:00:00+0000"},
                    {"id": "m2", "media_type": "IMAGE", "caption": "Post 2", "timestamp": "2026-02-21T10:00:00+0000"},
                ]
            }
            with patch.object(helper, "_request", return_value=mock_data):
                media = helper.list_recent_media(limit=2)
            self.assertEqual(len(media), 2)
            self.assertEqual(media[0]["id"], "m1")

    def test_returns_empty_list_on_empty_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            with patch.object(helper, "_request", return_value={"data": []}):
                media = helper.list_recent_media()
            self.assertEqual(media, [])


# ── list_comments tests ────────────────────────────────────────────────────────

class TestListComments(unittest.TestCase):

    def test_returns_comments_for_media(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            helper = _make_helper(Path(td))
            mock_data = {
                "data": [
                    {"id": "c1", "username": "alice", "text": "Nice post!", "timestamp": "2026-02-22T11:00:00+0000"},
                ]
            }
            with patch.object(helper, "_request", return_value=mock_data):
                comments = helper.list_comments("m1")
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["username"], "alice")


if __name__ == "__main__":
    unittest.main()
