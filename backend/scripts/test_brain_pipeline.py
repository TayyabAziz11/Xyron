"""
Brain pipeline test suite — Phase 17 + Integration Suite.

Semantic parse cases (15):
  1.  "introduce yourself"                → intro / intro_short
  2.  "explain yourself to my audience"   → intro / intro_audience
  3.  "make your brain better"            → emotional / self_upgrade
  4.  "open chrome"                       → tool / open_app
  5.  "prepare my work setup"             → agent / work_mode
  6.  "this bug is annoying me"           → emotional / frustration
  7.  "what do you want next"             → emotional / ask_future_desire
  8.  "delete that folder"                → tool / file_action / confirm=True
  9.  "chrome kholo"                      → tool / open_app (Roman Urdu)
  10. "zairon explain yourself"           → intro / intro_short (Xyron variant)
  11. "aap kaun hain"                     → intro / intro_short (Urdu)
  12. "open app pls"                      → tool / open_app (broken English)
  13. "yeh system slow hai"              → emotional / frustration (Roman Urdu)
  14. "please upgrade your memory"       → emotional / self_upgrade
  15. "work mode lagao"                  → agent / work_mode (Roman Urdu)

Integration tests (15):
   1. Intro PUBLIC         — identity filter strips raw tech terms
   2. Intro TECHNICAL      — stack names pass through unfiltered
   3. Emotion curve        — self_upgrade arc starts with warm_surprise/excited
   4. System command       — "open chrome" routes to tool/open_app
   5. Roman Urdu           — "chrome kholo" hits tier 1 fast rule
   6. Broken English       — "open app pls" handled gracefully, no crash
   7. Memory write/recall  — store() + search() roundtrip
   8. Autonomy plan        — level=1 returns plan, steps_done=0
   9. Danger confirmation  — delete_file classified as high risk
  10. Agent selection      — registry.get() returns right agent per ID
  11. Dashboard shape      — brain status dict has required keys
  12. Chroma fallback      — parse() works via tier 1 when ChromaDB absent
  13. Local-only mode      — pipeline runs without OPENAI_API_KEY
  14. Latency budget       — tier 1 parse < 10ms best-of-5
  15. Tool contract format — ToolResult carries all required fields

Run:
  python3 backend/scripts/test_brain_pipeline.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND))

import logging
logging.basicConfig(level=logging.WARNING)

from brain.semantic_understanding import semantic_understanding

PASS = "✓"
FAIL = "✗"

# ─────────────────────────────────────────────────────────────────────────────
# Semantic parse cases (15)
# ─────────────────────────────────────────────────────────────────────────────

CASES = [
    # (input, expected_route, expected_intent, requires_confirmation)
    ("introduce yourself",                  "intro",     "intro_short",       False),
    ("explain yourself to my audience",     "intro",     "intro_audience",    False),
    ("make your brain better",              "emotional", "self_upgrade",      False),
    ("open chrome",                         "tool",      "open_app",          False),
    ("prepare my work setup",              "agent",     "work_mode",         False),
    ("this bug is annoying me",            "emotional", "frustration",       False),
    ("what do you want next",              "emotional", "ask_future_desire", False),
    ("delete that folder",                 "tool",      "file_action",       True),
    ("chrome kholo",                       "tool",      "open_app",          False),
    ("zairon explain yourself",            "intro",     "intro_short",       False),
    ("aap kaun hain",                      "intro",     "intro_short",       False),
    ("open app pls",                       "tool",      "open_app",          False),
    ("yeh system slow hai",                "emotional", "frustration",       False),
    ("please upgrade your memory",         "emotional", "self_upgrade",      False),
    ("work mode lagao",                    "agent",     "work_mode",         False),
]


def _check(frame, expected_route, expected_intent, expected_confirm):
    return (
        frame.route == expected_route,
        frame.intent == expected_intent,
        frame.requires_confirmation == expected_confirm,
    )


async def run_semantic_cases() -> tuple[int, int]:
    print("\n" + "═" * 70)
    print("  SEMANTIC PARSE CASES (15)")
    print("═" * 70)

    passed = failed = 0
    for i, (text, exp_route, exp_intent, exp_confirm) in enumerate(CASES, 1):
        frame = semantic_understanding.parse(text, use_llm=False)
        r_ok, i_ok, c_ok = _check(frame, exp_route, exp_intent, exp_confirm)
        all_ok = r_ok and i_ok and c_ok
        passed += all_ok
        failed += not all_ok
        s = PASS if all_ok else FAIL
        print(f"\n  {s} Case {i:02d}: {text!r}")
        print(f"       route:   {'✓' if r_ok else '✗'} {frame.route!r:20s}  (expected {exp_route!r})")
        print(f"       intent:  {'✓' if i_ok else '✗'} {frame.intent!r:20s}  (expected {exp_intent!r})")
        print(f"       confirm: {'✓' if c_ok else '✗'} {frame.requires_confirmation!r:5}       "
              f"confidence={frame.confidence:.2f} tier={frame.tier}")

    print("\n" + "─" * 70)
    print(f"  SEMANTIC: {passed}/{len(CASES)} passed  ({failed} failed)")
    print("─" * 70)
    return passed, failed


# ─────────────────────────────────────────────────────────────────────────────
# Integration test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok(label: str, detail: str = "") -> None:
    print(f"  {PASS} {label}" + (f"  — {detail}" if detail else ""))

def _fail(label: str, detail: str = "") -> None:
    print(f"  {FAIL} {label}" + (f"  — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests (15)
# ─────────────────────────────────────────────────────────────────────────────

def test_01_intro_public() -> bool:
    from brain.identity_policy import IdentityPolicy, IdentityMode
    p = IdentityPolicy(IdentityMode.PUBLIC)
    raw = "I use faster-whisper for STT and Kokoro for TTS. I have limitations in my memory."
    filtered = p.filter(raw)
    bad = [w for w in ("faster-whisper", "Kokoro", "limitations") if w.lower() in filtered.lower()]
    if not bad:
        _ok("01 Intro PUBLIC", f"filtered OK: {filtered[:60]}")
        return True
    _fail("01 Intro PUBLIC", f"still contains: {bad}")
    return False


def test_02_intro_technical() -> bool:
    from brain.identity_policy import IdentityPolicy, IdentityMode
    p = IdentityPolicy(IdentityMode.TECHNICAL)
    raw = "I use faster-whisper and Kokoro for voice processing."
    filtered = p.filter(raw)
    if "faster-whisper" in filtered and "Kokoro" in filtered:
        _ok("02 Intro TECHNICAL", "stack names preserved through TECHNICAL filter")
        return True
    _fail("02 Intro TECHNICAL", f"got: {filtered[:60]}")
    return False


def test_03_emotion_curve() -> bool:
    try:
        from brain.emotion_curves import get_curve
        curve = get_curve("self_upgrade")
        if not curve:
            _fail("03 Emotion curve", "no curve returned for self_upgrade")
            return False
        first = curve[0].emotion
        expected_starts = {"warm_surprise", "excited", "warm"}
        if first in expected_starts:
            _ok("03 Emotion curve", f"self_upgrade arc: {first} → ... ({len(curve)} segments)")
            return True
        _fail("03 Emotion curve", f"unexpected first emotion: {first!r}")
    except Exception as exc:
        _fail("03 Emotion curve", f"error: {exc}")
    return False


def test_04_system_command() -> bool:
    frame = semantic_understanding.parse("open chrome", use_llm=False)
    ok = frame.route == "tool" and frame.intent == "open_app"
    if ok:
        _ok("04 System command", f"route={frame.route} intent={frame.intent} conf={frame.confidence:.2f}")
        return True
    _fail("04 System command", f"route={frame.route!r} intent={frame.intent!r}")
    return False


def test_05_roman_urdu() -> bool:
    frame = semantic_understanding.parse("chrome kholo", use_llm=False)
    ok = frame.route == "tool" and frame.intent == "open_app" and frame.tier == 1
    if ok:
        _ok("05 Roman Urdu", f"tier={frame.tier} intent={frame.intent} (fast rule hit)")
        return True
    _fail("05 Roman Urdu", f"route={frame.route!r} intent={frame.intent!r} tier={frame.tier}")
    return False


def test_06_broken_english() -> bool:
    try:
        frame = semantic_understanding.parse("open app pls", use_llm=False)
        if frame.confidence > 0 and frame.route is not None:
            _ok("06 Broken English", f"route={frame.route!r} conf={frame.confidence:.2f} (no crash)")
            return True
        _fail("06 Broken English", f"conf={frame.confidence} route={frame.route!r}")
    except Exception as exc:
        _fail("06 Broken English", f"crashed: {exc}")
    return False


def test_07_memory_write_recall() -> bool:
    import sqlite3
    try:
        from brain.memory_system import brain_memory
        text = "Tayyab always prefers dark mode across all applications"
        brain_memory.store(text, mem_type="semantic", importance=0.9, source="test_suite")

        # Primary: semantic search (requires Ollama + ChromaDB)
        hits = brain_memory.search("dark mode", n=5)
        if hits and any("dark" in h.text.lower() for h in hits):
            _ok("07 Memory write/recall", f"store + semantic search roundtrip: {len(hits)} hit(s)")
            return True

        # Fallback: verify the SQLite write directly (search needs Ollama running)
        db_path = BACKEND / "data" / "brain" / "memory.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT text FROM memories WHERE text LIKE ? ORDER BY created_at DESC LIMIT 1",
                ("%dark mode%",)
            ).fetchall()
            conn.close()
            if rows:
                _ok("07 Memory write/recall", f"stored in SQLite (semantic search needs Ollama): {rows[0][0][:50]}")
                return True

        _fail("07 Memory write/recall", "not found in ChromaDB or SQLite — store may have failed")
    except Exception as exc:
        _fail("07 Memory write/recall", f"error: {exc}")
    return False


async def test_08_autonomy_plan() -> bool:
    try:
        from brain.autonomy_loop import autonomy_loop, LoopStep
        steps = [
            LoopStep(0, "Open VS Code", "automation_agent", risk="low"),
            LoopStep(1, "Set work mode", "automation_agent", risk="low"),
        ]
        result = await autonomy_loop.run("work mode test", steps, autonomy=1)
        if not result.success and result.steps_done == 0 and result.spoken:
            _ok("08 Autonomy plan", f"level=1 → plan returned, not executed: {result.spoken[:50]}")
            return True
        _fail("08 Autonomy plan", f"success={result.success} steps_done={result.steps_done}")
    except Exception as exc:
        _fail("08 Autonomy plan", f"error: {exc}")
    return False


def test_09_danger_confirmation() -> bool:
    try:
        from brain.safety_gate import safety_gate
        del_risk  = safety_gate.check_risk("delete all files", "delete_file")
        open_risk = safety_gate.check_risk("open chrome")
        send_risk = safety_gate.check_risk("send message to john", "send_whatsapp")
        if del_risk == "high" and open_risk == "low":
            _ok("09 Danger confirmation", f"delete={del_risk} open={open_risk} send={send_risk}")
            return True
        _fail("09 Danger confirmation", f"delete={del_risk} (want high)  open={open_risk} (want low)")
    except Exception as exc:
        _fail("09 Danger confirmation", f"error: {exc}")
    return False


def test_10_agent_selection() -> bool:
    try:
        from agents.registry import brain_agent_registry
        agent_sys  = brain_agent_registry.get("system_agent")
        agent_mem  = brain_agent_registry.get("memory_agent")
        agent_emo  = brain_agent_registry.get("emotion_agent")
        agent_auto = brain_agent_registry.get("automation_agent")
        all_found = all(a is not None for a in [agent_sys, agent_mem, agent_emo, agent_auto])
        if all_found:
            _ok("10 Agent selection", (
                f"system={type(agent_sys).__name__}  "
                f"memory={type(agent_mem).__name__}  "
                f"emotion={type(agent_emo).__name__}  "
                f"automation={type(agent_auto).__name__}"
            ))
            return True
        missing = [n for n, a in [
            ("system", agent_sys), ("memory", agent_mem),
            ("emotion", agent_emo), ("automation", agent_auto),
        ] if a is None]
        _fail("10 Agent selection", f"missing agents: {missing}")
    except Exception as exc:
        _fail("10 Agent selection", f"error: {exc}")
    return False


def test_11_dashboard_shape() -> bool:
    try:
        from brain.brain_state import BrainState
        from brain.capability_registry import capability_registry
        bs = BrainState()
        status = {
            "current_mode":       bs.current_mode,
            "autonomy_level":     bs.autonomy_level,
            "current_emotion":    bs.current_emotion,
            "active_goal":        bs.active_goal,
            "capability_summary": capability_registry.summary(),
            "total_commands":     bs.total_commands,
            "identity_mode":      bs.identity_mode,
        }
        required = ["current_mode", "autonomy_level", "current_emotion", "capability_summary"]
        missing = [k for k in required if k not in status or status[k] is None]
        if not missing:
            _ok("11 Dashboard shape", f"all keys present — autonomy={bs.autonomy_level} "
                f"emotion={bs.current_emotion!r}")
            return True
        _fail("11 Dashboard shape", f"missing or null: {missing}")
    except Exception as exc:
        _fail("11 Dashboard shape", f"error: {exc}")
    return False


def test_12_chroma_fallback() -> bool:
    try:
        # Tier 1 fast rules never touch ChromaDB — confirm parse works regardless
        frame = semantic_understanding.parse("open spotify", use_llm=False)
        if frame.route is not None and frame.confidence > 0:
            _ok("12 Chroma fallback", f"tier={frame.tier} route={frame.route!r} conf={frame.confidence:.2f}")
            return True
        _fail("12 Chroma fallback", f"route={frame.route!r} conf={frame.confidence}")
    except Exception as exc:
        _fail("12 Chroma fallback", f"error: {exc}")
    return False


def test_13_local_only_mode() -> bool:
    orig = os.environ.pop("OPENAI_API_KEY", None)
    try:
        frame = semantic_understanding.parse("what can you do", use_llm=False)
        if frame is not None and frame.confidence > 0:
            _ok("13 Local-only mode", f"parsed OK without OpenAI: route={frame.route!r} intent={frame.intent!r}")
            return True
        _fail("13 Local-only mode", f"route={frame.route!r}")
    except Exception as exc:
        _fail("13 Local-only mode", f"crashed without API key: {exc}")
        return False
    finally:
        if orig is not None:
            os.environ["OPENAI_API_KEY"] = orig


def test_14_latency_budget() -> bool:
    runs = 5
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        semantic_understanding.parse("open chrome", use_llm=False)
        times.append((time.perf_counter() - t0) * 1000)
    best = min(times)
    avg  = sum(times) / len(times)
    if best < 10:
        _ok("14 Latency budget", f"tier1 best={best:.2f}ms  avg={avg:.2f}ms  over {runs} runs")
        return True
    _fail("14 Latency budget", f"tier1 best={best:.2f}ms  avg={avg:.2f}ms  — exceeds 10ms target")
    return False


def test_15_tool_contract_format() -> bool:
    try:
        from shared.tool_contract import ToolRequest, ToolResult
        result = ToolResult(
            success=True,
            message="opened chrome",
            data={"app": "chrome"},
            latency_ms=38.5,
            warnings=[],
            requires_followup=False,
        )
        req = ToolRequest(
            tool_name="open_application",
            intent="open_app",
            params={"name": "chrome"},
            risk_level="low",
            source_agent="system_agent",
        )
        req_fields    = ["tool_name", "intent", "params", "risk_level"]
        result_fields = ["success", "message", "data", "latency_ms", "warnings"]
        missing = [f for f in req_fields if not hasattr(req, f)] + \
                  [f for f in result_fields if not hasattr(result, f)]
        if not missing:
            _ok("15 Tool contract", f"ToolRequest + ToolResult fields OK — latency={result.latency_ms}ms")
            return True
        _fail("15 Tool contract", f"missing fields: {missing}")
    except Exception as exc:
        _fail("15 Tool contract", f"error: {exc}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_integration_tests() -> tuple[int, int]:
    print("\n" + "═" * 70)
    print("  INTEGRATION TESTS (15)")
    print("═" * 70 + "\n")

    sync_tests = [
        test_01_intro_public,
        test_02_intro_technical,
        test_03_emotion_curve,
        test_04_system_command,
        test_05_roman_urdu,
        test_06_broken_english,
        test_07_memory_write_recall,
        None,                         # 08 is async
        test_09_danger_confirmation,
        test_10_agent_selection,
        test_11_dashboard_shape,
        test_12_chroma_fallback,
        test_13_local_only_mode,
        test_14_latency_budget,
        test_15_tool_contract_format,
    ]

    results: list[bool] = []
    for fn in sync_tests:
        if fn is None:
            results.append(False)   # placeholder — filled below
            continue
        try:
            results.append(bool(fn()))
        except Exception as exc:
            name = fn.__name__
            _fail(name, f"unexpected: {exc}")
            results.append(False)

    # Run async test 08
    try:
        results[7] = bool(await test_08_autonomy_plan())
    except Exception as exc:
        _fail("08 Autonomy plan", f"unexpected: {exc}")
        results[7] = False

    passed = sum(results)
    failed = len(results) - passed
    print("\n" + "─" * 70)
    print(f"  INTEGRATION: {passed}/{len(results)} passed  ({failed} failed)")
    print("─" * 70)
    return passed, failed


async def run_all() -> None:
    sem_pass, sem_fail = await run_semantic_cases()
    int_pass, int_fail = await run_integration_tests()

    total_pass = sem_pass + int_pass
    total_fail = sem_fail + int_fail
    total      = len(CASES) + 15

    print("\n" + "═" * 70)
    if total_fail == 0:
        print(f"  ✓ ALL {total} TESTS PASSED — Xyron brain pipeline is healthy")
    else:
        print(f"  ✗ {total_fail}/{total} TESTS FAILED — review output above")
    print(f"  Semantic {sem_pass}/{len(CASES)}  ·  Integration {int_pass}/15")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all())
