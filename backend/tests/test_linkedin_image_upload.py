"""
Tests for LinkedIn image upload + post-with-image + ContentGenerator (mocked).

Validates:
- upload_image() calls POST initializeUpload then PUT to uploadUrl → returns image URN
- create_post_with_image() sends payload with content.media.id block
- ContentGenerator loads OpenAI key from .secrets/ai_credentials.json
- ContentGenerator.generate_linkedin_post() calls OpenAI and returns non-empty string
- ContentGenerator.generate_image() calls OpenAI DALL-E 3 and returns bytes

No real LinkedIn / OpenAI endpoints are called.
"""

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from ai_operator.core.linkedin_api_helper import (
    LinkedInAPIHelper,
    LinkedInAPIError,
)
from ai_operator.core.content_generator import (
    ContentGenerator,
    ContentGeneratorError,
)


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_helper(tmp_path: Path) -> LinkedInAPIHelper:
    """Build a LinkedInAPIHelper with a fake non-expired token and profile cache."""
    secrets_dir = tmp_path / '.secrets'
    secrets_dir.mkdir()

    expires_at = (
        (datetime.now(timezone.utc) + timedelta(days=60))
        .strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    )
    token = {'access_token': 'test-bearer', 'expires_at': expires_at}
    token_file = secrets_dir / 'linkedin_token.json'
    token_file.write_text(json.dumps(token))
    os.chmod(token_file, 0o600)

    creds = {
        'client_id': 'fake_id',
        'client_secret': 'fake_secret',
        'redirect_uri': 'http://localhost:8080/callback',
        'scopes': ['openid', 'profile', 'email', 'w_member_social'],
    }
    creds_file = secrets_dir / 'linkedin_credentials.json'
    creds_file.write_text(json.dumps(creds))
    os.chmod(creds_file, 0o600)

    profile_file = secrets_dir / 'linkedin_profile.json'
    profile_file.write_text(json.dumps({
        'person_urn': 'urn:li:person:TEST123',
        'person_id': 'TEST123',
        'method': 'v2_me',
        'cached_at': datetime.now(timezone.utc).isoformat(),
    }))
    os.chmod(profile_file, 0o600)

    return LinkedInAPIHelper(secrets_dir=secrets_dir)


def _mock_response(status_code: int, body: dict = None, headers: dict = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    resp.headers = headers or {}
    return resp


def _make_content_generator(tmp_path: Path) -> ContentGenerator:
    """Build a ContentGenerator with a fake ai_credentials.json (OpenAI-only)."""
    secrets_dir = tmp_path / '.secrets'
    secrets_dir.mkdir(exist_ok=True)

    ai_creds = {
        'openai_api_key': 'sk-fake',
    }
    ai_creds_file = secrets_dir / 'ai_credentials.json'
    ai_creds_file.write_text(json.dumps(ai_creds))

    generator = ContentGenerator(secrets_file=ai_creds_file)
    return generator


# ---------------------------------------------------------------------------
# Test 1: upload_image — initialize + PUT
# ---------------------------------------------------------------------------

class TestUploadImage:

    def test_upload_image_initializes_then_uploads(self, tmp_path):
        """
        upload_image() must:
        1. POST /rest/images?action=initializeUpload
        2. PUT to the uploadUrl with image bytes
        3. Return the image URN from the init response
        """
        helper = _make_helper(tmp_path)

        init_response = _mock_response(200, {
            'value': {
                'uploadUrl': 'https://linkedin-upload.example.com/put-here',
                'image': 'urn:li:image:FAKE_IMAGE_URN',
            }
        })
        put_response = _mock_response(201)

        fake_image_bytes = b'\xff\xd8\xff\xe0' + b'\x00' * 100  # minimal JPEG header

        call_order = []

        def fake_api_request_raw(method, endpoint, *, base=None, **kwargs):
            call_order.append((method, endpoint))
            if method == 'POST' and 'initializeUpload' in endpoint:
                return init_response
            return _mock_response(200, {})

        with patch.object(helper, '_api_request_raw', side_effect=fake_api_request_raw), \
             patch('requests.put', return_value=put_response) as mock_put:

            result_urn = helper.upload_image(fake_image_bytes)

        # Verify step 1: initializeUpload was called
        assert any('initializeUpload' in ep for _, ep in call_order), \
            "Must call POST /images?action=initializeUpload"

        # Verify step 2: PUT was called with image bytes and upload URL
        mock_put.assert_called_once()
        put_call = mock_put.call_args
        assert put_call[0][0] == 'https://linkedin-upload.example.com/put-here', \
            "PUT must target the uploadUrl from init response"
        assert put_call[1]['data'] == fake_image_bytes, \
            "PUT body must be the raw image bytes"

        # Verify return value
        assert result_urn == 'urn:li:image:FAKE_IMAGE_URN'

    def test_upload_image_raises_on_init_failure(self, tmp_path):
        """upload_image() must raise LinkedInAPIError if initializeUpload returns 4xx."""
        helper = _make_helper(tmp_path)

        error_response = _mock_response(403, {'message': 'Permission denied'})

        with patch.object(helper, '_api_request_raw', return_value=error_response):
            with pytest.raises(LinkedInAPIError) as exc_info:
                helper.upload_image(b'fake_bytes')

        assert '403' in str(exc_info.value)

    def test_upload_image_raises_on_put_failure(self, tmp_path):
        """upload_image() must raise LinkedInAPIError if PUT returns non-2xx."""
        helper = _make_helper(tmp_path)

        init_response = _mock_response(200, {
            'value': {
                'uploadUrl': 'https://upload.example.com/put',
                'image': 'urn:li:image:X',
            }
        })
        put_fail = _mock_response(500)
        put_fail.raise_for_status.side_effect = Exception("Server error")

        with patch.object(helper, '_api_request_raw', return_value=init_response), \
             patch('requests.put', return_value=put_fail):
            with pytest.raises(LinkedInAPIError):
                helper.upload_image(b'bytes')


# ---------------------------------------------------------------------------
# Test 2: create_post_with_image — content block in payload
# ---------------------------------------------------------------------------

class TestCreatePostWithImage:

    def test_create_post_with_image_sends_content_block(self, tmp_path):
        """
        create_post_with_image() must include content.media.id in the POST payload.
        """
        helper = _make_helper(tmp_path)

        captured_payload = {}

        def fake_raw(method, endpoint, *, base=None, **kwargs):
            captured_payload.update(kwargs.get('json', {}))
            resp = MagicMock()
            resp.status_code = 201
            resp.headers = {'x-restli-id': 'urn:li:share:99'}
            resp.json.return_value = {}
            resp.text = '{}'
            return resp

        with patch.object(helper, '_api_request_raw', side_effect=fake_raw):
            result = helper.create_post_with_image(
                'Test post with image',
                'urn:li:image:FAKE',
                image_title='AI visualization',
            )

        # Verify content.media block
        assert 'content' in captured_payload, "Payload must have 'content' key"
        media = captured_payload['content'].get('media', {})
        assert media.get('id') == 'urn:li:image:FAKE', \
            "content.media.id must be the image URN"
        assert media.get('title') == 'AI visualization'

        # Verify other required fields
        assert captured_payload.get('commentary') == 'Test post with image'
        assert captured_payload.get('author') == 'urn:li:person:TEST123'
        assert captured_payload.get('lifecycleState') == 'PUBLISHED'

    def test_create_post_with_image_returns_correct_dict(self, tmp_path):
        """Return dict must include id, image_urn, and endpoint_used='rest/posts+image'."""
        helper = _make_helper(tmp_path)

        resp_201 = _mock_response(201, {}, headers={'x-restli-id': 'urn:li:share:42'})

        with patch.object(helper, '_api_request_raw', return_value=resp_201):
            result = helper.create_post_with_image('Post text', 'urn:li:image:IMG1')

        assert result['id'] == 'urn:li:share:42'
        assert result['image_urn'] == 'urn:li:image:IMG1'
        assert result['endpoint_used'] == 'rest/posts+image'
        assert result['author'] == 'urn:li:person:TEST123'

    def test_create_post_with_image_raises_on_error(self, tmp_path):
        """create_post_with_image() must raise LinkedInAPIError on HTTP 4xx/5xx."""
        helper = _make_helper(tmp_path)

        error_resp = _mock_response(403, {'message': 'Forbidden'})

        with patch.object(helper, '_api_request_raw', return_value=error_resp):
            with pytest.raises(LinkedInAPIError) as exc_info:
                helper.create_post_with_image('text', 'urn:li:image:X')

        assert '403' in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: ContentGenerator — loads OpenAI key; generate_linkedin_post
# ---------------------------------------------------------------------------

class TestContentGeneratorPost:

    def test_content_generator_loads_openai_key(self, tmp_path):
        """ContentGenerator must load openai_api_key from .secrets/ai_credentials.json."""
        generator = _make_content_generator(tmp_path)
        creds = generator._load_credentials()
        assert 'openai_api_key' in creds
        assert creds['openai_api_key'] == 'sk-fake'

    def test_content_generator_post_mock(self, tmp_path):
        """
        generate_linkedin_post() must call OpenAI chat completions and return a non-empty string.
        """
        generator = _make_content_generator(tmp_path)

        # Mock OpenAI chat completions response
        mock_choice = MagicMock()
        mock_choice.message.content = "The future of AI agents is here. #AgenticAI #AI"

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(generator, '_get_openai_client', return_value=mock_client):
            result = generator.generate_linkedin_post(topic="Agentic AI")

        assert isinstance(result, str)
        assert len(result) > 0
        mock_client.chat.completions.create.assert_called_once()

        # Verify correct model was used
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get('model') == ContentGenerator.OPENAI_TEXT_MODEL

    def test_content_generator_post_raises_on_empty_response(self, tmp_path):
        """generate_linkedin_post() must raise ContentGeneratorError if OpenAI returns empty text."""
        generator = _make_content_generator(tmp_path)

        mock_choice = MagicMock()
        mock_choice.message.content = ''

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch.object(generator, '_get_openai_client', return_value=mock_client):
            with pytest.raises(ContentGeneratorError):
                generator.generate_linkedin_post()


# ---------------------------------------------------------------------------
# Test 4: ContentGenerator.generate_image — DALL-E mock
# ---------------------------------------------------------------------------

class TestContentGeneratorImage:

    def test_content_generator_image_mock(self, tmp_path):
        """
        generate_image() must call OpenAI DALL-E 3 and return image bytes.
        """
        generator = _make_content_generator(tmp_path)

        # Mock OpenAI response
        fake_image_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 50  # PNG header

        mock_image_data = MagicMock()
        mock_image_data.url = 'https://oai.example.com/generated-image.png'

        mock_images_response = MagicMock()
        mock_images_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_images_response

        mock_dl_response = MagicMock()
        mock_dl_response.content = fake_image_bytes
        mock_dl_response.raise_for_status = MagicMock()

        with patch.object(generator, '_get_openai_client', return_value=mock_client), \
             patch('requests.get', return_value=mock_dl_response):

            result = generator.generate_image()

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result == fake_image_bytes

        # Verify DALL-E 3 was requested
        mock_client.images.generate.assert_called_once()
        call_kwargs = mock_client.images.generate.call_args[1]
        assert call_kwargs.get('model') == ContentGenerator.DALLE_MODEL
        assert call_kwargs.get('size') == ContentGenerator.DALLE_SIZE

    def test_content_generator_image_raises_on_no_url(self, tmp_path):
        """generate_image() must raise ContentGeneratorError if DALL-E returns no URL."""
        generator = _make_content_generator(tmp_path)

        mock_image_data = MagicMock()
        mock_image_data.url = ''  # empty URL

        mock_response = MagicMock()
        mock_response.data = [mock_image_data]

        mock_client = MagicMock()
        mock_client.images.generate.return_value = mock_response

        with patch.object(generator, '_get_openai_client', return_value=mock_client):
            with pytest.raises(ContentGeneratorError):
                generator.generate_image()
