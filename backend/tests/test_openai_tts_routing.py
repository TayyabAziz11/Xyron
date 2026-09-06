"""
Tests for OpenAI TTS as the primary Urdu-family engine in voice/tts_router.py
and voice/openai_tts_service.py — added 2026-09-04 once OpenAI billing was
restored (previously Edge-TTS was primary; see test_edge_tts_routing.py's
updated docstring for that history).

No live network calls — voice.openai_tts_service.synthesize and
voice.edge_tts_service.synthesize are mocked throughout.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestTTSRouterOpenAIRouting(unittest.TestCase):
    def setUp(self):
        from voice import tts_router
        tts_router._ml_cache.clear()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_ur_tries_openai_first(self, mock_openai, mock_edge):
        mock_openai.return_value = b"OPENAI_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("کروم کھولو", lang="ur", voice="onyx")
        self.assertEqual(result, b"OPENAI_WAV_BYTES")
        mock_openai.assert_called_once()
        mock_edge.assert_not_called()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_ur_roman_tries_openai_first(self, mock_openai, mock_edge):
        mock_openai.return_value = b"OPENAI_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("Chrome khol raha hoon.", lang="ur_roman", voice="onyx")
        self.assertEqual(result, b"OPENAI_WAV_BYTES")
        mock_openai.assert_called_once()
        mock_edge.assert_not_called()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_mixed_tries_openai_first(self, mock_openai, mock_edge):
        mock_openai.return_value = b"OPENAI_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("WhatsApp orders check karo", lang="mixed", voice="onyx")
        self.assertEqual(result, b"OPENAI_WAV_BYTES")
        mock_openai.assert_called_once()
        mock_edge.assert_not_called()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_openai_none_falls_back_to_edge(self, mock_openai, mock_edge):
        mock_openai.return_value = None
        mock_edge.return_value = b"EDGE_FALLBACK_WAV"
        from voice.tts_router import synthesize
        result = synthesize("کروم کھولو", lang="ur", voice="onyx")
        self.assertEqual(result, b"EDGE_FALLBACK_WAV")
        mock_openai.assert_called_once()
        mock_edge.assert_called_once()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_openai_exception_falls_back_to_edge(self, mock_openai, mock_edge):
        # openai_tts_service.synthesize never raises per its own contract,
        # but tts_router's try/except around the call is still exercised
        # here as a defense-in-depth check.
        mock_openai.side_effect = RuntimeError("boom")
        mock_edge.return_value = b"EDGE_FALLBACK_WAV"
        from voice.tts_router import synthesize
        result = synthesize("کروم کھولو", lang="ur", voice="onyx")
        self.assertEqual(result, b"EDGE_FALLBACK_WAV")
        mock_edge.assert_called_once()

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_cache_key_includes_openai_engine(self, mock_openai, mock_edge):
        mock_openai.return_value = b"OPENAI_WAV"
        from voice.tts_router import synthesize, _ml_cache
        synthesize("same text", lang="ur", voice="onyx")
        keys = list(_ml_cache.keys())
        self.assertEqual(len(keys), 1)
        self.assertIn("openai_tts", keys[0])

    @patch("voice.edge_tts_service.synthesize")
    @patch("voice.openai_tts_service.synthesize")
    def test_second_call_same_text_hits_cache_not_openai_again(self, mock_openai, mock_edge):
        mock_openai.return_value = b"OPENAI_WAV"
        from voice.tts_router import synthesize
        synthesize("cached text", lang="ur", voice="onyx")
        synthesize("cached text", lang="ur", voice="onyx")
        self.assertEqual(mock_openai.call_count, 1)

    def test_en_never_touches_openai_tts(self):
        from voice.tts_router import synthesize
        with patch("api.routers.voice._kokoro_to_wav", return_value=b"KOKORO_WAV"), \
             patch("voice.openai_tts_service.synthesize") as mock_openai:
            synthesize("Hello there.", lang="en", voice="onyx")
            mock_openai.assert_not_called()


class TestOpenAITTSService(unittest.TestCase):
    def test_empty_text_returns_none_without_api_call(self):
        from voice.openai_tts_service import synthesize
        self.assertIsNone(synthesize("", "ur", "onyx"))
        self.assertIsNone(synthesize("   ", "ur", "onyx"))

    @patch("api.config.settings")
    def test_no_api_key_returns_none_without_import(self, mock_settings):
        mock_settings.openai_api_key = ""
        from voice import openai_tts_service
        openai_tts_service._client = None  # reset singleton
        result = openai_tts_service.synthesize("سلام", "ur", "onyx")
        self.assertIsNone(result)
        openai_tts_service._client = None  # don't leak into other tests

    def test_invalid_voice_falls_back_to_default(self):
        from voice import openai_tts_service
        with patch.object(openai_tts_service, "_get_client") as mock_get_client, \
             patch("voice.pronunciation_preprocessor.preprocess", side_effect=lambda t: t):
            mock_client = mock_get_client.return_value
            mock_resp = mock_client.audio.speech.create.return_value
            mock_resp.read.return_value = b"WAV_BYTES"
            result = openai_tts_service.synthesize("سلام", "ur", "not_a_real_voice")
            self.assertEqual(result, b"WAV_BYTES")
            _, kwargs = mock_client.audio.speech.create.call_args
            self.assertEqual(kwargs["voice"], "onyx")  # _DEFAULT_VOICE

    def test_api_exception_returns_none_not_raise(self):
        from voice import openai_tts_service
        with patch.object(openai_tts_service, "_get_client") as mock_get_client, \
             patch("voice.pronunciation_preprocessor.preprocess", side_effect=lambda t: t):
            mock_client = mock_get_client.return_value
            mock_client.audio.speech.create.side_effect = RuntimeError("api down")
            result = openai_tts_service.synthesize("سلام", "ur", "onyx")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
