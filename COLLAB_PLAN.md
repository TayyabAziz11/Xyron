# Xyron Collaboration Architecture — Tayyab × Qasim

> **Single source of truth** for responsibilities, ownership, boundaries, integration contracts, and conflict prevention.
> Last updated: 2026-05-17

---

## 1. System Overview

Xyron is a local-first AI operating intelligence — not a chatbot, not keyword automation, not disconnected tools.  
It understands meaning, remembers context, routes agents, plans tasks, controls the system, responds emotionally, speaks naturally, operates proactively, and evolves over time.

**Two developers. One system. Clear boundaries.**

| Developer | Handle | Owns |
|---|---|---|
| Tayyab | A | Brain, cognition, memory, emotion, orchestration, agents, identity |
| Qasim | B | Tools, OS control, execution, system automation, environment, safety |

---

## 2. Layered Architecture

```
┌─────────────────────────────────────────────────┐
│                  INPUT LAYER                     │
│  Wake word · Voice input · Text · Future channels│
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│               PERCEPTION LAYER                   │
│  STT · Transcript normalization                  │
│  Environment awareness · Window awareness        │
│  Screen awareness                                │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│             UNDERSTANDING LAYER                  │
│  Semantic understanding · Language detection     │
│  Intent parsing · Entity extraction              │
│  Emotional detection                             │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐  ← TAYYAB OWNS EVERYTHING ABOVE AND BELOW
│                 BRAIN LAYER                      │
│  Orchestrator · Planning · Memory                │
│  Autonomy · Cognition · Goals                    │
│  Emotional intelligence · Agent routing          │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│                 AGENT LAYER                      │
│  VoiceAgent · SystemAgent · DevAgent             │
│  MemoryAgent · EmotionAgent · AutomationAgent    │
│  ScreenAgent · ResearchAgent · ChannelAgent      │
└──────────────────────┬──────────────────────────┘
                       │  ← shared/tool_contract.py is the ONLY bridge here
┌──────────────────────▼──────────────────────────┐  ← QASIM OWNS EVERYTHING BELOW
│               EXECUTION LAYER                    │
│  Tools · OS control · Automation                 │
│  Filesystem · Browser · App launching            │
│  System control · Safety wrappers               │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐  ← TAYYAB OWNS THIS
│               EXPRESSION LAYER                   │
│  Response generation · Emotional curves · TTS    │
│  UI state · Audience mode · Intro mode           │
└─────────────────────────────────────────────────┘
```

---

## 3. Ownership Boundaries

### Developer A — Tayyab

**Owns completely:**

```
backend/brain/
backend/cognition/
backend/memory/
backend/voice/prosody_planner.py
backend/voice/emotion_tts_mapper.py
backend/voice/emotion_audio_fx.py
backend/voice/pronunciation_engine.py
backend/voice/pronunciation_lexicon.py
backend/voice/response_generator.py
backend/voice/voice_energy.py
backend/voice/voice_personality.py
backend/voice/self_intro_engine.py   (if moved here)
backend/api/routers/brain.py         (create when needed)
backend/api/routers/cognition.py
backend/api/services/cognitive_state.py
backend/api/services/pipeline.py
backend/api/services/response_pipeline.py
backend/api/services/memory_service.py
backend/api/services/episodic_memory.py
backend/api/services/intent_router.py
backend/api/services/model_router.py
backend/api/services/normalizer.py
web/src/brain/
web/src/agents/
web/src/emotion/
web/src/hooks/useEmotionState.ts
web/src/state/emotionState.ts
```

**Conceptual ownership:**
- Semantic understanding engine
- Memory system (episodic, semantic, procedural, relationship, project)
- Emotional intelligence and mood state machine
- Orchestrator and agent routing
- Planner and autonomy levels
- Public identity and intro system
- Brain dashboard UI
- Response shaping and voice prosody
- Self-upgrade detection

---

### Developer B — Qasim

**Owns completely:**

```
backend/api/tools/system_tools.py
backend/api/tools/automation_tools.py
backend/api/tools/browser_tools.py
backend/api/tools/screen_tools.py
backend/api/tools/web_tools.py
backend/api/tools/registry.py
backend/api/tools/safety.py
backend/api/services/fs_index.py
backend/api/services/window_context.py
backend/api/services/screen_context_service.py
backend/api/services/exec_validator.py
backend/api/services/ps_session.py
docs/system_tools_audit.md           (create)
```

**Conceptual ownership:**
- Semantic app routing and fuzzy app matching
- All OS/filesystem/process/window executors
- Browser controller
- Safe shell executor and PowerShell bridge
- System diagnostics and environment metrics
- Tool performance and reliability
- Clipboard, screenshot, battery, network tools
- Startup app control
- Tool registry contracts and definitions

---

## 4. Shared Contracts

### The Only Bridge: `backend/shared/tool_contract.py`

Brain decides **WHAT**. Tools decide **HOW**.

All communication between brain/agents and execution layer flows through `ToolRequest` / `ToolResult`.  
**No agent may call a tool executor directly.**

```python
# See backend/shared/tool_contract.py for full implementation
```

### Shared read-only files (coordinate before editing):

| File | Read by | Written by |
|---|---|---|
| `backend/api/services/command_service.py` | Both | Coordinate via PR |
| `backend/api/main.py` | Both | Coordinate via PR |
| `CLAUDE.md` | Both | Either, via PR |
| `backend/shared/tool_contract.py` | Both | Both via PR only |

---

## 5. Integration Rules

1. **No direct imports across the boundary.** Brain code never imports from `backend/api/tools/`. It uses `ToolRequest` only.
2. **No tool executor makes LLM calls.** Tools are deterministic executors. If reasoning is needed, that happens in the brain before the `ToolRequest` is sent.
3. **No agent bypasses the orchestrator.** Agents receive tasks from the orchestrator and return results to it.
4. **Emotional pipeline is read-only for Qasim.** System tools may read `mood_state` from the cognitive snapshot but never write to it.
5. **Tool descriptions are Qasim's contract.** The `"description"` field in each tool's OpenAI definition is the semantic routing surface — Qasim owns it, Tayyab uses it for routing tuning via PR.
6. **PRs for shared files always.** Neither developer pushes directly to main. See CLAUDE.md for the branch workflow.

---

## 6. File Ownership Map

```
Xyron/
├── backend/
│   ├── brain/              [A] orchestrator, planner, memory_manager
│   ├── cognition/          [A] all cognitive modules
│   ├── memory/             [A] relationship_memory + future stores
│   ├── voice/
│   │   ├── emotion_*.py    [A]
│   │   ├── prosody_*.py    [A]
│   │   ├── pronunciation_* [A]
│   │   ├── response_*.py   [A]
│   │   ├── tts_service.py  [A]
│   │   ├── whisper_*.py    [A] (STT)
│   │   └── wake_word_*.py  [A]
│   ├── api/
│   │   ├── tools/          [B] all tool modules + registry
│   │   ├── routers/        [coordinate] brain/cognition=A, others=discuss
│   │   ├── services/
│   │   │   ├── command_service.py     [coordinate]
│   │   │   ├── intent_router.py       [A]
│   │   │   ├── model_router.py        [A]
│   │   │   ├── pipeline.py            [A]
│   │   │   ├── memory_service.py      [A]
│   │   │   ├── episodic_memory.py     [A]
│   │   │   ├── cognitive_state.py     [A]
│   │   │   ├── response_pipeline.py   [A]
│   │   │   ├── normalizer.py          [A]
│   │   │   ├── fs_index.py            [B]
│   │   │   ├── window_context.py      [B]
│   │   │   ├── screen_context_service.py [B]
│   │   │   ├── exec_validator.py      [B]
│   │   │   └── ps_session.py          [B]
│   │   ├── main.py         [coordinate]
│   │   └── config.py       [coordinate]
│   └── shared/             [both, PR only]
│       └── tool_contract.py
├── web/
│   ├── src/brain/          [A]
│   ├── src/agents/         [A]
│   ├── src/emotion/        [A]
│   ├── src/hooks/
│   │   └── useEmotionState.ts [A]
│   └── src/state/
│       └── emotionState.ts [A]
├── docs/
│   ├── xyron_dev_history.md    [both append]
│   └── system_tools_audit.md  [B]
├── COLLAB_PLAN.md          [both, PR only]
├── FIXES.md                [both append]
└── CLAUDE.md               [both, PR only]
```

---

## 7. Safe Edit Zones

**Tayyab can freely edit without coordinating:**
- Anything under `backend/brain/`, `backend/cognition/`, `backend/memory/`
- All `voice/emotion_*`, `voice/prosody_*`, `voice/pronunciation_*`
- `backend/api/routers/cognition.py`
- `backend/api/services/intent_router.py`, `model_router.py`, `pipeline.py`, `response_pipeline.py`, `memory_service.py`, `episodic_memory.py`
- All `web/src/brain/`, `web/src/agents/`, `web/src/emotion/`

**Qasim can freely edit without coordinating:**
- Anything under `backend/api/tools/`
- `backend/api/services/fs_index.py`, `window_context.py`, `screen_context_service.py`, `exec_validator.py`, `ps_session.py`
- `docs/system_tools_audit.md`

**Always coordinate (open PR, tag each other):**
- `backend/api/services/command_service.py`
- `backend/api/main.py`
- `backend/api/config.py`
- `backend/shared/tool_contract.py`
- `CLAUDE.md`, `COLLAB_PLAN.md`

---

## 8. Merge Conflict Prevention

### Branch naming

```
feat/brain-<description>       # Tayyab — brain work
feat/cognition-<description>   # Tayyab — cognition work
feat/voice-<description>       # Tayyab — voice/TTS work
fix/brain-<description>        # Tayyab — brain bugfix

feat/tools-<description>       # Qasim — tool work
feat/system-<description>      # Qasim — system/OS work
fix/tools-<description>        # Qasim — tool bugfix
```

### Conflict prevention rules

1. **Sync from main before every session:** `git checkout main && git pull && git checkout -b your-branch`
2. **Never hold a branch open >48h without merging.** Stale branches = conflicts.
3. **Touch shared files last.** Do all your isolated work first, coordinate changes to shared files in the final commit.
4. **One logical change per PR.** No mega-PRs that touch both ownership zones.
5. **If you need a file the other person owns** — open a GitHub issue, describe what you need, let the owner implement it or explicitly hand it off.
6. **`command_service.py` is the highest-risk file.** Both developers need it. Tag each other on any PR that touches it. Never edit it on the same day without coordinating.

---

## 9. Shared Interfaces

### CognitiveSnapshot (read-only for tools)

```python
# Written by: Tayyab (cognition layer)
# Read by: Qasim (tools may read for context injection)
{
    "mood_state": str,          # e.g. "FOCUSED"
    "intent": str,
    "entities": dict,
    "language": str,
    "autonomy_level": int,      # 0–4
    "active_project": str | None,
    "active_file": str | None,
    "code_mode": bool,
}
```

### ToolRequest / ToolResult

See `backend/shared/tool_contract.py`. This is the **only** interface between brain and execution.

### AgentMessage

```python
# Internal orchestrator ↔ agent communication
{
    "agent": str,           # e.g. "system_agent"
    "task": str,
    "context": dict,
    "autonomy_level": int,
    "source": str,          # "orchestrator"
}
```

---

## 10. Future Expansion Plan

| Component | Owner | Status | Notes |
|---|---|---|---|
| `backend/brain/semantic_understanding.py` | A | Build | llama3.2:3b fast path |
| `backend/brain/identity_policy.py` | A | Build | Public persona rules |
| `backend/brain/emotion_curves.py` | A | Build | Emotional arc over time |
| `backend/brain/instant_response.py` | A | Build | <300ms ack path |
| `backend/brain/long_term_memory.py` | A | Build | ChromaDB + SQLite |
| `backend/brain/autonomy.py` | A | Build | Levels 0–4 |
| `backend/agents/voice_agent.py` | A | Build | |
| `backend/agents/system_agent.py` | A+B | Build | A routes, B provides tools |
| `backend/agents/memory_agent.py` | A | Build | |
| `backend/agents/emotion_agent.py` | A | Build | |
| `backend/agents/screen_agent.py` | A+B | Build | A routes, B provides screen tools |
| `backend/agents/research_agent.py` | A | Build | |
| `backend/agents/channel_agent.py` | A | Placeholder | Future channels |
| `docs/system_tools_audit.md` | B | Build | Audit all current tools |
| `backend/api/tools/semantic_router.py` | B | Build | Fuzzy app matching |
| `backend/api/tools/window_manager.py` | B | Build | |
| `backend/api/tools/process_manager.py` | B | Build | |
| `web/src/brain/BrainDashboard.tsx` | A | Build | |
| `logs/xyron.jsonl` | Both | Build | Structured observability log |

---

## 11. Performance Rules

Both developers must respect these targets on every PR:

| Operation | Target |
|---|---|
| Wake response | < 300 ms |
| Intent routing decision | < 120 ms |
| Tool routing to execution | < 80 ms |
| First audio chunk | < 500 ms |

**Non-negotiable rules:**
- No blocking I/O on the request path. Use `async/await` everywhere.
- No synchronous LLM calls that hold the voice pipeline.
- No giant in-process imports at request time — lazy-load heavy modules.
- Stream first, batch later. First chunk > complete response.
- Cache hot data: embeddings, tool definitions, cognitive snapshots.
- Preload models at startup, not on first request.

---

## 12. Logging Standards

**All significant events write to `logs/xyron.jsonl`** (newline-delimited JSON).

Required fields on every log entry:

```json
{
  "ts": "2026-05-17T07:10:35Z",
  "event": "tool_executed",
  "agent": "system_agent",
  "tool": "date_time",
  "latency_ms": 42,
  "success": true,
  "autonomy_level": 1,
  "mood_state": "FOCUSED"
}
```

Track at minimum:
- Intent routing decisions (which path, confidence, latency)
- Tool dispatch and result (tool name, latency, success/failure)
- Memory read/write operations
- Model invocations (which model, token count, latency)
- TTS synthesis (engine, latency, char count)
- Safety confirmations (tool, risk level, approved/rejected)
- Orchestration decisions (which agent, why)

**Do not log:** raw user transcripts (privacy), API keys, file contents.

---

## 13. Testing Standards

### Backend

```bash
cd backend && source .venv/bin/activate
pytest tests/                        # full suite
pytest tests/ -k "tool"              # tool-specific
pytest tests/ -k "brain or cognition" # brain-specific
```

- Every new tool executor needs a test in `tests/test_tools.py`
- Every new brain module needs a test in `tests/test_brain.py`
- Latency assertions: critical path tests must assert `latency_ms < target`
- No mocking the execution layer in brain tests (use real tool_contract)

### Frontend

```bash
cd web && npm run type-check
```

- Type errors = PR blocked
- `useEmotionState.ts` and state files must have TypeScript interfaces

### Pre-PR checklist

- [ ] `pytest tests/` passes
- [ ] `npm run type-check` passes (if web changes)
- [ ] No new `console.log` debug statements
- [ ] No hardcoded `localhost:8000` — use `API_BASE`
- [ ] Latency targets respected
- [ ] `docs/xyron_dev_history.md` updated

---

## 14. Integration Flow

### Normal voice request flow

```
User speaks
  → wake_word_service.py detects wake word                [A]
  → whisper_service.py transcribes                         [A]
  → normalizer.py cleans transcript                        [A]
  → pipeline.py entry point                               [A]
      → cognition layer: language + intent + emotion       [A]
      → orchestrator.py decides agent                      [A]
      → agent receives AgentMessage                        [A]
      → agent builds ToolRequest via tool_contract.py      [A/B shared]
      → tool executor runs                                  [B]
      → ToolResult returned                                [B→A]
      → response_pipeline.py shapes response               [A]
      → prosody_planner.py plans TTS chunks                [A]
      → tts_service.py / voice.py synthesizes              [A]
      → audio plays + UI orb updates                       [A]
```

### High-risk tool flow (autonomy gate)

```
Agent builds ToolRequest with risk_level="high"
  → orchestrator checks autonomy_level
  → if autonomy_level < 3: write to Pending_Approval/
  → halt execution
  → dashboard notifies user
  → user approves → move to Approved/ → tool executes
  → user rejects → ToolResult(success=False, message="rejected by user")
```

### Memory write flow

```
Any agent or tool result →
  memory_service.py extracts facts                         [A]
  episodic_memory.py stores turn in SQLite                 [A]
  if significant: ChromaDB vector store                    [A]
  relationship_memory.py updates if people involved        [A]
```

---

## Model Usage Policy

| Model | Use for | Never use for |
|---|---|---|
| `llama3.2:3b` | Fast routing, confirmations, short replies, emotional classification | Deep planning, long narration |
| `mistral:7b` | Planning, deeper reasoning, long-form intelligence, audience mode | Quick reactions (too slow) |
| `deepseek-coder` | Dev mode only | General conversation |
| `nomic-embed-text` | Embeddings only | Generation |

**No other models.** No LangChain. No AutoGen.

---

## Memory System Design

Five memory types — all owned by Tayyab:

| Type | Storage | Contains |
|---|---|---|
| Episodic | SQLite | Every turn: text, tool, success, timestamp |
| Semantic | ChromaDB | Facts, preferences, habits — vector searchable |
| Procedural | SQLite | Workflows, multi-step sequences |
| Relationship | SQLite | People, communication patterns |
| Project | SQLite | Active projects, files, goals |

**Store:** habits, upgrades, preferences, workflows, corrections, emotional events.  
**Do not store:** raw noise, every utterance, system logs.

---

## Autonomy Levels

| Level | Name | Behavior |
|---|---|---|
| 0 | Reactive | Only responds when spoken to |
| 1 | Suggestive | Offers suggestions, waits for approval |
| 2 | Assisted autonomous | Executes low-risk actions, confirms medium |
| 3 | Autonomous with confirmation | Executes all but destructive actions |
| 4 | Guarded autonomous | Full autonomy, safety wrappers still active |

**Destructive actions always require confirmation regardless of autonomy level.**

---

## Public Identity Rules

When Xyron speaks publicly or introduces itself:

- Sound cinematic, intelligent, polished, local-first, advanced.
- Never expose: weaknesses, missing memory, limitations, raw dependencies, fallback systems.
- Never say "I don't know", "I can't", "I'm just an AI".
- Public intros feel like a premium AI operating system, not a chatbot.

The intro engine (`self_intro_engine.py`, `intro_context_builder.py`) enforces this — **Qasim does not touch these files.**

---

*To update this document: open a PR, tag both developers, merge only with both approvals.*
