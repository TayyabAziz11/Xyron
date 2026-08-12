"""
Tests for STT phonetic drive-letter correction.

Pipeline under test:
  1. whisper_service._apply_corrections  — comma-tolerant patterns
  2. normalizer._correct_drive_phonetics — context-scoped phonetic fix
  3. intent_router.route()               — phonetic guard + open_drive routing
"""
import pytest
import re


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — whisper_service._apply_corrections (raw STT output, may have commas)
# ─────────────────────────────────────────────────────────────────────────────

class TestWhisperCorrections:

    def _correct(self, text: str) -> str:
        from voice.whisper_service import _apply_corrections
        return _apply_corrections(text)

    # Raw STT with commas (the actual failing case)
    def test_see_drive_with_comma(self):
        assert self._correct("Open, see, drive.").lower() == "open, c drive."

    def test_sea_drive_with_comma(self):
        assert "c drive" in self._correct("open, sea, drive.").lower()

    def test_cee_drive_no_comma(self):
        assert "c drive" in self._correct("open cee drive").lower()

    def test_cee_drive_with_comma(self):
        assert "c drive" in self._correct("open, cee, drive.").lower()

    def test_dee_drive_with_comma(self):
        assert "d drive" in self._correct("open, dee, drive.").lower()

    def test_ee_drive_no_comma(self):
        assert "e drive" in self._correct("open ee drive").lower()

    def test_eff_drive_with_comma(self):
        assert "f drive" in self._correct("open, eff, drive.").lower()

    def test_see_drive_plain(self):
        assert "c drive" in self._correct("open see drive").lower()

    def test_sea_drive_plain(self):
        assert "c drive" in self._correct("open sea drive").lower()


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — normalizer._correct_drive_phonetics (after comma stripping)
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizerPhoneticCorrection:

    def _correct(self, text: str) -> str:
        from api.services.normalizer import _correct_drive_phonetics
        return _correct_drive_phonetics(text)

    def test_see_drive(self):
        assert self._correct("open see drive") == "open c drive"

    def test_sea_drive(self):
        assert self._correct("open sea drive") == "open c drive"

    def test_cee_drive(self):
        assert self._correct("open cee drive") == "open c drive"

    def test_dee_drive(self):
        assert self._correct("open dee drive") == "open d drive"

    def test_ee_drive(self):
        assert self._correct("open ee drive") == "open e drive"

    def test_eff_drive(self):
        assert self._correct("open eff drive") == "open f drive"

    def test_show_see_drive(self):
        assert self._correct("show see drive") == "show c drive"

    def test_browse_cee_drive(self):
        assert self._correct("browse cee drive") == "browse c drive"

    # Context guard — "see" NOT in drive context must NOT be replaced
    def test_see_my_files_unchanged(self):
        result = self._correct("see my files")
        assert result == "see my files"

    def test_see_standalone_unchanged(self):
        result = self._correct("i want to see the report")
        assert result == "i want to see the report"

    def test_dee_without_drive_unchanged(self):
        result = self._correct("dee is a good name")
        assert result == "dee is a good name"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — full normalizer pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizerFull:

    def _normalize(self, text: str) -> str:
        from api.services.normalizer import normalize
        return normalize(text)

    def test_see_drive_normalizes_to_c(self):
        assert self._normalize("open see drive") == "open c drive"

    def test_sea_drive_normalizes_to_c(self):
        assert self._normalize("open sea drive") == "open c drive"

    def test_cee_drive_normalizes_to_c(self):
        assert self._normalize("open cee drive") == "open c drive"

    def test_c_drive_unchanged(self):
        assert self._normalize("open c drive") == "open c drive"

    def test_e_drive_unchanged(self):
        assert self._normalize("open e drive") == "open e drive"

    def test_see_my_files_safe(self):
        result = self._normalize("see my files")
        assert "c " not in result

    def test_comma_separated_stt_see_drive(self):
        # Simulate Whisper output that whisper_service already corrected
        result = self._normalize("Open C drive.")
        assert result == "open c drive"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 4 — intent_router end-to-end routing
# ─────────────────────────────────────────────────────────────────────────────

class TestDriveRouting:

    @pytest.fixture(scope="class")
    def ir(self):
        from api.services.intent_router import IntentRouter
        return IntentRouter()

    def test_open_see_drive(self, ir):
        r = ir.route("open see drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_open_sea_drive(self, ir):
        r = ir.route("open sea drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_open_cee_drive(self, ir):
        r = ir.route("open cee drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_open_c_drive(self, ir):
        r = ir.route("open c drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_show_see_drive(self, ir):
        r = ir.route("show see drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_go_to_sea_drive(self, ir):
        r = ir.route("open sea drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_browse_cee_drive(self, ir):
        r = ir.route("browse cee drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_open_e_drive(self, ir):
        r = ir.route("open e drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "E"

    def test_open_ee_drive(self, ir):
        r = ir.route("open ee drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "E"

    def test_open_d_drive(self, ir):
        r = ir.route("open d drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "D"

    def test_open_dee_drive(self, ir):
        r = ir.route("open dee drive")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "D"

    def test_see_drive_folder_resolves_c_drive(self, ir):
        # "see drive folder" after phonetic correction → "c drive folder"
        # open_drive pattern matches "open c drive" prefix, "folder" ignored
        r = ir.route("open see drive folder")
        assert r.tool_name == "open_drive"
        assert r.params.get("drive") == "C"

    def test_see_drive_never_opens_application(self, ir):
        r = ir.route("open see drive")
        assert r.tool_name != "open_application", (
            "Phonetic drive must not fall through to open_application"
        )

    def test_sea_drive_never_opens_application(self, ir):
        r = ir.route("open sea drive")
        assert r.tool_name != "open_application"

    def test_cee_drive_never_opens_application(self, ir):
        r = ir.route("open cee drive")
        assert r.tool_name != "open_application"
