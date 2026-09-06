"""
Language-parity tests — English / Roman Urdu / Urdu script / mixed must
converge to the SAME canonical action, through the SAME existing systems
(mixed_language_engine, intent_router, context_stack, object_resolver,
context_resolver, approval_intent), not a separate Urdu tool stack.

These are text-level tests (no audio, no live STT/TTS) — they exercise the
pipeline exactly as it runs after Whisper has already produced a
transcript. Qwen/local_comprehension is intentionally NOT exercised here
(it makes a real ~5-8s Ollama call per case) — see test_multilingual_pipeline.py
for its own dedicated, already-passing unit tests.
"""
from __future__ import annotations

import pytest

from api.services import mixed_language_engine as mle
from api.services import language_detector as ld
from api.services.context_stack import context_stack, ContextEntity
from api.services.intent_router import intent_router as ir
from api.services.approval_intent import parse_yes_no


def _canon(text: str) -> str | None:
    lang = ld.detect(text)["lang"]
    return mle.analyze(text, lang)


# ── "chalao" disambiguation (context-based, not blind regex) ─────────────────

@pytest.mark.parametrize("text,expected", [
    ("chrome chalao", "open chrome"),
    # "gana"/"video" chalao name no specific title — routed through YouTube
    # search (which actually plays something) rather than bare "play gana",
    # which intent_router's generic play/pause pattern turned into a no-op
    # media-key press when nothing was already playing (live bug, fixed
    # 2026-08-24: see mixed_language_engine._media_noun_query).
    ("gana chalao", "play trending songs on youtube"),
    ("video chalao", "play trending songs on youtube"),
    ("dobara chalao", "pause music"),  # resume/toggle
    ("youtube pe atif aslam chalao", "play atif aslam on youtube"),
])
def test_chalao_disambiguation(text, expected):
    assert _canon(text) == expected


# ── Pronoun / ordinal reference normalization ────────────────────────────────
# These must converge to plain English pronoun/ordinal phrasing so they are
# resolved by the SAME context_stack/object_resolver English logic already
# used for real English pronoun follow-ups — not a parallel Urdu resolver.

@pytest.mark.parametrize("text,expected", [
    ("isko band karo", "close it"),
    ("isko delete karo", "delete it"),
    ("pehla wala kholo", "open the first one"),
    ("wo wala kholo", "open that one"),
    ("doosra wala delete karo", "delete the second one"),
    ("yeh wala band karo", "close this one"),
])
def test_pronoun_ordinal_normalization(text, expected):
    assert _canon(text) == expected


def test_pronoun_resolves_via_existing_context_stack():
    """The canonical 'close it'/'open it' text produced above must actually
    resolve through the REAL intent_router + context_stack — not just look
    like valid English."""
    context_stack.clear()
    context_stack.push(ContextEntity(
        type="app", value="chrome", display="Chrome", source="open_application",
    ))
    try:
        close_result = ir.route("close it")
        open_result = ir.route("open it")
        assert close_result.tool_name == "kill_app"
        assert close_result.params.get("app_name") == "chrome"
        assert open_result.tool_name == "open_application"
        assert open_result.params.get("app_name") == "chrome"
    finally:
        context_stack.clear()


# ── Media control (Roman Urdu commands with no prior English equivalent) ────

@pytest.mark.parametrize("text,expected", [
    ("gana rok do", "pause music"),
    ("agla gana chalao", "next song"),
    ("pichla gana lagao", "previous song"),
    ("recycle bin khali karo", "empty recycle bin"),
    ("kachra saaf karo", "empty recycle bin"),
])
def test_new_command_coverage(text, expected):
    assert _canon(text) == expected


# ── English/Roman-Urdu/mixed parity: same canonical result ───────────────────

@pytest.mark.parametrize("english,roman_urdu", [
    ("play believer on youtube", "youtube pe believer chalao"),
])
def test_english_vs_roman_urdu_same_intent_shape(english, roman_urdu):
    # English commands don't go through mixed_language_engine at all
    # (it's a no-op for lang == "en" by design) — the real invariant is
    # that BOTH eventually reach the same tool via intent_router.
    ur_canonical = _canon(roman_urdu)
    assert ur_canonical == "play believer on youtube"
    r_en = ir.route(english)
    r_ur = ir.route(ur_canonical)
    assert r_en.tool_name == r_ur.tool_name == "search_youtube"


# ── Approval / cancel parity ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("yes", "yes"), ("yeah", "yes"), ("go ahead", "yes"),
    ("haan", "yes"), ("theek hai", "yes"), ("kar do", "yes"), ("chalo", "yes"),
    ("جی ہاں", "yes"), ("ٹھیک ہے", "yes"), ("کر دو", "yes"),
    ("no", "no"), ("cancel", "no"), ("never mind", "no"),
    ("nahi", "no"), ("rehne do", "no"), ("cancel karo", "no"),
    ("نہیں", "no"), ("رہنے دو", "no"),
    ("open chrome", "unclear"), ("what time is it", "unclear"),
    # Combined acknowledgment + core phrase — real speech stacks these
    # (found via the 50-command real-pipeline validation pass, not
    # invented in isolation — see approval_intent.py's 2026-08-24 fix note).
    ("Haan kar do.", "yes"), ("ہاں، کر دو۔", "yes"),
    ("Nahi, cancel karo.", "no"), ("Acha rehne do.", "no"), ("رہنے دو۔", "no"),
    ("yes I know but open chrome too", "unclear"),
])
def test_approval_parity(text, expected):
    assert parse_yes_no(text) == expected


# ── Phase 2 (real-pipeline validation) fixes ─────────────────────────────────

def test_drive_scope_reordered_to_match_english_word_order():
    # Urdu word order states the scope BEFORE the object ("E drive mein X
    # folder kholo"); English word order (what intent_router's regex
    # expects) puts it after ("open X folder in E drive"). Without
    # reordering, "open E drive perfume wala folder" prefix-matches
    # intent_router's drive-open regex and silently drops "perfume wala
    # folder" entirely.
    canon = _canon("E drive mein perfume wala folder kholo.")
    assert canon == "open perfume wala folder in E drive"
    route = ir.route(canon)
    assert route.tool_name == "smart_open"
    assert route.params.get("drive") == "E"


def test_unresolved_reference_defers_to_qwen_instead_of_misrouting():
    # "Is repo ka README kholo." ("open this repo's README") left "Is"
    # ("this") untranslated in the entity span. Confidently passing
    # "open Is repo README" to intent_router made it launch a nonexistent
    # app called "Is repo README" instead of ever reaching Qwen's
    # context_reference resolution. Correct behavior: return None here so
    # the caller falls through to Qwen (Tier 4), not a wrong deterministic
    # match.
    assert _canon("Is repo ka README kholo.") is None


@pytest.mark.parametrize("text,expected_tool", [
    ("search for Pakistan weather", "search_web"),
    ("search Pakistan weather", "search_web"),
    ("google Pakistan weather", "search_web"),
    ("what is on my screen", "read_screen"),
])
def test_deterministic_patterns_local_comprehension_depends_on(text, expected_tool):
    # These are plain-English canonical shapes local_comprehension's own
    # _synthesize_canonical() produces for "search the web" / "what's on
    # screen" style Qwen results. Both were previously unmapped at the
    # deterministic tier (search_web had zero regex coverage for "search
    # for X" at all; "what is on screen" only matched the "what's"
    # contraction) — meaning results Qwen understood CORRECTLY still
    # silently failed to route.
    route = ir.route(text)
    assert route.tool_name == expected_tool


def test_urdu_script_punctuation_stripped_from_entity_span():
    # Same root cause as the ASCII trailing-space bug, but for Urdu-script
    # punctuation: '.!?,;' never included Urdu ۔ (U+06D4) or ؟ (U+061F),
    # so "پہلا والا کھولو۔" left "پہلا والا ۔" as the entity span — the
    # trailing Urdu full stop broke the exact-phrase match against
    # "پہلا والا" in _PRONOUN_PHRASE_MAP, and the command fell through to
    # a raw (wrong) open_application guess instead of resolving as an
    # ordinal reference.
    assert mle.analyze("پہلا والا کھولو۔", "ur") == "open the first one"


def test_urdu_script_unresolved_reference_defers_to_qwen():
    # Urdu-script analog of test_compositional_relative_clause_defers_to_
    # qwen_not_intent_router_catchall — "جو فائل ... اسے بند کر دو۔"
    # ("close the file that was just opened") contains جو/اسے
    # (demonstrative/relative pronouns), a structural signal this fast
    # tier can't safely resolve. _has_unresolved_reference only checked
    # Roman-Urdu spellings (is/us/ye/wo/jo); the Urdu-script words were
    # missing entirely, so this canonicalized to a nonsense kill_app
    # target instead of deferring to Qwen.
    assert mle.analyze("جو فائل ابھی کھولی تھی اسے بند کر دو۔", "ur") is None


def test_strip_action_words_leaves_no_trailing_whitespace():
    # General bug in _strip_action_words: .strip('.!?,;') can expose a NEW
    # trailing whitespace character once the punctuation it removed is
    # gone (e.g. "folder ." -> stripping "." alone leaves "folder " with
    # an unstripped trailing space). Every canonical string produced by
    # analyze() silently carried this stray space whenever the source
    # utterance ended in punctuation preceded by a stripped connector
    # word — caught while investigating an unrelated routing failure.
    assert mle.analyze("Chrome kholo.", "mixed") == "open Chrome"
    assert mle.analyze("Zara mera Downloads folder khol dena.", "mixed") == "open Downloads folder"


def test_open_file_precedes_generic_open_catchall():
    # Pre-existing English bug (not Urdu-specific) found while tracing why
    # local_comprehension's Qwen-synthesized "open file X" canonical
    # misrouted: intent_router's generic "open <anything>" catch-all
    # matched first (first-match-wins), so "open file report.txt" was
    # captured whole as a literal app name instead of ever reaching the
    # more specific open_file rule.
    route = ir.route("open file report.txt")
    assert route.tool_name == "open_file"
    assert route.params.get("path") == "report.txt"


def test_filler_words_stripped_from_polite_roman_urdu_phrasing():
    # "Zara mera Downloads folder khol dena." ("please open my Downloads
    # folder") — "zara"/"mera"/"dena" (politeness softener, possessive,
    # helper verb) were left attached to the entity span, so the whole
    # phrase (including the politeness wrapper) was passed to
    # intent_router as if it were literally the object's name.
    canon = _canon("Zara mera Downloads folder khol dena.")
    assert canon == "open Downloads folder"
    route = ir.route(canon)
    assert route.tool_name == "open_directory"


def test_compositional_relative_clause_defers_to_qwen_not_intent_router_catchall():
    # Same failure as test_unresolved_reference_defers_to_qwen_instead_of_
    # misrouting, but caught one tier deeper: intent_router's OWN generic
    # "X kholo" -> open_application catch-all matches directly on raw
    # text (Tier 2, before Qwen ever runs) and — even after excluding
    # demonstrative words from the captured group — re.search just
    # re-anchors past them, matching "README kholo" alone and returning
    # open_application(app_name="README"). The reject_if guard on that
    # rule must reject the WHOLE match when a demonstrative/relative
    # pronoun appears anywhere in the sentence, not just in the captured
    # span.
    route = ir.route("Is repo ka README kholo.")
    assert route.tool_name is None


def test_arabic_script_never_reaches_the_english_only_semantic_classifier():
    # Tier 3's classifier (all-MiniLM-L6-v2) is English-only — real Urdu
    # script text produced a confident-but-wrong match ("آواز تھوڑی کم
    # کرو۔" -> brightness_control instead of volume) that pre-empted the
    # Qwen tier (which correctly understands Urdu script) from ever
    # running. This doesn't test _semantic_route's output directly (the
    # classifier may not be loaded in this test process) — it verifies no
    # tool is confidently returned via any tier for raw Arabic-script text
    # that has no deterministic rule, so the caller correctly falls
    # through to Qwen instead of trusting a semantic guess.
    route = ir.route("آواز تھوڑی کم کرو۔")
    assert route.tool_name is None


# ── Negation must never be mis-canonicalized as a command ───────────────────

@pytest.mark.parametrize("text", [
    "kholo nahi",
    "band mat karo",
    "isko delete mat karo",
])
def test_negation_never_canonicalized(text):
    assert _canon(text) is None


# ── Urdu fast-path: connector-edge stripping must never mutate payload ──────
# The 2026-09-04 fix for "YouTube کو کھولو اور کوئی گانا چلا دو" (see
# mixed_language_engine._URDU_CONNECTORS_EDGE_RE / _strip_urdu_connectors_
# at_edges) strips Urdu-script postpositions/conjunctions (کو/کا/کی/کے/
# میں/پر/پہ/بھی/نے/سے/اور/یا) ONLY from the leading/trailing edges of an
# entity span — never its interior — specifically so free-form payload
# text (search queries, filenames, folder names, note/message content)
# that legitimately contains these exact words is never corrupted. These
# tests are the regression guard for that boundary.

class TestUrduConnectorPayloadPreservation:
    def test_search_query_content_words_survive_untouched(self):
        # "دل کے ارماں" ("the heart's desires" — a real song title)
        # legitimately contains "کے" as part of the phrase, not as a
        # postposition to be stripped. Quoted, so quote-protection
        # (_protect_quoted_spans) is the primary mechanism; the connector
        # strip must never reach inside it either way.
        canon = _canon('Google پر "دل کے ارماں" search کرو')
        assert canon == "search for دل کے ارماں"

    def test_unquoted_folder_name_with_interior_connector_survives(self):
        # "علی کا کمرہ" ("Ali's room") — interior "کا" is part of the
        # folder's actual name, not grammatical glue around it. No quotes
        # here at all — this exercises _strip_urdu_connectors_at_edges'
        # edge-only design directly (interior words are never touched by
        # the regex in the first place, regardless of quoting).
        canon = _canon("علی کا کمرہ folder کھولو")
        assert canon == "open علی کا کمرہ folder"
        route = ir.route(canon)
        assert route.tool_name in ("smart_open", "open_directory", None)
        # Whatever the router does with it, the entity text itself must
        # never have silently dropped "کا".
        if route.params:
            joined = " ".join(str(v) for v in route.params.values())
            assert "علی" in joined

    def test_whatsapp_message_body_untouched_by_connector_or_verb_stripping(self):
        # WhatsApp intent parsing is a SEPARATE existing system
        # (api.integrations.whatsapp.wa_intent.parse_wa_intent) —
        # mixed_language_engine has no "message"/"send" verb mapping at
        # all and must never be extended to parse WhatsApp commands (see
        # CLAUDE.md: "WHATSAPP MUST REUSE PHASE 4/5" / "Do not build Urdu
        # WhatsApp execution"). This verifies PLANNING only — parse_wa_intent
        # never resolves a contact or sends anything — that a message body
        # containing Urdu postposition words ("میں" appears twice, once as
        # the pronoun "I" and once as the postposition "in") survives
        # completely untouched, byte-for-byte, exactly as the module's own
        # payload-preservation design already guarantees for English-verb-
        # plus-Urdu-content code-switching.
        from api.integrations.whatsapp.wa_intent import parse_wa_intent
        intent = parse_wa_intent("message Tayyab that میں گھر میں ہوں")
        assert intent is not None
        assert intent.action == "send_text"
        assert intent.message == "میں گھر میں ہوں"


# ── Urdu fast-path: deterministic compound-command splitting ────────────────
# The 2026-09-04 fix (mixed_language_engine.split_compound) for the exact
# live-caught bug: "YouTube کو کھولو اور کوئی گانا چلا دو" ("open YouTube
# and play some song") used to collapse to a SINGLE "open YouTube" action
# — the second half was silently discarded because the old single-shot
# analyze() takes the first _VERB_MAP match anywhere in the text and
# canonicalizes only that one action. split_compound() splits on اور/پھر/
# aur/phir/"us ke baad" FIRST, then runs the same per-clause analyze() on
# each piece — deterministic, no LLM, only used when every clause
# independently resolves (a partial resolution returns None, never
# executes a subset of clauses).

class TestUrduCompoundSplitting:
    def test_exact_real_failure_case_urdu_script(self):
        steps = mle.split_compound(
            "YouTube کو کھولو اور کوئی گانا چلا دو۔", "ur",
        )
        assert steps is not None
        assert len(steps) == 2
        assert steps[0] == "open YouTube"
        # Exact wording of the second step may vary with existing media
        # routing conventions, but action=play and platform/context=
        # youtube must always be present — it must never disappear.
        assert steps[1].startswith("play ")
        assert "youtube" in steps[1].lower()

    def test_roman_urdu_compound_youtube_and_song(self):
        steps = mle.split_compound("youtube kholo aur koi gana chalao", "mixed")
        assert steps is not None
        assert len(steps) == 2
        assert steps[0] == "open youtube"
        assert steps[1].startswith("play ")
        assert "youtube" in steps[1].lower()

    def test_roman_urdu_compound_chrome_and_gmail(self):
        steps = mle.split_compound("chrome kholo aur gmail open karo", "mixed")
        assert steps is not None
        assert len(steps) == 2
        assert steps[0] == "open chrome"
        assert steps[1] == "open gmail"

    def test_single_command_never_split(self):
        # A single, non-compound Urdu command must never be forced through
        # the compound path — split_compound() must decline (< 2 parts)
        # and let the caller's normal single-shot routing handle it.
        assert mle.split_compound("Chrome کھولو", "ur") is None
        assert mle.split_compound("chrome kholo", "mixed") is None

    def test_english_never_split_here(self):
        # English is untouched by design — split_compound() is gated on
        # detected_lang != "en" and must return None immediately, leaving
        # brain/orchestrator.py's existing English-only _MULTISTEP_RE the
        # sole compound path for English input.
        assert mle.split_compound("open chrome and then open gmail", "en") is None

    def test_partial_clause_resolution_returns_none_not_a_partial_plan(self):
        # If "aur"/"phir" appears as an ordinary word rather than a real
        # conjunction (one clause fails to independently canonicalize),
        # split_compound() must return None entirely — never execute only
        # the clauses that happened to parse.
        steps = mle.split_compound("Chrome کھولو اور بلاہ بلاہ کچھ نامعلوم بات", "ur")
        assert steps is None


# ── English / Urdu / Roman-Urdu single-command parity ────────────────────────
# All three phrasings of the same command must converge on the SAME
# downstream tool + equivalent params — through the SAME intent_router
# English text already uses, never a parallel Urdu tool stack.

class TestSingleCommandLanguageParity:
    def _route(self, text: str, lang: str | None = None):
        canon = text if lang == "en" else mle.analyze(text, lang or ld.detect(text)["lang"])
        assert canon is not None, f"{text!r} (lang={lang}) failed to canonicalize"
        return ir.route(canon)

    @pytest.mark.parametrize("text,lang", [
        ("open Chrome", "en"),
        ("Chrome کھولو", "ur"),
        ("chrome kholo", "mixed"),
    ])
    def test_chrome_parity(self, text, lang):
        route = self._route(text, lang)
        assert route.tool_name == "open_application"
        assert route.params.get("app_name", "").lower() == "chrome"

    @pytest.mark.parametrize("text,lang", [
        ("open YouTube", "en"),
        ("YouTube کھولو", "ur"),
        ("youtube kholo", "mixed"),
    ])
    def test_youtube_parity(self, text, lang):
        route = self._route(text, lang)
        assert route.tool_name == "open_application"
        assert route.params.get("app_name", "").lower() == "youtube"

    @pytest.mark.parametrize("text,lang", [
        ("open Spotify", "en"),
        ("Spotify کھولو", "ur"),
    ])
    def test_spotify_parity(self, text, lang):
        route = self._route(text, lang)
        assert route.tool_name == "open_application"
        assert route.params.get("app_name", "").lower() == "spotify"

    @pytest.mark.parametrize("text,lang", [
        ("open WhatsApp", "en"),
        ("WhatsApp کھولو", "ur"),
    ])
    def test_whatsapp_parity(self, text, lang):
        route = self._route(text, lang)
        # open_application catch-all vs wa_show_chat both mean "open
        # WhatsApp" for this router — same as the existing English
        # behavior; the requirement is that Urdu converges on the SAME
        # tool English does, not a specific tool name.
        route_en = self._route("open WhatsApp", "en")
        assert route.tool_name == route_en.tool_name
        assert route.params == route_en.params
