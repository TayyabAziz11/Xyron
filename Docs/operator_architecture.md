# Xyron Operator Architecture

## 1. Current Voice Flow

```
[Desktop App]
    │
    ├── Wake WS  ws://localhost:8000/api/v1/voice/ws/wake
    │       OWW + Whisper 2-stage detection
    │       → fires startSession() in voiceRuntime.ts
    │
    └── Session WS  ws://localhost:8000/api/v1/voice/ws/session
            binary float32 PCM frames (1280 samples / 80ms)
            ↓
    [ws_session() — voice_ws.py]
            ↓
    Config frame (voice, speed, preferred_name)
            ↓
    Greeting TTS → stream audio → wait tts_done
            ↓
    Main receive loop (VAD)
            RMS > threshold → accumulate PCM
            RMS < threshold for N frames → fire process_utterance()
```

### process_utterance() pipeline

```
1.  Silence gate         RMS < 0.010 → drop, send listening
2.  Whisper STT          fast mode, beam_size=1, auto-lang
3.  Language retry       if non-English + EN command words → retry with lang="en"
4.  Hallucination filter exact known phrases (known artifacts)
5.  Hallucination filter command-list pattern (sorted word lists)
6.  Staleness check      turn_id mismatch → drop
7.  Context resolution   pronouns → entities (context_resolver.py)
8.  Normalization        STT cleanup (normalizer.py)
9.  Title correction     Whisper phonetic mishearings (voice_title_corrector.py)
10. Tier 0 — Local clock instant, no LLM (_local_clock_route)
11. Tier 0b — System metrics instant (system_monitor_service)
12. Emotion detection    EmotionEngine + MoodStateMachine
13. Emotional guard      EmotionalIntentGuard classifies intent
14. Emotional branch     if EMOTIONAL_EVENT/CONVERSATION → bypass orchestrator
15. Orchestrator         decide() → OrchestratorDecision
16. Dispatch             STOP / INTERRUPT / CLARIFY / MEMORY_REF /
                         TOOL / MULTI_STEP / LLM
17. TTS                  _tts_sequential() or _run_llm_stream()
18. Bookkeeping          memory, trace, emotion state push
```

---

## 2. Current Routing Flow

```
IntentRouter.route(transcript)
    │
    ├── Tier 1  Cache (LRU)
    ├── Tier 2  Regex rules (_build_rules)
    │           volume, media, brightness, battery, power,
    │           screenshots, filesystem, window, process,
    │           search_youtube, play_media_file, smart_open, open_drive
    ├── Tier 3  SentenceTransformer semantic (lazy-loaded in background)
    └── Tier 4  LLM fallback (low confidence) → action=LLM

Orchestrator.decide() priority:
    1. STOP         — "stop xyron" / "shut down"
    2. INTERRUPT    — standalone "stop", "wait", "no"
    3. CLARIFY      — "what?" / "repeat"
    4. MULTI_STEP   — compound + connectors ("and then", "after that")
    5. MEMORY_REF   — pronoun context ("delete it", "open that")
    6. TOOL         — IntentRouter confidence ≥ 0.55
    7. FS/MEDIA     — _fallback_fs_media regex
    8. LLM          — everything else
```

---

## 3. Current Tool Flow

```
_run_tool(tool_name, tool_params)
    │
    ├── registry.execute(tool_name, params, ctx)
    │       ctx = {openai_key, active_window}
    │
    ├── Tool modules (auto-registered at import):
    │   system_tools.py      — OS automation via PowerShell WSL2 bridge
    │   automation_tools.py  — desktop_click, desktop_type, desktop_hotkey,
    │                           desktop_scroll, desktop_focus_app,
    │                           desktop_screenshot, run_workflow
    │   screen_tools.py      — read_clipboard, write_clipboard, read_screen,
    │                           type_text, switch_window, minimize_window,
    │                           close_window, maximize_window
    │   browser_tools.py     — Playwright: navigate, click, fill, read,
    │                           screenshot, close
    │   core_tools.py        — drives, file_search, media_control, app_finder,
    │                           play_media_file
    │   web_tools.py         — search_youtube, web_search
    │   gmail_tools.py
    │   calendar_tools.py
    │   content_tools.py
    │
    └── result.spoken or result.text → TTS
```

---

## 4. Current Planner Flow

```
MULTI_STEP decision
    │
    Planner.build(transcript)
        _smart_split() → ["open E drive", "open python folder"]
        → Plan([PlanStep(1, ...), PlanStep(2, ...)])
    │
    Planner.execute(plan, _step_fn, history)
        for each step:
            drive-context injection (inherit drive letter from step 1)
            _step_fn(step_text, history)
                → Orchestrator.decide(step_text)
                → _run_tool() or quick_response()
            drive-context extraction (store drive for next step)
            plan.advance(result)
        → combined response
```

---

## 5. Current Session Flow

```
voiceRuntime.ts (module singleton)
    _wakeWS       — persistent, survives React re-renders
    _sessionWS    — opened by startSession(), closed by stopSession()
    _audio        — ONE HTMLAudioElement, URL queue
    _mic          — PCM via ScriptProcessorNode, armed after greeting

VoiceSnapshot state:
    sessionState:  idle | greeting | listening | processing | speaking
    sessionActive: bool
    wakeConnected: bool
    messages:      ConvMessage[]
    offlineMode:   bool
```

---

## 6. Safe Extension Points for Operator Layer

### Extension Point A — TOOL branch intercept (voice_ws.py:~1100)
```python
elif decision.action == ActionType.TOOL:
    # INSERT: OPERATOR_MODE check here
    if OPERATOR_MODE and operator_engine.can_handle(decision.tool_name):
        response_text = await operator_engine.execute(decision.tool_name, decision.tool_params)
    else:
        response_text = await _run_tool(decision.tool_name, decision.tool_params)
```

### Extension Point B — Intent router Tier 2 additions (intent_router.py)
New regex patterns → operator-specific tool names:
- `"play .+ (on youtube|youtube)"` → `operator_youtube`
- `"open .+ in (explorer|file explorer)"` → `operator_explorer`

These patterns only fire when OPERATOR_MODE=true (operator_engine.can_handle filters them).

### Extension Point C — New ActionType.OPERATOR (optional, future)
Add `OPERATOR = auto()` to orchestrator's ActionType enum, handled in voice_ws dispatch.
Not needed for V1 — Extension Point A is sufficient.

### Extension Point D — config.py
Add `operator_mode: bool = False` to Settings.

---

## 7. Operator Layer Architecture (V1)

```
[Voice Pipeline]
    ↓ process_utterance()
    ↓ Orchestrator → ActionType.TOOL
    ↓
[Extension Point A — OPERATOR_MODE check]
    │
    ├── OPERATOR_MODE=false → existing _run_tool() (no change)
    │
    └── OPERATOR_MODE=true + can_handle()
            ↓
        [OperatorEngine]
            ↓
        [AgentLoop]
            OBSERVE → ScreenObserver.capture()
            THINK   → SkillRouter.select_skill(tool_name)
            ACT     → Skill.execute_step()
                        → MouseController / KeyboardController / WindowController
                        → existing registry.execute() for primitive actions
            VERIFY  → OperatorVerifier.verify(expected_state)
            RETRY if not verified (max 3)
            ↓
        response_text → voice_ws.py → TTS

backend/operator_mode/
├── __init__.py
├── operator_engine.py      main entry, OPERATOR_MODE check, can_handle()
├── operator_state.py       GoalState, GoalStatus enum, trace_id
├── operator_types.py       OperatorAction, OperatorResult, VerifySpec
├── operator_actions.py     high-level primitives (wrapping existing tools)
├── operator_executor.py    executes OperatorAction sequences
├── operator_verifier.py    post-action verification
├── screen_observer.py      screen state capture
├── mouse_controller.py     click/double_click/right_click/move/drag
├── keyboard_controller.py  type_text/hotkey/press_key
├── window_controller.py    focus/minimize/maximize/restore/close
├── agent_loop.py           observe-think-act-verify loop
└── skills/
    ├── __init__.py
    ├── base_skill.py       abstract BaseSkill
    ├── youtube_skill.py    play music/video on YouTube
    ├── explorer_skill.py   open folder via File Explorer
    ├── chrome_skill.py     generic Chrome automation
    └── vscode_skill.py     VS Code operations

Safety:
    OPERATOR_BLOCKLIST = {delete, format, shutdown, admin} — hard blocked
    Risky actions → existing approval system (Pending_Approval/)
    All work async — never blocks wake/whisper/Kokoro/VoiceRuntime

Log prefixes:
    [OPERATOR_START]  [OPERATOR_END]  [OPERATOR_ERROR]  [OPERATOR_RETRY]
    [SCREEN_CAPTURE]  [SCREEN_ACTIVE_WINDOW]
    [MOUSE_CLICK]     [MOUSE_DOUBLE_CLICK]  [MOUSE_MOVE]  [MOUSE_VERIFY]
    [KEYBOARD_TYPE]   [KEYBOARD_PRESS]      [KEYBOARD_HOTKEY]
    [WINDOW_FOCUS]    [WINDOW_MAXIMIZE]     [WINDOW_RESTORE]
    [VERIFY_START]    [VERIFY_SUCCESS]      [VERIFY_FAIL]  [VERIFY_RETRY]
    [SKILL_START]     [SKILL_STEP]          [SKILL_SUCCESS] [SKILL_FAIL]
    [AGENT_OBSERVE]   [AGENT_THINK]         [AGENT_ACT]    [AGENT_VERIFY]
    [EXPLORER_NAVIGATE] [EXPLORER_OPEN_FOLDER] [EXPLORER_VERIFY]
    [YOUTUBE_SEARCH]    [YOUTUBE_PLAY]         [YOUTUBE_VERIFY]
    [TRACE VX-xxx] prefix on every operator log
```
