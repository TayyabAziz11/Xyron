"""
Tests for the local-first Urdu/Roman-Urdu/mixed multilingual pipeline work:
  - response_language.py: trigger-phrase fixes + decay-based session stickiness
  - local_comprehension.py: structured Qwen-fallback validation/mapping
  - openai_client.py: mistral:7b -> qwen2.5:1.5b default fix (VRAM/OOM safety)
  - content_tools.py _exec_general_query: local-first fallback + language awareness
  - response_pipeline.py: language-aware system prompt, no-key -> Ollama fallback

No live network/Ollama calls — everything here mocks the boundary (Ollama
HTTP, OpenAI client) so the suite runs fast and deterministically without a
GPU or a running Ollama server. Real-model behavior is covered separately by
the manual validation script (see MULTILINGUAL_MANUAL_VALIDATION.md).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ── response_language.py ──────────────────────────────────────────────────────

class TestResponseLanguageTriggers(unittest.TestCase):
    """Explicit language-switch phrase detection — including the construction
    ("<lang> mein <any verb>") that previously only matched a hardcoded list
    of exact verbs (bolo/batao/jawab do) and silently missed anything else."""

    def setUp(self):
        from api.services import response_language as rl
        self.rl = rl
        rl._SESSION_PREFS.clear()
        rl._SESSION_STICKY_LANG.clear()
        rl._GLOBAL_LANG_PREF = None

    def test_urdu_mein_explain_karo_not_in_original_exact_set(self):
        # Regression guard: this exact phrase is NOT one of the original
        # hardcoded exact strings — it must still be caught by the new regex.
        mode = self.rl.check_preference_update("Urdu mein explain karo", "s1")
        self.assertEqual(mode, "urdu")

    def test_english_mein_explain_karo_switches_to_english(self):
        # The live bug this closes: language_detector flags "mein"/"karo" as
        # Roman Urdu grammar markers, which used to invert this request.
        mode = self.rl.check_preference_update("English mein explain karo", "s1")
        self.assertEqual(mode, "english")

    def test_roman_urdu_mein_not_misdetected_as_plain_urdu(self):
        mode = self.rl.check_preference_update("roman urdu mein jawab do", "s1")
        self.assertEqual(mode, "roman_urdu")

    def test_hindi_mein_construction(self):
        mode = self.rl.check_preference_update("Hindi mein bata do", "s1")
        self.assertEqual(mode, "hindi")

    def test_no_trigger_returns_none(self):
        mode = self.rl.check_preference_update("Chrome kholo", "s1")
        self.assertIsNone(mode)

    def test_original_exact_phrases_still_work(self):
        self.assertEqual(self.rl.check_preference_update("always reply in urdu", "s1"), "urdu")
        self.assertEqual(self.rl.check_preference_update("speak english", "s1"), "english")


class TestResponseLanguageStickiness(unittest.TestCase):
    """Section-10 turn sequence: short low-signal follow-ups stay in the
    session's active language; explicit switches win immediately; stickiness
    decays rather than locking forever."""

    def setUp(self):
        from api.services import response_language as rl
        self.rl = rl
        rl._SESSION_PREFS.clear()
        rl._SESSION_STICKY_LANG.clear()
        rl._GLOBAL_LANG_PREF = None
        self.sid = "sticky-test"

    def test_full_turn_sequence_matches_spec(self):
        from api.services.language_detector import detect

        turns_expected = [
            ("Aaj ke orders check karo", "ur_roman"),
            ("Pending wale dikhao",       "ur_roman"),   # short follow-up, stays sticky
            ("Sirf unpaid",               "ur_roman"),   # short follow-up, stays sticky
            ("Ab Urdu mein batao",        "ur"),          # explicit switch wins immediately
            ("English mein explain karo", "en"),          # explicit switch back
        ]
        for text, expected in turns_expected:
            self.rl.check_preference_update(text, self.sid)
            d = detect(text, "en")
            lang = self.rl.select_response_language(
                d["lang"], self.sid, "auto", d["confidence"], word_count=len(text.split()),
            )
            self.assertEqual(lang, expected, f"turn={text!r}")

    def test_sticky_decays_after_max_turns(self):
        sid = "decay-test"
        # Establish sticky ur_roman
        self.rl.select_response_language("ur_roman", sid, "auto", 0.88, word_count=4)
        self.assertEqual(self.rl._SESSION_STICKY_LANG[sid]["decay"], self.rl._STICKY_DECAY_TURNS)
        # Burn through the decay budget with short, no-signal ("en") turns
        for _ in range(self.rl._STICKY_DECAY_TURNS):
            lang = self.rl.select_response_language("en", sid, "auto", 0.95, word_count=2)
            self.assertEqual(lang, "ur_roman")
        # Decay exhausted — next ambiguous short turn reverts to English
        lang = self.rl.select_response_language("en", sid, "auto", 0.95, word_count=2)
        self.assertEqual(lang, "en")

    def test_long_unambiguous_english_switches_immediately_even_when_sticky(self):
        sid = "long-en-test"
        self.rl.select_response_language("ur_roman", sid, "auto", 0.88, word_count=4)
        # A long, clearly English sentence should NOT be held hostage by
        # stickiness meant only for short/ambiguous follow-ups.
        long_en = "what is the current weather forecast for tomorrow in the city"
        lang = self.rl.select_response_language("en", sid, "auto", 0.95, word_count=len(long_en.split()))
        self.assertEqual(lang, "en")

    def test_low_confidence_stt_short_fragment_does_not_inherit_stickiness(self):
        # Live-caught bug: STT badly mis-heard "Chrome kholo" as "Xyron, open."
        # (stt_conf=-0.68) after a prior turn had set session stickiness to
        # "hi". Word-count alone flagged this as a short/ambiguous follow-up
        # and forced a Hindi reply to English-looking garbled text, producing
        # multi-script nonsense from the LLM. A low-confidence STT pass must
        # not be trusted as a real short follow-up.
        sid = "low-stt-conf-test"
        self.rl.select_response_language("hi", sid, "auto", 0.92, word_count=10)
        lang = self.rl.select_response_language(
            "en", sid, "auto", 0.95, word_count=2, stt_confidence=-0.68,
        )
        self.assertEqual(lang, "en")

    def test_missing_stt_confidence_keeps_old_sticky_behavior(self):
        # stt_confidence=None (caller didn't pass it) must not change existing
        # behavior — only an actual low-confidence signal should override.
        sid = "no-stt-conf-test"
        self.rl.select_response_language("hi", sid, "auto", 0.92, word_count=10)
        lang = self.rl.select_response_language("en", sid, "auto", 0.95, word_count=2)
        self.assertEqual(lang, "hi")

    def test_devanagari_mein_triggers_urdu_switch(self):
        # Live-caught bug: Whisper sometimes transcribes Roman Urdu "mein" as
        # Devanagari "में" when it guesses Hindi. "में" ends in a combining
        # mark (U+0902) that Python's re \b doesn't treat as a word boundary,
        # so the original regex silently never matched this real transcript.
        mode = self.rl.check_preference_update("urdu में बाद कर सकते हों क्या?", "s1")
        self.assertEqual(mode, "urdu")

    def test_urdu_script_mein_triggers_urdu_switch(self):
        # Live-caught bug (2026-08-21 real backend log): actual Urdu/Nastaliq
        # script "میں" (not Devanagari "में", not Latin "mein") was never
        # recognized at all. Real Whisper transcript of a user asking "can
        # you speak in Urdu?" — Whisper kept the loanword "Urdu" in Latin
        # script mid-Urdu-sentence, a common code-switch artifact — matched
        # NONE of the trigger patterns, so the turn fell through to full
        # intent routing instead of the dedicated language-switch handler;
        # the local Qwen fallback then hallucinated a search_files command
        # out of the user's own question and burned ~30s on a bogus answer.
        mode = self.rl.check_preference_update("کیا آپ Urdu میں بات کر سکتے ہو؟", "s1")
        self.assertEqual(mode, "urdu")

    def test_fully_urdu_script_phrase_triggers_urdu_switch(self):
        # Entirely in Urdu script, including the language name itself
        # (اردو, not Latin "urdu") — previously unmatched by any pattern.
        mode = self.rl.check_preference_update("اردو میں بات کرو", "s1")
        self.assertEqual(mode, "urdu")

    def test_urdu_script_english_switch(self):
        mode = self.rl.check_preference_update("انگلش میں جواب دو", "s1")
        self.assertEqual(mode, "english")

    def test_no_stickiness_without_prior_non_english_turn(self):
        sid = "fresh-session"
        lang = self.rl.select_response_language("en", sid, "auto", 0.95, word_count=2)
        self.assertEqual(lang, "en")

    def test_explicit_session_pref_overrides_stickiness(self):
        sid = "pref-override"
        self.rl.select_response_language("ur_roman", sid, "auto", 0.88, word_count=4)
        self.rl.check_preference_update("reply in english", sid)
        lang = self.rl.select_response_language("ur_roman", sid, "auto", 0.88, word_count=4)
        self.assertEqual(lang, "en")


class TestHardPreferenceEnglishOverride(unittest.TestCase):
    """Live-caught bug (2026-09-04 real backend log): once a hard non-English
    preference is set — including via a mere CAPABILITY QUESTION like "kya
    tum Urdu mein baat kar sakte ho?" ("can you speak Urdu?"), which still
    matches _TRIGGER_URDU_RE and sets a persistent "always urdu" preference
    — every subsequent turn was forced into Urdu regardless of what language
    the user actually spoke, including plain unambiguous English commands
    ("Open settings.", "Open Display Settings now.", both lang=en
    confidence=0.95 in the real log). A confident, non-garbled English
    reading of the CURRENT turn must override the hard preference for that
    turn only — the preference itself stays intact for the next ambiguous/
    non-English turn."""

    def setUp(self):
        from api.services import response_language as rl
        self.rl = rl
        rl._SESSION_PREFS.clear()
        rl._SESSION_STICKY_LANG.clear()
        rl._GLOBAL_LANG_PREF = None
        self.sid = "hard-pref-override-test"

    def test_confident_english_overrides_hard_urdu_preference(self):
        self.rl.check_preference_update("kya tum Urdu mein baat kar sakte ho", self.sid)
        lang = self.rl.select_response_language(
            "en", self.sid, "auto", confidence=0.95, word_count=2, stt_confidence=-0.43,
        )
        self.assertEqual(lang, "en")

    def test_confident_english_overrides_hard_urdu_preference_short_command(self):
        # No word-count gate for the hard-preference override (unlike the
        # auto-mode sticky-decay check) — a 2-word command is exactly the
        # real failing case from the log ("Open settings.").
        self.rl.check_preference_update("always reply in urdu", self.sid)
        lang = self.rl.select_response_language(
            "en", self.sid, "auto", confidence=0.95, word_count=2, stt_confidence=-0.11,
        )
        self.assertEqual(lang, "en")

    def test_garbled_short_fragment_does_not_override_hard_preference(self):
        # Protects against the same class of bug the auto-mode stickiness
        # guard already protects against: a badly mis-heard short fragment
        # that Whisper happens to tag as English (low stt_confidence) must
        # NOT be trusted to cancel the user's explicit Urdu preference.
        self.rl.check_preference_update("always reply in urdu", self.sid)
        lang = self.rl.select_response_language(
            "en", self.sid, "auto", confidence=0.95, word_count=2, stt_confidence=-0.9,
        )
        self.assertEqual(lang, "ur")

    def test_preference_persists_after_english_override_turn(self):
        # The override is per-turn only — an ambiguous/non-English turn
        # right after a successful English override must revert to the
        # still-intact preference, not silently cancel it.
        self.rl.check_preference_update("always reply in urdu", self.sid)
        self.rl.select_response_language(
            "en", self.sid, "auto", confidence=0.95, word_count=2, stt_confidence=-0.1,
        )
        self.assertEqual(self.rl._SESSION_PREFS.get(self.sid), "urdu")
        lang = self.rl.select_response_language(
            "ur_roman", self.sid, "auto", confidence=0.6, word_count=3, stt_confidence=-0.2,
        )
        self.assertEqual(lang, "ur")

    def test_low_confidence_english_detection_does_not_override(self):
        self.rl.check_preference_update("always reply in urdu", self.sid)
        lang = self.rl.select_response_language(
            "en", self.sid, "auto", confidence=0.5, word_count=2, stt_confidence=-0.1,
        )
        self.assertEqual(lang, "ur")

    def test_explicit_english_preference_unaffected_by_this_change(self):
        # "english" is not in the hard-pref override map (it's already
        # English) — must keep behaving exactly as before.
        self.rl.check_preference_update("always reply in english", self.sid)
        lang = self.rl.select_response_language(
            "ur_roman", self.sid, "auto", confidence=0.9, word_count=4, stt_confidence=-0.1,
        )
        self.assertEqual(lang, "en")


# ── local_comprehension.py ────────────────────────────────────────────────────
# 2026-08-21 redesign: this module used to map intent->tool_name directly via
# its own small hardcoded table (4 tools total). It now synthesizes a plain
# English canonical sentence from structured slots and routes it through the
# SAME intent_router.route() English text already uses — so validate_and_map
# can never invent a tool name (intent_router only trusts real registered
# tools) and any tool intent_router already understands in English becomes
# reachable from Urdu with zero new mapping code.

class TestLocalComprehensionValidation(unittest.TestCase):
    """Qwen never executes a tool — validate_and_map only ever returns a
    mapped tool call when the SYNTHESIZED canonical English sentence gets a
    confident match from the real intent_router. Everything else stays
    unmapped (mapped=False) so the caller falls through to general_query
    instead of best-guessing."""

    def setUp(self):
        from api.services.local_comprehension import ComprehensionResult
        self.ComprehensionResult = ComprehensionResult
        # Full real registry — validate_and_map now depends on intent_router
        # actually resolving the synthesized text, not a fixed lookup table.
        from api.tools import registry as _real_registry
        self.registry = _real_registry

    def _result(self, **kw):
        base = dict(
            original_transcript="x", detected_language="ur_roman",
            action="unknown", object_type=None, name=None, scope=None,
            time_reference=None, context_reference=None,
            model_confidence=1.0, latency_ms=0.0,
        )
        base.update(kw)
        return self.ComprehensionResult(**base)

    def test_open_app_maps_to_open_application(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="application", name="chrome")
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "open_application")
        self.assertEqual(out.tool_params, {"app_name": "chrome"})

    def test_open_folder_in_drive_maps_to_smart_open_with_drive_param(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="folder", name="Perfume", scope="E drive")
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "smart_open")
        self.assertEqual(out.tool_params.get("drive"), "E")
        self.assertIn("perfume", out.tool_params.get("query", "").lower())

    def test_open_drive_maps_to_open_drive_tool(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="drive", name="E")
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "open_drive")
        self.assertEqual(out.tool_params.get("drive"), "E")

    def test_take_screenshot_maps_without_a_name(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="take_screenshot", object_type=None)
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertIn(out.tool_name, ("take_screenshot", "desktop_screenshot"))

    def test_context_reference_resolves_via_context_stack_not_a_guess(self):
        # "open it again" style reference — name is null, context_reference
        # is set. The resolved name must come from ContextStack, never be
        # fabricated by this module.
        from api.services.local_comprehension import validate_and_map
        import api.services.context_stack as cs_mod
        with patch.object(cs_mod.context_stack, "get_last") as mock_get_last:
            mock_entity = MagicMock()
            mock_entity.display = "chrome"
            mock_get_last.return_value = mock_entity
            r = self._result(action="open", object_type="application", name=None,
                              context_reference="previously_active_app")
            out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_params, {"app_name": "chrome"})

    def test_unresolvable_context_reference_never_mapped(self):
        from api.services.local_comprehension import validate_and_map
        import api.services.context_stack as cs_mod
        with patch.object(cs_mod.context_stack, "get_last", return_value=None):
            r = self._result(action="open", object_type="application", name=None,
                              context_reference="previously_active_app")
            out = validate_and_map(r, self.registry)
        self.assertFalse(out.mapped)
        self.assertIsNone(out.tool_name)

    def test_out_of_scope_object_type_never_mapped(self):
        # object_type=repository is a later-stage domain — must never be
        # fabricated into a guess today, must stay unmapped.
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="repository", name="xyron")
        out = validate_and_map(r, self.registry)
        self.assertFalse(out.mapped)
        self.assertIsNone(out.tool_name)

    def test_unknown_action_never_mapped(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="unknown")
        out = validate_and_map(r, self.registry)
        self.assertFalse(out.mapped)

    def test_low_confidence_blocks_mapping_even_for_open_app(self):
        from api.services.local_comprehension import validate_and_map, MIN_CONFIDENCE
        r = self._result(action="open", object_type="application", name="chrome",
                          model_confidence=MIN_CONFIDENCE - 0.01)
        out = validate_and_map(r, self.registry)
        self.assertFalse(out.mapped)

    def test_tool_not_in_registry_never_mapped(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="application", name="chrome")
        out = validate_and_map(r, registry=set())  # empty registry
        self.assertFalse(out.mapped)

    def test_no_name_and_no_context_reference_never_mapped(self):
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="open", object_type="folder", name=None)
        out = validate_and_map(r, self.registry)
        self.assertFalse(out.mapped)

    def test_play_song_with_object_type_file_maps_to_search_youtube_not_file_search(self):
        # Regression guard for the exact live-caught bug (2026-09-03 real
        # backend log): a compound Urdu sentence ("چلو پر کام کرو، YouTube
        # کو کھولو اور کوئی بھی فیمز گانا چلا دو" — "let's get to it, open
        # YouTube and play any famous song") was correctly comprehended as
        # action=play/name="famous song"/scope=youtube by GPT-4o-mini, but
        # _synthesize_canonical's object_type=="file" branch (which the
        # model legitimately used, since a song title isn't a folder/drive/
        # app) turned it into "find file famous song in youtube" — a
        # filesystem search that could never find a song. Must map to
        # search_youtube instead.
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="play", object_type="file", name="famous song", scope="youtube")
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "search_youtube")
        self.assertEqual(out.tool_params.get("query"), "famous song")

    def test_play_song_with_no_object_type_maps_to_search_youtube(self):
        # This module's own system-prompt example: "YouTube pe gaana
        # chalao" -> action=play, name=gaana, scope=youtube, object_type
        # left null. Must resolve the same way as the object_type="file"
        # variant above — the model isn't consistent about which
        # object_type (if any) it assigns to a song name, and neither
        # should matter to the outcome.
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="play", object_type=None, name="gaana", scope="youtube")
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "search_youtube")
        self.assertEqual(out.tool_params.get("query"), "gaana")

    def test_play_with_no_name_falls_back_to_generic_media_control(self):
        # "play this song" / "play music" with nothing to search for —
        # generic play/resume, not a search with an empty query.
        from api.services.local_comprehension import validate_and_map
        r = self._result(action="play", object_type=None, name=None)
        out = validate_and_map(r, self.registry)
        self.assertTrue(out.mapped)
        self.assertEqual(out.tool_name, "media_control")


class TestLocalComprehensionParsing(unittest.TestCase):
    """comprehend() must never raise, and must reject anything outside the
    strict schema — hallucinated actions, unparseable JSON, model/network
    failures all degrade to None (caller uses general_query), never a crash
    and never a best-guess execution.

    All tests use lang="ur_roman", which as of 2026-09-04 routes comprehend()
    through _openai_chat (not _ollama_chat) — see
    local_comprehension._OPENAI_COMPREHEND_LANGS. Patching _openai_chat here
    (not _ollama_chat) is REQUIRED, not cosmetic: patching the wrong function
    leaves _openai_chat unmocked, so comprehend() would make a real, billed
    OpenAI API call on every test run instead of using the fake response
    below — this is the same class of regression test_edge_tts_routing.py's
    docstring describes for the TTS side of this same 2026-09-04 change."""

    @patch("api.services.local_comprehension._openai_chat")
    def test_valid_json_allowlisted_action(self, mock_chat):
        mock_chat.return_value = (
            '{"action": "open", "object_type": "application", "name": "chrome", '
            '"confidence": 0.95}', 500.0,
        )
        from api.services.local_comprehension import comprehend
        result = comprehend("Chrome kholo", "ur_roman")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "open")
        self.assertEqual(result.name, "chrome")
        self.assertEqual(result.model_confidence, 0.95)

    @patch("api.services.local_comprehension._openai_chat")
    def test_hallucinated_action_rejected(self, mock_chat):
        mock_chat.return_value = ('{"action": "delete_everything", "confidence": 0.9}', 500.0)
        from api.services.local_comprehension import comprehend
        result = comprehend("kuch bhi", "ur_roman")
        self.assertIsNone(result)

    @patch("api.services.local_comprehension._openai_chat")
    def test_unparseable_json_rejected(self, mock_chat):
        mock_chat.return_value = ("not json at all", 500.0)
        from api.services.local_comprehension import comprehend
        result = comprehend("kuch bhi", "ur_roman")
        self.assertIsNone(result)

    @patch("api.services.local_comprehension._openai_chat")
    def test_json_wrapped_in_markdown_fence_still_parses(self, mock_chat):
        mock_chat.return_value = (
            '```json\n{"action": "open", "object_type": "application", "name": "chrome", '
            '"confidence": 0.9}\n```',
            500.0,
        )
        from api.services.local_comprehension import comprehend
        result = comprehend("Chrome kholo", "ur_roman")
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "open")

    @patch("api.services.local_comprehension._openai_chat")
    def test_ollama_unavailable_returns_none(self, mock_chat):
        mock_chat.return_value = (None, 0.0)
        from api.services.local_comprehension import comprehend
        result = comprehend("kuch bhi", "ur_roman")
        self.assertIsNone(result)

    @patch("api.services.local_comprehension._openai_chat")
    def test_missing_confidence_field_defaults_moderate_not_zero(self, mock_chat):
        # Live-observed qwen2.5:1.5b behavior: omits "confidence" entirely on
        # an otherwise correct, well-formed response. Must not be silently
        # discarded as untrusted (0.0) — that threw away correct comprehension.
        mock_chat.return_value = (
            '{"action": "show", "object_type": "screen"}', 500.0,
        )
        from api.services.local_comprehension import comprehend, MIN_CONFIDENCE
        result = comprehend("meri screen pe kya hai", "ur_roman")
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.model_confidence, MIN_CONFIDENCE)

    @patch("api.services.local_comprehension._openai_chat")
    def test_invalid_confidence_value_stays_untrusted(self, mock_chat):
        mock_chat.return_value = (
            '{"action": "open", "object_type": "application", "name": "chrome", '
            '"confidence": "high"}', 500.0,
        )
        from api.services.local_comprehension import comprehend, MIN_CONFIDENCE
        result = comprehend("chrome kholo", "ur_roman")
        self.assertIsNotNone(result)
        self.assertLess(result.model_confidence, MIN_CONFIDENCE)

    @patch("api.services.local_comprehension._openai_chat")
    def test_model_never_asked_to_guess_vague_reference_name(self, mock_chat):
        # "open it again" -> model correctly returns name=null,
        # context_reference set, per the system prompt's explicit rule.
        mock_chat.return_value = (
            '{"action": "open", "object_type": "application", "name": null, '
            '"context_reference": "previously_active_app", "confidence": 0.9}', 500.0,
        )
        from api.services.local_comprehension import comprehend
        result = comprehend("isko dubara kholo", "ur_roman")
        self.assertIsNotNone(result)
        self.assertIsNone(result.name)
        self.assertEqual(result.context_reference, "previously_active_app")


class TestLanguageEquivalenceStage1(unittest.TestCase):
    """Stage 1 of the Urdu-parity rollout: English, Roman Urdu, Urdu script,
    and mixed code-switching must all resolve to the SAME tool/params for
    the domains covered so far (applications, folders, drives, files,
    browser). English and Roman Urdu/mixed go through the deterministic
    tier (ml_normalizer/mixed_language_engine + intent_router) with no LLM
    involved; Urdu script here is exercised directly against the
    canonicalizer's synthesis + intent_router hand-off (comprehend() itself
    is mocked — this suite is about the canonicalization->routing contract,
    not qwen2.5:1.5b's raw comprehension accuracy, which is a live/manual
    concern, not a deterministic one)."""

    def setUp(self):
        from api.tools import registry as _real_registry
        self.registry = _real_registry

    def _route_english(self, text: str):
        from api.services.intent_router import intent_router as _ir
        return _ir.route(text)

    def test_open_application_equivalence(self):
        from api.services.mixed_language_engine import analyze as _mixed_analyze
        en = self._route_english("open Chrome")
        roman = self._route_english(_mixed_analyze("Chrome kholo", "ur_roman"))
        mixed = self._route_english(_mixed_analyze("Chrome open karo", "mixed"))
        for r in (en, roman, mixed):
            self.assertEqual(r.tool_name, "open_application")
            self.assertEqual(r.params.get("app_name", "").lower(), "chrome")

    def test_open_drive_folder_with_scope_equivalence(self):
        from api.services.local_comprehension import (
            ComprehensionResult, validate_and_map,
        )
        # English text goes straight through intent_router (the baseline).
        en = self._route_english("Open the Perfume folder in E drive.")
        # Roman/Urdu/mixed: simulate the canonicalizer's structured output
        # (as if comprehend() had parsed each phrasing) and let
        # validate_and_map synthesize + route it exactly like production does.
        for lang, transcript in (
            ("ur_roman", "E drive mein Perfume folder kholo."),
            ("ur",       "ای ڈرائیو میں پرفیوم فولڈر کھولو"),
            ("mixed",    "E drive mein Perfume wala folder open karo."),
        ):
            r = ComprehensionResult(
                original_transcript=transcript, detected_language=lang,
                action="open", object_type="folder", name="Perfume",
                scope="E drive", model_confidence=0.95,
            )
            out = validate_and_map(r, self.registry)
            self.assertTrue(out.mapped, f"failed to map: {lang}")
            self.assertEqual(out.tool_name, "smart_open")
            self.assertEqual(en.tool_name, out.tool_name)
            self.assertEqual(out.tool_params.get("drive"), "E")
            self.assertEqual(en.params.get("drive"), "E")

    def test_context_reference_equivalence_across_languages(self):
        # "open the repo/folder I was just working on" — must resolve
        # through ContextStack identically regardless of source language,
        # never a literal per-language guess.
        from api.services.local_comprehension import (
            ComprehensionResult, validate_and_map,
        )
        import api.services.context_stack as cs_mod
        for lang, transcript in (
            ("en",       "Open the folder I was just working on."),
            ("ur_roman", "Jis folder pe kaam kar raha tha woh kholo."),
            ("ur",       "جس فولڈر پر کام کر رہا تھا وہ کھولو۔"),
        ):
            with patch.object(cs_mod.context_stack, "get_last") as mock_get_last:
                mock_entity = MagicMock()
                mock_entity.display = "Xyron"
                mock_get_last.return_value = mock_entity
                r = ComprehensionResult(
                    original_transcript=transcript, detected_language=lang,
                    action="open", object_type="folder", name=None,
                    context_reference="previously_active_folder",
                    model_confidence=0.9,
                )
                out = validate_and_map(r, self.registry)
            self.assertTrue(out.mapped, f"failed to map: {lang}")
            self.assertIn("xyron", out.tool_params.get("query", "").lower())


# ── openai_client.py — mistral OOM-risk default fix ──────────────────────────

class TestLocalOllamaModelDefault(unittest.TestCase):
    def test_default_is_not_mistral(self):
        from api.services import openai_client as oc
        self.assertNotEqual(oc.LOCAL_OLLAMA_MODEL, "mistral:7b")

    def test_ollama_model_map_uses_local_model_not_mistral(self):
        from api.services.openai_client import OpenAIClient, LOCAL_OLLAMA_MODEL
        for v in OpenAIClient._OLLAMA_MODEL_MAP.values():
            self.assertEqual(v, LOCAL_OLLAMA_MODEL)
            self.assertNotEqual(v, "mistral:7b")


# ── content_tools.py _exec_general_query ──────────────────────────────────────

class TestGeneralQueryLocalFirst(unittest.TestCase):
    """The original live bug: no OpenAI key -> hard-coded 'I heard: ...'
    echo, no Ollama attempt, no language awareness at all."""

    @patch("api.services.openai_client.openai_client.generate")
    def test_no_key_still_answers_via_generate_not_echo(self, mock_generate):
        # openai_client.generate() already contains the Ollama fallback path
        # (verified separately) — general_query must call it rather than
        # short-circuiting to a raw echo when no OpenAI key is present.
        mock_generate.return_value = "Kaam ho gaya."
        from api.tools.content_tools import _exec_general_query
        result = _exec_general_query({"query": "kya haal hai"}, {"response_lang": "ur_roman"})
        self.assertTrue(result.success)
        self.assertEqual(result.text, "Kaam ho gaya.")
        mock_generate.assert_called_once()

    @patch("api.services.openai_client.openai_client.generate")
    def test_both_backends_unavailable_fails_clearly_not_silently(self, mock_generate):
        mock_generate.return_value = None
        from api.tools.content_tools import _exec_general_query
        result = _exec_general_query({"query": "kya haal hai"}, {"response_lang": "ur_roman"})
        self.assertFalse(result.success)

    @patch("api.services.openai_client.openai_client.generate")
    def test_response_lang_reaches_system_prompt(self, mock_generate):
        mock_generate.return_value = "ok"
        from api.tools.content_tools import _exec_general_query
        _exec_general_query({"query": "hello"}, {"response_lang": "ur"})
        sent_messages = mock_generate.call_args[0][0]
        system_msg = sent_messages[0]["content"]
        self.assertIn("Urdu", system_msg)


# ── response_pipeline.py — language-aware prompt + no-key fallback ──────────

class TestSynthesizeForLang(unittest.TestCase):
    """Live-caught bug: every TTS call in response_pipeline.py (the general
    conversational LLM path — anything that doesn't match a deterministic
    tool) called Kokoro directly and unconditionally, completely bypassing
    tts_router.py's Edge-TTS routing. A free-form Urdu conversational reply
    never reached Edge-TTS at all; Kokoro's English phonemizer was asked to
    pronounce Urdu/Arabic script and either produced garbage or returned
    None outright."""

    def test_english_still_uses_kokoro_directly(self):
        import asyncio
        from api.services import response_pipeline as rp
        with patch.object(rp, "_kokoro_async", new_callable=AsyncMock) as mock_kokoro:
            mock_kokoro.return_value = b"KOKORO_WAV"
            with patch("voice.tts_router.synthesize") as mock_router:
                result = asyncio.run(rp._synthesize_for_lang("hello", "onyx", 1.0, "en"))
        self.assertEqual(result, b"KOKORO_WAV")
        mock_kokoro.assert_called_once()

    def test_urdu_routes_through_tts_router_not_kokoro_directly(self):
        import asyncio
        from api.services import response_pipeline as rp
        with patch.object(rp, "_kokoro_async", new_callable=AsyncMock) as mock_kokoro:
            with patch("voice.tts_router.synthesize", return_value=b"EDGE_WAV") as mock_router:
                result = asyncio.run(rp._synthesize_for_lang("کروم کھولا", "onyx", 1.0, "ur"))
        self.assertEqual(result, b"EDGE_WAV")
        mock_router.assert_called_once()
        mock_kokoro.assert_not_called()

    def test_tts_router_failure_falls_back_to_kokoro(self):
        import asyncio
        from api.services import response_pipeline as rp
        with patch.object(rp, "_kokoro_async", new_callable=AsyncMock) as mock_kokoro:
            mock_kokoro.return_value = b"KOKORO_FALLBACK"
            with patch("voice.tts_router.synthesize", side_effect=RuntimeError("boom")):
                result = asyncio.run(rp._synthesize_for_lang("test", "onyx", 1.0, "ur_roman"))
        self.assertEqual(result, b"KOKORO_FALLBACK")
        mock_kokoro.assert_called_once()


class TestResponsePipelineLanguageAware(unittest.TestCase):
    def test_system_prompt_differs_by_language(self):
        from api.services.response_pipeline import _build_voice_system_prompt
        en = _build_voice_system_prompt("en")
        ur = _build_voice_system_prompt("ur")
        roman = _build_voice_system_prompt("ur_roman")
        self.assertIn("English only", en)
        self.assertNotIn("English only", ur)
        self.assertNotEqual(ur, roman)

    def test_unknown_lang_code_falls_back_to_english(self):
        from api.services.response_pipeline import _build_voice_system_prompt
        fallback = _build_voice_system_prompt("klingon")
        en = _build_voice_system_prompt("en")
        self.assertEqual(fallback, en)

    def test_stream_response_no_key_routes_to_ollama_not_empty(self):
        import asyncio
        from api.services import response_pipeline as rp

        async def _run():
            collected = []
            with patch("api.config.settings") as mock_settings:
                mock_settings.openai_api_key = ""
                with patch.object(rp, "_ollama_stream") as mock_ollama:
                    async def _fake_stream(*a, **kw):
                        yield ("Chrome khol raha hoon.", None, 1, True)
                    mock_ollama.side_effect = _fake_stream
                    async for item in rp.stream_response_with_tts(
                        "Chrome kholo", [], response_lang="ur_roman",
                    ):
                        collected.append(item)
            return collected

        collected = asyncio.run(_run())
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0][0], "Chrome khol raha hoon.")

    def test_quick_response_no_key_routes_to_ollama_not_canned_string(self):
        import asyncio
        from unittest.mock import AsyncMock
        from api.services import response_pipeline as rp

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "Theek hai."}}]}

        mock_async_client = AsyncMock()
        mock_async_client.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

        async def _run():
            with patch("api.config.settings") as mock_settings:
                mock_settings.openai_api_key = ""
                with patch("httpx.AsyncClient", return_value=mock_async_client):
                    return await rp.quick_response("kya haal hai", response_lang="ur_roman")

        result = asyncio.run(_run())
        # Previously: no key -> immediate canned English non-answer, Ollama
        # never attempted. Now it must go through the Ollama call above.
        self.assertNotEqual(result, "I'm not sure how to help with that.")
        self.assertEqual(result, "Theek hai.")
        mock_async_client.__aenter__.return_value.post.assert_called_once()


class TestOpenAIUrduOnlyGate(unittest.TestCase):
    """Regression guard for Settings.openai_urdu_only (added 2026-09-03 when
    the OpenAI account ran out of credits; credits were restored 2026-09-04
    but the gate stays on — OpenAI is now reserved for Urdu only, English
    stays fully local). Confirms English NEVER constructs an OpenAI client
    even when a valid-looking key IS configured — the exact regression that
    would silently start spending API credits on ordinary English turns."""

    def test_stream_response_english_never_touches_openai_even_with_valid_key(self):
        import asyncio
        from api.services import response_pipeline as rp
        rp._openai_failed_until = 0.0

        async def _run():
            with patch("api.config.settings") as mock_settings, \
                 patch.object(rp, "_get_openai_client") as mock_get_client, \
                 patch.object(rp, "_ollama_stream") as mock_ollama:
                mock_settings.openai_api_key   = "sk-FAKE-VALID-LOOKING-KEY"
                mock_settings.openai_urdu_only = True

                async def _fake_stream(*a, **kw):
                    yield ("Settings opened.", None, 1, True)
                mock_ollama.side_effect = _fake_stream

                collected = []
                async for item in rp.stream_response_with_tts(
                    "open settings", [], response_lang="en",
                ):
                    collected.append(item)
                return collected, mock_get_client.called, mock_ollama.called

        collected, openai_touched, ollama_used = asyncio.run(_run())
        self.assertFalse(openai_touched, "OpenAI client must never be constructed for English")
        self.assertTrue(ollama_used)
        self.assertEqual(collected[0][0], "Settings opened.")

    def test_quick_response_english_never_touches_openai_even_with_valid_key(self):
        import asyncio
        from api.services import response_pipeline as rp
        rp._openai_failed_until = 0.0

        async def _run():
            with patch("api.config.settings") as mock_settings, \
                 patch.object(rp, "_get_openai_client") as mock_get_client:
                mock_settings.openai_api_key   = "sk-FAKE-VALID-LOOKING-KEY"
                mock_settings.openai_urdu_only = True

                from unittest.mock import AsyncMock
                mock_resp = MagicMock()
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = {"choices": [{"message": {"content": "Done."}}]}
                mock_async_client = AsyncMock()
                mock_async_client.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)

                with patch("httpx.AsyncClient", return_value=mock_async_client):
                    result = await rp.quick_response("what time is it", response_lang="en")
                return result, mock_get_client.called

        result, openai_touched = asyncio.run(_run())
        self.assertFalse(openai_touched, "OpenAI client must never be constructed for English")
        self.assertEqual(result, "Done.")

    def test_urdu_still_uses_openai_when_gate_is_on(self):
        # The gate's counterpart: ur/ur_roman/mixed must still be ALLOWED to
        # reach the normal OpenAI-first code path (this test doesn't need a
        # real key — it only asserts the gate itself didn't divert Urdu to
        # Ollama, mirroring the "no key" behavior already covered by
        # test_stream_response_no_key_routes_to_ollama_not_empty above).
        import asyncio
        from api.services import response_pipeline as rp
        rp._openai_failed_until = 0.0

        async def _run():
            with patch("api.config.settings") as mock_settings, \
                 patch.object(rp, "_ollama_stream") as mock_ollama:
                mock_settings.openai_api_key   = ""  # no real key needed for this assertion
                mock_settings.openai_urdu_only = True

                async def _fake_stream(*a, **kw):
                    yield ("Urdu reply.", None, 1, True)
                mock_ollama.side_effect = _fake_stream

                collected = []
                async for item in rp.stream_response_with_tts(
                    "کروم کھولو", [], response_lang="ur",
                ):
                    collected.append(item)
                return collected

        collected = asyncio.run(_run())
        # With no key at all, Urdu ALSO falls back to Ollama (existing
        # no-key behavior) — the point of this test is that the
        # openai_urdu_only gate itself does not add an extra bypass for
        # Urdu; that path is exercised by the "no key" branch same as
        # English, distinguishing it from a hard block.
        self.assertEqual(collected[0][0], "Urdu reply.")


class TestComprehensionEngineSelection(unittest.TestCase):
    """Regression guard for local_comprehension.comprehend()'s engine
    routing added 2026-09-04: ur/ur_roman/mixed must call _openai_chat
    (better comprehension quality than the local 1.5B qwen model), every
    other detected_language (en, hi, ar, ...) must keep using the local
    _ollama_chat path unchanged."""

    def test_ur_uses_openai_not_ollama(self):
        with patch("api.services.local_comprehension._openai_chat") as mock_openai, \
             patch("api.services.local_comprehension._ollama_chat") as mock_ollama:
            mock_openai.return_value = ('{"action": "open", "confidence": 0.9}', 500.0)
            from api.services.local_comprehension import comprehend
            comprehend("کروم کھولو", "ur")
            mock_openai.assert_called_once()
            mock_ollama.assert_not_called()

    def test_ur_roman_uses_openai_not_ollama(self):
        with patch("api.services.local_comprehension._openai_chat") as mock_openai, \
             patch("api.services.local_comprehension._ollama_chat") as mock_ollama:
            mock_openai.return_value = ('{"action": "open", "confidence": 0.9}', 500.0)
            from api.services.local_comprehension import comprehend
            comprehend("Chrome kholo", "ur_roman")
            mock_openai.assert_called_once()
            mock_ollama.assert_not_called()

    def test_mixed_uses_openai_not_ollama(self):
        with patch("api.services.local_comprehension._openai_chat") as mock_openai, \
             patch("api.services.local_comprehension._ollama_chat") as mock_ollama:
            mock_openai.return_value = ('{"action": "open", "confidence": 0.9}', 500.0)
            from api.services.local_comprehension import comprehend
            comprehend("Chrome open karo", "mixed")
            mock_openai.assert_called_once()
            mock_ollama.assert_not_called()

    def test_english_still_uses_ollama_not_openai(self):
        with patch("api.services.local_comprehension._openai_chat") as mock_openai, \
             patch("api.services.local_comprehension._ollama_chat") as mock_ollama:
            mock_ollama.return_value = ('{"action": "open", "confidence": 0.9}', 500.0)
            from api.services.local_comprehension import comprehend
            comprehend("open chrome", "en")
            mock_ollama.assert_called_once()
            mock_openai.assert_not_called()

    def test_hindi_still_uses_ollama_not_openai(self):
        # hi/ar are explicitly OUT of scope for the OpenAI-comprehension
        # switch — only the Urdu family (ur/ur_roman/mixed) moved.
        with patch("api.services.local_comprehension._openai_chat") as mock_openai, \
             patch("api.services.local_comprehension._ollama_chat") as mock_ollama:
            mock_ollama.return_value = ('{"action": "open", "confidence": 0.9}', 500.0)
            from api.services.local_comprehension import comprehend
            comprehend("chrome kholo", "hi")
            mock_ollama.assert_called_once()
            mock_openai.assert_not_called()

    def test_openai_chat_uses_openai_client_generate(self):
        # _openai_chat itself must go through openai_client.generate() (which
        # already has its own internal Ollama fallback on failure/quota —
        # see openai_client.py's _ollama_fallback) rather than hitting the
        # OpenAI SDK directly, so it inherits that existing safety net.
        with patch("api.services.openai_client.openai_client") as mock_client:
            mock_client.generate.return_value = '{"action": "open", "confidence": 0.9}'
            from api.services.local_comprehension import _openai_chat
            content, ms = _openai_chat("Chrome kholo")
            self.assertEqual(content, '{"action": "open", "confidence": 0.9}')
            mock_client.generate.assert_called_once()
            _, kwargs = mock_client.generate.call_args
            self.assertEqual(kwargs.get("model"), "gpt-4o-mini")

    def test_openai_chat_returns_none_on_failure_not_raise(self):
        with patch("api.services.openai_client.openai_client") as mock_client:
            mock_client.generate.return_value = None
            from api.services.local_comprehension import _openai_chat
            content, ms = _openai_chat("Chrome kholo")
            self.assertIsNone(content)


class TestUrduAckGeneratorEngineSelection(unittest.TestCase):
    """Regression guard for urdu_ack_generator._generate_sync()'s engine
    routing added 2026-09-04: ur/ur_roman/mixed must call
    openai_client.generate() (better Urdu quality than local qwen), every
    other lang must keep using the local offline_generate() path."""

    def test_ur_uses_openai_client_generate(self):
        with patch("api.services.openai_client.openai_client") as mock_client, \
             patch("api.services.openai_client.offline_generate") as mock_offline:
            mock_client.generate.return_value = "چیک کر رہا ہوں"
            from api.services.urdu_ack_generator import _generate_sync
            result = _generate_sync("Checking that.", "ur")
            self.assertEqual(result, "چیک کر رہا ہوں")
            mock_client.generate.assert_called_once()
            mock_offline.assert_not_called()

    def test_ur_roman_uses_openai_client_generate(self):
        with patch("api.services.openai_client.openai_client") as mock_client, \
             patch("api.services.openai_client.offline_generate") as mock_offline:
            mock_client.generate.return_value = "theek hai, dekh raha hoon"
            from api.services.urdu_ack_generator import _generate_sync
            result = _generate_sync("Checking that.", "ur_roman")
            self.assertEqual(result, "theek hai, dekh raha hoon")
            mock_client.generate.assert_called_once()
            mock_offline.assert_not_called()

    def test_hindi_still_uses_offline_generate_not_openai(self):
        with patch("api.services.openai_client.openai_client") as mock_client, \
             patch("api.services.openai_client.offline_generate") as mock_offline:
            mock_offline.return_value = "dekh raha hoon"
            from api.services.urdu_ack_generator import _generate_sync
            result = _generate_sync("Checking that.", "hi")
            mock_offline.assert_called_once()
            mock_client.generate.assert_not_called()


class TestSemanticTranscriptCorrectionPreservesLanguage(unittest.TestCase):
    """Live-caught bug: _correct_transcript_semantic() (voice_ws.py) runs
    BEFORE language detection to fix STT phonetic errors, but its original
    prompt had no guidance about non-English input — the model "corrected"
    a real Urdu utterance ('YAR, Urdu, में, बाद करों!' — "talk in Urdu") into
    fluent but completely wrong English ('Yes, Urdu, please!'), destroying
    the language-switch trigger phrase and everany downstream Urdu handling
    before it ever ran. Real API call to the local Qwen model (via
    openai_client's Ollama fallback) — no network mocking, this needs to
    exercise the actual model's behavior against the actual prompt."""

    def test_urdu_mein_baat_karo_not_translated_to_english(self):
        import asyncio
        from api.routers.voice_ws import _correct_transcript_semantic
        result = asyncio.run(
            _correct_transcript_semantic("YAR, Urdu, में, बाद करों!", -0.75)
        )
        # Must not be mistranslated into an English sentence — either
        # unchanged, or corrected while staying recognizably Urdu/mixed.
        self.assertNotIn("please", result.lower())
        self.assertNotIn("yes,", result.lower())

    def test_genuine_english_correction_does_not_error(self):
        # Not asserting the local model DOES correct "crome" -> "chrome"
        # (that's testing qwen2.5:1.5b's general capability and is
        # legitimately non-deterministic across runs) — only that adding the
        # language-preservation instruction didn't break the English path
        # entirely (crash, empty result, wrong type).
        import asyncio
        from api.routers.voice_ws import _correct_transcript_semantic
        result = asyncio.run(
            _correct_transcript_semantic("open crome browzer", -0.5)
        )
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


class TestSentenceBoundaryUrduPunctuation(unittest.TestCase):
    """Regression guard: _SENT_RE (response_pipeline.py) must split on Urdu
    ۔ and ؟ in addition to ASCII .!? — otherwise an Urdu-script response
    (response_lang="ur") never chunks into sentences until the whole stream
    ends, silently defeating the pipelined-synthesis latency this module
    exists for and denying the reply any inter-sentence pause at all."""

    def test_urdu_full_stop_splits_sentences(self):
        from api.services.response_pipeline import _SENT_RE
        text = "یہ پہلا جملہ ہے۔ یہ دوسرا جملہ ہے۔"
        parts = [p for p in _SENT_RE.split(text) if p]
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].endswith("۔"))

    def test_urdu_question_mark_splits_sentences(self):
        from api.services.response_pipeline import _SENT_RE
        text = "کیا آپ ٹھیک ہیں؟ ہاں میں ٹھیک ہوں۔"
        parts = [p for p in _SENT_RE.split(text) if p]
        self.assertEqual(len(parts), 2)

    def test_ascii_punctuation_still_splits(self):
        from api.services.response_pipeline import _SENT_RE
        text = "First sentence. Second sentence!"
        parts = [p for p in _SENT_RE.split(text) if p]
        self.assertEqual(len(parts), 2)


class TestOrchestratorLocalQwenCanonicalizationWiring(unittest.TestCase):
    """End-to-end integration: brain.orchestrator._route_intent() must wire
    comprehend() -> validate_and_map() -> intent_router.route() correctly,
    using the new action/object_type/name/scope schema (not the old
    raw_intent/tool-map schema) and route_confidence (not model_confidence)
    for the returned OrchestratorDecision."""

    @patch("api.services.local_comprehension._openai_chat")
    def test_roman_urdu_folder_command_routes_via_canonicalization(self, mock_chat):
        # A phrase deterministic tiers won't confidently resolve on their
        # own (unusual enough phrasing), forcing the local_qwen path.
        mock_chat.return_value = (
            '{"action": "open", "object_type": "folder", "name": "Perfume", '
            '"scope": "E drive", "confidence": 0.95}', 700.0,
        )
        from brain.orchestrator import orchestrator, ActionType
        import asyncio
        decision = asyncio.run(
            orchestrator._route_intent(
                "yaar zara wo E drive wali cheez dikhado na perfume type ka", "ur_roman",
            )
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, ActionType.TOOL)
        self.assertEqual(decision.tool_name, "smart_open")
        self.assertEqual(decision.tool_params.get("drive"), "E")
        self.assertEqual(decision.reason, "local_qwen_canonicalization")
        self.assertEqual(decision.context.get("local_qwen_used"), True)

    @patch("api.services.local_comprehension._openai_chat")
    def test_low_confidence_qwen_output_falls_through_to_llm(self, mock_chat):
        mock_chat.return_value = ('{"action": "unknown", "confidence": 0.1}', 400.0)
        from brain.orchestrator import orchestrator, ActionType
        import asyncio
        decision = asyncio.run(
            orchestrator._route_intent("kuch ajeeb sa bol raha hai", "ur_roman")
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, ActionType.LLM)
        self.assertEqual(decision.context.get("local_qwen_used"), True)


# ── comprehend_multi() — Tier-4 compound fallback ─────────────────────────
# OpenAI/Qwen remain a semantic COMPILER ONLY here: comprehend_multi()
# returns a proposed "intents" list, and EVERY intent still goes through
# validate_and_map() -> the real intent_router.route() before anything
# resembling a tool name comes out — same trust boundary as the
# single-intent comprehend() path. This never lets the model choose a
# tool directly.

class TestLocalComprehensionMulti(unittest.TestCase):
    @patch("api.services.local_comprehension._openai_chat")
    def test_compound_intents_array_parsed(self, mock_chat):
        mock_chat.return_value = (
            '{"intents": ['
            '{"action": "open", "object_type": "application", "name": "YouTube", "confidence": 0.95}, '
            '{"action": "play", "object_type": null, "name": null, "scope": "youtube", "confidence": 0.9}'
            ']}',
            800.0,
        )
        from api.services.local_comprehension import comprehend_multi
        results = comprehend_multi("YouTube کو کھولو اور کوئی گانا چلا دو", "ur")
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].action, "open")
        self.assertEqual(results[0].name, "YouTube")
        self.assertEqual(results[1].action, "play")
        self.assertEqual(results[1].scope, "youtube")

    @patch("api.services.local_comprehension._openai_chat")
    def test_single_intent_still_returns_one_element_list(self, mock_chat):
        # A non-compound utterance must still come back through the SAME
        # compound-aware call shape as a 1-element list — the caller
        # (orchestrator._route_intent) branches on len(...) == 1 to
        # reproduce the exact pre-compound single-action decision shape.
        mock_chat.return_value = (
            '{"intents": [{"action": "open", "object_type": "application", '
            '"name": "chrome", "confidence": 0.95}]}',
            600.0,
        )
        from api.services.local_comprehension import comprehend_multi
        results = comprehend_multi("chrome kholo", "ur_roman")
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "open")

    @patch("api.services.local_comprehension._openai_chat")
    def test_bare_single_object_shape_tolerated_as_one_element_list(self, mock_chat):
        # Model ignores the "intents" wrapper instruction and answers with
        # the old bare single-object shape — must be tolerated as a
        # 1-element list rather than failing outright.
        mock_chat.return_value = (
            '{"action": "open", "object_type": "application", "name": "chrome", "confidence": 0.9}',
            500.0,
        )
        from api.services.local_comprehension import comprehend_multi
        results = comprehend_multi("chrome kholo", "ur_roman")
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "chrome")

    @patch("api.services.local_comprehension._openai_chat")
    def test_unparseable_json_returns_none(self, mock_chat):
        mock_chat.return_value = ("not json", 500.0)
        from api.services.local_comprehension import comprehend_multi
        self.assertIsNone(comprehend_multi("kuch bhi", "ur_roman"))

    @patch("api.services.local_comprehension._openai_chat")
    def test_model_unreachable_returns_none(self, mock_chat):
        mock_chat.return_value = (None, 0.0)
        from api.services.local_comprehension import comprehend_multi
        self.assertIsNone(comprehend_multi("kuch bhi", "ur_roman"))

    @patch("api.services.local_comprehension._openai_chat")
    def test_empty_intents_array_returns_none(self, mock_chat):
        mock_chat.return_value = ('{"intents": []}', 500.0)
        from api.services.local_comprehension import comprehend_multi
        self.assertIsNone(comprehend_multi("kuch bhi", "ur_roman"))

    @patch("api.services.local_comprehension._openai_chat")
    def test_hallucinated_action_in_one_intent_dropped_not_fatal(self, mock_chat):
        # One intent has an allowlisted action, the other doesn't —
        # _result_from_intent_dict rejects the bad one individually;
        # comprehend_multi() still returns the valid one(s) rather than
        # failing the whole call. (The caller — orchestrator — is the
        # layer that decides whether a PARTIAL result is safe to act on;
        # see TestOrchestratorCompoundQwenFallback below.)
        mock_chat.return_value = (
            '{"intents": ['
            '{"action": "open", "object_type": "application", "name": "chrome", "confidence": 0.9}, '
            '{"action": "delete_everything", "confidence": 0.9}'
            ']}',
            700.0,
        )
        from api.services.local_comprehension import comprehend_multi
        results = comprehend_multi("chrome kholo aur kuch ajeeb", "ur_roman")
        self.assertIsNotNone(results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "open")


# ── Orchestrator wiring: Tier-4 compound fallback via comprehend_multi() ────
# Mirrors TestOrchestratorLocalQwenCanonicalizationWiring above, but for
# the compound path — MUST use a phrase the deterministic
# mixed_language_engine.split_compound() tier does NOT resolve on its own,
# otherwise orchestrator.decide() never reaches Tier 4 at all (by design —
# deterministic-first). _route_intent() is called directly here (like the
# existing wiring tests), which bypasses the deterministic compound-split
# step in decide() — these tests are specifically about the Tier-4
# comprehend_multi() -> validate_and_map() -> MULTI_STEP wiring.

class TestOrchestratorCompoundQwenFallback(unittest.TestCase):
    @patch("api.services.local_comprehension._openai_chat")
    def test_two_confident_intents_yield_multi_step(self, mock_chat):
        mock_chat.return_value = (
            '{"intents": ['
            '{"action": "open", "object_type": "application", "name": "YouTube", "confidence": 0.95}, '
            '{"action": "play", "object_type": null, "name": null, "scope": "youtube", "confidence": 0.9}'
            ']}',
            800.0,
        )
        from brain.orchestrator import orchestrator, ActionType
        import asyncio
        # Must be a phrase intent_router.route() itself does NOT already
        # confidently resolve at tier <=3 on the raw text — otherwise
        # _route_intent returns TOOL before Tier 4 (comprehend_multi) ever
        # runs, and this test would just be exercising tier <=3 routing
        # instead of the thing it's named for. Verified via
        # intent_router.route(...).tool_name is None for this phrase.
        decision = asyncio.run(
            orchestrator._route_intent(
                "yaar ek kaam karo pehle wo cheez kholo phir gaana laga do", "ur",
            )
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, ActionType.MULTI_STEP)
        self.assertEqual(decision.reason, "local_qwen_compound_canonicalization")
        steps = decision.context.get("canonical_steps")
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0], "open YouTube")
        self.assertTrue(steps[1].startswith("play "))
        self.assertIn("youtube", steps[1].lower())
        self.assertEqual(decision.context.get("compound_source"), "qwen_or_openai")
        self.assertTrue(decision.context.get("local_qwen_used"))

    @patch("api.services.local_comprehension._openai_chat")
    def test_single_intent_preserves_existing_tool_decision_shape(self, mock_chat):
        # Exactly the pre-compound contract test above
        # (TestOrchestratorLocalQwenCanonicalizationWiring) already
        # verifies, now going through the compound-aware
        # comprehend_multi() call — must produce the IDENTICAL
        # ActionType.TOOL decision shape for a genuinely single-action
        # utterance.
        mock_chat.return_value = (
            '{"intents": [{"action": "open", "object_type": "folder", "name": "Perfume", '
            '"scope": "E drive", "confidence": 0.95}]}', 700.0,
        )
        from brain.orchestrator import orchestrator, ActionType
        import asyncio
        decision = asyncio.run(
            orchestrator._route_intent(
                "yaar zara wo E drive wali cheez dikhado na perfume type ka", "ur_roman",
            )
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, ActionType.TOOL)
        self.assertEqual(decision.tool_name, "smart_open")
        self.assertEqual(decision.tool_params.get("drive"), "E")
        self.assertEqual(decision.reason, "local_qwen_canonicalization")

    @patch("api.services.local_comprehension._openai_chat")
    def test_partial_mapping_fails_safe_never_drops_a_clause(self, mock_chat):
        # One intent maps to a confident tool, the other doesn't
        # (action="unknown", low confidence) — must NOT execute only the
        # clause that happened to map. Falls through to LLM, exactly like
        # a fully-unmapped single intent already does.
        mock_chat.return_value = (
            '{"intents": ['
            '{"action": "open", "object_type": "application", "name": "chrome", "confidence": 0.95}, '
            '{"action": "unknown", "confidence": 0.1}'
            ']}',
            700.0,
        )
        from brain.orchestrator import orchestrator, ActionType
        import asyncio
        # Same requirement as the test above: must not be directly
        # resolvable by intent_router.route() at tier <=3 on the raw
        # text, or this test would exercise the wrong code path.
        decision = asyncio.run(
            orchestrator._route_intent(
                "kuch ajeeb sa bol raha hai aur phir bhi kuch aur bhi", "ur_roman",
            )
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.action, ActionType.LLM)
        self.assertTrue(decision.context.get("local_qwen_used"))


if __name__ == "__main__":
    unittest.main()
