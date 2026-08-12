"""
Tests for object_resolver.py — Phase 3.5 Contextual Object Understanding.

Covers the proven incident: "Now in E drive, can you also open perfume
folder?" was routed to open_application(app_name="perfume folder") instead
of a folder open. These tests cover the Object Resolver itself (Part 1-3)
and the open_application invariant (Part 4) that backstops every upstream
router regardless of which one produced a folder-shaped app_name.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services import object_resolver as orz


class TestExplicitTypeNouns(unittest.TestCase):
    """An explicit type noun is decisive and must never be overridden by how
    closely the name fuzzy-matches an application."""

    def test_folder_noun_wins(self):
        r = orz.resolve("Now in E drive, can you also open perfume folder?")
        self.assertEqual(r.object_type, "folder")
        self.assertEqual(r.name, "perfume")
        self.assertGreaterEqual(r.confidence, 0.9)

    def test_folder_noun_wins_without_drive_clause(self):
        r = orz.resolve("open the perfume folder")
        self.assertEqual(r.object_type, "folder")
        self.assertEqual(r.name, "perfume")

    def test_directory_synonym(self):
        r = orz.resolve("show the perfume directory")
        self.assertEqual(r.object_type, "folder")
        self.assertEqual(r.name, "perfume")

    def test_file_noun(self):
        r = orz.resolve("open a file named perfume.txt")
        self.assertEqual(r.object_type, "file")
        self.assertEqual(r.name, "perfume.txt")

    def test_application_noun(self):
        r = orz.resolve("open the Perfume application")
        self.assertEqual(r.object_type, "application")
        self.assertEqual(r.name, "Perfume")

    def test_drive_as_actual_object(self):
        r = orz.resolve("open E drive")
        self.assertEqual(r.object_type, "drive")
        self.assertEqual(r.name, "E")

    def test_drive_mentioned_only_as_scope_is_not_the_object_type(self):
        """'perfume in E drive' — the OBJECT is 'perfume', not the drive;
        the drive is scope. This was a regression caught while building the
        resolver: matching "drive" anywhere in the text misclassified the
        object itself as a drive."""
        r = orz.resolve("open perfume in E drive")
        self.assertNotEqual(r.object_type, "drive")
        self.assertEqual(r.name, "perfume")


class TestScopeAwareFallback(unittest.TestCase):
    """No explicit noun — must prefer a real filesystem match in the
    current scope over guessing it's an application."""

    def test_bare_name_resolves_to_folder_when_scope_has_a_match(self):
        with patch.object(orz, "_get_scope", return_value={"current_folder": "/mnt/e", "drive": "E"}), \
             patch("api.services.file_resolver.resolve") as mock_resolve:
            mock_resolve.return_value = type(
                "R", (), {"decision": "open", "confidence": 0.8, "path": Path("/mnt/e/Perfume")}
            )()
            r = orz.resolve("go inside perfume")
            self.assertEqual(r.object_type, "folder")
            self.assertEqual(r.name, "perfume")

    def test_bare_name_falls_back_to_known_app_without_scope(self):
        with patch.object(orz, "_get_scope", return_value={}):
            r = orz.resolve("now open chrome")
            self.assertEqual(r.object_type, "application")

    def test_bare_name_is_unknown_with_zero_evidence(self):
        with patch.object(orz, "_get_scope", return_value={}):
            r = orz.resolve("now open zzzznonexistentqqq")
            self.assertEqual(r.object_type, "unknown")
            self.assertLess(r.confidence, 0.6)


class TestPronounResolution(unittest.TestCase):
    """Reuses ContextStack — must not re-implement pronoun resolution."""

    def test_open_it_resolves_previous_folder(self):
        from api.services.context_stack import context_stack, ContextEntity
        context_stack.clear()
        context_stack.push(ContextEntity(
            type="folder", value="/mnt/e/Perfume", display="Perfume", source="smart_open",
        ))
        r = orz.resolve("open it")
        self.assertEqual(r.object_type, "folder")
        self.assertEqual(r.name, "/mnt/e/Perfume")
        context_stack.clear()


class TestToolInvariant(unittest.TestCase):
    """Part 4: folder/file-shaped objects must never be dispatched to
    open_application."""

    def test_forbids_open_application_for_folder(self):
        self.assertTrue(orz.forbids_open_application("folder"))

    def test_forbids_open_application_for_file(self):
        self.assertTrue(orz.forbids_open_application("file"))

    def test_allows_open_application_for_application(self):
        self.assertFalse(orz.forbids_open_application("application"))

    def test_allows_open_application_for_drive(self):
        # A drive is filesystem-shaped but has its own dedicated tool
        # (open_drive) rather than being forbidden from open_application
        # specifically — tool_for() below routes it correctly either way.
        self.assertFalse(orz.forbids_open_application("drive"))

    def test_tool_for_mapping(self):
        self.assertEqual(orz.tool_for("folder"), "smart_open")
        self.assertEqual(orz.tool_for("file"), "smart_open")
        self.assertEqual(orz.tool_for("drive"), "open_drive")
        self.assertEqual(orz.tool_for("application"), "open_application")
        self.assertEqual(orz.tool_for("website"), "open_url")


class TestOpenApplicationExecutorInvariant(unittest.TestCase):
    """End-to-end: the exact misrouted call from the incident report must be
    redirected, never launched via the cmd.exe unknown-app fallback."""

    def test_folder_shaped_app_name_is_redirected(self):
        from api.tools.system_tools import _exec_open_application
        with patch("api.tools.system_tools._exec_smart_open") as mock_smart_open:
            mock_smart_open.return_value = "REDIRECTED"
            result = _exec_open_application({"app_name": "perfume folder"}, {})
        self.assertEqual(result, "REDIRECTED")
        mock_smart_open.assert_called_once()
        call_params = mock_smart_open.call_args[0][0]
        self.assertEqual(call_params["query"], "perfume")
        self.assertEqual(call_params["type"], "folder")

    def test_genuine_app_name_is_not_redirected(self):
        from api.tools.system_tools import _exec_open_application
        with patch("api.tools.system_tools._exec_smart_open") as mock_smart_open:
            _exec_open_application({"app_name": "chrome"}, {})
        mock_smart_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
