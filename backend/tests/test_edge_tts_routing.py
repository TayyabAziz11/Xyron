"""
Tests for the Edge-TTS routing added to voice/tts_router.py and
voice/edge_tts_service.py — the 2026-08-19 replacement for XTTS as the
primary Urdu-family TTS engine (XTTS is unable to load on this machine's
dependency stack; see edge_tts_service.py's module docstring).

2026-09-04: OpenAI TTS (voice.openai_tts_service) became the new PRIMARY
engine for ur/ur_roman/mixed once OpenAI billing was restored (better Urdu
prosody than Edge-TTS) — Edge-TTS is now the fallback when OpenAI TTS
fails. Every test below that exercises the Edge-TTS path therefore also
mocks voice.openai_tts_service.synthesize to return None (simulating
"OpenAI TTS unavailable"), so these tests keep testing Edge-TTS behavior
specifically without silently making a real, billed OpenAI API call every
test run — that's a real regression this file caught: without the
openai_tts_service mock, these tests started hitting the live OpenAI TTS
endpoint (see test_openai_tts_routing.py for the OpenAI-primary-path tests).

No live network calls — edge_tts_service.synthesize and
openai_tts_service.synthesize are mocked throughout, matching the existing
test suite's approach for external-service boundaries.
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestTTSRouterEdgeRouting(unittest.TestCase):
    def setUp(self):
        from voice import tts_router
        tts_router._ml_cache.clear()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_ur_routes_to_edge_tts(self, mock_edge, mock_openai):
        mock_edge.return_value = b"FAKE_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("کروم کھولو", lang="ur", voice="onyx")
        self.assertEqual(result, b"FAKE_WAV_BYTES")
        mock_edge.assert_called_once()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_ur_roman_routes_to_edge_tts(self, mock_edge, mock_openai):
        mock_edge.return_value = b"FAKE_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("Chrome khol raha hoon.", lang="ur_roman", voice="onyx")
        self.assertEqual(result, b"FAKE_WAV_BYTES")
        mock_edge.assert_called_once()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_mixed_routes_to_edge_tts(self, mock_edge, mock_openai):
        mock_edge.return_value = b"FAKE_WAV_BYTES"
        from voice.tts_router import synthesize
        result = synthesize("WhatsApp orders check karo", lang="mixed", voice="onyx")
        self.assertEqual(result, b"FAKE_WAV_BYTES")
        mock_edge.assert_called_once()

    def test_en_still_routes_to_kokoro_not_edge(self):
        from voice.tts_router import synthesize
        with patch("api.routers.voice._kokoro_to_wav", return_value=b"KOKORO_WAV") as mock_kokoro, \
             patch("voice.edge_tts_service.synthesize") as mock_edge, \
             patch("voice.openai_tts_service.synthesize") as mock_openai:
            result = synthesize("Hello there.", lang="en", voice="onyx")
            self.assertEqual(result, b"KOKORO_WAV")
            mock_edge.assert_not_called()
            mock_openai.assert_not_called()
            mock_kokoro.assert_called_once()

    def test_hi_still_routes_to_xtts_not_edge(self):
        # hi/ar preserved on the existing XTTS path per explicit instruction
        # not to touch languages other than the Urdu family.
        from voice.tts_router import synthesize
        with patch("voice.xtts_service.synthesize", return_value=b"XTTS_WAV") as mock_xtts, \
             patch("voice.edge_tts_service.synthesize") as mock_edge, \
             patch("voice.openai_tts_service.synthesize") as mock_openai:
            result = synthesize("नमस्ते", lang="hi", voice="onyx")
            self.assertEqual(result, b"XTTS_WAV")
            mock_edge.assert_not_called()
            mock_openai.assert_not_called()
            mock_xtts.assert_called_once()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_ur_script_edge_failure_does_not_fall_back_to_kokoro(self, mock_edge, mock_openai):
        # Live-caught design requirement: pure Urdu script must never be fed
        # to Kokoro's English phonemizer on Edge-TTS failure — that produces
        # unintelligible audio, not a usable degraded response. (OpenAI TTS
        # is also mocked as unavailable here so this test still exercises
        # the "both cloud engines failed" case it was written for.)
        mock_edge.return_value = None
        from voice.tts_router import synthesize
        with patch("api.routers.voice._kokoro_to_wav") as mock_kokoro:
            result = synthesize("کروم کھولو", lang="ur", voice="onyx")
            self.assertIsNone(result)
            mock_kokoro.assert_not_called()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_ur_roman_edge_failure_falls_back_to_kokoro(self, mock_edge, mock_openai):
        # Roman/Latin-script text IS reasonably renderable by Kokoro, so this
        # case should fall back rather than go silent.
        mock_edge.return_value = None
        from voice.tts_router import synthesize
        with patch("api.routers.voice._kokoro_to_wav", return_value=b"KOKORO_FALLBACK") as mock_kokoro:
            result = synthesize("Chrome khol raha hoon.", lang="ur_roman", voice="onyx")
            self.assertEqual(result, b"KOKORO_FALLBACK")
            mock_kokoro.assert_called_once()

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_cache_key_includes_engine(self, mock_edge, mock_openai):
        # Regression guard for the exact bug named in the instructions: a
        # cache key without the engine could serve a stale XTTS-synthesized
        # entry under the new Edge-TTS routing (or vice versa).
        mock_edge.return_value = b"EDGE_WAV"
        from voice.tts_router import synthesize, _ml_cache
        synthesize("same text", lang="ur_roman", voice="onyx")
        keys = list(_ml_cache.keys())
        self.assertEqual(len(keys), 1)
        self.assertIn("edge_tts", keys[0])

    @patch("voice.openai_tts_service.synthesize", return_value=None)
    @patch("voice.edge_tts_service.synthesize")
    def test_second_call_same_text_hits_cache_not_edge_again(self, mock_edge, mock_openai):
        mock_edge.return_value = b"EDGE_WAV"
        from voice.tts_router import synthesize
        synthesize("cached text", lang="ur_roman", voice="onyx")
        synthesize("cached text", lang="ur_roman", voice="onyx")
        self.assertEqual(mock_edge.call_count, 1)


class TestEdgeTTSService(unittest.TestCase):
    def test_voice_for_lang_defaults(self):
        from voice.edge_tts_service import voice_for_lang, DEFAULT_URDU_VOICE
        self.assertEqual(voice_for_lang("ur"), DEFAULT_URDU_VOICE)
        self.assertEqual(voice_for_lang("ur_roman"), DEFAULT_URDU_VOICE)
        self.assertEqual(voice_for_lang("mixed"), DEFAULT_URDU_VOICE)
        self.assertEqual(voice_for_lang("en"), DEFAULT_URDU_VOICE)  # unmapped -> default

    def test_empty_text_returns_none_without_network_call(self):
        from voice.edge_tts_service import synthesize
        self.assertIsNone(synthesize("", "ur"))
        self.assertIsNone(synthesize("   ", "ur"))

    @patch("voice.edge_tts_service._synthesize_mp3_async")
    def test_timeout_returns_none_not_raise(self, mock_async):
        import asyncio
        async def _hang(*a, **kw):
            await asyncio.sleep(100)
        mock_async.side_effect = _hang
        from voice.edge_tts_service import synthesize
        import voice.edge_tts_service as svc
        with patch.object(svc, "_TIMEOUT_S", 0.2):
            result = synthesize("test", "ur")
        self.assertIsNone(result)

    @patch("voice.edge_tts_service._mp3_to_wav")
    @patch("voice.edge_tts_service._synthesize_mp3_async")
    def test_network_error_returns_none_not_raise(self, mock_async, mock_conv):
        async def _fail(*a, **kw):
            raise ConnectionError("no network")
        mock_async.side_effect = _fail
        from voice.edge_tts_service import synthesize
        result = synthesize("test", "ur")
        self.assertIsNone(result)
        mock_conv.assert_not_called()

    @patch("voice.edge_tts_service._mp3_to_wav")
    @patch("voice.edge_tts_service._synthesize_mp3_async")
    def test_successful_synthesis_returns_wav_bytes(self, mock_async, mock_conv):
        async def _ok(*a, **kw):
            return b"FAKE_MP3"
        mock_async.side_effect = _ok
        mock_conv.return_value = b"FAKE_WAV"
        from voice.edge_tts_service import synthesize
        result = synthesize("test text", "ur")
        self.assertEqual(result, b"FAKE_WAV")
        mock_conv.assert_called_once_with(b"FAKE_MP3")

    @patch("voice.pronunciation_preprocessor.preprocess")
    @patch("voice.edge_tts_service._mp3_to_wav")
    @patch("voice.edge_tts_service._synthesize_mp3_async")
    def test_pronunciation_preprocessor_applied_before_synthesis(
        self, mock_async, mock_conv, mock_preprocess
    ):
        # Regression guard: pronunciation_preprocessor.py existed but was
        # only wired into xtts_service.py (which cannot load on this
        # machine) — edge_tts_service.py is the actual active Urdu path and
        # never called it at all.
        async def _ok(text, voice):
            return b"FAKE_MP3"
        mock_async.side_effect = _ok
        mock_conv.return_value = b"FAKE_WAV"
        mock_preprocess.return_value = "PREPROCESSED TEXT"
        from voice.edge_tts_service import synthesize
        synthesize("raw text with WhatsApp", "ur")
        mock_preprocess.assert_called_once_with("raw text with WhatsApp")
        mock_async.assert_called_once()
        self.assertEqual(mock_async.call_args[0][0], "PREPROCESSED TEXT")

    def test_mp3_to_wav_produces_valid_wav_from_real_mp3(self):
        # Regression guard for the pydub/ffmpeg-subprocess -> soundfile
        # swap (soundfile decodes MP3 in-process via libsndfile, ~50x
        # faster than shelling out to ffmpeg per call) — verifies the new
        # path actually produces playable WAV bytes, not just that a mock
        # was called correctly. Fixture MP3 built locally via ffmpeg
        # (no network) so this stays a fast, deterministic unit test.
        import shutil
        import subprocess
        import wave

        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not available to build test fixture")

        proc = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
             "-ar", "24000", "-ac", "1", "-f", "mp3", "pipe:1"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        mp3_bytes = proc.stdout
        self.assertTrue(len(mp3_bytes) > 0)

        from voice.edge_tts_service import _mp3_to_wav
        wav_bytes = _mp3_to_wav(mp3_bytes)

        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        with wave.open(io.BytesIO(wav_bytes)) as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertGreater(w.getnframes(), 0)

    @patch("voice.edge_tts_service._mp3_to_wav")
    @patch("edge_tts.Communicate")
    def test_prosody_env_vars_passed_to_communicate(self, mock_communicate_cls, mock_conv):
        import importlib
        import voice.edge_tts_service as svc

        async def _stream():
            yield {"type": "audio", "data": b"X"}
        mock_instance = MagicMock()
        mock_instance.stream = _stream
        mock_communicate_cls.return_value = mock_instance
        mock_conv.return_value = b"FAKE_WAV"

        with patch.object(svc, "_RATE", "-10%"), \
             patch.object(svc, "_PITCH", "+3Hz"), \
             patch.object(svc, "_VOLUME", "+5%"):
            svc.synthesize("test", "ur")

        mock_communicate_cls.assert_called_once_with(
            "test", svc.DEFAULT_URDU_VOICE, rate="-10%", pitch="+3Hz", volume="+5%"
        )


if __name__ == "__main__":
    unittest.main()
