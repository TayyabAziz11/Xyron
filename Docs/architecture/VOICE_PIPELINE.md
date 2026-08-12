# Voice Pipeline

## Purpose

Speech in, speech out: wake word detection → STT → intent routing → tool
execution / agent dispatch → response generation → TTS, over a persistent
WebSocket session.

## Architecture

```
backend/voice/ (22 files, ~4400 lines) — STT/TTS/wake-word/prosody engines
 ├─ whisper_service.py    — faster-whisper, lazy-loaded, fast+accurate models
 ├─ tts_service.py        — local TTS (pyttsx3/espeak-ng)
 └─ (kokoro/edge-tts paths live in api/routers/voice.py, not here)

api/routers/voice.py     (5026 lines) — REST voice endpoints: synthesize,
                           STT, response generation (respond_stream/
                           _generate — see Code Quality note below)
api/routers/voice_ws.py  (3808 lines) — the WebSocket session lifecycle
                           (ws_session, process_utterance — see below),
                           wake-word tier dispatch, confirmation/
                           disambiguation gates, post-tool-execution
                           context-update fan-out
```

## Session lifecycle (high level)

```
WS connect → wake word listening → STT (fast pass, then accurate pass)
  → transcript → context_resolver.resolve() [pronoun substitution]
  → Tier 0-N routing (keyword -> semantic classifier -> AI intent -> agent dispatch)
  → tool execution / single-agent or coordinator dispatch (see PLANNING_ENGINE.md)
  → post-tool-execution fan-out: active_context, context_stack, world_state,
    context_memory all updated in parallel (asyncio.create_task, fire-and-forget)
  → response generation → TTS → audio streamed back over the WS
```

Pending-confirmation and disambiguation-match state live in
`_session_state`/`memory_service` and are checked *before* normal routing
each turn (Tier 0d in the existing numbering) — see FILESYSTEM.md for how
`smart_open`'s medium/low-confidence resolutions plug into this exact gate.

## Code quality note (platform-stabilization audit finding)

`voice.py` and `voice_ws.py` are the two largest files in the codebase
(5026 and 3808 lines) and contain the four largest single functions found
anywhere in the audit: `ws_session()` (voice_ws.py, **3302 lines**),
`respond_stream()` (voice.py, 2431 lines), its `_generate()` helper (voice.py,
2379 lines, closure-nested), and `process_utterance()` (voice_ws.py, 2255
lines) — roughly 10,000 lines of function body across four functions in the
two hottest files in the voice pipeline. This is the single largest code-
quality finding in the codebase. See TECHNICAL_DEBT.md for why this wasn't
refactored during this pass (risk vs. reward for a live, latency-sensitive,
heavily-tuned system) and what a safe extraction would look like.

## Performance-sensitive design decisions already in place (do not undo)

These are documented here specifically so future work doesn't
accidentally regress them:

- **No eager Chrome warmup** — `browser_workspace.get_or_create_page()`
  launches Chrome on first real use, never at boot or on a background
  timer. Perception Engine's observation loop respects this (checks
  `is_healthy` before ever touching the browser — see PERCEPTION_ENGINE.md).
- **Immediate-ack decoupled from STT/classification** — an early filler
  response can play while STT/intent classification is still running.
- **Progress-update deduplication** — `coordinator_agent.py`'s
  `send_progress` skips identical `(message, pct)` pairs to avoid flooding
  the WS during multi-agent workflow polling.
- **Warm PowerShell session** (`ps_session.py`) for anything that needs
  Windows-side state — cold `powershell.exe` spawns cost 400-800ms
  (measured, sometimes 2-3s under load); the warm session costs ~20-90ms.
  24 files still bypass this and spawn per-call — see TECHNICAL_DEBT.md.

## Extension points

- New wake-word/STT tier: `voice_ws.py`'s tiered dispatch (Tier 0a-0g
  documented inline via log prefixes like `[TIER_0D]`).
- New response-generation backend: `voice/response_generator.py` tries
  OpenAI first, falls back to per-agent template strings.
- New TTS voice/backend: `api/routers/voice.py`'s `_get_kokoro`/
  `_kokoro_to_wav` (local) vs `voice/tts_service.py` (pyttsx3) vs
  `edge-tts` (cloud fallback) — three backends already exist; prefer
  extending one of these over adding a fourth.
