# Xyron Emotional Cognition Architecture

## Overview

Xyron's emotional cognition system transforms the assistant from a functional voice
interface into a persistent, emotionally-adaptive AI entity. Every voice interaction
now carries emotional context that shapes how Xyron speaks, what it remembers, and
how the interface visually responds.

---

## Emotional Pipeline

```
Transcript (from STT)
        │
        ▼
[Phase 1] EmotionEngine.detect_text()          <15ms heuristic path
        │  emotion, energy, confidence, importance
        │
        ▼
[Phase 3] MoodStateMachine.update()            <5ms state transition
        │  11 states: CALM → HYPED → DOMINANT → LOCKED_IN ...
        │  → Updates CognitiveState.mood_state
        │
        ▼
[Phase 4] RelationshipMemory.record()          async, non-blocking
        │  Stores: achievements, late_night, frustrations, upgrades
        │
        ▼
[Phase 5] ExpressionEngine.shape()             <2ms post-generation
        │  Adds: contextual openers, pacing markers, emphasis
        │
        ▼
[Phase 6] EmotionTTSMapper.transform()         <1ms pure string ops
        │  Shapes text for Kokoro: speed hints, pauses, emphasis
        │
        ▼
[Phase 2] VoiceEnergyAnalyzer (optional)       ~50ms via librosa
        │  Runs on audio bytes post-STT for energy/stress metrics
        │
        ▼
Synthesis (Kokoro → edge-tts → pyttsx3)
```

---

## State Machine

### 11 Mood States

| State       | Trigger                        | Decay Target  | Hold Time |
|-------------|-------------------------------|---------------|-----------|
| CALM        | low energy baseline           | —             | ∞         |
| FOCUSED     | code_mode or seriousness      | CALM          | 10 min    |
| EXCITED     | pride/excitement ≥ 0.6        | FOCUSED       | 2 min     |
| HYPED       | hype emotion ≥ 0.8            | EXCITED       | 1 min     |
| PLAYFUL     | humor detected                | CALM          | 3 min     |
| DOMINANT    | ui_mode == "takeover"         | FOCUSED       | ∞ (until exit) |
| ANALYTICAL  | code_mode + 8+ focus turns    | FOCUSED       | 15 min    |
| LOCKED_IN   | 20+ consecutive focus turns   | FOCUSED       | 30 min    |
| INTENSE     | stress ≥ 0.6                  | FOCUSED       | 5 min     |
| LATE_NIGHT  | hour 23-5                     | CALM          | ∞ (until morning) |
| PROTECTIVE  | 3+ consecutive failures       | CALM          | 5 min     |

### State Effects

Each state provides:
1. **Personality addendum** — injected into OpenAI system prompt
2. **UI theme** — primary color, glow, pulse speed
3. **TTS speed hint** — 0.80 (DOMINANT) to 1.18 (HYPED)
4. **Pause factor** — 0.5 (INTENSE) to 2.0 (DOMINANT)

---

## Emotion Detection (Phase 1)

### 10 Detectable Emotions

| Emotion     | Key Signals                                     | LLM Fallback |
|-------------|------------------------------------------------|--------------|
| hype        | "LETS GO", hype vocab + caps + !!!             | No           |
| excitement  | excite vocab + punctuation energy              | No           |
| frustration | frustration vocab ("ugh", "broken", "again")   | No           |
| stress      | urgency words ("ASAP", "urgent") + multi-?     | No           |
| pride       | past achievement pattern ("i fixed/built")     | No           |
| humor       | "lol", "lmao", "haha"                         | No           |
| curiosity   | curiosity vocab × 2 OR + ?                    | Sometimes    |
| calmness    | ≥6 words, no caps, no exclamation             | No           |
| seriousness | direct, short, no emotion markers              | Sometimes    |
| sarcasm     | "oh great", "yeah sure" without exclaim energy | No           |

### Performance Characteristics

- **Heuristic path**: < 15ms (pure Python, no imports)
- **LLM fallback** (llama3.2:3b): ~200-400ms, triggered when confidence < 0.4
- **Audio path** (VoiceEnergyAnalyzer): ~50ms via librosa, optional

---

## Memory Integration (Phase 4)

### Relationship Memory (`~/.ai-operator/relationship_memory.db`)

SQLite table `events(id, event_type, description, metadata, ts)`.

Event types:
- `achievement` — user expressed pride, milestone completed
- `frustration` — repeated tool failures
- `feature_upgrade` — Xyron capabilities upgraded
- `focus_streak` — long coding session
- `late_night` — session after 23:00
- `task_success` / `task_failure` — per tool execution

Contextual recall lines are injected into the system prompt when patterns form
(e.g., 3+ late_night events → "You always push past midnight.").

### Integration with Existing Memory

- `memory_service.py` — session turns, long-term facts (unchanged)
- `episodic_memory.py` — SQLite per-turn history (unchanged)
- `cognition/memory/memory_bridge.py` — semantic + episodic bridge (unchanged)
- `memory/relationship_memory.py` — NEW: emotional event log

---

## Expression Engine (Phase 5)

The expression engine adds emotional style to AI responses **post-generation, pre-TTS**.

### Opener Cooldown

Openers only fire when:
- `emotion_energy >= 0.55`
- `importance >= 0.60`
- `turn_count - last_opener_turn >= 4`

This prevents opener spam while preserving cinematic moments.

### Examples by State

```
HYPED   + pride:   "Finally. I've been waiting for this. [response]"
EXCITED + excite:  "There it is. [response]"
DOMINANT:          "[response] ACKNOWLEDGED..."
FOCUSED:           "[response]."
LOCKED_IN:         "[response]."
PLAYFUL + humor:   "Okay — [response]"
PROTECTIVE:        "I've got you. [response]"
```

---

## TTS Mapping (Phase 6)

Text transformations before Kokoro synthesis:

| State       | Speed | Key Transform                              |
|-------------|-------|--------------------------------------------|
| DOMINANT    | 0.80  | CAPITALIZE status words + add `...` pauses |
| HYPED       | 1.18  | Capitalize emphasis words (FINALLY, DONE)  |
| EXCITED     | 1.10  | Light emphasis on key word                 |
| INTENSE     | 1.12  | Strip hedging, comma → period staccato     |
| FOCUSED     | 1.02  | Strip filler openers                       |
| ANALYTICAL  | 0.96  | Strip filler, hard period ending           |
| CALM        | 0.88  | Soft ending, exclamation → period          |
| LATE_NIGHT  | 0.85  | Soft ending                                |

---

## UI Emotional Layer (Phase 7)

### Backend → Frontend Flow

```
Pipeline turn completes
  → CognitiveState.mood_state updated
  → GET /api/v1/cognition/mood → MoodStateResponse {state, theme, held_sec}
  → GET /api/v1/cognition/state → {emotion_label, emotion_energy, ...}
  → useEmotionState hook merges both at 500ms poll interval
  → CinematicOrb reads ORB_VARIANTS[mood_state]
  → Framer Motion animates: scale, glow color, pulse speed
```

### Orb Color Map

| State       | Primary      | Character              |
|-------------|--------------|------------------------|
| CALM        | #4fc3f7 cyan | Slow breathe           |
| FOCUSED     | #00e5ff      | Steady neural pulse    |
| EXCITED     | #ff6f00 orange | Fast warm pulse      |
| HYPED       | #ff1744 red  | Burst, intense         |
| PLAYFUL     | #ae52d4 purple | Bouncy soft          |
| DOMINANT    | #b71c1c dark red | Sharp, precise     |
| ANALYTICAL  | #0097a7 teal | Neural steady          |
| LOCKED_IN   | #00bcd4      | Deep calm cyan         |
| INTENSE     | #e53935 red  | Rapid high-energy      |
| LATE_NIGHT  | #5c6bc0 indigo | Dim, cool            |
| PROTECTIVE  | #546e7a slate | Stable, grounded      |

---

## Session Awareness (Phase 9)

Xyron detects:
- **Late night** — `hour >= 23 or hour < 5` → LATE_NIGHT state + records event
- **Focus streaks** — `focus_turn_count` in MoodStateMachine → ANALYTICAL → LOCKED_IN
- **Failure streaks** — `failure_streak >= 3` → PROTECTIVE state
- **Achievement moments** — pride emotion + energy ≥ 0.6 → `achievement` event recorded

Session context surfaces through `relationship_memory.get_context_string()` which
is injected into the system prompt on every AI generation call.

---

## Personality Evolution (Phase 10)

Personality evolves through context-driven state transitions:

- **Takeover mode** → DOMINANT: commanding, precise, no warmth
- **Long code session** → ANALYTICAL → LOCKED_IN: terse, minimal interruption
- **Multiple upgrades** → relationship_memory records `feature_upgrade` events;
  Xyron references them: "You keep making me better."
- **Late night** → LATE_NIGHT: quieter, more grounded acknowledgment of the grind
- **Humor exchange** → PLAYFUL: brief wit, one remark per exchange, never forced

Personality does not drift randomly — every state transition is causal. The
`_OPENER_COOLDOWN_TURNS = 4` prevents emotional spam and ensures reactions feel earned.

---

## API Endpoints

### New endpoints (Phase 7)

| Method | Path                              | Description                    |
|--------|-----------------------------------|--------------------------------|
| GET    | `/api/v1/cognition/mood`          | Live mood state + UI theme     |
| POST   | `/api/v1/cognition/mood/force`    | Force mood state (dev/test)    |
| GET    | `/api/v1/cognition/relationship/events` | Recent emotional events  |
| POST   | `/api/v1/cognition/relationship/events` | Record event manually    |

### Enhanced cognition/state

`GET /api/v1/cognition/state` now returns three additional fields:
- `mood_state: str` — current MoodState value
- `emotion_label: str` — last detected user emotion
- `emotion_energy: float` — 0.0–1.0 emotion intensity

---

## Performance Notes

| Component          | Latency     | Blocking? |
|--------------------|-------------|-----------|
| EmotionEngine      | < 15ms      | Yes, in pipeline |
| MoodStateMachine   | < 1ms       | Yes, in pipeline |
| ExpressionEngine   | < 2ms       | Yes, post-AI-gen |
| EmotionTTSMapper   | < 1ms       | Yes, pre-TTS |
| VoiceEnergyAnalyzer| ~50ms       | Optional, audio path only |
| RelationshipMemory | ~1ms write  | No (wrapped in try/except) |
| LLM fallback       | 200-400ms   | Only when conf < 0.4 |

The emotion detection and mood state update add ~16ms to the pipeline critical path.
All other components are either negligible or run in the non-blocking path.

---

## File Map

```
backend/
  cognition/
    emotion_engine.py          Phase 1 — text emotion detection
    mood_state_machine.py      Phase 3 — 11-state machine with decay
    expression_engine.py       Phase 5 — response style shaping
    cognitive_state.py         Extended: mood_state, emotion_label, emotion_energy
  voice/
    voice_energy.py            Phase 2 — audio energy analysis wrapper
    emotion_tts_mapper.py      Phase 6 — TTS text transformation
    emotion_detector.py        Existing — acoustic feature extraction (unchanged)
  memory/
    relationship_memory.py     Phase 4 — SQLite emotional event log
  api/
    services/pipeline.py       Modified — Steps 2c, 5b, 6b, 7 wired
    routers/cognition.py       Extended — mood + relationship endpoints

web/src/
  state/
    emotionState.ts            Phase 7 — ORB_VARIANTS, MoodTheme, CSS vars
  hooks/
    useEmotionState.ts         Phase 7 — 500ms polling hook
    useCognitiveState.ts       Extended — mood_state, emotion_label, emotion_energy
  components/im-home/
    CinematicOrb.tsx           Phase 7 — live emotion-reactive orb
```

---

## Future Expansion

- **Phase 8 micro-expressions**: "hmm", breath pauses, small laughs — add as
  `_MICRO_EXPRESSIONS` dict in `expression_engine.py`, trigger on specific
  event types from `relationship_memory`
- **Phase 9 voice energy integration**: Wire `voice_energy_analyzer.analyze()`
  into the audio pipeline (currently structured but not on the hot path)
- **Emotion WebSocket**: Replace polling with a WebSocket push from the pipeline
  for zero-latency UI updates
- **Multi-modal fusion**: Combine text emotion + voice energy scores for higher
  confidence composite classification
- **Kokoro voice clone**: When Urdu voice added to Kokoro, route LATE_NIGHT and
  PROTECTIVE states through edge-tts with softer voice profile
