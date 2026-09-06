"""
Tests for whisper_service._validate_model_size — specifically the
large-v3-turbo / turbo support added for higher-accuracy Urdu STT.

No model loading here (no GPU/network dependency) — this only covers the
pure string-validation function, which previously had zero test coverage.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import unittest


class TestValidateModelSize(unittest.TestCase):
    def test_large_v3_turbo_accepted(self):
        from voice.whisper_service import _validate_model_size
        self.assertEqual(_validate_model_size("large-v3-turbo"), "large-v3-turbo")

    def test_turbo_alias_accepted(self):
        from voice.whisper_service import _validate_model_size
        self.assertEqual(_validate_model_size("turbo"), "turbo")

    def test_existing_sizes_still_accepted(self):
        from voice.whisper_service import _validate_model_size
        for size in ("small", "medium", "large-v3", "distil-large-v3"):
            self.assertEqual(_validate_model_size(size), size)

    def test_unknown_size_falls_back_to_small(self):
        from voice.whisper_service import _validate_model_size
        self.assertEqual(_validate_model_size("not-a-real-model"), "small")


if __name__ == "__main__":
    unittest.main()
