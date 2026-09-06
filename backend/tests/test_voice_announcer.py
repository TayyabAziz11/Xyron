"""
test_voice_announcer.py — the thread-safe bridge that lets a background
thread (outside the voice WebSocket's asyncio event loop) ask the active
voice session to speak something.
"""
from __future__ import annotations

import asyncio

import pytest

from api.services import voice_announcer


@pytest.fixture(autouse=True)
def _reset_registry():
    # Each test starts from a clean slate regardless of execution order.
    voice_announcer._active_loop = None
    voice_announcer._active_queue = None
    yield
    voice_announcer._active_loop = None
    voice_announcer._active_queue = None


class TestNoActiveSession:
    def test_announce_returns_false_when_nothing_registered(self):
        assert voice_announcer.is_session_active() is False
        assert voice_announcer.announce({"text": "hi"}) is False


class TestRegisterAndAnnounce:
    def test_register_marks_session_active(self):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        try:
            voice_announcer.register(loop, queue)
            assert voice_announcer.is_session_active() is True
        finally:
            loop.close()

    def test_announce_delivers_payload_onto_the_registered_loop(self):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        voice_announcer.register(loop, queue)
        try:
            delivered = voice_announcer.announce({"text": "Qasim sent you a message"})
            assert delivered is True

            async def _drain():
                return await asyncio.wait_for(queue.get(), timeout=1.0)

            got = loop.run_until_complete(_drain())
            assert got == {"text": "Qasim sent you a message"}
        finally:
            loop.close()

    def test_unregister_with_matching_loop_clears_registration(self):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        voice_announcer.register(loop, queue)
        voice_announcer.unregister(loop)
        assert voice_announcer.is_session_active() is False
        loop.close()

    def test_unregister_with_stale_loop_does_not_clear_a_newer_registration(self):
        old_loop = asyncio.new_event_loop()
        old_queue: asyncio.Queue = asyncio.Queue()
        voice_announcer.register(old_loop, old_queue)

        new_loop = asyncio.new_event_loop()
        new_queue: asyncio.Queue = asyncio.Queue()
        voice_announcer.register(new_loop, new_queue)

        # The old session's cleanup fires late, after a newer session has
        # already registered — must not clobber the newer registration.
        voice_announcer.unregister(old_loop)
        assert voice_announcer.is_session_active() is True

        old_loop.close()
        new_loop.close()

    def test_announce_after_unregister_returns_false(self):
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        voice_announcer.register(loop, queue)
        voice_announcer.unregister(loop)
        assert voice_announcer.announce({"text": "too late"}) is False
        loop.close()
