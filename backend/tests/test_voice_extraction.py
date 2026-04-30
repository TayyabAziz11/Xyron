"""
Tests for voice.py extraction functions — the code we actually changed.
These cover _extract_folder_name, _extract_delete_target, and _strip_punct.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Import the functions under test directly
from api.routers.voice import (
    _extract_folder_name,
    _extract_delete_target,
    _strip_punct,
)


# ── _strip_punct ──────────────────────────────────────────────────────────────

def test_strip_punct_removes_trailing_period():
    assert _strip_punct("teyab.") == "teyab"

def test_strip_punct_removes_trailing_comma():
    assert _strip_punct("PSJF,") == "PSJF"

def test_strip_punct_clean_name_unchanged():
    assert _strip_punct("TestXyron") == "TestXyron"


# ── _extract_folder_name ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # "name as X" — Pakistani English pattern (was broken: returned "as X")
    ("Create a folder in D drive, name as teyab.", "teyab"),
    ("create a folder on desktop name as tayyab", "tayyab"),
    # "named as X"
    ("Create a folder on desktop named as psjf.", "psjf"),
    ("create a folder named as Projects", "Projects"),
    # "name is X"
    ("create a folder on Desktop name is PSJF", "PSJF"),
    ("Create a folder in D drive name is Reports", "Reports"),
    # "named X" (no "as")
    ("create folder named TestXyron on Desktop", "TestXyron"),
    # "called X"
    ("create folder called MyWork in D drive", "MyWork"),
    # "create folder NAME" — word directly after folder
    ("create folder TestXyron on Desktop", "TestXyron"),
    ("create folder PSJF", "PSJF"),
    # Multi-word name
    ("create folder Test Xyron on Desktop", "Test Xyron"),
    # Location-only (no name) — should return empty
    ("create a folder in D drive", ""),
    ("create a folder on desktop", ""),
])
def test_extract_folder_name(text, expected):
    result = _extract_folder_name(text)
    assert result == expected, f"Got {result!r} for {text!r}"


# ── _extract_delete_target ────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    # "named as X" — was broken: returned "as teyup"
    ("delete a folder in D drive named as teyup", "teyup"),
    ("delete a folder in D drive named as tayyab", "tayyab"),
    # "name as X"
    ("delete a folder in D drive name as tayyab", "tayyab"),
    # "name is X" — was broken: returned "is STF"
    ("delete a folder in D drive name is STF", "STF"),
    ("delete folder in D drive name is Reports", "Reports"),
    # "named X" (no "as")
    ("delete the folder named TestXyron", "TestXyron"),
    ("delete folder named Projects on Desktop", "Projects"),
    # Standard "delete folder NAME"
    ("delete folder TestXyron", "TestXyron"),
    ("delete the folder PSJF on Desktop", "PSJF"),
    # Trailing punctuation
    ("delete the folder PSJF.", "PSJF"),
    ("please delete the folder PSJF", "PSJF"),
    # Multi-word / Whisper splits hyphen: "s-games" → "s games"
    ("delete the folder in the drive, name is s-games", "s-games"),
    ("delete folder in d drive name is s games", "s games"),
    # Pattern 1 correctly reads "the name is s games" → folder name is "s games"
    ("no in d drive I say delete the folder the name is s games", "s games"),
])
def test_extract_delete_target(text, expected):
    result = _extract_delete_target(text)
    assert result == expected, f"Got {result!r} for {text!r}"
