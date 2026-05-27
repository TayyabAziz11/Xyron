# Xyron Brain Architecture

> Local-first autonomous AI operating intelligence — no cloud, no paid APIs.

---

## Overview

The Xyron Brain is the semantic decision layer that sits between raw voice transcripts
and the existing tool/response pipeline. It adds:

- **Semantic understanding** instead of keyword matching
- **Specialized agents** for each domain
- **Persistent brain state** across sessions
- **Identity policy** (PUBLIC/TECHNICAL/DEBUG modes)
- **Autonomous planning** with risk-gated execution
- **Long-term memory** with ChromaDB semantic recall
- **Instant responses** < 500 ms to first audio

The brain **wraps** the existing system safely — it does not replace any existing routing.

---

## Architecture Diagram

```
Voice Input (STT)
      │
      ▼
  Normalizer (normalizer.py)
      │
      ▼
 BrainOrchestrator.orchestrate()          ← brain/orchestrator.py
      │
      ├─ STOP / CLARIFY / INTERRUPT ──────► base Orchestrator (existing)
      │
      ├─ SemanticUnderstanding.parse()    ← brain/semantic_understanding.py
      │      Tier 1: Fast rules (regex, <1ms)
      │      Tier 2: nomic-embed-text + ChromaDB (~20ms)
      │      Tier 3: llama3.2:3b JSON judge (only if conf < 0.70)
      │
      ├─ [route = intro]    → self_intro_engine.generate()   ← identity_policy filtered
      │
      ├─ [route = emotional] → emotion pipeline (existing emotional guard)
      │
      ├─ [route = agent]    → BrainAgentRegistry.select()   ← agents/registry.py
      │                          VoiceAgent | SystemAgent | DevAgent
      │                          MemoryAgent | EmotionAgent | AutomationAgent
      │                          ScreenAgent | ResearchAgent | ChannelAgent(placeholder)
      │
      ├─ [route = tool]     → existing Orchestrator.decide() (preserved)
      │
      └─ [route = conv]     → LLM response generator (existing)

      ▼
  InstantResponse.get()                   ← brain/instant_response.py
      │   First audio chunk < 500ms
      ▼
  TTS Pipeline (Kokoro → edge-tts → pyttsx3)
```

---

## Brain State

**File:** `backend/brain/brain_state.py`  
**Persisted to:** `backend/data/brain/brain_state.json`

| Field | Type | Description |
|---|---|---|
| `current_mode` | str | "voice" / "dev" / "chill" |
| `active_goal` | str? | Current active goal |
| `operator_name` | str | "Tayyab" |
| `recent_topics` | list | Last 10 intent topics |
| `recent_upgrades` | list | Last 20 upgrades with timestamps |
| `active_agents` | list | Currently active agent IDs |
| `last_agent_used` | str? | Last dispatched agent |
| `current_emotion` | str | From emotion pipeline |
| `autonomy_level` | int | 0–4 (see below) |
| `identity_mode` | str | "PUBLIC" / "TECHNICAL" / "INTERNAL_DEBUG" |
| `total_commands` | int | All-time command count |
| `last_decision` | dict | Most recent BrainDecision |
| `confidence_history` | list | Last 50 confidence scores |

### Autonomy Levels

| Level | Label | Behaviour |
|---|---|---|
| 0 | Reactive | Only responds, never acts |
| 1 | Suggestive | Suggests but never executes |
| **2** | **Assisted** (default) | Acts on low-risk; confirms medium/high |
| 3 | Autonomous+Confirm | Acts autonomously; confirms only high-risk |
| 4 | High | Acts autonomously; minimal confirmation |

---

## Semantic Understanding

**File:** `backend/brain/semantic_understanding.py`

### Three-Tier Pipeline

**Tier 1 — Fast Rules** (`<1 ms`)
- 22 regex patterns covering all major intents
- Handles English, Roman Urdu, broken English, Xyron name variants
- Returns if confidence ≥ 0.80

**Tier 2 — Embedding Similarity** (`~20 ms`)
- `nomic-embed-text` via Ollama
- ChromaDB persistent vector store at `data/chroma/`
- Collection: `brain_intents` (built by `scripts/build_brain_intent_index.py`)
- Returns if confidence ≥ 0.72

**Tier 3 — LLM Judge** (`~500 ms`)
- `llama3.2:3b` with strict JSON schema
- Only invoked when Tiers 1+2 both score < 0.70
- Returns structured SemanticFrame

### SemanticFrame

```python
SemanticFrame(
    route="tool|agent|intro|emotional|conversation|clarify",
    intent="open_app|self_upgrade|intro_audience|work_mode|...",
    target="system_agent|emotion_agent|self_intro|brain|...",
    entities={"app": "chrome"},
    emotion_hint="neutral|warm_surprise|focused|...",
    confidence=0.95,
    requires_confirmation=False,
    reason="fast_rule",
    tier=1,
)
```

---

## Intent Categories (18)

| Intent | Route | Agent |
|---|---|---|
| `self_upgrade` | emotional | emotion_agent |
| `frustration` | emotional | emotion_agent |
| `ask_future_desire` | emotional | emotion_agent |
| `intro_short` | intro | self_intro |
| `intro_audience` | intro | self_intro |
| `intro_technical` | intro | self_intro |
| `open_app` | tool | system_agent |
| `file_action` | tool | system_agent |
| `system_status` | tool | system_agent |
| `dev_help` | agent | dev_agent |
| `work_mode` | agent | automation_agent |
| `chill_mode` | agent | automation_agent |
| `home_mode` | agent | automation_agent |
| `takeover_mode` | agent | automation_agent |
| `explain_capability` | conversation | brain |
| `automation_request` | agent | automation_agent |
| `memory_query` | agent | memory_agent |
| `screen_help` | agent | screen_agent |

---

## Agent System

**Base:** `backend/agents/base.py`  
**Registry:** `backend/agents/registry.py`

Every agent implements:
- `can_handle(frame, brain_state) → float` — 0.0–1.0 routing score
- `plan(frame, brain_state) → AgentPlan`
- `execute(plan, context) → AgentResult`
- `summarize_result(result) → str`

### Registered Agents

| ID | Name | Domain |
|---|---|---|
| `voice_agent` | Voice Agent | TTS/STT diagnostics, latency |
| `system_agent` | System Agent | Apps, files, volume, OS tools |
| `dev_agent` | Dev Agent | VS Code, coding, debugging |
| `memory_agent` | Memory Agent | Short/long-term recall |
| `emotion_agent` | Emotion Agent | Mood, personality, self-upgrade |
| `automation_agent` | Automation Agent | Work/chill/home/takeover routines |
| `screen_agent` | Screen Agent | Screenshot analysis, UI context |
| `research_agent` | Research Agent | Local knowledge (future: web) |
| `channel_agent` | Channel Agent | **PLACEHOLDER** — future social channels |

---

## Identity Policy

**File:** `backend/brain/identity_policy.py`

| Mode | Reveals | Use case |
|---|---|---|
| `PUBLIC` | Brand-safe language only | Demos, audience, users |
| `TECHNICAL` | Stack names allowed | Developers, builders |
| `INTERNAL_DEBUG` | Everything | Testing, debugging |

**Example — PUBLIC mode filtering:**
- `faster-whisper` → `voice understanding`
- `Kokoro` → `local voice synthesis`
- `Ollama` → `local AI models`
- `"I have limitations"` → `"I'm continuously evolving"`

The identity_policy filter is applied in `self_intro_engine.generate()` automatically.

---

## Capability Registry

**File:** `backend/brain/capability_registry.py`  
**Endpoint:** `GET /api/v1/brain/capabilities?mode=PUBLIC`

18 capabilities, each with `public_description`, `technical_description`, `status`, `agents[]`, `demo_value`.

Statuses: `active` (13) | `partial` (4) | `planned` (1)

---

## Memory System

**File:** `backend/brain/memory_system.py`

### 5 Memory Types

| Type | What's stored | Examples |
|---|---|---|
| `episodic` | What happened | "User asked to open Chrome at 3pm" |
| `semantic` | Facts, knowledge | "User is a software developer" |
| `procedural` | How to do things | "Work mode = open VS Code + terminal" |
| `relationship` | Preferences, corrections | "User prefers dark themes" |
| `project` | Milestones, upgrades | "Added brain semantic understanding layer" |

### Storage
- **SQLite** at `data/brain/memory.db` — structured records
- **ChromaDB** at `data/chroma/` — semantic embeddings (nomic-embed-text)
- Minimum importance threshold: `0.40` (low-value events are discarded)

---

## Emotion Curves

**File:** `backend/brain/emotion_curves.py`

Instead of one flat emotion for an entire response, curves define sentence-level arcs:

```python
# self_upgrade arc
[
    EmotionSegment("reaction",    "warm_surprise", speed=1.00, pause_ms=0),
    EmotionSegment("realization", "excited",       speed=1.06, pause_ms=80),
    EmotionSegment("body",        "warm",          speed=1.02, pause_ms=40),
    EmotionSegment("future",      "ambitious",     speed=1.04, pause_ms=60),
    EmotionSegment("closing",     "confident",     speed=1.00, pause_ms=50),
]
```

16 curves defined for all major intents. Falls back to `_default` (neutral flat).

---

## Instant Response Engine

**File:** `backend/brain/instant_response.py`

Returns a micro-reaction within 500ms of the brain decision:
- `ACK` — "On it.", "Got it.", "Sure."
- `EMOTIONAL_BURST` — "Oh wow.", "That's exciting."
- `THINKING` — "Give me a second.", "Let me check."
- `ACTION_CONFIRM` — "Opening it.", "Creating it."
- `INTRO_START` — "Alright, let me introduce myself properly."

Streamed as an `ack` event type before the main response begins.

---

## Safety Gate

**File:** `backend/brain/safety_gate.py`

### Risk Classification
- `low` — execute immediately
- `medium` — confirm if autonomy_level < 3
- `high` — always confirm (delete, send message, post online, run shell)

### Always High-Risk Actions
- `delete_file`, `delete_folder`
- `send_whatsapp`, `send_email`, `post_linkedin`, `post_instagram`
- `run_shell_command`

### Confirmation flow
1. Safety gate registers pending confirmation token (UUID)
2. Voice response: "This is a high-risk action: [action]. Say yes to confirm, or I'll cancel in 5 seconds."
3. User says "yes" → `safety_gate.confirm(token, True)`
4. Auto-reject after 5s timeout

---

## Autonomous Planning

**File:** `backend/brain/planner.py` (enhanced)

Plan schema:
```python
AgentPlan(
    goal="work_mode",
    steps=[
        PlanStep(0, "Open VS Code", tool="open_app", tool_args={"app": "code"}, risk="low"),
        PlanStep(1, "Open terminal", tool="open_app", tool_args={"app": "terminal"}, risk="low"),
        PlanStep(2, "Set focus mode", risk="low"),
    ],
    risk_level="low",
    requires_confirmation=False,
)
```

Autonomy gate:
- `low + autonomy ≥ 2` → execute
- `medium` → ask confirmation
- `high / destructive` → always confirm
- External posting/messaging → always confirm

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/brain/status` | Full brain state snapshot |
| `GET` | `/api/v1/brain/capabilities` | Capability registry (mode=PUBLIC/TECHNICAL) |
| `GET` | `/api/v1/brain/agents` | All registered agents |
| `GET` | `/api/v1/brain/memory/recent` | Recent memories (n=20, type=episodic\|…) |
| `POST` | `/api/v1/brain/memory/search` | Semantic memory search |
| `POST` | `/api/v1/brain/state` | Update brain state fields |
| `GET` | `/api/v1/brain/parse` | Debug: parse transcript through semantic layer |

---

## Building the Intent Index

Run once after setup or when `intent_examples.py` changes:

```bash
# Pull the embedding model (one time)
ollama pull nomic-embed-text

# Build the ChromaDB intent vector index
python3 backend/scripts/build_brain_intent_index.py
```

---

## Running Tests

```bash
python3 backend/scripts/test_brain_pipeline.py
```

Expected: 10/10 semantic cases + identity policy + brain state + capabilities + safety gate all pass.

---

## Future Channel Agents

The `ChannelAgent` is a registered placeholder. It returns `can_handle() = 0.0` (never selected).
When channel integrations are ready:
1. Create `agents/channel/whatsapp_agent.py` extending `BaseAgent`
2. Override `can_handle()` for WhatsApp-specific intents
3. Register via `brain_agent_registry.register(WhatsAppAgent())`
4. Add HITL approval gate for all outbound messages (always high-risk)

Planned channels: WhatsApp, Gmail, GitHub, LinkedIn, Instagram.

---

## File Map

```
backend/
├── brain/
│   ├── orchestrator.py        — BrainOrchestrator + existing Orchestrator
│   ├── brain_state.py         — Persistent brain state (JSON)
│   ├── identity_policy.py     — PUBLIC/TECHNICAL/INTERNAL_DEBUG filter
│   ├── capability_registry.py — 18 capabilities with descriptions
│   ├── semantic_understanding.py — 3-tier intent parser
│   ├── intent_examples.py     — 18 intent categories, 230+ examples
│   ├── instant_response.py    — First-audio ACK engine
│   ├── memory_system.py       — 5-type memory, SQLite+ChromaDB
│   ├── emotion_curves.py      — Sentence-level emotion arcs
│   ├── safety_gate.py         — Risk classification + confirmation
│   ├── planner.py             — Multi-step autonomous planning
│   └── memory_manager.py      — (existing)
├── agents/
│   ├── base.py                — BaseAgent, AgentPlan, AgentResult, AgentContext
│   └── registry.py            — 9 registered agents
├── api/routers/
│   └── brain.py               — 7 brain API endpoints
├── scripts/
│   ├── build_brain_intent_index.py  — ChromaDB index builder
│   └── test_brain_pipeline.py       — 10-case test suite
└── data/
    ├── brain/
    │   ├── brain_state.json   — Persistent state
    │   └── memory.db          — SQLite memory store
    └── chroma/                — ChromaDB vector stores
```

---

*Architecture documented 2026-05-17. Xyron v2.0 — Autonomous Brain System.*
