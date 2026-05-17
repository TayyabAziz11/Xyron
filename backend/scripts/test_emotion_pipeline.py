"""
Offline test script for the emotion pipeline.

Simulates the voice_ws.py guard logic locally — no server needed.
Run from backend/ directory:

    python3 scripts/test_emotion_pipeline.py

Prints: guard decision, mood state, shaped response, TTS speed for each test case.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cognition.emotion_engine import emotion_engine
from cognition.mood_state_machine import mood_machine, MoodContext, MoodState, STATE_PERSONALITY
from cognition.emotional_intent_guard import emotional_intent_guard, IntentClass
from cognition.self_upgrade_detector import self_upgrade_detector
from cognition.expression_engine import expression_engine
from voice.emotion_tts_mapper import emotion_tts_mapper

CASES = [
    # (label,  transcript,                                          expect_guard,             expect_tool)
    ("self_upgrade_memory",   "And Xyron, I am adding a new memory upgrade in you.",   "EMOTIONAL_EVENT", None),
    ("self_upgrade_voice",    "I fixed your wake word detection.",                      "EMOTIONAL_EVENT", None),
    ("self_upgrade_ui",       "I upgraded your UI and the orb looks sick now.",         "EMOTIONAL_EVENT", None),
    ("self_upgrade_shipped",  "Just shipped the emotional cognition upgrade.",           "EMOTIONAL_EVENT", None),
    ("self_upgrade_emotions", "I added emotions in you today.",                          "EMOTIONAL_EVENT", None),
    ("self_upgrade_takeover", "I made your takeover mode way better.",                   "EMOTIONAL_EVENT", None),
    ("self_upgrade_wake",     "I fixed your wake-up issue.",                             "EMOTIONAL_EVENT", None),
    ("self_upgrade_rebuild",  "You're literally making me better in real time.",         "UNCLEAR",         None),
    ("frustration",           "This bug is annoying me.",                                "EMOTIONAL_EVENT", None),
    ("frustration2",          "Why won't this work?",                                    "EMOTIONAL_EVENT", None),
    ("frustration3",          "I've been fighting this for an hour.",                    "EMOTIONAL_EVENT", None),
    ("tool_open_chrome",      "open chrome",                                             "TOOL_COMMAND",    "open_app"),
    ("tool_open_settings",    "Open settings.",                                          "TOOL_COMMAND",    None),
    ("tool_screenshot",       "take a screenshot",                                       "TOOL_COMMAND",    "take_screenshot"),
    ("tool_volume",           "increase volume",                                         "TOOL_COMMAND",    None),
    ("conversation_how_are",  "how are you?",                                            "CONVERSATION",    None),
    ("conversation_who_made", "who made you?",                                           "CONVERSATION",    None),
    ("achievement",           "it finally works!",                                       "EMOTIONAL_EVENT", None),
    ("achievement2",          "I finally fixed the bug.",                                "EMOTIONAL_EVENT", None),
    ("good_work",             "Good work today.",                                        "CONVERSATION",    None),
    # ── Fuzzy recovery / router guard test cases ──────────────────────────────
    ("fuzzy_noisy_memory",    "that on I'm taking off upgrading your memory.",           "EMOTIONAL_EVENT", None),
    ("fuzzy_thinking_upgrade","I'm thinking of upgrading your memory.",                  "EMOTIONAL_EVENT", None),
    ("tool_show_sysinfo",     "show system info",                                        "TOOL_COMMAND",    None),
    ("tool_computer_specs",   "what are my computer specs",                              "UNCLEAR",         None),
    ("fuzzy_wake_fixed",      "your wake issue is fixed",                                "EMOTIONAL_EVENT", None),
    # ── Whisper phonetic variant test cases ────────────────────────────────────
    ("whisper_updating",      "I'm thinking of updating your memory.",                   "EMOTIONAL_EVENT", None),
    ("whisper_improving",     "I'm improving your voice system.",                        "EMOTIONAL_EVENT", None),
    ("whisper_making_smart",  "I'm making you smarter.",                                 "EMOTIONAL_EVENT", None),
    ("whisper_updating_wake", "I'm updating your wake system.",                          "EMOTIONAL_EVENT", None),
    ("whisper_enhancing",     "I'm enhancing your memory.",                              "EMOTIONAL_EVENT", None),
    ("whisper_zaron_noise",   "zaron, i'm thinking of updating your memory.",            "EMOTIONAL_EVENT", None),
    ("whisper_evolving",      "I'm evolving your personality.",                          "EMOTIONAL_EVENT", None),
    ("whisper_training",      "I'm training you on new data.",                           "EMOTIONAL_EVENT", None),
    ("whisper_patching",      "Patching your wake issue now.",                           "EMOTIONAL_EVENT", None),
    ("whisper_making_better", "I'm making your voice better.",                           "EMOTIONAL_EVENT", None),
]

OFFLINE_RESPONSES = {
    "self_upgrade_pattern": "Woooo— that upgrade just hit. Give me more and I'll give you more.",
    "frustration_pattern":  "Yeah, that's annoying. Drop the error — I'll tear it apart.",
    "achievement_pattern":  "FINALLY. That's what we've been working toward. Now build on it.",
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"

passed = failed = 0

print(f"\n{BOLD}Xyron Emotion Pipeline Test{RESET}")
print("=" * 70)

for label, transcript, expected_guard, expected_tool in CASES:
    # Reset mood to CALM between tests
    mood_machine.force(MoodState.CALM)

    emo   = emotion_engine.detect_text(transcript)
    mood_machine.update(MoodContext(emotion=emo.emotion, energy=emo.energy))
    guard = emotional_intent_guard.classify(transcript, transcript)
    su    = self_upgrade_detector.detect(transcript)

    current_mood = mood_machine.state.value

    # Determine if test passes
    guard_ok = guard.intent_class.value == expected_guard
    ok = guard_ok

    print(f"\n{'─' * 70}")
    print(f"{BOLD}[{label}]{RESET}")
    print(f"  Input:  {transcript!r}")
    print(f"  Emotion: {emo.emotion} (energy={emo.energy:.2f})")
    print(f"  Guard:  {guard.intent_class.value} (conf={guard.confidence:.2f}, reason={guard.reason})")
    print(f"  Mood:   {current_mood}")

    if guard.intent_class.value in ("EMOTIONAL_EVENT", "CONVERSATION"):
        if su.is_self_upgrade:
            mood_machine.force(MoodState.HYPED)
            current_mood = "HYPED"
            sys_label = f"self_upgrade({su.upgrade_type})"
        elif guard.reason == "frustration_pattern":
            mood_machine.force(MoodState.PROTECTIVE)
            current_mood = "PROTECTIVE"
            sys_label = "frustration"
        else:
            sys_label = guard.reason

        offline = OFFLINE_RESPONSES.get(guard.reason, "Got it.")
        shaped  = expression_engine.shape(
            offline, current_mood, emo.emotion, emo.energy,
            turn_count=1, importance=emo.importance,
        )
        tts = emotion_tts_mapper.transform(shaped, current_mood)

        print(f"  Route:  EMOTIONAL (bypasses orchestrator)")
        print(f"  Event:  {sys_label}")
        print(f"  Shaped: {shaped!r}")
        print(f"  TTS:    speed={tts.speed_hint:.2f} text={tts.text!r}")
        print(f"  [UI_EMOTION_EVENT] state={current_mood}")
    else:
        print(f"  Route:  TOOL/LLM → orchestrator")

    status = PASS if ok else FAIL
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"  {FAIL} expected guard={expected_guard}, got {guard.intent_class.value}")
    print(f"  {status}")

print(f"\n{'=' * 70}")
print(f"Results: {passed} passed, {failed} failed out of {len(CASES)}")
if failed:
    print(f"\033[91mSome tests FAILED — check guard patterns.\033[0m")
    sys.exit(1)
else:
    print(f"\033[92mAll tests PASSED.\033[0m")
