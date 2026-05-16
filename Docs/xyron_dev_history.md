# Xyron Development History

Chronological log of major architectural milestones. Each entry records what was built, why it was needed, and any notable decisions or tradeoffs.

---

## 2026-05-15 — Emotion UI + Voice Pipeline Full-Stack Fix (Phase 1–7)

**Branch:** `feat/multilingual-cognition`

### What was fixed

End-to-end emotion propagation: backend mood → frontend orb color/pulse → audible voice excitement. All fixes are patches to existing paths, no new architecture.

#### Phase 1 — `synthesize-stream` emotion logs
The live TTS endpoint (`/voice/synthesize-stream`) had no emotion logs. Added mood detection + `[TTS_EMOTION]` and `[AUDIO_FX]` logs at endpoint entry. Speed is now computed at the endpoint level (not just inside `_kokoro_to_wav`), so the log reflects the actual synthesis speed.

#### Phase 2 — HYPED speed 1.22
`emotion_tts_mapper._SPEED_MAP["HYPED"]` was 1.18. Updated to 1.22. `_kokoro_to_wav` already boosted to `max(speed, 1.22)` for HYPED; now the TTS mapper and the actual synthesis speed are in sync. `synthesize-stream` also applies `speed = max(speed, 1.22)` before calling `_kokoro_to_wav`, so the log and audio are always aligned.

#### Phase 3 — Command center VoiceOrb wired to emotion
The visible `VoiceOrb` in `command-center/page.tsx` was a hardcoded component (color always `#ff2020`, animation always 3s). Added `useEmotionState()` + `ORB_VARIANTS` import. HYPED now drives: primary color `#ff1744`, glow `glowIntensity=1.0` (2.5× wider), pulse `duration=0.9s` (vs 4.5s CALM). Console logs `[EMOTION_UI]` and `[ORB_STATE]` fire on state change.

#### Phase 4 — Mood hold log
`MoodStateMachine.force()` now logs `[MOOD_HOLD] state=HYPED hold_ms=60000` on every forced state so the hold duration is visible in backend logs.

#### Phase 5 — Remove OpenAI from emotional path
The emotional branch was calling `gpt-4o-mini` for every upgrade/frustration/achievement event. Replaced with: cinematic reaction library (already built) + `expression_engine.shape()`. Zero latency, zero API cost, offline. The reaction text is identical quality because the upgrade_reactions dict already contains hand-crafted cinematic lines.

#### Phase 6 — Micro-reaction log renamed
`[EMOTION] micro_reaction=...` → `[MICRO_REACTION_AUDIO] text=... state=HYPED emitted=true` for easier log correlation.

### Expected log sequence for "I'm thinking of upgrading your memory"

```
[VOICE_TRACE] stage=emotional_guard intent=EMOTIONAL_EVENT conf=0.92
[EMOTION_FORCE] event=self_upgrade mood=HYPED energy=0.90
[MOOD_HOLD] state=HYPED hold_ms=60000
[MICRO_REACTION_AUDIO] text='hold on—' state=HYPED emitted=true
[VOICE_TRACE] emotional_response=offline mood=HYPED
[TTS_EMOTION] state=HYPED speed=1.22 transform_applied=true       ← from [UI_EMOTION_EVENT]
[UI_EMOTION_EVENT] state=HYPED emitted=true
[TTS_EMOTION] endpoint=synthesize-stream state=HYPED speed=1.22   ← from synthesize-stream
[AUDIO_FX] endpoint=synthesize-stream preset=cinematic applied=true
```

### Restart required
After these changes: `kill $(lsof -ti:8000)` + restart backend, restart `npm run dev` for VoiceOrb changes to take effect.

---

## 2026-05-15 — Emotional Cognition Architecture (Phase 1–10) + Runtime Fix

**Branch:** `feat/multilingual-cognition`

### What was built

Full emotional cognition system across 10 phases, then patched to actually intercept the live voice pipeline (phases were wired but the guard was missing, causing emotional statements to fall through to tool routing).

#### Phase 1 — Emotion Engine (`backend/cognition/emotion_engine.py`)
- 10-emotion heuristic detector: hype, excitement, frustration, stress, pride, humor, curiosity, calmness, seriousness, sarcasm
- Heuristic path < 15ms (pure Python regex + counters, zero imports)
- LLM fallback via `llama3.2:3b` when confidence < 0.4
- Sarcasm/excitement conflict resolved: sarcasm fires only when there is no punctuation energy accompanying excitement vocab

#### Phase 2 — Voice Energy (`backend/voice/voice_energy.py`)
- Thin wrapper over existing `AudioEmotionDetector`
- Returns `VoiceEnergyResult(voice_state, energy, stress)` for optional audio-path enrichment
- Not on the hot path — activated only when raw audio bytes are available

#### Phase 3 — Mood State Machine (`backend/cognition/mood_state_machine.py`)
- 11 states: CALM, FOCUSED, EXCITED, HYPED, PLAYFUL, DOMINANT, ANALYTICAL, LOCKED_IN, INTENSE, LATE_NIGHT, PROTECTIVE
- Time-based decay: HYPED → EXCITED (60s) → FOCUSED (120s) → CALM (600s)
- Context triggers: `failure_streak >= 3` → PROTECTIVE; `focus_turn_count >= 20` → LOCKED_IN
- Each state carries: personality addendum (system prompt injection), UI theme colors, TTS speed hint, pause factor

#### Phase 4 — Relationship Memory (`backend/memory/relationship_memory.py`)
- SQLite at `~/.ai-operator/relationship_memory.db`
- Event types: `achievement`, `frustration`, `feature_upgrade`, `focus_streak`, `late_night`, `task_success`, `task_failure`
- `get_context_string()` generates 1–2 sentence recall lines injected into every AI system prompt
- Pattern-based: 3+ late_night events → "You always push past midnight." injected into context

#### Phase 5 — Expression Engine (`backend/cognition/expression_engine.py`)
- Post-generation response shaper — fires AFTER AI text is generated, BEFORE TTS
- Adds contextual openers with cooldown: only when `energy >= 0.55`, `importance >= 0.60`, and `turns_since_last >= 4`
- Opener examples: HYPED + pride → "Finally. I've been waiting for this." | DOMINANT → appends "ACKNOWLEDGED..."
- Cooldown prevents opener spam while preserving earned cinematic moments

#### Phase 6 — Emotion TTS Mapper (`backend/voice/emotion_tts_mapper.py`)
- Pre-Kokoro text transformation: speed hints, emphasis capitalization, filler stripping, pause markers
- DOMINANT: capitalizes status words + `...` pauses; HYPED: capitalizes FINALLY/DONE; FOCUSED: strips filler
- "Sure thing" placed before "Sure" in filler regex to prevent partial match truncation

#### Phase 7 — Reactive UI (`web/src/state/emotionState.ts`, `web/src/hooks/useEmotionState.ts`)
- `ORB_VARIANTS` dict maps all 11 mood states to `{scale, duration, primaryColor, glowIntensity}`
- `useEmotionState` hook polls `/api/v1/cognition/mood` + `/api/v1/cognition/state` at 500ms
- `CinematicOrb.tsx` fully driven by mood: orb color, glow color, pulse speed, scale, spark colors, segment ring colors
- `hexToRgb()` utility for canvas spark rgba conversion

#### Phase 8/9 — Session Awareness (in `mood_state_machine.py` + `pipeline.py`)
- Late night detection: `hour >= 23 or hour < 5` → LATE_NIGHT state + `late_night` event recorded
- Focus streaks: consecutive focus-mode turns increment `_focus_turn_count` → ANALYTICAL → LOCKED_IN
- Failure streaks: `_failure_streak` increments on tool failure; resets on success

#### Phase 10 — Dynamic Personality Evolution (`backend/api/services/pipeline.py`)
- Relationship memory context injected into `_build_messages()` on every AI call
- State personality addendum injected into system prompt per turn
- Multiple `feature_upgrade` events → Xyron references "You keep making me better."

### Critical runtime fix — Emotional Intent Guard

**Bug:** Emotional statements like "I am adding a new memory upgrade in you" were routing to `system_info` tool and returning Windows specs. The emotion detection at step 2c set the mood state but had no way to block tool execution at step 4.

**Fix:** Two new modules inserted as step 4d in the pipeline, BETWEEN complexity scoring and the tool yield:

- `backend/cognition/emotional_intent_guard.py` — classifies TOOL_COMMAND / EMOTIONAL_EVENT / CONVERSATION / UNCLEAR
  - Priority order: self_upgrade → frustration → achievement → imperative_verb → i_want_to_tool → conversation → UNCLEAR
  - UNCLEAR falls through to tool router (safe default, no false negatives on real commands)

- `backend/cognition/self_upgrade_detector.py` — detects "I upgraded your X" and classifies upgrade_type
  - Types: memory, voice, language, personality, ui, takeover, code, general
  - Language checked before code to avoid "language detection module" → code misclassification

**Mechanism:** When guard returns EMOTIONAL_EVENT or CONVERSATION:
```python
tool_name = None   # blocks tool yield chunk
tool_params = {}   # blocks tool execution
```
Then `_emotional_event_type` is set, which routes `_generate_and_validate()` to `_build_messages_emotional()` — a specialized prompt path that generates mood-appropriate responses without tool context.

**Offline fallbacks:** `_emotional_offline_response()` provides per-event canned responses when no API is available.

### Debug logging

Structured `[EMOTION_PIPELINE]` logs at every intercept point for runtime verification:
```
[EMOTION_PIPELINE] raw="..." guard=EMOTIONAL_EVENT(0.92) reason=self_upgrade_pattern
[EMOTION_PIPELINE] self_upgrade type=memory emotion=hype importance=0.90
[EMOTION_PIPELINE] mood_state forced: CALM → HYPED (self_upgrade)
[EMOTION_PIPELINE] final_response="Finally. I've been waiting for this. Memory upgrade..."
```

### Verified behavior

| Input | Guard result | Tool | Response |
|---|---|---|---|
| "I am adding a new memory upgrade in you" | EMOTIONAL_EVENT (self_upgrade) | None (blocked) | Excited, references memory upgrade |
| "This bug is annoying me" | EMOTIONAL_EVENT (frustration) | None (blocked) | Empathetic frustration response |
| "open chrome" | TOOL_COMMAND (imperative_verb) | open_app | Chrome opens |
| "take a screenshot" | TOOL_COMMAND (imperative_verb) | take_screenshot | Screenshot taken |

### New API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/cognition/mood` | Live mood state + UI theme |
| POST | `/api/v1/cognition/mood/force` | Force mood state (dev/test) |
| GET | `/api/v1/cognition/relationship/events` | Recent emotional events |
| POST | `/api/v1/cognition/relationship/events` | Record event manually |

---

## 2026-05-15 — Emotional Performance Upgrade (8-Phase Cinematic Overhaul) + Fuzzy Routing Fix

**Branch:** `feat/multilingual-cognition`

### Cinematic performance upgrade (8 phases)

Goal: move Xyron from polite-assistant responses ("That sounds great, boss!") to cinematic, alive reactions ("Woooo— finally. That wake issue was driving me insane.").

1. **Expression engine** — 9 HYPED cinematic openers, randomized + de-duplicated (last 10 tracked). Adaptive cooldown: energy ≥ 0.85 drops cooldown from 4 to 2 turns.
2. **`emotion_audio_fx.py`** (new) — maps MoodState → DSP preset (cinematic/crisp/warm/subtle). Overrides voice_personality for strong states (HYPED/DOMINANT/INTENSE/PROTECTIVE/EXCITED). Wired into `_kokoro_to_wav`.
3. **TTS chunk pacing** — offline fallback responses shortened and punchier.
4. **Micro-reactions** — `expression_engine.get_micro_reaction()` emits a 1-2 word fragment ("hah—", "wait wait—") as the first SSE chunk before LLM response when HYPED/EXCITED at energy ≥ 0.85. Separate 6-turn cooldown.
5. **Relationship evolution** — upgrade count injected into HYPED system prompt at thresholds (2+, 4+, 8+). Every self_upgrade detection logged to `relationship_memory.db`.
6. **Breathing pacing** — expression engine injects pause markers for HYPED state.
7. **Strict personality** — `STATE_PERSONALITY` rewrites forbid generic openers. Voice router system prompts explicitly block "That sounds great", "Awesome", "Certainly", etc.
8. **Test coverage** — 20 test cases (was 15), 20/20 passing.

### Fuzzy routing bug fix

**Root cause:** Whisper noise prefixes (e.g. "that on I'm taking off upgrading your memory.") break regex anchors. The emotional guard returned UNCLEAR, the intent router returned 0.36 confidence (below 0.65 threshold), and LAYER 3 `_is_system_info_query` matched bare `"memory"` → routed to `system_info`.

**Fix 1 — emotional guard fuzzy recovery** (`emotional_intent_guard.py`):
- Added `_FUZZY_UPGRADE_VERBS` and `_FUZZY_UPGRADE_TARGETS` frozensets.
- New step 7 in `classify()`: if any verb AND any target found via substring match → `EMOTIONAL_EVENT` (conf=0.78) with `[EMOTION_RECOVERY]` log line.

**Fix 2 — router anti-false-tool guard** (`voice.py`):
- Added `_SYSTEM_INFO_BLOCK_TERMS` frozenset (upgrade phrases that can't be system_info).
- LAYER 2 (`_is_system_health_query`) and LAYER 3 (`_is_system_info_query`) now check `_SYSTEM_INFO_BLOCK_TERMS` before assigning tool_name. If blocked → `[ROUTER_GUARD] blocked_tool=system_info reason=low_conf_emotional_text` log, tool_name stays None.

**Test results:** 25/25 passing (5 new cases: fuzzy noisy memory, fuzzy thinking upgrade, show system info, computer specs, wake issue fixed).

---

## 2026-05-14 — Multilingual Cognition (Urdu/Roman Urdu)

**Commit:** `f9a9004 fix(multilingual): wire pipeline, fix language mirroring, add debug endpoint`
**Commit:** `add42da feat(multilingual): Phase 1-10 multilingual cognition upgrade`

Wired multilingual normalization into the pipeline. Language detection returns `detected_language` which is forwarded to `_generate_and_validate()` to mirror the user's language in responses. Debug endpoint added for live language detection testing.

---

## Earlier — Phase 11: Urdu Support

---

## 2026-05-16 — Natural Emotional Delivery + Emotion Profiles

**Branch:** `feat/multilingual-cognition`

### Problem

After implementing the 10-phase emotional cognition system, Xyron's upgrade reactions were over-acted — ALL CAPS words, repeated phrases ("wait wait WAIT"), em-dash spam, fragmented bursts. The voice sounded like a sci-fi movie trailer, not a real system reacting genuinely. TTS speed was 1.35x (too fast), audio FX heavily compressed, and the upgrade type detector misclassified "voice conversion" as generic "voice".

### What was changed

#### Reaction packs — natural conversational language
Rewrote all upgrade reaction packs (`backend/api/routers/voice.py`) to use natural first-person reactions. Before: `"WOOOO— wait wait WAIT. Memory upgrade?"`. After: `"Wait… really? That's actually huge for me. Better memory means I can finally hold deeper context instead of rebuilding every session."` No ALL CAPS, max 1 emphasis phrase, max 2 em-dashes per response.

#### Upgrade type detection — voice_conversion and prosody
`backend/cognition/self_upgrade_detector.py` — added `voice_conversion` and `prosody` patterns before the generic `voice` pattern. "I'm adding emotional voice conversion" now correctly routes to the `voice_conversion` reaction pack instead of the wake-word pack.

#### Emotion profiles — HYPED_NATURAL, RELIEVED_EXCITED, PROTECTIVE_FOCUSED, PROUD_CALM
`backend/voice/prosody_planner.py` — added four named emotion profiles:
- **HYPED_NATURAL** (upgrade events): 1.10x speed, 130ms natural sentence pauses
- **RELIEVED_EXCITED** (achievement/fixes): 1.05x, first chunk slower then builds
- **PROTECTIVE_FOCUSED** (frustration): 0.96x, single chunk, calm
- **PROUD_CALM** (praise): 1.00x, warm stable

Profiles are set per-turn via `_current_emotion_profile` module-level signal in voice.py, read by `_kokoro_to_wav()`, passed to `prosody_planner.plan(profile=...)`.

#### Audio FX — softened hyped preset
`backend/voice/audio_fx.py` — hyped preset: compression ratio 6.0 → 3.5, threshold 0.40 → 0.55, saturation drive 0.06 → 0.02, air boost 0.18 → 0.10, reverb wet 0.07 → 0.05, normalize target 0.98 → 0.92, stereo width 0.30 → 0.18. Result: warm and bright, not distorted.

#### TTS speed — corrected across all paths
Synthesize-stream endpoint: HYPED override changed from `max(speed, 1.25)` → `max(speed, 1.10)`. Prosody planner EXTREME: 1.35x → 1.20x. Single-chunk HYPED fallback: 1.25x → 1.10x.

#### LLM system prompt — no more formatting instructions
System prompt for emotional responses now explicitly bans ALL CAPS, repeated words, em-dash spam. Instructs: "Emotion comes from what you say, not from formatting."

#### Micro-reactions — humanized
`backend/cognition/expression_engine.py` — replaced `"WOOOO—"`, `"YES YES YES—"` with `"Wait…"`, `"Oh wow."`, `"Okay, that's big."`, `"No way."`, `"Honestly?"`.

#### Missing log markers — added
`[EMOTION_STATE]`, `[EMOTION_INTENSITY]`, `[ORB_STATE]`, `[AUDIO_FX]` (in respond-stream path), `[EMOTION_QA]` all added.

### QA Results (live tested)

All 5 test phrases produced naturalness=9/10, robotic=0/10. WAV comparison samples at `backend/data/voice_tests/`. Roadmap for Phase 2 (RVC emotional voice conversion) at `Docs/voice_emotion_roadmap.md`.

**Commits:** `1b7d39a`, `6ee35d7`, `c94e527`, `61a6dc7`

Added Urdu/Roman Urdu intent patterns, Whisper language auto-detection, wake word pronunciation tolerance. Fixed `close_app` → `kill_app` (tool name mismatch in registry).

---

## 2026-05-16 — RVC Emotional Voice Conversion Layer

### What Was Built

Full RVC (Retrieval-based Voice Conversion) pipeline between Kokoro TTS and AudioFX:

- **Three-tier engine** (`backend/voice/rvc_engine.py`): auto-selects `full_rvc` → `lightweight` → `passthrough` based on available deps and model files
- **Lightweight tier**: librosa pitch_shift + scipy butterworth spectral EQ — no ML models needed, activates immediately
- **Full RVC tier**: rvc_python + HuBERT feature extraction, requires model `.pth` files at `~/.xyron/models/rvc/<preset>/model.pth`
- **Emotion → preset mapping**: 8 presets (hyped, relieved, dominant, protective, calm, late_night, audience_mode, neutral) mapped from mood state labels
- **Latency guard**: skips conversion if previous call exceeded 250ms, resets each turn
- **Safe fallback chain**: full_rvc → lightweight → original audio, never raises to caller
- **`/rvc-status` endpoint**: tier, latency, and preset availability
- **Frontend indicator**: subtle "KOKORO + RVC LITE" / "KOKORO + RVC" badge on Command Center
- **WAV comparison test**: `backend/scripts/test_rvc_pipeline.py` — kokoro_only.wav + rvc_<preset>.wav per preset

### Config

```env
ENABLE_RVC=true
RVC_MAX_LATENCY_MS=250
RVC_DEVICE=auto
```

### Architecture Doc

Full architecture at `Docs/rvc_voice_system.md`.

### Current Status

Lightweight tier active. Full RVC blocked by fairseq build failures — requires source build from GitHub. Real audio differentiation working now via lightweight tier.

---

## Earlier — Phase 9: Offline Ollama Fallback

**Commit:** `4530bca`, `e0aaf73`

3-tier AI chain: OpenAI → Ollama (llama3.2:3b local) → template strings. Wake word false rejection rate reduced by trimming Whisper audio clip and lowering threshold.

---

## Earlier — Phase 12: Self-Reflection

**Commit:** `fedb77f` (merged from `feat/phase-12-self-reflection`)

Self-awareness layer allowing Xyron to describe its own capabilities and state.
