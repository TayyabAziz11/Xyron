"""
Unit tests for store_agent.py — the canonical Microsoft Store install-intent
detector, cancel handling, and app_finder fuzzy-match hardening.

Covers the regression case reported in production: "Open Microsoft Store and
install Instagram" was misrouted to open_application (app_name = the whole
sentence), which fuzzy-matched to "Microsoft Visual Studio Installer".
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = str(Path(__file__).parent.parent)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api.services.store_agent import (
    detect_install_intent,
    is_cancel_phrase,
    store_context_active,
    cancel_install_context,
    StoreInstallState,
    set_store_state,
    CONTINUE_INSTALL_WORDS,
)
from api.services.active_context import ActiveContextService
import api.tools.core.app_finder as app_finder


# ── detect_install_intent ──────────────────────────────────────────────────────

class TestDetectInstallIntent:

    def test_the_reported_bug_phrase(self):
        # The exact transcript from the production bug report.
        r = detect_install_intent("open Microsoft Store and install Instagram")
        assert r is not None
        assert r.product == "Instagram"
        assert r.phrasing == "compound"

    def test_real_whisper_transcript_with_comma(self):
        # Discovered via live audio E2E test: real Whisper STT naturally inserts
        # a comma at the clause boundary ("store, and install") that the
        # original comma-less regex didn't tolerate, silently falling through
        # to open_application despite the bug being "fixed" against clean text.
        r = detect_install_intent("open microsoft store, and install instagram.")
        assert r is not None
        assert r.product == "instagram"
        assert r.phrasing == "compound"

    def test_bare_install_with_trailing_comma(self):
        r = detect_install_intent("install instagram,")
        assert r is not None and r.product == "instagram"

    def test_compound_lowercase_with_punctuation(self):
        r = detect_install_intent("open microsoft store and install instagram.")
        assert r is not None
        assert r.product == "instagram"

    def test_bare_install(self):
        r = detect_install_intent("install instagram")
        assert r is not None and r.product == "instagram"

    def test_bare_download(self):
        r = detect_install_intent("download spotify")
        assert r is not None and r.product == "spotify"

    def test_bare_get(self):
        r = detect_install_intent("get whatsapp")
        assert r is not None and r.product == "whatsapp"

    def test_bare_telegram(self):
        r = detect_install_intent("install telegram")
        assert r is not None and r.product == "telegram"

    def test_from_store_suffix(self):
        r = detect_install_intent("install netflix from the store")
        assert r is not None and r.product == "netflix"

    def test_articled_app_suffix(self):
        r = detect_install_intent("install the vs code app")
        assert r is not None and r.product == "vs code"

    def test_bare_open_microsoft_store_is_not_an_install(self):
        # No install verb — must not be treated as a product install.
        assert detect_install_intent("open microsoft store") is None

    def test_install_this_is_excluded(self):
        assert detect_install_intent("install this policy") is None

    def test_get_me_a_coffee_is_excluded(self):
        assert detect_install_intent("get me a coffee") is None

    def test_npm_package_excluded(self):
        assert detect_install_intent("install npm package lodash") is None

    def test_pip_install_excluded(self):
        assert detect_install_intent("pip install requests") is None

    def test_apt_get_excluded(self):
        assert detect_install_intent("get apt update") is None

    def test_empty_string(self):
        assert detect_install_intent("") is None
        assert detect_install_intent("   ") is None


# ── Cancel handling ─────────────────────────────────────────────────────────────

class TestCancelHandling:

    def test_is_cancel_phrase_variants(self):
        for phrase in ("cancel install", "never mind", "nevermind", "stop",
                       "forget it", "cancel", "don't install", "no, don't"):
            assert is_cancel_phrase(phrase), f"expected cancel: {phrase!r}"

    def test_continue_is_not_a_cancel_phrase(self):
        assert not is_cancel_phrase("continue")
        assert not is_cancel_phrase("install it")
        assert not is_cancel_phrase("yes")

    def test_store_context_active_via_platform(self):
        assert store_context_active({"current_platform": "microsoft_store"}, {})

    def test_store_context_active_via_pending_candidates(self):
        assert store_context_active({}, {"pending_store_candidates": {"x": 1}})

    def test_store_context_active_via_pending_open_after_install(self):
        assert store_context_active({}, {"pending_open_after_install": {"app_name": "x"}})

    def test_store_context_inactive(self):
        assert not store_context_active({"current_platform": None}, {})

    def test_cancel_install_context_clears_session_state(self):
        session_state = {
            "pending_store_candidates": {"candidates": []},
            "pending_open_after_install": {"app_name": "instagram"},
        }
        actx = ActiveContextService()
        actx.update_from_tool("install_store_app", {"app_name": "instagram"}, {"app_id": "X1"}, success=True)
        assert actx.current_platform() == "microsoft_store"

        cancel_install_context(actx, session_state)

        assert session_state["pending_store_candidates"] is None
        assert session_state["pending_open_after_install"] is None
        assert actx.current_platform() is None
        assert session_state["store_install_state"] == StoreInstallState.CANCELLED.value

    def test_cancel_install_context_leaves_unrelated_platform_alone(self):
        session_state = {"pending_store_candidates": None, "pending_open_after_install": None}
        actx = ActiveContextService()
        actx.update_from_tool("search_youtube", {"query": "believer"}, {}, success=True)
        assert actx.current_platform() == "youtube"

        cancel_install_context(actx, session_state)

        # Not a store platform — must not be wiped by a store-scoped cancel.
        assert actx.current_platform() == "youtube"


# ── State tracking ──────────────────────────────────────────────────────────────

class TestStoreState:

    def test_set_store_state_records_value(self):
        session_state: dict = {}
        set_store_state(session_state, StoreInstallState.WAITING_INSTALL)
        assert session_state["store_install_state"] == "waiting_install"
        set_store_state(session_state, StoreInstallState.INSTALLING)
        assert session_state["store_install_state"] == "installing"


# ── Shared continue-word list used by both follow-up resolvers ────────────────

class TestContinueWords:

    def test_continue_and_proceed_present(self):
        import re
        pat = re.compile(CONTINUE_INSTALL_WORDS, re.IGNORECASE)
        assert pat.fullmatch("continue")
        assert pat.fullmatch("proceed")
        assert pat.fullmatch("yes")
        assert not pat.fullmatch("cancel")


# ── app_finder fuzzy-match hardening ────────────────────────────────────────────

class TestAppFinderFuzzyGuard:

    def setup_method(self):
        self._orig_index = dict(app_finder._APP_INDEX)
        self._orig_built = app_finder._APP_INDEX_BUILT
        app_finder._APP_INDEX = {
            "microsoft visual studio installer": {
                "name": "Microsoft Visual Studio Installer", "path": "x", "source": "startmenu",
            },
            "chrome": {"name": "Chrome", "path": "chrome.exe", "source": "registry"},
            "spotify": {"name": "Spotify", "path": "x", "source": "registry"},
        }
        app_finder._APP_INDEX_BUILT = True

    def teardown_method(self):
        app_finder._APP_INDEX = self._orig_index
        app_finder._APP_INDEX_BUILT = self._orig_built

    def test_reported_bug_phrase_never_fuzzy_matches(self):
        entry, match_type = app_finder._search_index("microsoft store and install instagram")
        assert entry is None
        assert match_type == ""

    def test_bare_verb_query_skips_fuzzy(self):
        entry, match_type = app_finder._search_index("install instagram")
        assert entry is None

    def test_legit_typo_still_fuzzy_matches(self):
        entry, match_type = app_finder._search_index("crome")
        assert entry is not None
        assert entry["name"] == "Chrome"
        assert match_type == "fuzzy"

    def test_legit_typo_spotify_still_fuzzy_matches(self):
        entry, match_type = app_finder._search_index("spotifyy")
        assert entry is not None
        assert entry["name"] == "Spotify"

    def test_exact_match_unaffected(self):
        entry, match_type = app_finder._search_index("chrome")
        assert entry is not None and match_type == "exact"
