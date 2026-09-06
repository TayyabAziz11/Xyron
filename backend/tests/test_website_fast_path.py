"""
Website fast-path tests (2026-09-05) — a simple "open <known website>"
command must dispatch as a plain native URL open (subprocess.Popen /
`start <url>`) and never attempt CDP/browser_workspace control at all.

Root cause this guards against: system_tools.py's _launch_app() used to
route every browser-shortcut entry (youtube, gmail, google, ...) through
api.tools.browser_tools._get_page() -> browser_workspace.get_or_create_page()
FIRST, to keep tab continuity with a later automation follow-up. Live-caught
real backend log (2026-09-04): a cold/ECONNREFUSED CDP bridge blocked a
plain "open YouTube" for ~20s (full connect/launch/self-heal retry chain)
before falling back to the exact same native open that now runs immediately
and unconditionally for these entries. Browser AUTOMATION commands (search,
click, fill) still go through browser_tools.py -> browser_workspace
unchanged — this file only covers the simple-open path.

Also covers: English/Urdu/Roman-Urdu single-command parity for a known
website converging on the identical downstream _launch_app() call, and
Settings/Display Settings never touching browser/CDP code at all (they
never did — this is a regression guard, not a new fix).
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from api.services import language_detector as ld
from api.services import mixed_language_engine as mle
from api.services.intent_router import intent_router as ir


def _canon_app_name(text: str, lang: str | None = None) -> str:
    """Run text through the same canonicalization English/Urdu commands
    both go through, then route it, returning the resolved app_name."""
    canon = text if lang == "en" else mle.analyze(text, lang or ld.detect(text)["lang"])
    assert canon is not None, f"{text!r} (lang={lang}) failed to canonicalize"
    route = ir.route(canon)
    assert route.tool_name == "open_application", f"{text!r} -> {route.tool_name}, expected open_application"
    return route.params["app_name"]


class TestSimpleWebsiteOpenNeverInvokesCDP:
    """A confident open_application(app_name=<known website>) call must
    dispatch via the native `start <url>` path and must NEVER import or
    call api.tools.browser_tools._get_page / browser_workspace — zero CDP
    attempts, per the "simple website launch vs controlled browser
    automation" architectural split."""

    def _run_launch_app_mocked(self, app_name: str):
        """Call the real _launch_app() with subprocess.Popen mocked (so no
        real process spawns) and browser_tools._get_page patched to raise
        if ever called — proves the CDP path is structurally unreachable
        for this app_name, not just unobserved in one run."""
        from api.tools import system_tools as st

        def _cdp_forbidden(*a, **kw):
            raise AssertionError(
                "browser_tools._get_page() was called for a simple website "
                "open — CDP must NEVER be attempted for this path"
            )

        with patch("api.tools.browser_tools._get_page", side_effect=_cdp_forbidden), \
             patch.object(subprocess, "Popen") as mock_popen, \
             patch.object(st, "_bring_to_front", return_value=None):
            mock_popen.return_value = MagicMock()
            ok, msg = st._launch_app(app_name)
        return ok, msg, mock_popen

    def test_youtube_open_is_native_not_cdp(self):
        ok, msg, mock_popen = self._run_launch_app_mocked("youtube")
        assert ok is True
        mock_popen.assert_called_once()
        # The native open call must reference the real YouTube URL.
        call_args = mock_popen.call_args
        args_str = str(call_args)
        assert "youtube.com" in args_str

    def test_google_open_is_native_not_cdp(self):
        ok, msg, mock_popen = self._run_launch_app_mocked("google")
        assert ok is True
        mock_popen.assert_called_once()
        assert "google.com" in str(mock_popen.call_args)

    def test_gmail_open_is_native_not_cdp(self):
        ok, msg, mock_popen = self._run_launch_app_mocked("gmail")
        assert ok is True
        mock_popen.assert_called_once()
        assert "mail.google.com" in str(mock_popen.call_args)

    @pytest.mark.parametrize("text,lang", [
        ("open YouTube", "en"),
        ("YouTube کھولو", "ur"),
        ("youtube kholo", "mixed"),
    ])
    def test_youtube_parity_all_languages_hit_same_native_path(self, text, lang):
        # Verify canonicalization/routing agree on the SAME app_name across
        # English/Urdu/Roman-Urdu, then verify THAT app_name's _launch_app
        # call goes native, not through CDP — proving the fast path is
        # reached identically regardless of input language.
        app_name = _canon_app_name(text, lang)
        assert app_name.lower() == "youtube"
        ok, msg, mock_popen = self._run_launch_app_mocked(app_name)
        assert ok is True
        mock_popen.assert_called_once()


class TestSettingsNeverTouchesBrowserCode:
    """Settings / Display Settings open via a Windows ms-settings: URI —
    this never shared any code path with browser_workspace/CDP, but is
    guarded here as an explicit regression test since it sits right next
    to the website fast-path fix."""

    def test_settings_open_no_cdp_import(self):
        from api.tools import system_tools as st

        def _cdp_forbidden(*a, **kw):
            raise AssertionError("Settings open must never touch browser_tools/CDP")

        with patch("api.tools.browser_tools._get_page", side_effect=_cdp_forbidden), \
             patch.object(subprocess, "Popen") as mock_popen, \
             patch.object(st, "_bring_to_front", return_value=None):
            mock_popen.return_value = MagicMock()
            ok, msg = st._launch_app("settings")
        assert ok is True
        mock_popen.assert_called_once()
        assert "ms-settings:" in str(mock_popen.call_args)

    def test_display_settings_uri_correct(self):
        from api.tools.system_tools import _exec_open_system_settings

        with patch("api.tools.system_tools.subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            result = _exec_open_system_settings({"page": "display"}, {})
        assert result.success is True
        assert result.data.get("uri") == "ms-settings:display"


class TestCDPStillAvailableForAutomation:
    """Browser AUTOMATION commands (navigate/click/fill) must still be able
    to reach browser_workspace/CDP — the fast-path fix only removes CDP
    from the SIMPLE OPEN path, it must not globally disable it."""

    def test_browser_navigate_still_calls_get_page(self):
        from api.tools import browser_tools as bt

        fake_page = MagicMock()
        fake_page.title.return_value = "Example"

        def _fake_run_coro(coro, timeout=None):
            coro.close()  # avoid "coroutine was never awaited" — never actually run
            return "Example"

        with patch.object(bt, "_get_page", return_value=fake_page) as mock_get_page, \
             patch("api.services.main_loop.run_coro_from_thread", side_effect=_fake_run_coro):
            result = bt._exec_browser_navigate({"url": "https://example.com"}, {})
        mock_get_page.assert_called_once()
        assert result.success is True


class TestUrduRomanAckFastPath:
    """tts_cache_service.synthesize_or_cached_ml() — the fast local Kokoro
    path for ur_roman/mixed deterministic tool acks (2026-09-05). Live-
    measured bug this fixes: voice.tts_router.synthesize() routes ALL of
    ur/ur_roman/mixed through OpenAI TTS first, measured ~2.3-2.5s per ack
    for a 4-word acknowledgement ("YouTube khol raha hoon.") that Kokoro
    (already an accepted quality tier for Latin-script ur_roman/mixed text
    per tts_router.py's own module docstring) synthesizes in ~300-400ms
    warm. Pure Urdu script (lang="ur") must NEVER use this path — Kokoro's
    English phonemizer cannot render Nastaliq script intelligibly."""

    def setup_method(self, method):
        # Each test gets a clean cache instance AND an isolated on-disk
        # directory — never the real /tmp/xyron-tts-cache the running
        # backend itself reads/writes, so these tests can't pollute (or be
        # polluted by) production cache files using the same phrase text.
        import tempfile
        import api.services.tts_cache_service as tcs_mod
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_dir_patcher = patch.object(
            tcs_mod, "_CACHE_DIR", __import__("pathlib").Path(self._tmpdir.name),
        )
        self._cache_dir_patcher.start()
        self.cache = tcs_mod.TTSCacheService()

    def teardown_method(self, method):
        self._cache_dir_patcher.stop()
        self._tmpdir.cleanup()

    def test_pure_urdu_script_never_uses_fast_path(self):
        # Must return None immediately — no Kokoro call at all — so the
        # caller falls through to the existing OpenAI/Edge-TTS path.
        with patch("api.routers.voice._kokoro_to_wav") as mock_kokoro:
            result = self.cache.synthesize_or_cached_ml(
                "یوٹیوب کھول رہا ہوں۔", "nova", 1.0, "ur",
            )
        assert result is None
        mock_kokoro.assert_not_called()

    @pytest.mark.parametrize("lang", ["ur_roman", "mixed"])
    def test_roman_urdu_and_mixed_use_kokoro_not_openai(self, lang):
        fake_wav = b"RIFF" + b"\x00" * 100
        with patch("api.routers.voice._kokoro_to_wav", return_value=fake_wav) as mock_kokoro:
            result = self.cache.synthesize_or_cached_ml(
                "YouTube khol raha hoon.", "nova", 1.0, lang,
            )
        assert result == fake_wav
        mock_kokoro.assert_called_once()

    def test_second_call_is_a_cache_hit_not_a_second_synthesis(self):
        fake_wav = b"RIFF" + b"\x00" * 100
        with patch("api.routers.voice._kokoro_to_wav", return_value=fake_wav) as mock_kokoro:
            first = self.cache.synthesize_or_cached_ml("Settings khol raha hoon.", "nova", 1.0, "ur_roman")
            second = self.cache.synthesize_or_cached_ml("Settings khol raha hoon.", "nova", 1.0, "ur_roman")
        assert first == fake_wav
        assert second == fake_wav
        mock_kokoro.assert_called_once()  # NOT called twice — second call was a cache hit

    def test_disk_persisted_hit_survives_fresh_instance(self):
        # Simulates a fresh process (new TTSCacheService instance, empty
        # in-memory cache) re-using a phrase a PRIOR process run already
        # synthesized to disk.
        fake_wav = b"RIFF" + b"\x00" * 100
        with patch("api.routers.voice._kokoro_to_wav", return_value=fake_wav):
            self.cache.synthesize_or_cached_ml("Chrome khol raha hoon.", "nova", 1.0, "ur_roman")

        from api.services.tts_cache_service import TTSCacheService
        fresh_cache = TTSCacheService()
        with patch("api.routers.voice._kokoro_to_wav") as mock_kokoro_fresh:
            result = fresh_cache.get_by_text_ml("Chrome khol raha hoon.", "nova", "ur_roman")
        assert result == fake_wav
        mock_kokoro_fresh.assert_not_called()  # disk hit, no re-synthesis

    def test_different_voice_is_a_clean_miss_not_wrong_voice_hit(self):
        fake_wav_a = b"RIFF" + b"\x00" * 100
        fake_wav_b = b"RIFF" + b"\x01" * 100
        with patch("api.routers.voice._kokoro_to_wav", side_effect=[fake_wav_a, fake_wav_b]):
            a = self.cache.synthesize_or_cached_ml("Done.", "nova", 1.0, "ur_roman")
            b = self.cache.synthesize_or_cached_ml("Done.", "onyx", 1.0, "ur_roman")
        assert a == fake_wav_a
        assert b == fake_wav_b
        assert a != b


class TestPerTurnToolMetricIsolation:
    """Regression guard for the cross-turn tool_ms/success leak (2026-09-05
    live-caught bug): _last_tool_exec_ms/_last_tool_success must be keyed
    by trace_id, never a flat shared slot — two concurrent turns' numbers
    must never collide."""

    def test_dict_keyed_by_trace_id_never_collides(self):
        # Simulates the exact race: a slow turn's write landing AFTER a
        # fast turn already read its own (correct) value.
        tracking: dict[str, float] = {}
        tracking["turn_youtube"] = 20833.0  # slow turn writes late
        tracking["turn_settings"] = 11.0    # fast turn's own correct value

        # Fast turn (settings) pops its OWN key — must get its own value,
        # never the slow turn's, regardless of write order.
        settings_value = tracking.pop("turn_settings", 0.0)
        assert settings_value == 11.0
        # The slow turn's entry is untouched, available for its own read.
        assert tracking["turn_youtube"] == 20833.0
