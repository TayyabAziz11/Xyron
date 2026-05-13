# Xyron Collaboration Plan — Tayyab × Qasim

> **Last updated:** 2026-05-13 (Phase 7 done)
> **Goal:** Evolve Xyron from a command-router into a cognitively-aware, emotionally-responsive, ambient AI assistant.

---

## Quick Orientation — What Already Exists

Before you start, know the ground truth:

| Folder | Status | Notes |
|---|---|---|
| `backend/brain/` | ✅ Exists | orchestrator, memory_manager, planner — Qasim's Phase 0 extends this |
| `backend/cognition/` | ✅ Exists | Phase 0 (Qasim): cognitive_state.py, state_transitions.py, memory/ |
| `backend/src/ai_operator/agents/` | ✅ Exists | 6 agents (approval, email, integration, linkedin, reporting, base) |
| `backend/agents/` (root) | ❌ Does NOT exist | If plan says "backend/agents/", it means `backend/src/ai_operator/agents/` |
| `backend/voice/` | ✅ Exists | TTS: Kokoro→edge-tts→pyttsx3, Whisper STT, wake word |
| `backend/utils/` | ✅ Exists | Only path_utils.py — add to it |
| `backend/dev/` | ✅ Exists | Phase 9: file_intelligence, project_memory, terminal_intelligence, dev_observer |
| `backend/data/project_memory/` | ✅ Exists | Phase 9: per-project JSON memory files (auto-created) |
| `backend/cognition/memory/` | ✅ Exists | Phase 1: semantic_store.py + memory_bridge.py (Qasim) |
| `web/src/components/` | ✅ Exists | 50+ components, Tayyab adds to this |

---

## Git Workflow (BOTH follow this — no exceptions)

```bash
# Start every new phase:
git checkout main && git pull origin main
git checkout -b feat/phase-X-description

# Commit often (at least once per file):
git add <specific-files>
git commit -m "feat(phase-X): description"

# Before opening a PR:
git pull origin main
git merge main   # resolve conflicts here, not in PR

# Open PR → request review from the other person → merge to main
```

**Branch naming:**
```
feat/phase-0-cognitive-state       (Qasim)
feat/phase-1-memory-chromadb       (Qasim)
feat/phase-2-emotion-detection     (Tayyab)
feat/phase-3-goals-personality     (Qasim)
feat/phase-4-ambient-ui            (Tayyab)
feat/phase-5-environment-monitor   (Tayyab)
feat/phase-6-adaptive-ui-modes     (Tayyab)
feat/phase-7-agent-hierarchy       (Qasim)
feat/phase-8-code-assistant        (Tayyab)
feat/phase-9-dev-intelligence      (Tayyab) ← DONE
feat/phase-10-elevenlabs-voice     (Tayyab)
feat/phase-11-urdu-support         (shared)
feat/phase-12-self-reflection      (Qasim)
```

**Never commit to `main` directly.**

---

## START ORDER

```
Week 1:   Qasim → Phase 0      Tayyab → Phase 2
Week 2:   Qasim → Phase 1      Tayyab → Phase 4
Week 3:   Qasim → Phase 3      Tayyab → Phase 5
Week 4:   Qasim → Phase 7      Tayyab → Phase 6
Week 5:   Qasim → Phase 9 ✅   Tayyab → Phase 8 ✅
Week 6:   Qasim → Phase 12     Tayyab → Phase 10
Week 7:   Phase 11 — BOTH together
```

Phase 0 must merge to main BEFORE Tayyab's Phase 2 can use cognitive state. Everything else runs in parallel.

---

---

# QASIM'S PLAN

## Phase 0 — Cognitive State Engine ✅ DONE

**Branch:** `feat/phase-0-cognitive-state`
**Status:** Merged to main (commit `4530df6`). CognitiveState singleton live. All routers read/write via `cognitive_state.update()`.
**Depends on:** Nothing (start immediately)
**Blocks:** All other phases that read AI state

### What to build

A shared singleton that represents the AI's "mental state" at any moment. Every router and service reads from it. Nothing else sets global AI state randomly.

### Files to create

```
backend/cognition/__init__.py
backend/cognition/cognitive_state.py      ← main state object
backend/cognition/state_transitions.py   ← rules for state changes
```

### Files to modify

```
backend/api/main.py                       ← import and init cognition on startup
backend/api/services/command_service.py  ← update state after each command
backend/brain/orchestrator.py            ← read state before routing decisions
```

### `cognitive_state.py` — what to implement

```python
# backend/cognition/cognitive_state.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import threading, time

class AttentionLevel(str, Enum):
    IDLE       = "idle"
    LISTENING  = "listening"
    PROCESSING = "processing"
    SPEAKING   = "speaking"
    FOCUSED    = "focused"

class MoodBias(str, Enum):
    NEUTRAL    = "neutral"
    ALERT      = "alert"
    CALM       = "calm"
    STRESSED   = "stressed"   # fed by Tayyab's emotion detector

@dataclass
class CognitiveState:
    attention:          AttentionLevel = AttentionLevel.IDLE
    mood_bias:          MoodBias       = MoodBias.NEUTRAL
    active_goal:        Optional[str]  = None
    current_task:       Optional[str]  = None
    context_summary:    str            = ""
    last_user_emotion:  str            = "neutral"   # written by Tayyab Phase 2
    active_ui_mode:     str            = "default"   # written by Tayyab Phase 6
    turn_count:         int            = 0
    last_updated:       float          = field(default_factory=time.time)
    _lock: threading.RLock             = field(default_factory=threading.RLock, repr=False)

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)
            self.last_updated = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

# Global singleton — import this everywhere
cognitive_state = CognitiveState()
```

### State transition rules (`state_transitions.py`)

- User starts speaking → `attention = LISTENING`
- Whisper returns transcript → `attention = PROCESSING`, `turn_count += 1`
- Response starts streaming → `attention = SPEAKING`
- Response ends → `attention = IDLE`
- Goal set → `active_goal = <goal>`

### API endpoint to expose state (add to `backend/api/routers/system.py` or new `cognition.py` router)

```python
GET /api/v1/cognition/state  → CognitiveState.snapshot()
```

Tayyab's frontend polls this to drive ambient UI.

### Install requirements

No new packages needed for Phase 0.

---

## Phase 1 — Memory Architecture (ChromaDB) ✅ DONE

**Branch:** `feat/phase-1-memory-chromadb`
**Status:** Merged to main via PR #8 (commit `37b42b7`). ChromaDB + all-MiniLM-L6-v2 live. `chromadb>=0.5.0` added to requirements.txt.
**Depends on:** Phase 0 merged
**Install:** `pip install chromadb sentence-transformers`

### What to build

Replace the current SQLite episodic memory with a two-layer system:
- **Episodic** (short-term): keep existing `episodic_memory.py` as-is (SQLite, fast)
- **Semantic** (long-term): ChromaDB vector store for "recall by meaning" queries

### Files to create

```
backend/cognition/memory/
    __init__.py
    semantic_store.py    ← ChromaDB client, embed + upsert + query
    memory_bridge.py     ← unifies episodic + semantic into one API
```

### Files to modify

```
backend/api/services/memory_service.py   ← add semantic recall alongside keyword recall
backend/brain/memory_manager.py          ← delegate long-term recalls to semantic_store
```

### `semantic_store.py` — what to implement

```python
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

class SemanticMemoryStore:
    COLLECTION = "xyron_memories"

    def __init__(self, persist_dir="~/.ai-operator/chroma"):
        ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._col = self._client.get_or_create_collection(self.COLLECTION, embedding_function=ef)

    def remember(self, text: str, metadata: dict) -> str:
        """Store a memory. Returns its ID."""
        ...

    def recall(self, query: str, n=5) -> list[dict]:
        """Return top-n semantically similar memories."""
        ...

    def forget(self, memory_id: str) -> None:
        """Delete a specific memory by ID."""
        ...
```

### Model choice

Use `all-MiniLM-L6-v2` (sentence-transformers) — it's small (~80MB), fast, already used in `intent_router.py` so it's likely cached.

### ChromaDB storage path

`~/.ai-operator/chroma/` — alongside existing `memory.json` and `episodes.db`.

---

## Phase 3 — Goals & Personality Engine

**Branch:** `feat/phase-3-goals-personality`
**Depends on:** Phase 0, Phase 1 merged

### Files to create

```
backend/cognition/goals.py         ← GoalTracker: set, complete, list, prioritize
backend/cognition/personality.py   ← PersonalityProfile: traits, tone modifiers
```

### Files to modify

```
backend/api/services/command_service.py   ← detect goal-setting phrases ("I want to...", "remind me to...")
backend/voice/response_generator.py      ← apply personality tone to responses
backend/cognition/cognitive_state.py     ← add active_goal tracking
```

### `goals.py` — what to implement

```python
@dataclass
class Goal:
    id: str
    description: str
    created_at: float
    deadline: Optional[float]
    priority: int        # 1=low, 5=critical
    status: str          # active | paused | completed | failed
    sub_goals: list[str] = field(default_factory=list)

class GoalTracker:
    def set_goal(self, description: str, priority=3, deadline=None) -> Goal: ...
    def complete_goal(self, goal_id: str) -> None: ...
    def get_active_goals(self) -> list[Goal]: ...
    def prioritize(self) -> Goal: ...   # returns highest priority active goal
```

### `personality.py` — what to implement

```python
@dataclass
class PersonalityProfile:
    name: str = "Xyron"
    tone: str = "direct"           # direct | warm | formal | casual
    verbosity: str = "concise"     # concise | verbose | minimal
    humor_level: float = 0.3       # 0.0 (none) → 1.0 (high)
    confidence_level: float = 0.8

    def get_tone_prompt(self) -> str:
        """Returns a system prompt fragment that encodes personality."""
        ...
```

---

## Phase 7 — Agent Hierarchy ✅ DONE

**Branch:** `feat/phase-7-agent-hierarchy`
**Status:** Merged to main via PR #10 (commit `60b895c`). 4 agents live: FocusAgent, MemoryAgent, SentinelAgent, PlannerAgent. `route_direct()` added to router. `plan_goal()` added to brain/planner.
**Depends on:** Phase 0, Phase 1, Phase 3 merged

### What to build

Four new specialized agents. Add them to the existing registry at `backend/src/ai_operator/agents/registry.py`.

### Files to create

```
backend/src/ai_operator/agents/
    focus_agent.py      ← manages attention, filters distractions
    memory_agent.py     ← answers "what did I tell you about X?" queries
    sentinel_agent.py   ← monitors system health, raises alerts
    planner_agent.py    ← breaks multi-step goals into ordered tasks
```

### Files to modify

```
backend/src/ai_operator/agents/registry.py   ← register new agents
backend/src/ai_operator/agents/router.py     ← add routing rules for new agents
backend/brain/planner.py                     ← delegate to planner_agent
```

### Each agent follows the existing `BaseAgent` pattern in `base.py`

```python
class FocusAgent(BaseAgent):
    name = "focus"
    description = "Manages AI attention and filters low-priority interruptions during focused tasks"

    async def handle(self, command: str, context: dict) -> AgentResponse:
        ...
```

### Sentinel agent — what it monitors

- Backend health (is FastAPI up?)
- Memory usage (warn if ChromaDB grows > 1GB)
- Goal deadlines (alert if deadline < 1 hour away)
- Whisper STT failures (fallback to typed input)

---

## Phase 9 — Persistent Contextual Dev Intelligence ✅ DONE

**Branch:** `main` (committed directly — `8d55ba6`)
**Depends on:** Phase 8 merged
**Status:** Fully live. All 10 tasks shipped. No new packages needed beyond Ollama (already used in Phase 8).

### What was built

Xyron upgraded from "code-aware assistant" to "persistent autonomous development intelligence."

When VS Code/Cursor is active, Xyron now understands the **full engineering session** — not just the current command.

---

### New files (all in `backend/dev/`)

| File | What it does |
|---|---|
| `backend/dev/file_intelligence.py` | Reads active file, detects language/framework, extracts symbols, TODOs, imports, issues. Hash-cached. Max 300KB. |
| `backend/dev/project_memory.py` | Persistent JSON memory per project — stack, git branch, recurring errors, recent files, session summaries. Atomic writes. |
| `backend/dev/terminal_intelligence.py` | 16-pattern regex classifier for npm/pip/Python/TypeScript/Docker/git errors. Falls back to `mistral:7b` for low-confidence cases. |
| `backend/dev/dev_observer.py` | Background asyncio loop (5s interval). Emits passive insights via SSE. Max 1 insight per 60s. |

---

### New API endpoints (`/api/v1/dev/`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/active-file-context` | GET | Full analysis of active file |
| `/project-memory` | GET | Project memory for active/given project |
| `/project-memory/update` | POST | Update architecture notes, tasks, etc. |
| `/project-memory/session-summary` | POST | Append a session summary |
| `/analyze-terminal` | POST | Classify a terminal error + suggest fix |
| `/terminal-output` | POST | Receive terminal output from frontend/wrapper |
| `/latest-terminal-error` | GET | Most recent error for the active project |
| `/observer-stream` | GET | SSE stream of passive insights |
| `/patch-preview` | POST | Show patch preview (auto-apply is disabled — safety) |

---

### Modified files

**`backend/src/ai_operator/agents/dev_agent.py`**
Before answering, DevAgent now injects:
- Active file context (language, framework, symbols, issues)
- Project memory summary (stack, errors, past fixes, last session)
- Latest terminal error (type, cause, suggested fix)

Contextual phrase resolution:
- `"explain this"` → uses active file
- `"what went wrong"` → uses latest terminal error
- `"fix it"` / `"fix the error"` → uses terminal error + active file
- `"summarize this project"` → uses project memory

**`backend/voice/voice_command_router.py`**
Same contextual resolution as DevAgent, applied to voice commands before routing.
System commands (volume, music, open app, shutdown, etc.) are NEVER hijacked.

**`backend/api/main.py`**
Starts `observer_loop()` as an `asyncio.create_task()` at startup.

---

### Frontend (`web/src/components/code/CodeAssistantPanel.tsx`)

5 new live sections added to the Code Assistant panel:

1. **Active File Intelligence** — language, framework, summary, symbols (tags), TODOs, issues
2. **Project Memory** — stack tags, git branch, recurring errors with counts, recent files, last session summary
3. **Terminal Intelligence** — error type, severity badge, cause, fix, one-click copy commands
4. **Passive Insights Feed** — live SSE from observer, severity-colored badges, animated entries
5. **Dev Context Status** — project, file, language, context freshness timestamp

All sections only render when `code_mode = true`.

---

### Observability — log prefixes

| Prefix | Where |
|---|---|
| `[DEV_CONTEXT]` | dev router endpoints |
| `[ACTIVE_FILE]` | file_intelligence.py |
| `[PROJECT_MEMORY]` | project_memory.py |
| `[TERMINAL_INTEL]` | terminal_intelligence.py |
| `[DEV_OBSERVER]` | dev_observer.py |
| `[DEV_AGENT_CONTEXT]` | dev_agent.py |
| `[PASSIVE_INSIGHT]` | observer insight emission |

Each log includes: `latency_ms`, `project`, `file`, `cache_hit`, `action`, `confidence`.

---

### What Qasim can build next (Phase 10+)

- **Phase 3 (Goals & Personality)** — uses project memory to set `active_goal` in CognitiveState
- **Session summary automation** — after each coding session, call `POST /dev/project-memory/session-summary`
- **ChromaDB semantic search across project memory** — store session summaries in semantic_store, recall by meaning
- **Self-reflection loop** — DevObserver reads session_summaries and proposes next steps autonomously

---

## Phase 12 — Self Reflection Loop

**Branch:** `feat/phase-12-self-reflection`
**Depends on:** All Qasim phases merged

### Files to create

```
backend/cognition/reflection.py   ← ReflectionEngine: periodic self-analysis
```

### Files to modify

```
backend/api/main.py   ← start reflection loop as background task on startup
```

### What the reflection loop does

Every 30 minutes:
1. Read last 50 turns from episodic memory
2. Read active goals from GoalTracker
3. Ask LLM: "Based on these interactions, what patterns do you see? What should I do differently?"
4. Store insights to SemanticStore with tag `type=reflection`
5. Update `PersonalityProfile` if strong patterns detected (e.g., user prefers shorter answers)

```python
class ReflectionEngine:
    async def run_reflection_cycle(self) -> ReflectionResult:
        episodes  = episodic_memory.get_recent(50)
        goals     = goal_tracker.get_active_goals()
        insights  = await llm_reflect(episodes, goals)
        semantic_store.remember(insights.summary, {"type": "reflection"})
        return insights
```

---

---

# TAYYAB'S PLAN

## Phase 2 — Emotion Detection

**Branch:** `feat/phase-2-emotion-detection`
**Depends on:** Phase 0 merged (to write detected emotion into cognitive state)
**Install:** `pip install librosa soundfile numpy scipy`

### What to build

Analyze the raw audio from the user's microphone to detect emotional signals (pitch, energy, speech rate) and write the result to `cognitive_state.last_user_emotion`. The UI and personality engine both read this.

### Files to create

```
backend/voice/emotion_detector.py   ← AudioEmotionDetector class
```

### Files to modify

```
backend/api/routers/voice.py        ← call emotion detection after STT, before response
backend/voice/whisper_service.py    ← return audio bytes alongside transcript
```

### `emotion_detector.py` — what to implement

```python
import librosa
import numpy as np

EMOTIONS = ["neutral", "happy", "stressed", "tired", "excited", "sad"]

class AudioEmotionDetector:
    def detect(self, audio_bytes: bytes, sample_rate: int = 16000) -> EmotionResult:
        """
        Returns dominant emotion + confidence.
        Uses pitch, energy, ZCR, speech rate as features.
        No ML model needed — rule-based is accurate enough for v1.
        """
        y = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        pitch       = self._extract_pitch(y, sample_rate)
        energy      = self._extract_energy(y)
        zcr         = librosa.feature.zero_crossing_rate(y).mean()
        speech_rate = self._estimate_speech_rate(y, sample_rate)

        return self._classify(pitch, energy, zcr, speech_rate)

    def _extract_pitch(self, y, sr) -> float:
        f0, _, _ = librosa.pyin(y, fmin=80, fmax=400, sr=sr)
        return float(np.nanmean(f0)) if f0 is not None else 0.0

    def _extract_energy(self, y) -> float:
        return float(np.sqrt(np.mean(y**2)))

    def _estimate_speech_rate(self, y, sr) -> float:
        # count syllable-like energy peaks per second
        ...

    def _classify(self, pitch, energy, zcr, rate) -> EmotionResult:
        # Rule-based heuristics:
        # high pitch + high energy + high rate → excited/stressed
        # low pitch + low energy + low rate    → tired/sad
        # moderate all                         → neutral
        # high pitch + low energy              → happy/curious
        ...

@dataclass
class EmotionResult:
    emotion: str    # one of EMOTIONS
    confidence: float
    features: dict  # raw features for debugging
```

### Integration point

In `backend/api/routers/voice.py`, after STT returns transcript:

```python
emotion_result = emotion_detector.detect(audio_bytes)
cognitive_state.update(last_user_emotion=emotion_result.emotion)
```

### API endpoint to expose emotion

```python
GET /api/v1/voice/emotion  → last detected emotion + confidence
```

Tayyab's frontend uses this for AmbientOrb color changes in Phase 4.

---

## Phase 4 — Ambient Presence UI

**Branch:** `feat/phase-4-ambient-ui`
**Depends on:** Phase 0 merged (GET /api/v1/cognition/state)
**Tech:** Framer Motion (already installed), React Context, CSS custom properties

### What to build

Three new always-on UI components that reflect the AI's cognitive state:
- **AmbientOrb** — pulsing circle in command center, color = user emotion, pulse rate = AI attention
- **PassiveHUD** — slim top bar showing active goal + current task (collapses to icon when idle)
- **ThoughtStream** — scrolling "AI inner monologue" panel, optionally visible

### Files to create

```
web/src/components/ambient/
    AmbientOrb.tsx        ← main orb component
    PassiveHUD.tsx        ← slim status bar
    ThoughtStream.tsx     ← scrolling thoughts panel
    index.ts              ← re-exports
web/src/hooks/useCognitiveState.ts   ← polls GET /api/v1/cognition/state every 500ms
```

### Files to modify

```
web/src/app/app/command-center/page.tsx   ← embed AmbientOrb + PassiveHUD
web/src/components/layout/AppShell.tsx    ← embed PassiveHUD at layout level
```

### `useCognitiveState.ts` hook

```typescript
// Polls /api/v1/cognition/state every 500ms
export function useCognitiveState() {
  const [state, setState] = useState<CognitiveState | null>(null);
  useEffect(() => {
    const interval = setInterval(async () => {
      const res = await fetch('/api/v1/cognition/state');
      setState(await res.json());
    }, 500);
    return () => clearInterval(interval);
  }, []);
  return state;
}
```

### `AmbientOrb.tsx` — color mapping

| Emotion | Orb color |
|---|---|
| neutral | `#00ffff` (cyan) |
| stressed | `#ff3333` (red) |
| happy | `#00ff88` (green) |
| excited | `#ff9900` (orange) |
| tired | `#8855ff` (purple) |
| sad | `#4444ff` (blue) |

Pulse animation speed: `IDLE=2s`, `LISTENING=0.8s`, `PROCESSING=0.4s`, `SPEAKING=0.6s`

Use Framer Motion `animate` prop to transition colors and pulse speed smoothly.

### `PassiveHUD.tsx` — layout

```
[ XYRON ]  [ Attention: LISTENING ]  [ Goal: Write report ]  [ Emotion: stressed ]
```
Collapses to a single dot when `attention = IDLE`.

---

## Phase 5 — Environment Monitor

**Branch:** `feat/phase-5-environment-monitor`
**Depends on:** Phase 0 merged
**Tech:** `psutil` (already likely installed), Next.js polling hook

### What to build

A backend loop that tracks battery, CPU, memory, active window, and exposes it via an endpoint. Frontend displays it in a live panel.

### Files to create

```
backend/api/routers/environment.py    ← new router: GET /api/v1/environment/status
web/src/hooks/useEnvironment.ts       ← polls the endpoint every 2s
web/src/components/system/EnvironmentPanel.tsx   ← live panel component
```

### Files to modify

```
backend/api/main.py   ← register new environment router
web/src/app/app/dashboard/page.tsx   ← add EnvironmentPanel
```

### Backend — `environment.py` router

```python
import psutil, subprocess
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/environment", tags=["environment"])

@router.get("/status")
async def get_environment_status():
    battery = psutil.sensors_battery()
    return {
        "cpu_percent":      psutil.cpu_percent(interval=0.1),
        "ram_percent":      psutil.virtual_memory().percent,
        "battery_percent":  battery.percent if battery else None,
        "battery_charging": battery.power_plugged if battery else None,
        "active_window":    _get_active_window(),   # via xdotool or wmctrl on WSL2
        "timestamp":        time.time(),
    }

def _get_active_window() -> str:
    try:
        return subprocess.check_output(["xdotool", "getactivewindow", "getwindowname"],
                                       text=True).strip()
    except Exception:
        return "unknown"
```

### Frontend panel — key metrics to show

- CPU/RAM bars (animated, color turns red >80%)
- Battery indicator with charging status
- Active window name
- Alert threshold: notify via ProactiveToast if CPU >90% for >30s

### Feed into cognitive state

```python
# In environment.py background task (run every 10s)
if cpu_percent > 85:
    cognitive_state.update(active_ui_mode="stressed")
```

---

## Phase 6 — Adaptive UI Modes

**Branch:** `feat/phase-6-adaptive-ui-modes`
**Depends on:** Phase 4 (ambient UI), Phase 5 (environment monitor)
**Tech:** React Context, CSS custom properties, Framer Motion

### 4 UI Modes

| Mode | Trigger | Visual |
|---|---|---|
| **Focus** | User says "focus mode" or goal requires concentration | Minimal UI, hide sidebar, muted colors, no animations |
| **Calm** | User emotion = tired/sad OR low CPU + nighttime | Soft purple/blue palette, slow animations, dimmed |
| **Overdrive** | CPU >85% OR user emotion = excited/stressed | Red accents, fast pulse, system metrics front and center |
| **Sentinel** | sentinel_agent fires alert | Full-screen alert overlay, flashing red border |

### Files to create

```
web/src/contexts/UIModeContext.tsx    ← React context + mode state
web/src/hooks/useUIMode.ts           ← reads cognitive state → derives UI mode
web/src/styles/ui-modes.css          ← CSS custom property overrides per mode
```

### Files to modify

```
web/src/app/app/layout.tsx             ← wrap in UIModeProvider
web/src/components/layout/AppShell.tsx ← apply mode class to root div
web/src/app/globals.css                ← import ui-modes.css
```

### Mode switching

```typescript
// useUIMode.ts
export function useUIMode() {
  const cogState = useCognitiveState();
  const env      = useEnvironment();

  return useMemo(() => {
    if (cogState?.active_ui_mode === "sentinel") return "sentinel";
    if (env?.cpu_percent > 85 || cogState?.last_user_emotion === "stressed") return "overdrive";
    if (cogState?.last_user_emotion === "tired") return "calm";
    if (cogState?.active_goal?.includes("focus")) return "focus";
    return "default";
  }, [cogState, env]);
}
```

### CSS approach

```css
/* ui-modes.css */
[data-ui-mode="focus"] {
  --accent-primary: #00ffff44;
  --sidebar-width: 0px;
  --animation-speed: 0s;
}
[data-ui-mode="overdrive"] {
  --accent-primary: #ff3333;
  --pulse-speed: 0.3s;
}
[data-ui-mode="calm"] {
  --accent-primary: #8855ff;
  --pulse-speed: 3s;
}
[data-ui-mode="sentinel"] {
  --border-color: #ff0000;
  --flash: infinite 0.5s border-flash;
}
```

---

## Phase 8 — Code Assistant Mode ✅ DONE

**Branch:** `feat/phase-8-code-assistant`
**Status:** Merged to main (commit `c028e8b`). DevAgent, CodeAssistantPanel, environment monitor, voice routing all live.
**Depends on:** Phase 5 (environment monitor for active window detection), Phase 6 (UI modes)

### What to build

When VS Code / Cursor is the active window, Xyron automatically switches into a dev-focused mode:
- Route voice commands to a `dev_agent` that understands code intent
- Show a "Code Assistant" indicator in the UI
- Enable code-specific shortcuts ("explain this function", "write a test", "check for bugs")

### Files to create

```
backend/src/ai_operator/agents/dev_agent.py    ← new agent for code tasks
web/src/components/code/CodeAssistantPanel.tsx ← code mode UI panel
```

### Files to modify

```
backend/voice/voice_command_router.py          ← detect code intent → route to dev_agent
backend/api/services/command_service.py        ← add code intent patterns
backend/cognition/cognitive_state.py           ← add "code_mode": bool field
web/src/hooks/useUIMode.ts                     ← add code mode visual
```

### Window detection → mode switch

```python
# In background loop (environment.py or screen_context_service.py)
CODE_EDITORS = ["code", "cursor", "vim", "nvim", "pycharm", "webstorm", "intellij"]

def _is_code_editor_active(active_window: str) -> bool:
    return any(editor in active_window.lower() for editor in CODE_EDITORS)

# Update cognitive state:
if _is_code_editor_active(active_window):
    cognitive_state.update(code_mode=True, active_ui_mode="focus")
else:
    cognitive_state.update(code_mode=False)
```

### `dev_agent.py` — key capabilities

```python
class DevAgent(BaseAgent):
    name = "dev"
    description = "Code assistance: explain, write, test, debug"

    INTENTS = {
        "explain": ["explain", "what does", "how does", "describe"],
        "write":   ["write", "create", "generate", "implement"],
        "test":    ["test", "write test", "unit test"],
        "debug":   ["debug", "fix", "bug", "error", "why is"],
    }

    async def handle(self, command: str, context: dict) -> AgentResponse:
        intent = self._classify_dev_intent(command)
        # Call OpenAI with code-focused system prompt
        ...
```

### UI panel

Show in command center when `code_mode = true`:
- Current file/project detection (from window title)
- Quick action buttons: "Explain", "Test", "Debug", "Refactor"
- Last code response in a `<pre>` block with syntax highlighting (use `highlight.js` or `prism`)

---

## Phase 10 — Custom Voice (ElevenLabs)

**Branch:** `feat/phase-10-elevenlabs-voice`
**Depends on:** Nothing — self-contained voice upgrade
**Install:** `pip install elevenlabs`

### What to build

Add ElevenLabs as Tier 0 in the TTS fallback chain. Result: ultra-realistic voice when API key is set, automatic fallback to Kokoro when offline.

### Files to modify

```
backend/voice/tts_service.py   ← add Tier 0: ElevenLabs
backend/.env                   ← add ELEVENLABS_API_KEY
backend/api/config.py          ← expose elevenlabs_api_key setting
```

### Updated TTS chain in `tts_service.py`

```
Tier 0  ElevenLabs API          — cloud, ultra-realistic (if ELEVENLABS_API_KEY set)
Tier 1  Kokoro (ONNX)           — local, ~100ms, offline
Tier 2  edge-tts                — Microsoft cloud
Tier 3  pyttsx3 / espeak-ng     — local final fallback
```

### Implementation pattern (follows existing tier pattern)

```python
_ELEVEN_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"   # Rachel — natural, clear

def synthesize_elevenlabs(text: str) -> Optional[bytes]:
    api_key = settings.elevenlabs_api_key
    if not api_key:
        return None
    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        audio = client.generate(
            text=text,
            voice=_ELEVEN_VOICE_ID,
            model="eleven_turbo_v2",   # fastest model
        )
        return b"".join(audio)
    except Exception as e:
        logger.warning("ElevenLabs failed: %s", e)
        return None
```

### Voice selection UI

Add a voice selector in `web/src/app/app/settings/page.tsx`:
- Dropdown: "ElevenLabs (Premium)", "Kokoro (Local)", "Edge TTS (Cloud)"
- Preview button — plays 3-second sample
- Voice ID input for custom ElevenLabs voices

---

---

# PHASE 11 — Urdu Support (SHARED)

**Branch:** `feat/phase-11-urdu-support`
**Both work on this branch** — coordinate before starting

### Qasim's part: Backend intent patterns

```
backend/api/services/command_service.py   ← add Urdu keyword patterns to INTENT_PATTERNS
backend/cognition/personality.py          ← add Urdu language support to tone system
```

```python
# In INTENT_PATTERNS — add Urdu variants alongside English
INTENT_PATTERNS = [
    # existing...
    (("کھولو", "شروع کرو", "چلاؤ"), ("system", "open_app")),    # open/start/run
    (("بند کرو", "بند"), ("system", "close_app")),               # close
    (("یاد رکھو", "نوٹ کرو"), ("memory", "remember")),           # remember/note
]
```

### Tayyab's part: Whisper language config

```
backend/voice/whisper_service.py   ← add language detection + Urdu transcription
```

```python
# In whisper_service.py — add language parameter support
def transcribe(audio_path: str, language: str = "auto") -> str:
    """
    language: "auto" for auto-detect, "ur" for Urdu-only, "en" for English-only
    """
    segments, info = self.model.transcribe(
        audio_path,
        language=None if language == "auto" else language,
        task="transcribe",
    )
    ...
```

Add a language setting in `backend/.env`:
```
WHISPER_LANGUAGE=auto    # auto | en | ur | en+ur
```/


---

---

# API CONTRACTS (Tayyab reads, Qasim writes)

These are the endpoints Tayyab's frontend depends on from Qasim's backend. Qasim must not change the response shape after they're agreed on.

| Endpoint | Owner | Response shape |
|---|---|---|
| `GET /api/v1/cognition/state` | Qasim Phase 0 | `{ attention, mood_bias, active_goal, current_task, last_user_emotion, active_ui_mode, turn_count }` |
| `GET /api/v1/voice/emotion` | Tayyab Phase 2 | `{ emotion, confidence, timestamp }` |
| `GET /api/v1/environment/status` | Tayyab Phase 5 | `{ cpu_percent, ram_percent, battery_percent, active_window }` |
| `GET /api/v1/cognition/goals` | Qasim Phase 3 | `[ { id, description, priority, status } ]` |

Tayyab writes `last_user_emotion` into cognitive_state via internal function call (not HTTP).
Qasim writes `active_ui_mode` into cognitive_state based on goal/mood logic.

---

---

# TECHNOLOGY REFERENCE

## Qasim — packages to install

```bash
cd backend && source .venv/bin/activate
pip install chromadb sentence-transformers ollama
# Ollama binary:
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b
```

## Tayyab — packages to install

```bash
cd backend && source .venv/bin/activate
pip install librosa soundfile scipy elevenlabs psutil

# xdotool for active window detection (WSL2):
sudo apt-get install xdotool

# Frontend — no new packages needed (Framer Motion + Tailwind already installed)
```

---

## File Ownership Quick Reference

| File | Owner | Phase |
|---|---|---|
| `backend/cognition/` (entire folder) | Qasim | Phase 0 |
| `backend/src/ai_operator/agents/focus_agent.py` | Qasim | Phase 7 |
| `backend/src/ai_operator/agents/memory_agent.py` | Qasim | Phase 7 |
| `backend/src/ai_operator/agents/sentinel_agent.py` | Qasim | Phase 7 |
| `backend/src/ai_operator/agents/planner_agent.py` | Qasim | Phase 7 |
| `backend/src/ai_operator/agents/dev_agent.py` | Tayyab | Phase 8 |
| `backend/voice/emotion_detector.py` | Tayyab | Phase 2 |
| `backend/voice/response_generator.py` | Qasim | Phase 9 |
| `backend/api/routers/environment.py` | Tayyab | Phase 5 |
| `backend/api/routers/voice.py` | Tayyab | Phase 2, 10 |
| `backend/voice/tts_service.py` | Tayyab | Phase 10 |
| `backend/voice/whisper_service.py` | Tayyab | Phase 2, 11 |
| `web/src/components/ambient/` | Tayyab | Phase 4 |
| `web/src/contexts/UIModeContext.tsx` | Tayyab | Phase 6 |
| `web/src/components/code/` | Tayyab | Phase 8 |

---

## Conflict Prevention Rules

1. **Qasim never touches** `backend/api/routers/voice.py`, `backend/voice/tts_service.py`, `web/src/`
2. **Tayyab never touches** `backend/cognition/`, `backend/src/ai_operator/agents/` (except dev_agent.py), `backend/voice/response_generator.py`
3. **Shared files** (`command_service.py`, `main.py`, `whisper_service.py`) — coordinate changes in PR description before merging
4. **When in doubt:** open a PR and ask the other person to review before merging
