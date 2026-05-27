"""
Tests for Windows Settings deep-link routing.

Verifies that natural-language settings phrases route to open_system_settings
with the correct page parameter — never to open_application / app_finder.

Run: pytest tests/test_settings_routing.py -v
"""
from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path regardless of cwd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(scope="module")
def router():
    from api.services.intent_router import IntentRouter
    return IntentRouter()


# ── display settings ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open display settings",
    "open windows display settings",
    "display settings",
    "screen settings",
    "monitor settings",
    "show me display settings",
    "open display settings please",
    "open display settings for me",
    "can you open display settings for me",
    "change display settings",
    "screen resolution settings",
    "open monitor settings",
])
def test_display_settings(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings", (
        f"{phrase!r} → {result.tool_name!r} (expected open_system_settings)"
    )
    assert result.params.get("page") == "display", (
        f"{phrase!r} → page={result.params.get('page')!r} (expected 'display')"
    )


# ── sound settings ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open sound settings",
    "sound settings",
])
def test_sound_settings(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings"
    assert result.params.get("page") == "sound"


# ── bluetooth settings ────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open bluetooth settings",
    "bluetooth settings",
])
def test_bluetooth_settings(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings"
    assert result.params.get("page") == "bluetooth"


# ── network / wifi ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_page", [
    ("open wifi settings",    "wifi"),
    ("wifi settings",         "wifi"),
    ("open network settings", "network"),
    ("network settings",      "network"),
])
def test_network_settings(router, phrase, expected_page):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings"
    assert result.params.get("page") == expected_page


# ── other pages ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_page", [
    ("open privacy settings",        "privacy"),
    ("open windows update",          "update"),
    ("open window updates",          "update"),   # Issue 1 — singular "window"
    ("open windows updates",         "update"),   # plural updates
    ("update settings",              "update"),
    ("open update settings",         "update"),
    ("open power settings",          "power"),
    ("open apps settings",           "apps"),
])
def test_other_settings_pages(router, phrase, expected_page):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings"
    assert result.params.get("page") == expected_page


# ── must NOT reach app_finder ─────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open display settings",
    "display settings",
    "open sound settings",
    "open bluetooth settings",
    "monitor settings",
    "screen settings",
])
def test_never_routes_to_open_application(router, phrase):
    result = router.route(phrase)
    assert result.tool_name != "open_application", (
        f"{phrase!r} incorrectly routed to open_application "
        f"(app_name={result.params.get('app_name')!r})"
    )


# ── catch-all polite suffix stripping ─────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_app", [
    ("open chrome for me please",   "chrome"),
    ("open notepad please",         "notepad"),
    ("open spotify buddy",          "spotify"),
    ("open vscode now",             "vscode"),
])
def test_polite_suffix_stripped_from_app_name(router, phrase, expected_app):
    result = router.route(phrase)
    assert result.tool_name == "open_application"
    assert result.params.get("app_name") == expected_app, (
        f"{phrase!r} → app_name={result.params.get('app_name')!r} "
        f"(expected {expected_app!r})"
    )


# ── Urdu shortcuts (Issue 5) ──────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_page", [
    ("wifi kholo",              "wifi"),
    ("wifi on karo",            "wifi"),
    ("wifi dikao",              "wifi"),
    ("network kholo",           "network"),
    ("bluetooth kholo",         "bluetooth"),
    ("display kholo",           "display"),
    ("updates kholo",           "update"),
    ("windows update kholo",    "update"),
])
def test_urdu_settings_shortcuts(router, phrase, expected_page):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings", (
        f"{phrase!r} → {result.tool_name!r} (expected open_system_settings)"
    )
    assert result.params.get("page") == expected_page, (
        f"{phrase!r} → page={result.params.get('page')!r} (expected {expected_page!r})"
    )


# ── open wifi (standalone, no "settings" keyword) — Issue 5 ──────────────────

@pytest.mark.parametrize("phrase", [
    "open wifi",
    "open wifi settings",
    "show wifi",
    "show wifi settings",
])
def test_open_wifi_routes_to_settings(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "open_system_settings", (
        f"{phrase!r} → {result.tool_name!r} (expected open_system_settings)"
    )
    assert result.params.get("page") == "wifi", (
        f"{phrase!r} → page={result.params.get('page')!r} (expected 'wifi')"
    )
