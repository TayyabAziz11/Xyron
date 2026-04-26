"""Tests for the natural voice layer: English enforcement, SQLite memory bridge."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from voice.response_generator import _is_non_english, generate_assistant_response
from api.services.sqlite_memory import record_turn, get_recent_context, get_full_context_for_prompt


# ── Test 1: English detection ─────────────────────────────────────────────────

def test_english_detection():
    assert _is_non_english("Opening Chrome") is False
    assert _is_non_english("Done. I processed your request.") is False
    assert _is_non_english("مرحبا بالعالم") is True    # Arabic
    assert _is_non_english("آپ کا شکریہ") is True       # Urdu
    assert _is_non_english("नमस्ते") is True             # Hindi


# ── Test 2: record_turn + retrieval ──────────────────────────────────────────

def test_record_turn():
    """record_turn saves user+assistant turns; get_recent_context retrieves them."""
    session = f"test_record_{uuid.uuid4().hex}"
    record_turn(session, "open chrome", "Chrome is now open.", tool_name="open_app")
    context = get_recent_context(session, n=5)
    roles = [m["role"] for m in context]
    texts = [m["content"] for m in context]
    assert "user" in roles
    assert "assistant" in roles
    assert "open chrome" in texts
    assert "Chrome is now open." in texts


# ── Test 3: full context string ───────────────────────────────────────────────

def test_get_full_context():
    """get_full_context_for_prompt returns a non-empty string."""
    session = f"test_ctx_{uuid.uuid4().hex}"
    record_turn(session, "what time is it", "It's 3 PM.")
    result = get_full_context_for_prompt(session)
    assert isinstance(result, str)
    assert len(result) > 0


# ── Test 4: template fallback with no API key ─────────────────────────────────

def test_response_fallback():
    """generate_assistant_response returns a non-empty string even without an API key."""
    resp = generate_assistant_response(
        command_text="open chrome",
        result="Chrome launched",
        agent="system",
        skill="open_app",
    )
    assert isinstance(resp, str)
    assert len(resp) > 0


# ── Test 5: session isolation ─────────────────────────────────────────────────

def test_session_isolation():
    """Two different session IDs do not share conversation history."""
    session_a = f"test_iso_a_{uuid.uuid4().hex}"
    session_b = f"test_iso_b_{uuid.uuid4().hex}"

    record_turn(session_a, "open spotify", "Spotify is open.")
    record_turn(session_b, "set volume to 50", "Volume set to 50%.")

    texts_a = {m["content"] for m in get_recent_context(session_a, n=10)}
    texts_b = {m["content"] for m in get_recent_context(session_b, n=10)}

    assert "Spotify is open." in texts_a
    assert "Volume set to 50%." not in texts_a

    assert "Volume set to 50%." in texts_b
    assert "Spotify is open." not in texts_b
