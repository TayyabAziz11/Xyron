"""
test_hybrid_stt_retry.py — regression coverage for _needs_retry()'s
entity-hint keyword checks in hybrid_stt_router.py.

Two real bugs, both from live backend logs:

1. The user said something like "message Qasim" — Whisper's tiny.en fast
   model mis-heard it as "No message. Custom." (confidence -0.43, 3 words).
   Neither the confidence rule (needs < -0.45) nor the word-count rule
   (needs 4+ words) fired, so the accurate retry model never ran, and "No
   message. Custom." went straight to the LLM fallback instead of being
   recognized as a WhatsApp command. Fix: any message/WhatsApp-related
   keyword in the transcript forces a retry — plus fixing a punctuation-
   stripping bug that would have stopped "message" from ever matching
   mid-sentence (only the whole string's trailing punctuation was
   stripped, not each word's).

2. Follow-up regression from fix #1: forcing that retry UNCONDITIONALLY
   meant a real WhatsApp reply command the fast model already transcribed
   correctly and confidently ("Send him a reply, I am good...", conf=-0.15)
   still paid the full ~1.1s accurate-model round trip for nothing,
   blowing the turn's latency budget (3833ms measured against a 2000ms
   target). Fix: gate the messaging-keyword rule on confidence (< -0.25)
   so it still catches genuinely uncertain mishearings but skips the retry
   once the fast model is already reasonably sure. The pre-existing
   folder/file/directory/document rule is intentionally NOT gated — it
   predates both bugs above and hasn't shown the same latency problem.
"""
from __future__ import annotations

from voice.hybrid_stt_router import _needs_retry


class TestMessagingEntityHintForcesRetryWhenUncertain:
    def test_real_world_mishearing_of_message_qasim(self):
        # The exact transcript + confidence from the live log that
        # motivated this rule in the first place.
        needs_retry, reason = _needs_retry(
            {"text": "No message. Custom.", "confidence": -0.43}, audio_dur_ms=2920,
        )
        assert needs_retry is True
        assert reason.startswith("msg_entity_hint_keyword")

    def test_message_keyword_matches_even_with_internal_punctuation(self):
        # Regression for the punctuation-stripping bug specifically: the
        # keyword sits mid-sentence with a period directly attached to it,
        # which the old `text.lower().rstrip('.!?,')` (whole-string only)
        # would never strip.
        needs_retry, reason = _needs_retry(
            {"text": "Hello message. World.", "confidence": -0.4}, audio_dur_ms=2000,
        )
        assert needs_retry is True
        assert reason.startswith("msg_entity_hint_keyword")

    def test_whatsapp_keyword_forces_retry_when_uncertain(self):
        needs_retry, reason = _needs_retry(
            {"text": "open whatsapp", "confidence": -0.4}, audio_dur_ms=1200,
        )
        assert needs_retry is True
        assert reason.startswith("msg_entity_hint_keyword")

    def test_msg_text_chat_reply_keywords_all_force_retry_when_uncertain(self):
        for word in ("msg", "text", "chat", "reply"):
            needs_retry, reason = _needs_retry(
                {"text": f"send a {word} now", "confidence": -0.4}, audio_dur_ms=1500,
            )
            assert needs_retry is True, f"keyword {word!r} should force retry"
            assert reason.startswith("msg_entity_hint_keyword")


class TestMessagingEntityHintSkipsRetryWhenConfident:
    """The actual latency-fix regression coverage — the real turn-7
    transcript and confidence from the live log."""

    def test_real_confident_reply_command_skips_retry(self):
        needs_retry, reason = _needs_retry(
            {"text": "Send him a reply, I am good. How are you?", "confidence": -0.15},
            audio_dur_ms=2650,
        )
        assert needs_retry is False
        assert reason.startswith("acceptable_conf")

    def test_boundary_at_threshold_skips_retry(self):
        # conf < -0.25 is the retry condition — exactly -0.25 does not
        # qualify (strict less-than), so this must skip.
        needs_retry, _ = _needs_retry(
            {"text": "reply to him", "confidence": -0.25}, audio_dur_ms=1500,
        )
        assert needs_retry is False

    def test_boundary_just_below_threshold_still_retries(self):
        needs_retry, reason = _needs_retry(
            {"text": "reply to him", "confidence": -0.26}, audio_dur_ms=1500,
        )
        assert needs_retry is True
        assert reason.startswith("msg_entity_hint_keyword")


class TestExistingFilesystemEntityHintUnaffectedByConfidenceGate:
    def test_folder_file_hints_still_force_retry_regardless_of_confidence(self):
        # Pre-existing rule, deliberately NOT gated on confidence — must
        # not regress from the messaging-keyword fix above.
        needs_retry, reason = _needs_retry(
            {"text": "open the downloads folder", "confidence": -0.1}, audio_dur_ms=1800,
        )
        assert needs_retry is True
        assert reason == "entity_hint_keyword"


class TestUnrelatedShortHighConfidenceStillSkipsRetry:
    def test_ordinary_command_with_no_entity_hint_and_ok_confidence_skips_retry(self):
        needs_retry, reason = _needs_retry(
            {"text": "pause the music", "confidence": -0.2}, audio_dur_ms=1000,
        )
        assert needs_retry is False
        assert reason.startswith("acceptable_conf")
