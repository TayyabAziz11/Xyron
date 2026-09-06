"""
Regression tests for Roman Urdu / mixed command coverage added to
api/services/ml_normalizer.py on 2026-09-04 — user-requested parity so
common English voice commands (open YouTube, play a song, open a drive,
create a folder, open Microsoft Store, download something) also work when
spoken in Roman Urdu / mixed code-switching.

This is the module voice_ws.py actually calls on the live path (see its
"Normalize non-English command to English for intent routing" step) — not
mixed_language_engine.analyze(), which is a separate pre-pass.

Covers three live-caught bugs found while adding this coverage:
  1. "youtube ko kholo" leaking the "ko" particle into the app name
     ("open youtube ko" -> app_name="youtube ko" instead of "youtube").
  2. "despacito gana chalao" (named song) being swallowed by the bare/
     no-name "gana chalao" rule -> "despacito play music" instead of
     "play despacito on youtube".
  3. "is naam ka folder banao" (pronoun only, no real name given) matching
     the no-"naam" fallback folder rule anyway -> "create folder named is
     naam" instead of correctly falling through unmatched.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.ml_normalizer import normalize
from api.services.intent_router import IntentRouter


class TestKoParticleStripped(unittest.TestCase):
    """Live-caught bug: the object-marker particle "ko" leaked into the
    captured app/object name for every "<X> ko <verb>" phrasing."""

    def setUp(self):
        self.ir = IntentRouter()

    def test_youtube_ko_kholo_opens_youtube_not_youtube_ko(self):
        canon = normalize("youtube ko kholo", "mixed")
        self.assertEqual(canon, "open youtube")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "open_application")
        self.assertEqual(r.params.get("app_name"), "youtube")

    def test_c_drive_ko_kholo_still_opens_c_drive(self):
        canon = normalize("c drive ko kholo", "ur_roman")
        self.assertEqual(canon, "open c drive")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "open_drive")
        self.assertEqual(r.params.get("drive"), "C")


class TestSongPlayCommands(unittest.TestCase):
    def setUp(self):
        self.ir = IntentRouter()

    def test_named_song_routes_to_search_youtube_not_media_control(self):
        canon = normalize("despacito gana chalao", "ur_roman")
        self.assertEqual(canon, "play despacito on youtube")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "search_youtube")
        self.assertEqual(r.params.get("query"), "despacito")

    def test_named_song_with_word_song_also_routes_correctly(self):
        canon = normalize("believer song chalao", "ur_roman")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "search_youtube")
        self.assertEqual(r.params.get("query"), "believer")

    def test_pronoun_referenced_song_falls_back_to_generic_play(self):
        # "play this song" with no actual name given — nothing to search
        # for, so this should resolve to a generic play/resume action
        # rather than searching YouTube for the literal word "this".
        for transcript in ("ye wala gana chalao", "is gaane ko chalao"):
            canon = normalize(transcript, "ur_roman")
            self.assertEqual(canon, "play music", msg=transcript)
            r = self.ir.route(canon)
            self.assertEqual(r.tool_name, "media_control", msg=transcript)

    def test_bare_song_request_does_not_swallow_a_real_name(self):
        # Regression guard for the exact bug: the bare "gana chalao" rule
        # must not match as a substring inside a longer NAMED request.
        canon = normalize("koi gana chalao", "ur_roman")
        self.assertEqual(canon, "play music")
        canon2 = normalize("despacito gana chalao", "ur_roman")
        self.assertNotEqual(canon2, "despacito play music")
        self.assertEqual(canon2, "play despacito on youtube")


class TestFolderCreationCommands(unittest.TestCase):
    def setUp(self):
        self.ir = IntentRouter()

    def test_named_folder_creation_naam_ka_form(self):
        canon = normalize("project naam ka folder banao", "mixed")
        self.assertEqual(canon, "create folder named project")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "create_folder")
        self.assertEqual(r.params.get("name"), "project")

    def test_named_folder_creation_ka_form_without_naam(self):
        canon = normalize("test ka folder banao", "mixed")
        self.assertEqual(canon, "create folder named test")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "create_folder")
        self.assertEqual(r.params.get("name"), "test")

    def test_pronoun_only_folder_request_falls_through_unmatched(self):
        # "is naam ka folder banao" ("a folder with THIS name") has no
        # actual name in it — must NOT create a folder literally named
        # "is" or "is naam"; falls through unmatched instead (same
        # limitation English has for an equally incomplete instruction).
        canon = normalize("is naam ka folder banao", "mixed")
        self.assertEqual(canon, "is naam ka folder banao")  # unchanged
        r = self.ir.route(canon)
        self.assertIsNone(r.tool_name)


class TestExistingRulesUnaffected(unittest.TestCase):
    """Regression guard: the "ko"-stripping preprocessing and new song/
    folder rules must not change any pre-existing, already-working
    normalization."""

    def setUp(self):
        self.ir = IntentRouter()

    def test_chrome_kholo_unaffected(self):
        self.assertEqual(normalize("Chrome kholo", "ur_roman"), "open chrome")

    def test_wifi_settings_kholo_unaffected(self):
        self.assertEqual(normalize("wifi settings kholo", "ur_roman"), "open wifi settings")

    def test_microsoft_store_kholo_unaffected(self):
        self.assertEqual(normalize("microsoft store kholo", "mixed"), "open microsoft store")

    def test_store_se_download_unaffected(self):
        canon = normalize("microsoft store se whatsapp download karo", "mixed")
        self.assertEqual(canon, "download whatsapp from microsoft store")
        r = self.ir.route(canon)
        self.assertEqual(r.tool_name, "install_store_app")

    def test_volume_barhao_unaffected(self):
        self.assertEqual(normalize("volume barhao", "ur_roman"), "volume up")

    def test_youtube_par_x_chalao_unaffected(self):
        canon = normalize("youtube par believer chalao", "ur_roman")
        self.assertEqual(canon, "play believer on youtube")


if __name__ == "__main__":
    unittest.main()
