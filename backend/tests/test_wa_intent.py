"""
test_wa_intent.py — Phase 4 deterministic WhatsApp intent parser.

parse_wa_intent() never resolves contacts/files/context — these tests only
check the parsed shape (action/contact_ref/message/artifact_ref/show_ui/
confidence/requires_cache_hit), never call into ContactResolver or the
identity store.
"""
from __future__ import annotations

import pytest

from api.integrations.whatsapp.wa_intent import parse_wa_intent, WAIntent


class TestSendTextExplicitWhatsapp:
    def test_whatsapp_verb_comma(self):
        i = parse_wa_intent("whatsapp Tayyab, I'll be there.")
        assert i.action == "send_text"
        assert i.contact_ref == "Tayyab"
        assert i.message == "I'll be there."
        assert i.requires_cache_hit is False
        assert i.confidence == 1.0

    def test_whatsapp_verb_no_separator(self):
        i = parse_wa_intent("whatsapp tayyab i will be there")
        assert i.action == "send_text"
        assert i.contact_ref == "tayyab"
        assert i.message == "i will be there"
        assert i.requires_cache_hit is False

    def test_send_a_whatsapp_to_saying(self):
        i = parse_wa_intent("send a whatsapp to Tayyab saying Xyron fast-path test")
        assert i.action == "send_text"
        assert i.contact_ref == "Tayyab"
        assert i.message == "Xyron fast-path test"
        assert i.requires_cache_hit is False

    def test_send_whatsapp_message_to(self):
        i = parse_wa_intent("send whatsapp message to Tayyab: on my way")
        assert i.action == "send_text"
        assert i.contact_ref == "Tayyab"
        assert i.message == "on my way"

    def test_send_whatsapp_message_saying_no_space_before_colon(self):
        # Regression: "saying:" (no space before the colon) is both a
        # reserved separator AND a plausible second contact-name word, so
        # a naive multi-word contact capture greedily swallowed it whole
        # ("Tayyab saying" as the contact) before the separator ever got a
        # chance to match. This is the exact Phase 4 Validation B command.
        i = parse_wa_intent("Send a WhatsApp message to Tayyab saying: Xyron fast-path test.")
        assert i.action == "send_text"
        assert i.contact_ref == "Tayyab"
        assert i.message == "Xyron fast-path test."

    def test_reply_saying_no_space_before_colon(self):
        i = parse_wa_intent("reply to Tayyab saying: on my way")
        assert i.action == "reply"
        assert i.contact_ref == "Tayyab"
        assert i.message == "on my way"


class TestSendTextAmbiguousVerbs:
    def test_message_verb_no_separator(self):
        i = parse_wa_intent("Message Tayyab I'm outside.")
        assert i.action == "send_text"
        assert i.contact_ref == "Tayyab"
        assert i.message == "I'm outside."
        assert i.requires_cache_hit is True   # no "whatsapp" keyword — caller must verify

    def test_message_verb_with_comma(self):
        i = parse_wa_intent("message tayyab, i am outside")
        assert i.contact_ref == "tayyab"
        assert i.message == "i am outside"
        assert i.requires_cache_hit is True

    def test_tell_verb_pronoun(self):
        i = parse_wa_intent("tell him I'll call later")
        assert i.action == "send_text"
        assert i.contact_ref == "him"
        assert i.message == "I'll call later"
        assert i.requires_cache_hit is True

    def test_no_separator_multiword_name_not_swallowed_whole(self):
        # Regression: contact must not greedily absorb words that belong
        # to the message when there's no separator.
        i = parse_wa_intent("message tayyab i am outside")
        assert i.contact_ref == "tayyab"
        assert i.message == "i am outside"


class TestReply:
    def test_reply_to_pronoun(self):
        i = parse_wa_intent("Reply to him: I'll call him later.")
        assert i.action == "reply"
        assert i.contact_ref == "him"
        assert i.message == "I'll call him later."

    def test_reply_to_name_no_separator(self):
        i = parse_wa_intent("reply to tayyab i will call you")
        assert i.action == "reply"
        assert i.contact_ref == "tayyab"
        assert i.message == "i will call you"

    def test_reply_requires_cache_hit_without_whatsapp_word(self):
        i = parse_wa_intent("reply to tayyab i will call you")
        assert i.requires_cache_hit is True


class TestReplyNaturalPhrasingsFromLiveLog:
    """Regression coverage from a real voice session: after Xyron announced
    an incoming WhatsApp message, none of these natural follow-up phrasings
    were recognized — they all fell through to the generic LLM fallback
    instead of routing to wa_reply. These are the literal (normalizer-
    lowercased) transcripts from that session."""

    def test_reply_him_no_to_period_separator(self):
        i = parse_wa_intent("reply him. i am busy tonight")
        assert i.action == "reply"
        assert i.contact_ref == "him"
        assert i.message == "i am busy tonight"

    def test_send_him_reply_period_say_double_separator(self):
        # "reply." (period) then a redundant "say" before the actual
        # message — the fix must not leave "say" glued onto the message.
        i = parse_wa_intent("send him reply. say i am busy tonight")
        assert i.action == "reply"
        assert i.contact_ref == "him"
        assert i.message == "i am busy tonight"

    def test_leading_yes_period_stripped_before_reply_him(self):
        i = parse_wa_intent("yes. reply him. i am busy tonight")
        assert i.action == "reply"
        assert i.contact_ref == "him"
        assert i.message == "i am busy tonight"

    def test_leading_sure_comma_stripped_before_send_reply(self):
        i = parse_wa_intent("sure, send him a reply saying i am busy tonight")
        assert i.action == "reply"
        assert i.contact_ref == "him"
        assert i.message == "i am busy tonight"

    def test_send_her_a_reply_variant(self):
        i = parse_wa_intent("send her a reply saying on my way")
        assert i.action == "reply"
        assert i.contact_ref == "her"
        assert i.message == "on my way"

    def test_leading_ack_alone_still_fails_cleanly(self):
        # No message content ("yes, send him" from the same session) — must
        # not crash, and must not fabricate a message. Falling through to
        # None (→ LLM fallback) is correct here since there's nothing to send.
        i = parse_wa_intent("yes, send him")
        assert i is None or i.action != "reply"


class TestSendFile:
    def test_send_this_pdf_to(self):
        i = parse_wa_intent("Send this PDF to Tayyab.")
        assert i.action == "send_file"
        assert i.contact_ref == "Tayyab"
        assert i.artifact_ref == {"kind": "context", "query": "this PDF"}

    def test_send_contact_this_image(self):
        i = parse_wa_intent("Send Tayyab this image")
        assert i.action == "send_file"
        assert i.contact_ref == "Tayyab"
        assert i.artifact_ref["kind"] == "context"
        assert "image" in i.artifact_ref["query"].lower()

    def test_send_screenshot_i_just_took(self):
        i = parse_wa_intent("send the screenshot I just took to Tayyab")
        assert i.action == "send_file"
        assert i.contact_ref == "Tayyab"
        assert "screenshot" in i.artifact_ref["query"].lower()

    def test_send_it_to_contact(self):
        i = parse_wa_intent("send it to him")
        assert i.action == "send_file"
        assert i.contact_ref == "him"
        assert i.artifact_ref == {"kind": "context", "query": "it"}


class TestSendFileExactFilename:
    """'name the file by voice' — an exact filename with extension routes
    to {"kind": "filename", "name": ...} for a precise, whole-computer
    search (not the fuzzy/recency-based 'context' kind)."""

    def test_send_filename_to_contact(self):
        i = parse_wa_intent("send photo.jpg to Qasim")
        assert i.action == "send_file"
        assert i.contact_ref == "Qasim"
        assert i.artifact_ref == {"kind": "filename", "name": "photo.jpg"}

    def test_send_contact_filename(self):
        i = parse_wa_intent("send Qasim resume.pdf")
        assert i.action == "send_file"
        assert i.contact_ref == "Qasim"
        assert i.artifact_ref == {"kind": "filename", "name": "resume.pdf"}

    def test_filename_with_spaces(self):
        i = parse_wa_intent("send vacation photo 2024.jpg to Tayyab")
        assert i.action == "send_file"
        assert i.contact_ref == "Tayyab"
        assert i.artifact_ref == {"kind": "filename", "name": "vacation photo 2024.jpg"}

    def test_filename_with_leading_demonstrative_is_stripped(self):
        i = parse_wa_intent("send the resume.pdf to Qasim")
        assert i.action == "send_file"
        assert i.artifact_ref == {"kind": "filename", "name": "resume.pdf"}

    def test_document_extension_variants(self):
        for fname in ("notes.docx", "budget.xlsx", "slides.pptx", "data.csv"):
            i = parse_wa_intent(f"send {fname} to Qasim")
            assert i.action == "send_file", fname
            assert i.artifact_ref == {"kind": "filename", "name": fname}, fname


class TestSendFileTypeDescriptionPhrase:
    """A demonstrative + up to 2 descriptor words + a real file-type noun,
    no extension — routes through the existing fuzzy 'context' kind."""

    def test_the_invoice_pdf(self):
        i = parse_wa_intent("send the invoice pdf to Qasim")
        assert i.action == "send_file"
        assert i.contact_ref == "Qasim"
        assert i.artifact_ref == {"kind": "context", "query": "the invoice pdf"}

    def test_my_resume(self):
        i = parse_wa_intent("send my resume to Qasim")
        assert i.action == "send_file"
        assert i.artifact_ref == {"kind": "context", "query": "my resume"}

    def test_two_descriptor_words_before_type_noun(self):
        i = parse_wa_intent("send the march expense report to Qasim")
        assert i.action == "send_file"
        assert i.artifact_ref == {"kind": "context", "query": "the march expense report"}

    def test_contact_first_type_phrase(self):
        i = parse_wa_intent("send Qasim the invoice pdf")
        assert i.action == "send_file"
        assert i.contact_ref == "Qasim"
        assert i.artifact_ref == {"kind": "context", "query": "the invoice pdf"}

    def test_ordinary_non_file_sentence_is_not_hijacked(self):
        # "news" is not a file-type noun — must NOT be claimed as a file
        # send at all (falls through to None / other tiers), regardless of
        # how the broader description-phrase rule is structured.
        i = parse_wa_intent("send the good news to Tayyab")
        assert i is None or i.action != "send_file"


class TestShowChat:
    def test_show_me_whatsapp(self):
        i = parse_wa_intent("Show me Tayyab's WhatsApp.")
        assert i.action == "show_chat"
        assert i.contact_ref == "Tayyab"
        assert i.show_ui is True
        assert i.requires_cache_hit is False   # explicit "whatsapp"

    def test_open_chat(self):
        i = parse_wa_intent("Open Tayyab's chat.")
        assert i.action == "show_chat"
        assert i.contact_ref == "Tayyab"
        assert i.requires_cache_hit is True    # bare "chat" — needs cache/context proof


class TestBareOpenWhatsapp:
    """'open whatsapp' with no contact named — must reuse the existing,
    already-logged-in Chrome WhatsApp Web tab (WhatsAppUIAdapter), not the
    generic open_application launcher, which was observed failing outright
    (WhatsApp Desktop not installed on this machine)."""

    @pytest.mark.parametrize("text", [
        "open whatsapp",
        "open whatsapp.",
        "open whatsapp web",
        "show whatsapp",
        "show me whatsapp",
        "open my whatsapp",
        "open the whatsapp",
    ])
    def test_bare_variants_resolve_to_show_chat_no_contact(self, text):
        i = parse_wa_intent(text)
        assert i is not None
        assert i.action == "show_chat"
        assert i.contact_ref is None
        assert i.requires_cache_hit is False
        assert i.confidence == 1.0

    def test_does_not_swallow_a_real_contact(self):
        # Must NOT match — "tayyab" after "whatsapp" means this isn't the
        # bare-open shape; rule 0b (compound) or rule 1 must claim it instead.
        i = parse_wa_intent("open whatsapp and open tayyab chat")
        assert i.contact_ref == "tayyab"

    def test_does_not_match_unrelated_app(self):
        assert parse_wa_intent("open chrome") is None


class TestShowChatCompoundOpenWhatsapp:
    """Regression coverage for a real, live-observed voice failure: 'open
    whatsapp and open/show <contact> chat[s]' fell through Tier 0.5 entirely
    (no rule matched this compound shape), letting a generic Tier 2 'open
    <anything>' catch-all in intent_router.py swallow the whole remainder
    as a garbled app_name — confirmed from an actual backend log."""

    @pytest.mark.parametrize("text,expected_contact", [
        ("open whatsapp and open tayyab chat", "tayyab"),
        ("open whatsapp and open tayyab chats", "tayyab"),
        ("open whatsapp and show me the tayyab chat", "tayyab"),
        ("open whatsapp and show the tayyab chat", "tayyab"),
        ("open whatsapp, open tayyab chat", "tayyab"),
        # exact real transcripts from the log (Whisper misheard "Tayyab" as "Yup")
        ("open whatsapp and show the yup chat", "yup"),
        ("open whatsapp and open yup chat", "yup"),
    ])
    def test_compound_shape_resolves_to_show_chat(self, text, expected_contact):
        i = parse_wa_intent(text)
        assert i is not None
        assert i.action == "show_chat"
        assert i.contact_ref == expected_contact
        assert i.requires_cache_hit is False  # explicit "whatsapp" keyword present

    def test_bare_open_whatsapp_now_claimed_no_contact(self):
        # Superseded: bare "open whatsapp" now maps to show_chat with no
        # contact (see TestBareOpenWhatsapp below) — open_application's
        # generic app-launch was observed failing outright for "whatsapp"
        # (LAUNCH_UNVERIFIED, WhatsApp Desktop not installed), so this is a
        # deliberate redirect to the working Chrome-tab-reuse UI path.
        i = parse_wa_intent("open whatsapp")
        assert i is not None
        assert i.action == "show_chat"
        assert i.contact_ref is None

    def test_compound_without_chat_word_does_not_misfire(self):
        # "open whatsapp and open chrome" must NOT be captured as a contact
        # named "chrome" — the tail must literally be "chat"/"chats".
        assert parse_wa_intent("open whatsapp and open chrome") is None


class TestContactCannotCrossSentenceBoundary:
    """Regression coverage for a real, live-observed bug: _CONTACT_WORD's
    character class used to allow a literal '.' inside a captured word, so
    a contact-name span could bridge across a full sentence boundary. Live
    logs showed Whisper hallucinating a leading clause on short audio for a
    bare 'open whatsapp' utterance ('Open Microsoft. Open WhatsApp.' /
    'Open VS Code. Open WhatsApp.' — a different fabricated first sentence
    each time) — with the old char class, 'microsoft.' + 'open' (or
    'vs code.' + 'open') both got swallowed as a single 2-word "contact",
    so wa_show_chat was incorrectly claimed for an utterance that was never
    actually about a WhatsApp contact at all."""

    @pytest.mark.parametrize("text", [
        "open microsoft. open whatsapp",
        "open vs code. open whatsapp",
        "open something random. open whatsapp",
    ])
    def test_hallucinated_leading_clause_does_not_claim_whatsapp(self, text):
        assert parse_wa_intent(text) is None

    def test_bare_open_whatsapp_alone_claimed_with_no_contact(self):
        # The real, intended utterance behind all the hallucinated variants
        # above. Superseded by the bare-open-whatsapp feature (see
        # TestBareOpenWhatsapp): this is the ONE shape that should now
        # match, distinctly from the hallucinated "open X. open whatsapp"
        # variants above (which correctly still return None).
        i = parse_wa_intent("open whatsapp")
        assert i is not None
        assert i.contact_ref is None

    @pytest.mark.parametrize("text,expected_contact", [
        ("Send this PDF to Tayyab.", "Tayyab"),
        ("Send Tayyab this image.", "Tayyab"),
    ])
    def test_trailing_sentence_period_still_tolerated(self, text, expected_contact):
        # The fix must not regress legitimate trailing punctuation on a
        # send_file command called directly (bypassing normalize(), which
        # would otherwise strip it) — this exercises parse_wa_intent's own
        # explicit [.!?]* tolerance, not the old (buggy) char-class escape.
        i = parse_wa_intent(text)
        assert i is not None
        assert i.action == "send_file"
        assert i.contact_ref == expected_contact


class TestGetMessages:
    def test_what_did_x_say(self):
        i = parse_wa_intent("What did Tayyab say?")
        assert i.action == "get_messages"
        assert i.contact_ref == "Tayyab"
        assert i.requires_cache_hit is True


class TestShowMeSuffix:
    def test_send_and_show_me(self):
        i = parse_wa_intent("Send it to him and show me.")
        assert i.action == "send_file"
        assert i.show_ui is True

    def test_show_me_too(self):
        i = parse_wa_intent("whatsapp tayyab thanks, show me too")
        assert i.show_ui is True
        assert i.message == "thanks"


class TestNegativeCases:
    """These must all fall through to None — never hijack unrelated intent."""

    @pytest.mark.parametrize("text", [
        "what is the weather today",
        "open chrome",
        "what did the doctor say about my results",  # trailing clause breaks the get_messages shape
        "read my messages",
        "create a new document",
        "",
        "   ",
    ])
    def test_no_match(self, text):
        assert parse_wa_intent(text) is None

    def test_never_resolves_contacts_itself(self):
        # parse_wa_intent must not import ContactResolver / the identity
        # store — it only extracts references, never resolves them.
        import api.integrations.whatsapp.wa_intent as mod
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(mod))
        imported = {
            n.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for n in node.names
        }
        assert "ContactResolver" not in imported
        assert "get_default_identity_store" not in imported
        assert "WhatsAppIdentityStore" not in imported


class TestAmbiguousShapesProduceGatedCandidates:
    """
    parse_wa_intent() itself is permissive for ambiguous verb shapes — it
    always returns a candidate WAIntent with requires_cache_hit=True. The
    actual "don't hijack unrelated queries" guarantee (handoff §29) is
    enforced one layer up, by the caller checking requires_cache_hit
    against the identity store/context before committing to the WhatsApp
    route (see test_intent_router_whatsapp.py for that end-to-end check).
    """

    @pytest.mark.parametrize("text", [
        "message a random stranger nobody knows about this",
        "message board for the office",
        "tell alice about the meeting tomorrow",
    ])
    def test_ambiguous_shape_is_gated_not_none(self, text):
        i = parse_wa_intent(text)
        assert i is not None
        assert i.requires_cache_hit is True


class TestConfidence:
    def test_explicit_whatsapp_keyword_is_max_confidence(self):
        assert parse_wa_intent("whatsapp Tayyab, hi").confidence == 1.0

    def test_ambiguous_verb_is_lower_confidence(self):
        assert parse_wa_intent("message Tayyab hi").confidence < 1.0
