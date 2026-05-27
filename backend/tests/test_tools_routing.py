"""
Tests for file, folder, and app tool routing.

Verifies that natural-language file/folder/app phrases route to the correct
tools — never to open_application when a dedicated tool exists.

Run: pytest tests/test_tools_routing.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(scope="module")
def router():
    from api.services.intent_router import IntentRouter
    return IntentRouter()


# ── find folder ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "find folder named alpha",
    "find folder called alpha",
    "find folder alpha",
    "locate folder ios",
    "where is folder projects",
    "find the ios folder",
    "locate my projects folder",
])
def test_find_folder(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "smart_open", (
        f"{phrase!r} → {result.tool_name!r} (expected smart_open)"
    )
    assert result.params.get("type") == "folder", (
        f"{phrase!r} → type={result.params.get('type')!r} (expected 'folder')"
    )


# ── open folder (arbitrary names) ─────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open folder alpha",
    "open folder called alpha",
    "open folder named My Projects",
    "open the folder called ios",
    "browse folder Games",
    "show folder Backups",
])
def test_open_arbitrary_folder(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "smart_open", (
        f"{phrase!r} → {result.tool_name!r} (expected smart_open)"
    )
    assert result.params.get("type") == "folder"


# ── open system folder — must still hit open_directory ───────────────────────

@pytest.mark.parametrize("phrase, expected_path", [
    ("open downloads folder", "downloads"),
    ("open my documents",     "documents"),
    ("open the desktop",      "desktop"),
    ("open pictures folder",  "pictures"),
])
def test_system_folders_still_use_open_directory(router, phrase, expected_path):
    result = router.route(phrase)
    assert result.tool_name == "open_directory", (
        f"{phrase!r} → {result.tool_name!r} (expected open_directory)"
    )
    assert result.params.get("path") == expected_path


# ── find file ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    # Note: "resume" is intentionally excluded — it is ambiguous with media_control
    # (resume playback). Use unambiguous file names in voice commands.
    "find file budget",
    "find file called budget",
    "find file named report",
    "locate file budget",
    "search for file project",
    "look for file budget",
    "find my budget file",
    "locate the budget file",
])
def test_find_file(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "smart_open", (
        f"{phrase!r} → {result.tool_name!r} (expected smart_open)"
    )
    assert result.params.get("type") == "file", (
        f"{phrase!r} → type={result.params.get('type')!r} (expected 'file')"
    )


# ── rename folder ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "rename folder alpha to beta",
    "rename alpha to beta",
    "change folder name alpha to beta",
])
def test_rename_folder(router, phrase):
    result = router.route(phrase)
    assert result.tool_name == "rename_file", (
        f"{phrase!r} → {result.tool_name!r} (expected rename_file)"
    )
    assert result.params.get("new_name", "").lower() == "beta", (
        f"{phrase!r} → new_name={result.params.get('new_name')!r} (expected 'beta')"
    )


# ── create folder ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_name", [
    ("create folder alpha in C drive",  "alpha"),
    ("make a new folder called alpha",  "alpha"),
    ("create folder alpha",             "alpha"),
])
def test_create_folder(router, phrase, expected_name):
    result = router.route(phrase)
    assert result.tool_name == "create_folder", (
        f"{phrase!r} → {result.tool_name!r} (expected create_folder)"
    )
    assert result.params.get("name", "").lower() == expected_name


# ── open app ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, expected_app", [
    ("open VS Code",             "VS Code"),
    ("open chrome",              "chrome"),
    ("launch spotify",           "spotify"),
    ("open notepad",             "notepad"),
])
def test_open_app(router, phrase, expected_app):
    result = router.route(phrase)
    assert result.tool_name == "open_application", (
        f"{phrase!r} → {result.tool_name!r} (expected open_application)"
    )
    assert result.params.get("app_name", "").lower() == expected_app.lower(), (
        f"{phrase!r} → app_name={result.params.get('app_name')!r} (expected {expected_app!r})"
    )


# ── open Windows settings ─────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "open Windows settings",
    "open settings",
])
def test_open_windows_settings(router, phrase):
    result = router.route(phrase)
    # Settings can route to open_application with app_name="settings" or
    # open_system_settings — both are acceptable
    assert result.tool_name in ("open_application", "open_system_settings"), (
        f"{phrase!r} → {result.tool_name!r} (expected open_application or open_system_settings)"
    )


# ── generic LLM must NOT answer "I can't do that" for tools that exist ───────

@pytest.mark.parametrize("phrase", [
    "find folder alpha",
    "find file budget",
    "open folder named ios",
    "rename folder alpha to beta",
    "create folder alpha in C drive",
    "open VS Code",
])
def test_no_llm_fallthrough(router, phrase):
    """All these should match a specific tool, not fall through to LLM (tool_name=None)."""
    result = router.route(phrase)
    assert result.tool_name is not None, (
        f"{phrase!r} fell through to LLM (tool_name=None) — add a routing rule"
    )
