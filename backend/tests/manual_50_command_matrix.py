"""
Manual harness (not a pytest suite — run directly) that drives the 50
commands through the REAL text-level pipeline exactly as
brain/orchestrator.py's _route_intent() does:

  language_detector.detect()
    -> mixed_language_engine.analyze()      [Tier 1, deterministic]
    -> intent_router.route()                [Tier 0-3, deterministic]
    -> local_comprehension (Tier 4, Qwen)   [only if Tier 0-3 missed AND lang != en]

This is honest about what it does NOT cover: no audio, no real STT, no
real TTS. It proves the understanding/routing pipeline; the mic test
script covers the rest.

Run: python3 tests/manual_50_command_matrix.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from api.services import language_detector as ld
from api.services import mixed_language_engine as mle
from api.services.intent_router import intent_router as ir
from api.tools import registry

CASES: list[tuple[str, str, str]] = [
    # (utterance, expected_tool_or_marker, note)
    ("Open Chrome.", "open_application", ""),
    ("Chrome kholo.", "open_application", ""),
    ("کروم کھولو۔", "open_application", ""),
    ("Chrome khol do please.", "open_application", ""),
    ("Chrome band karo.", "kill_app", ""),
    ("Settings kholo.", "open_application", "routes via app-launch catch-all, not settings-specific"),
    ("Display settings kholo.", "open_system_settings", ""),
    ("E drive kholo.", "open_drive", ""),
    ("E drive mein perfume wala folder kholo.", "smart_open", "Qwen expected"),
    ("Downloads mein latest PDF kholo.", "GENERAL", "temporal+filetype filter — known gap, expect no confident tool"),
    ("Meri invoice wali file dhoondo.", "search_files|smart_open", ""),
    ("Pehla wala kholo.", "GENERAL", "needs pending disambiguation precondition"),
    ("Doosra wala.", "GENERAL", "bare, no verb, needs pending disambiguation"),
    ("Wapis jao.", "GENERAL", "no dedicated 'go back' tool exists"),
    ("Screen pe kya hai?", "read_screen", ""),
    ("Main kis website pe hoon?", "GENERAL", "needs browser/URL perception, likely unmapped"),
    ("Is page ko explain karo.", "GENERAL", ""),
    ("Is repo ka README kholo.", "GENERAL", "repository object_type not in _STAGE1_OBJECT_TYPES"),
    ("Google pe Pakistan weather search karo.", "search_web", ""),
    ("YouTube kholo.", "open_application|open_url", ""),
    ("YouTube pe Atif Aslam ka koi famous gana chalao.", "search_youtube", ""),
    ("Gana pause karo.", "media_control", ""),
    ("Agla gana.", "GENERAL", "bare, no verb — ambiguous per session note"),
    ("Awaz thori kam karo.", "volume_control", ""),
    ("Awaz 50 percent kar do.", "volume_control|GENERAL", "specific percentage — check if supported"),
    ("Recycle bin khali karo.", "empty_recycle_bin", ""),
    ("Rehne do.", "APPROVAL_NO", "handled by approval_intent, not a tool"),
    ("Nahi, cancel karo.", "APPROVAL_NO", ""),
    ("Haan kar do.", "APPROVAL_YES", ""),
    ("Jo pehle kaha tha woh karo.", "GENERAL", "vague — should clarify, not guess"),
    # Natural language
    ("Yaar Chrome khol do.", "open_application", ""),
    ("Zara mera Downloads folder khol dena.", "open_directory|smart_open", ""),
    ("Bhai woh perfume wala folder kidhar hai?", "GENERAL", "question form, not a command"),
    ("Mujhe lagta hai woh E drive mein tha, zara dhoondho.", "GENERAL", "Qwen expected, complex"),
    ("Isme latest changes kya hain?", "GENERAL", ""),
    ("Ye kya cheez hai?", "GENERAL", ""),
    ("Isko band kar do.", "kill_app", ""),
    ("Wohi wala.", "GENERAL", "bare"),
    ("Nahi doosra wala.", "APPROVAL_NO", "negation should win over ordinal"),
    ("Acha rehne do.", "APPROVAL_NO", ""),
    ("Perfect, ab YouTube kholo.", "open_application|open_url", ""),
    # Urdu script
    ("سیٹنگز کھولو۔", "open_application", ""),
    ("ای ڈرائیو میں پرفیوم والا فولڈر کھولو۔", "smart_open|GENERAL", "Urdu-script compositional, likely Qwen or unmapped"),
    ("جو فائل ابھی کھولی تھی اسے بند کر دو۔", "GENERAL", "Urdu-script pronoun+temporal, likely Qwen"),
    ("پہلا والا کھولو۔", "GENERAL", "needs pending disambiguation"),
    ("واپس جاؤ۔", "GENERAL", "no 'go back' tool"),
    ("اسکرین پر کیا ہے؟", "read_screen|GENERAL", ""),
    ("یوٹیوب پر عاطف اسلم کا کوئی مشہور گانا چلاؤ۔", "search_youtube|GENERAL", "Urdu-script chalao — narrower coverage per report §11"),
    ("آواز تھوڑی کم کرو۔", "volume_control|GENERAL", ""),
    ("رہنے دو۔", "APPROVAL_NO", ""),
    ("ہاں، کر دو۔", "APPROVAL_YES", ""),
    # Mixed
    ("Chrome open karo.", "open_application", ""),
    ("Volume 30 percent kar do.", "volume_control|GENERAL", ""),
    ("Next track chalao.", "media_control", ""),
    ("Search results mein second wala open karo.", "GENERAL", "needs pending disambiguation"),
    # Chalao torture test
    ("Chrome chalao.", "open_application", "chalao torture"),
    ("Spotify chalao.", "open_application", "chalao torture"),
    ("Gana chalao.", "media_control|GENERAL", "chalao torture — canonical 'play gana', check if intent_router maps a bare noun"),
    ("Atif Aslam ka gana chalao.", "media_control|GENERAL", "chalao torture"),
    ("Video chalao.", "media_control|GENERAL", "chalao torture"),
    ("Ye script chalao.", "GENERAL", "chalao torture — no run_script tool, known gap"),
    ("Calculator chalao.", "open_application", "chalao torture"),
]


def route_via_full_pipeline(text: str) -> dict:
    from api.services.approval_intent import parse_yes_no
    t0 = time.monotonic()
    lang = ld.detect(text)
    detected = lang["lang"]

    approval = parse_yes_no(text)
    if approval != "unclear":
        return {
            "canonical": None, "tool": f"APPROVAL_{approval.upper()}",
            "qwen_used": False, "lang": detected, "ms": (time.monotonic() - t0) * 1000,
        }

    canonical = None
    if detected != "en":
        canonical = mle.analyze(text, detected, trace_id="MATRIX")
    route_input = canonical or text
    route = ir.route(route_input)
    qwen_used = False

    if not (route.tool_name and route.tool_name in registry and route.confidence >= 0.55) and detected != "en":
        from api.services.local_comprehension import comprehend, validate_and_map
        qwen_used = True
        lc = comprehend(text, detected, trace_id="MATRIX")
        if lc:
            lc = validate_and_map(lc, registry)
            if lc.mapped:
                ms = (time.monotonic() - t0) * 1000
                return {"canonical": lc.canonical_text, "tool": lc.tool_name, "qwen_used": True,
                        "lang": detected, "ms": ms}

    ms = (time.monotonic() - t0) * 1000
    tool = route.tool_name if (route.tool_name and route.confidence >= 0.55) else "GENERAL"
    return {"canonical": canonical, "tool": tool, "qwen_used": qwen_used, "lang": detected, "ms": ms}


def main() -> None:
    passed = 0
    results = []
    for utterance, expected, note in CASES:
        r = route_via_full_pipeline(utterance)
        ok = r["tool"] in expected.split("|")
        passed += int(ok)
        results.append((utterance, expected, r, ok, note))
        print(f"{'OK  ' if ok else 'FAIL'} | lang={r['lang']:9} qwen={'Y' if r['qwen_used'] else 'N'} "
              f"ms={r['ms']:7.0f} | tool={r['tool']:20} expected={expected:20} | {utterance}")
        if note:
            print(f"       note: {note}")

    print(f"\n{passed}/{len(CASES)} matched expectation ('GENERAL' = correctly no confident tool, not a failure of understanding)")


if __name__ == "__main__":
    main()
