"""
test_whatsapp_ui_adapter.py — Phase 3 milestone: visual WhatsApp surface.

Covers:
  - WhatsAppUITarget.from_chat_id (UI-safe phone derivation, LID/group safety)
  - WhatsAppUIAdapter.open_whatsapp / open_chat / focus_chat
  - contact-targeting safety (exact phone deep link; LID/group REFUSED,
    never a fuzzy fallback)
  - fallback policy (Desktop preferred; Web only when explicitly enabled;
    never silent — report states the target)
  - verification semantics (window-level verify, honest non-verification of
    chat content)
  - no-send guarantee (launch URLs never carry text=; exactly one launch
    per action; the adapter has no transport at all)

Hermetic: opener/window-probe/desktop-probe are injected fakes; the only
real-module touch is one delegation test for the default opener
(monkeypatched system_tools.open_url_native).
"""
from __future__ import annotations

import subprocess as real_subprocess
from pathlib import Path

import pytest

from api.integrations.whatsapp.wa_ui_adapter import (
    UIActionReport,
    WhatsAppUIAdapter,
    WhatsAppUITarget,
    _default_desktop_available,
    _default_window_probe,
    get_default_ui_adapter,
)


EXPECTED_CHAT = "923001234567@s.whatsapp.net"
EXPECTED_PHONE = "923001234567"

WA_PROBE_HIT = {"whatsapp_window_title": "WhatsApp", "foreground_title": "WhatsApp"}
WA_PROBE_MISS = {"whatsapp_window_title": None, "foreground_title": "Terminal"}
# WhatsApp window visible but another window holds the foreground.
WA_PROBE_HIT_BG = {"whatsapp_window_title": "WhatsApp", "foreground_title": "Terminal"}


class FakeOpener:
    def __init__(self, ok: bool = True):
        self.calls: list[str] = []
        self.ok = ok

    def __call__(self, url: str) -> bool:
        self.calls.append(url)
        return self.ok


class ProbeScript:
    """Window probe that returns scripted results, recording call count."""

    def __init__(self, results):
        # results: list of dicts; last one repeats once exhausted.
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


def make_adapter(opener, probe_results, desktop=True, web=False, **kw):
    probe = probe_results if callable(probe_results) else ProbeScript(probe_results)
    return WhatsAppUIAdapter(
        opener=opener, window_probe=probe,
        desktop_probe=lambda: desktop, allow_web_fallback=web, **kw
    )


# ---------------------------------------------------------------------------
# WhatsAppUITarget
# ---------------------------------------------------------------------------

class TestWhatsAppUITarget:

    def test_phone_derived_from_s_whatsapp_net_jid(self):
        t = WhatsAppUITarget.from_chat_id(EXPECTED_CHAT, display_name="Tayyab Aziz")
        assert t.phone == EXPECTED_PHONE
        assert t.display_name == "Tayyab Aziz"
        assert t.chat_id == EXPECTED_CHAT

    def test_lid_jid_has_no_phone(self):
        t = WhatsAppUITarget.from_chat_id("176016366547081@lid", display_name="Old")
        assert t.phone is None
        assert t.normalized_phone() is None

    def test_group_jid_has_no_phone(self):
        t = WhatsAppUITarget.from_chat_id("1203630234@g.us")
        assert t.phone is None

    def test_garbage_jid_has_no_phone(self):
        assert WhatsAppUITarget.from_chat_id("").phone is None
        assert WhatsAppUITarget.from_chat_id("not-a-jid").phone is None

    def test_too_short_user_part_is_rejected(self):
        t = WhatsAppUITarget.from_chat_id("12345@s.whatsapp.net")
        assert t.phone is None

    def test_normalized_phone_strips_plus_and_separators(self):
        t = WhatsAppUITarget(chat_id=EXPECTED_CHAT, phone="+92 301 1496677")
        assert t.normalized_phone() == EXPECTED_PHONE

    def test_normalized_phone_rejects_short_digits(self):
        assert WhatsAppUITarget(phone="12345").normalized_phone() is None
        assert WhatsAppUITarget(phone=None).normalized_phone() is None


# ---------------------------------------------------------------------------
# Contact-targeting safety — refusals
# ---------------------------------------------------------------------------

class TestTargetSafetyRefusals:

    def test_lid_target_refused_without_fuzzy_fallback(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id("176016366547081@lid"))
        assert isinstance(r, UIActionReport)
        assert r.ok is False
        assert r.ui_target == "none"
        assert r.contact_targeting == "none"
        assert "exact phone" in r.detail
        assert opener.calls == []          # nothing launched — no guess

    def test_group_target_refused(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT])
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id("1203630234@g.us"))
        assert r.ok is False
        assert opener.calls == []

    def test_none_target_refused(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT])
        assert adapter.open_chat(None).ok is False
        assert opener.calls == []

    def test_empty_chat_id_refused(self):
        adapter = make_adapter(FakeOpener(), [WA_PROBE_HIT])
        r = adapter.open_chat(WhatsAppUITarget(chat_id=""))
        assert r.ok is False and r.contact_targeting == "none"

    def test_focus_chat_propagates_refusal(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT])
        r = adapter.focus_chat(WhatsAppUITarget.from_chat_id("1234@lid"))
        assert r.ok is False and r.action == "focus_chat"
        assert opener.calls == []


# ---------------------------------------------------------------------------
# Desktop deep-link path
# ---------------------------------------------------------------------------

class TestDesktopPath:

    def test_open_chat_exact_phone_deep_link(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=True)
        target = WhatsAppUITarget.from_chat_id(EXPECTED_CHAT, "Tayyab Aziz")
        r = adapter.open_chat(target)
        assert r.ok is True
        assert opener.calls == [f"whatsapp://send?phone={EXPECTED_PHONE}"]
        assert len(opener.calls) == 1               # exactly one launch
        assert r.ui_target == "desktop"
        assert r.launch_method == "deep_link"
        assert r.contact_targeting == "exact_phone_deep_link"
        assert r.deep_link == opener.calls[0]

    def test_open_chat_verified_with_foreground(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.verified is True
        assert r.whatsapp_window_title == "WhatsApp"
        assert r.foreground_title == "WhatsApp"
        assert "foreground window" in r.verification_detail
        # honest limit: chat content is not machine-verified
        assert "NOT machine-verified" in r.verification_detail

    def test_open_chat_verified_when_foreground_is_other_window(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT_BG], desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.verified is True
        assert "different window" in r.verification_detail

    def test_open_chat_unverified_when_window_never_appears(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_MISS], desktop=True)
        r = adapter.open_chat(
            WhatsAppUITarget.from_chat_id(EXPECTED_CHAT), verify_timeout_s=0.0
        )
        assert r.ok is True                       # launch accepted
        assert r.verified is False
        assert "no WhatsApp-titled window" in r.verification_detail

    def test_verification_polls_until_window_appears(self):
        opener = FakeOpener()
        # first poll misses, second poll sees the window
        adapter = make_adapter(opener, [WA_PROBE_MISS, WA_PROBE_HIT], desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.verified is True

    def test_open_whatsapp_app_root_uses_bare_deep_link(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=True)
        r = adapter.open_whatsapp()
        assert r.ok is True
        assert opener.calls == ["whatsapp://"]
        assert r.contact_targeting == "app_only"

    def test_opener_failure_reported(self):
        opener = FakeOpener(ok=False)
        probe = ProbeScript([WA_PROBE_HIT])
        adapter = make_adapter(opener, probe, desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is False
        assert r.deep_link is None
        assert "not accepted" in r.detail
        assert probe.calls == 0                   # no verification without launch

    def test_opener_exception_is_contained(self):
        def boom(url):
            raise RuntimeError("shell broke")
        adapter = make_adapter(boom, [WA_PROBE_HIT], desktop=True)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is False


# ---------------------------------------------------------------------------
# Web fallback policy — explicit, never silent
# ---------------------------------------------------------------------------

class TestWebFallbackPolicy:

    def test_web_fallback_stops_when_no_tab_found(self):
        """When no WhatsApp tab is found, STOP and report. Do NOT open new tab automatically.
        
        This test verifies the new Phase 3 UX behavior: when CDP, window activation,
        and keyboard cycling all fail, the adapter reports the failure instead of
        silently opening a new tab.
        """
        opener = FakeOpener()
        # Mock keyboard cycling to return "not found"
        kb_result = {
            "method": "keyboard_cycling",
            "found": False,
            "windows_enumerated": 2,
            "windows_probed": 2,
            "windows_report": [],
        }
        adapter = make_adapter(
            opener, [WA_PROBE_MISS], desktop=False, web=True,
            keyboard_find_tab_fn=lambda: kb_result,
        )
        target = WhatsAppUITarget.from_chat_id(EXPECTED_CHAT, "Tayyab Aziz")
        r = adapter.open_chat(target)
        # Should STOP and report, NOT open new tab
        assert r.ok is False
        assert r.ui_target == "web"
        assert r.launch_method == "none"
        assert r.contact_targeting == "exact_phone_deep_link"
        assert opener.calls == []  # NO new tab opened
        # Phase 3 UX: the report states the failure explicitly
        assert "NOT FOUND" in r.detail
        assert "No new tab opened" in r.detail
        assert r.cdp_tab_reused is False  # CDP not available in this test

    def test_no_web_fallback_means_refusal_not_guessing(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=False, web=False)
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is False
        assert r.ui_target == "none"
        assert opener.calls == []
        assert "fuzzy" in r.detail

    def test_open_whatsapp_web_root_stops_when_no_tab_found(self):
        """When open_whatsapp() is called and no tab is found, STOP and report."""
        opener = FakeOpener()
        # Mock keyboard cycling to return "not found"
        kb_result = {
            "method": "keyboard_cycling",
            "found": False,
            "windows_enumerated": 1,
            "windows_probed": 1,
            "windows_report": [],
        }
        adapter = make_adapter(
            opener, [WA_PROBE_MISS], desktop=False, web=True,
            keyboard_find_tab_fn=lambda: kb_result,
        )
        r = adapter.open_whatsapp()
        # Should STOP and report, NOT open new tab
        assert r.ok is False
        assert opener.calls == []  # NO new tab opened
        assert "NOT FOUND" in r.detail

    def test_open_whatsapp_refused_without_any_target(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=False, web=False)
        r = adapter.open_whatsapp()
        assert r.ok is False and opener.calls == []
        assert "not installed" in r.detail


# ---------------------------------------------------------------------------
# focus_chat
# ---------------------------------------------------------------------------

class TestFocusChat:

    def test_focus_chat_when_window_already_open(self):
        opener = FakeOpener()
        probe = ProbeScript([
            WA_PROBE_HIT,   # pre-flight: already open
            WA_PROBE_HIT,   # post-launch verification
        ])
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=probe,
            desktop_probe=lambda: True, allow_web_fallback=False,
        )
        r = adapter.focus_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.action == "focus_chat"
        assert r.ok is True and r.verified is True
        assert "already open" in r.detail
        assert opener.calls == [f"whatsapp://send?phone={EXPECTED_PHONE}"]

    def test_focus_chat_when_window_not_open_opens_it(self):
        opener = FakeOpener()
        probe = ProbeScript([
            WA_PROBE_MISS,  # pre-flight: not open
            WA_PROBE_HIT,   # post-launch verification
        ])
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=probe,
            desktop_probe=lambda: True, allow_web_fallback=False,
        )
        r = adapter.focus_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert "not open" in r.detail


# ---------------------------------------------------------------------------
# No-send guarantee
# ---------------------------------------------------------------------------

class TestNoSendGuarantee:

    def test_launch_urls_never_carry_text_param(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_HIT], desktop=True)
        adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        adapter.open_whatsapp()
        for url in opener.calls:
            assert "text=" not in url
            assert url.startswith(("whatsapp://", "https://web.whatsapp.com/"))

    def test_exactly_one_launch_per_action(self):
        opener = FakeOpener()
        adapter = make_adapter(opener, [WA_PROBE_MISS], desktop=True)
        adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT),
                          verify_timeout_s=0.0)
        assert len(opener.calls) == 1

    def test_adapter_has_no_transport_dependency(self):
        # The visual adapter must never hold a Baileys/transport handle.
        import inspect
        sig = inspect.signature(WhatsAppUIAdapter.__init__)
        assert "transport" not in sig.parameters


# ---------------------------------------------------------------------------
# Detection + default wiring
# ---------------------------------------------------------------------------

class TestDetectionAndDefaults:

    def test_desktop_available_is_memoized(self):
        counter = {"n": 0}

        def probe():
            counter["n"] += 1
            return True

        adapter = WhatsAppUIAdapter(
            opener=FakeOpener(), window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=probe,
        )
        assert adapter.desktop_available() is True
        assert adapter.desktop_available() is True
        assert counter["n"] == 1

    def test_default_opener_delegates_to_system_tools(self, monkeypatch):
        import api.tools.system_tools as st
        seen: list[str] = []
        monkeypatch.setattr(st, "open_url_native", lambda url: seen.append(url) or True)
        adapter = WhatsAppUIAdapter(
            window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: True, allow_web_fallback=False,
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert seen == [f"whatsapp://send?phone={EXPECTED_PHONE}"]

    def test_get_default_ui_adapter_is_singleton_with_web_fallback(self):
        a1 = get_default_ui_adapter()
        a2 = get_default_ui_adapter()
        assert a1 is a2
        assert a1._allow_web is True

    def test_default_desktop_available_via_exe_path(self, monkeypatch, tmp_path):
        import winreg
        monkeypatch.setattr(winreg, "OpenKey", self._no_key)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))
        exe = tmp_path / "AppData" / "Local" / "WhatsApp" / "WhatsApp.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"MZ")
        assert _default_desktop_available() is True

    def test_default_desktop_available_via_appx_count(self, monkeypatch, tmp_path):
        import winreg
        monkeypatch.setattr(winreg, "OpenKey", self._no_key)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "pf86"))

        class FakeRun:
            def __init__(self, stdout, returncode=0):
                self.stdout, self.returncode = stdout, returncode

        import api.integrations.whatsapp.wa_ui_adapter as mod
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeRun("1"))
        assert _default_desktop_available() is True
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeRun("0"))
        assert _default_desktop_available() is False

    @staticmethod
    def _no_key(*a, **k):
        raise OSError("no such key")

    def test_window_probe_parses_powershell_output(self, monkeypatch):
        import api.integrations.whatsapp.wa_ui_adapter as mod

        class FakeRun:
            returncode = 0
            stdout = "WA_WINDOW:WhatsApp Web - Google Chrome|FOREGROUND:Code"

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeRun())
        result = _default_window_probe()
        assert result["whatsapp_window_title"] == "WhatsApp Web - Google Chrome"
        assert result["foreground_title"] == "Code"

    def test_window_probe_tolerates_garbage_output(self, monkeypatch):
        import api.integrations.whatsapp.wa_ui_adapter as mod

        class FakeRun:
            returncode = 1
            stdout = "Command timed out"

        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: FakeRun())
        result = _default_window_probe()
        assert result["whatsapp_window_title"] is None
        assert result["foreground_title"] is None


# ---------------------------------------------------------------------------
# Phase 3 UX — CDP tab reuse
# ---------------------------------------------------------------------------

class TestCDPTabReuse:
    """Tests for Chrome DevTools Protocol tab reuse (existing WhatsApp Web tab)."""

    def test_cdp_discovers_and_reuses_whatsapp_tab(self):
        opener = FakeOpener()
        cdp_tabs = [
            {"id": "TAB1", "url": "https://web.whatsapp.com/", "title": "WhatsApp"},
            {"id": "TAB2", "url": "https://example.com/", "title": "Example"},
        ]
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=lambda tid, port: True,
            cdp_navigate_tab_fn=lambda url, port: True,
            activate_window_fn=lambda: True,
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT, "Tayyab Aziz"))
        assert r.ok is True
        assert r.cdp_tab_reused is True
        assert r.cdp_tab_url_before == "https://web.whatsapp.com/"
        assert r.cdp_tab_url_after == f"https://web.whatsapp.com/send?phone={EXPECTED_PHONE}"
        assert r.launch_method == "cdp_tab_reuse"
        assert r.window_activated is True
        assert opener.calls == []  # no new tab opened — reused existing

    def test_cdp_tab_reuse_without_navigation(self):
        opener = FakeOpener()
        cdp_tabs = [{"id": "TAB1", "url": "https://web.whatsapp.com/"}]
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=lambda tid, port: True,
            cdp_navigate_tab_fn=lambda url, port: True,
            activate_window_fn=lambda: True,
        )
        r = adapter.open_whatsapp()  # no specific chat — just activate
        assert r.ok is True
        assert r.cdp_tab_reused is True
        assert r.cdp_tab_url_after == r.cdp_tab_url_before  # no navigation
        assert opener.calls == []

    def test_cdp_unavailable_falls_back_to_window_activation(self):
        opener = FakeOpener()
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],  # CDP unavailable
            activate_window_fn=lambda: True,  # but window found
            enumerate_chrome_fn=lambda: [],  # no Chrome windows for omnibox
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert r.cdp_tab_reused is False
        assert r.window_activated is True
        assert "activated existing WhatsApp-titled" in r.detail
        assert opener.calls == []  # no new tab

    def test_no_cdp_no_window_stops_without_new_tab(self):
        """When CDP unavailable and no WhatsApp window found, STOP and report.
        
        Do NOT open a new tab automatically. User must choose next action deliberately.
        """
        opener = FakeOpener()
        # Mock keyboard cycling to return "not found"
        kb_result = {
            "method": "keyboard_cycling",
            "found": False,
            "windows_enumerated": 2,
            "windows_probed": 2,
            "windows_report": [],
        }
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_MISS]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],
            activate_window_fn=lambda: False,  # no WhatsApp window
            keyboard_find_tab_fn=lambda: kb_result,  # keyboard cycling also fails
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        # Should STOP and report, NOT open new tab
        assert r.ok is False
        assert r.cdp_tab_reused is False
        assert r.window_activated is False
        assert "NOT FOUND" in r.detail
        assert "No new tab opened" in r.detail
        assert opener.calls == []  # NO new tab opened

    def test_cdp_activate_failure_falls_back_to_window(self):
        opener = FakeOpener()
        cdp_tabs = [{"id": "TAB1", "url": "https://web.whatsapp.com/"}]
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=lambda tid, port: False,  # activation fails
            activate_window_fn=lambda: True,  # but window activation works
            enumerate_chrome_fn=lambda: [],  # no Chrome windows for omnibox
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert r.window_activated is True
        assert "activated existing WhatsApp-titled" in r.detail
        assert opener.calls == []

    def test_cdp_navigate_failure_reports_honestly(self):
        opener = FakeOpener()
        cdp_tabs = [{"id": "TAB1", "url": "https://web.whatsapp.com/"}]
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=lambda tid, port: True,
            cdp_navigate_tab_fn=lambda url, port: False,  # navigation fails
            activate_window_fn=lambda: True,
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert r.cdp_tab_reused is True
        assert r.cdp_tab_url_after == r.cdp_tab_url_before  # navigation failed
        assert "navigated=False" in r.detail

    def test_cdp_identifies_tab_by_url_not_title(self):
        # Tab with WhatsApp URL but misleading title should still be found
        opener = FakeOpener()
        cdp_tabs = [
            {"id": "TAB1", "url": "https://web.whatsapp.com/send?phone=123", "title": "Loading..."},
        ]
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=lambda tid, port: True,
            activate_window_fn=lambda: True,
        )
        r = adapter.open_whatsapp()
        assert r.cdp_tab_reused is True  # found by URL, not title

    def test_multiple_whatsapp_tabs_prefers_first(self):
        opener = FakeOpener()
        cdp_tabs = [
            {"id": "TAB1", "url": "https://web.whatsapp.com/"},
            {"id": "TAB2", "url": "https://web.whatsapp.com/send?phone=111"},
        ]
        activated_ids = []
        def track_activate(tid, port):
            activated_ids.append(tid)
            return True
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: cdp_tabs,
            cdp_activate_tab_fn=track_activate,
            activate_window_fn=lambda: True,
        )
        adapter.open_whatsapp()
        assert activated_ids == ["TAB1"]  # first WhatsApp tab activated


class TestKeyboardCyclingFallback:
    """Tests for keyboard-based tab cycling when CDP and UIA are unavailable."""

    def test_keyboard_fallback_when_cdp_and_window_activation_fail(self):
        opener = FakeOpener()
        kb_result = {
            "method": "keyboard_cycling",
            "found": True,
            "chrome_pid": 1234,
            "hwnd": 0x12345,
            "tabs_cycled": 3,
            "final_title": "WhatsApp Web",
            "windows_enumerated": 2,
            "distinct_states": 4,
        }
        omnibox_result = {
            "ok": True,
            "detail": "Navigated successfully",
            "title_before": "WhatsApp",
            "title_after": "WhatsApp - Contact",
            "guards_passed": True,
            "guard_failures": [],
        }
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],  # CDP unavailable
            activate_window_fn=lambda: False,  # window activation fails
            keyboard_find_tab_fn=lambda: kb_result,  # keyboard cycling succeeds
            navigate_omnibox_fn=lambda target_url, target_hwnd, target_pid: omnibox_result,
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert r.launch_method == "keyboard_cycling"
        assert r.window_activated is True
        assert "keyboard cycling" in r.detail
        assert "tabs_cycled=3" in r.detail
        assert "hwnd=0x12345" in r.detail
        assert opener.calls == []  # no new tab opened

    def test_keyboard_fallback_reports_no_navigation(self):
        opener = FakeOpener()
        kb_result = {
            "method": "keyboard_cycling",
            "found": True,
            "chrome_pid": 1234,
            "hwnd": 0x12345,
            "tabs_cycled": 5,
            "windows_enumerated": 1,
        }
        omnibox_fail = {
            "ok": False,
            "detail": "Guard failed: target HWND not found",
            "title_before": "",
            "title_after": "",
            "guards_passed": False,
            "guard_failures": ["HWND not found"],
        }
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_HIT]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],
            activate_window_fn=lambda: False,
            keyboard_find_tab_fn=lambda: kb_result,
            navigate_omnibox_fn=lambda target_url, target_hwnd, target_pid: omnibox_fail,
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        assert r.ok is True
        assert "omnibox navigation failed" in r.detail
        assert "No new tab opened" in r.detail

    def test_keyboard_cycling_not_found_stops_without_new_tab(self):
        """When keyboard cycling completes without finding WhatsApp, STOP and report.
        
        Do NOT open a new tab automatically. User must choose next action deliberately.
        """
        opener = FakeOpener()
        kb_result = {
            "method": "keyboard_cycling",
            "found": False,
            "windows_enumerated": 2,
            "windows_probed": 2,
            "windows_report": [
                {
                    "hwnd": 0x100,
                    "pid": 1000,
                    "class_name": "Chrome_WidgetWin_1",
                    "initial_title": "YouTube",
                    "title_changed": True,
                    "distinct_states": 5,
                    "tabs_cycled": 10,
                    "whatsapp_found": False,
                    "restored_original": True,
                },
                {
                    "hwnd": 0x200,
                    "pid": 1000,
                    "class_name": "Chrome_WidgetWin_1",
                    "initial_title": "Gmail",
                    "title_changed": False,
                    "distinct_states": 1,
                    "tabs_cycled": 1,
                    "whatsapp_found": False,
                    "restored_original": False,
                },
            ],
        }
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_MISS]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],  # CDP unavailable
            activate_window_fn=lambda: False,  # window activation fails
            keyboard_find_tab_fn=lambda: kb_result,  # keyboard cycling completes, not found
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        # Should STOP and report, NOT open new tab
        assert r.ok is False
        assert r.launch_method == "none"
        assert "NOT FOUND" in r.detail
        assert "No new tab opened" in r.detail
        assert "User must choose next action deliberately" in r.detail
        assert "Window diagnostic" in r.detail
        assert opener.calls == []  # NO new tab opened

    def test_all_methods_fail_stops_without_new_tab(self):
        """When all methods fail, STOP and report. Do NOT open new tab automatically."""
        opener = FakeOpener()
        adapter = WhatsAppUIAdapter(
            opener=opener, window_probe=ProbeScript([WA_PROBE_MISS]),
            desktop_probe=lambda: False, allow_web_fallback=True,
            cdp_list_tabs_fn=lambda port: [],  # CDP unavailable
            activate_window_fn=lambda: False,  # window activation fails
            keyboard_find_tab_fn=lambda: None,  # keyboard cycling fails/returns None
        )
        r = adapter.open_chat(WhatsAppUITarget.from_chat_id(EXPECTED_CHAT))
        # Should STOP and report, NOT open new tab
        assert r.ok is False
        assert r.launch_method == "none"
        assert "NOT FOUND" in r.detail
        assert "No new tab opened" in r.detail
        assert opener.calls == []  # NO new tab opened


# ---------------------------------------------------------------------------
# WSL -> Windows temp-file path regression (real keyboard-cycling primitives)
# ---------------------------------------------------------------------------
# _enumerate_chrome_windows and _run_ps_fresh both write a generated .ps1
# script to a temp file and invoke native powershell.exe -File <path>. That
# path MUST be Windows-visible (e.g. C:\Windows\Temp\...): a bare WSL path
# like /tmp/tmpXXXX.ps1 is unresolvable by powershell.exe, which then prints
# an error to stderr but still exits 0 — every caller silently saw an empty
# window list / failed keyboard op regardless of what Chrome actually had
# open. These tests assert the actual subprocess.run() invocation always
# uses a C:\ path for -File, never a raw /tmp or /mnt/... path.

class TestPowerShellTempFileUsesWindowsPath:
    def test_enumerate_chrome_windows_file_arg_is_windows_path(self, monkeypatch):
        import api.integrations.whatsapp.wa_ui_adapter as mod

        captured = {}

        class FakeRun:
            returncode = 0
            stdout = "[]"

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeRun()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        mod._enumerate_chrome_windows()

        assert "cmd" in captured, "subprocess.run was never called"
        file_idx = captured["cmd"].index("-File")
        file_arg = captured["cmd"][file_idx + 1]
        assert file_arg.startswith("C:\\"), (
            f"-File argument must be a Windows path, got {file_arg!r} — "
            "native powershell.exe cannot resolve a bare WSL path"
        )
        assert not file_arg.startswith("/"), "must not pass a raw WSL/Linux path to -File"

    def test_run_ps_fresh_file_arg_is_windows_path(self, monkeypatch):
        import api.integrations.whatsapp.wa_ui_adapter as mod

        captured = {}

        class FakeRun:
            returncode = 0
            stdout = "ok"

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeRun()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        ok, out = mod._run_ps_fresh("Write-Output 'hi'", timeout=5)

        assert ok is True
        file_idx = captured["cmd"].index("-File")
        file_arg = captured["cmd"][file_idx + 1]
        assert file_arg.startswith("C:\\")
        assert not file_arg.startswith("/")

    def test_enumerate_chrome_windows_writes_to_windows_visible_dir(self, tmp_path, monkeypatch):
        # The WSL-side write path (what Python's own filesystem calls use)
        # must be the /mnt/c/Windows/Temp mount, not /tmp — otherwise
        # powershell.exe on the Windows side has nothing to read even if a
        # Windows-looking path string were constructed.
        import api.integrations.whatsapp.wa_ui_adapter as mod
        import inspect

        src = inspect.getsource(mod._enumerate_chrome_windows)
        assert "/mnt/c/Windows/Temp" in src

    def test_run_ps_fresh_writes_to_windows_visible_dir(self):
        import api.integrations.whatsapp.wa_ui_adapter as mod
        import inspect

        src = inspect.getsource(mod._run_ps_fresh)
        assert "/mnt/c/Windows/Temp" in src
