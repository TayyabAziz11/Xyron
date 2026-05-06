# Xyron Codebase Technical Audit
**Date:** 2026-04-27  
**Scope:** Full read-only audit — voice pipeline, LLM usage, routing, memory, stack versions, known bugs, performance

---

## 1. VOICE PIPELINE — Full Flow from Mic to Response

### Audio Capture
- **Library:** `sounddevice` (Python) at the desktop/CLI layer; the web/Electron frontends use the browser's **MediaRecorder API** (WebM/Opus by default)
- **Sample rate:** 16 kHz, mono (required by Whisper)
- **VAD (Voice Activity Detection):** Two independent implementations
  - **Backend CLI** (`audio_utils.py`): RMS energy threshold (`0.01`), 100ms chunks, stops after 1.2s of silence, max 15s recording
  - **Frontend** (`voice-core.ts` `VAD` object): Calibration over 25 frames, threshold multiplier 4.5×, 750ms silence = end of speech, 6s no-speech timeout, 30s max total

### Whisper — Which Model, What Size, When Loaded
There are **two separate Whisper model instances** that serve different code paths:

| Instance | Location | Model | Device | Quantization | When Loaded |
|---|---|---|---|---|---|
| `_local_whisper_model` | `routers/voice.py:83` | `base` | CPU | int8 | Background thread at startup (`main.py:59-62`) — pre-warmed |
| `_model` | `voice/whisper_service.py:12` | `base` | CPU | int8 | Lazy on first CLI/recorder use |

The startup pre-warm (`main.py`) correctly pre-loads the HTTP route's model. The standalone CLI/recorder path (`voice/whisper_service.py`) still cold-starts on first use (~3-8 seconds to load the `base` model).

### STT Step-by-Step (HTTP `/api/v1/voice/transcribe`)

```
Browser records WebM audio via MediaRecorder
    ↓
POST /api/v1/voice/transcribe (multipart upload)
    ↓
Audio < 4000 bytes? → Drop silently (return empty text)
    ↓
1. Try OpenAI Whisper API (whisper-1 model)
   - Write to temp file with correct extension (.webm/.mp3/.ogg/.wav)
   - Call client.audio.transcriptions.create(model="whisper-1", response_format="verbose_json")
   - Accept language only if "en" or "ur"; any other → force "en"
   - On success → return {text, language, engine: "openai"}
   - On BadRequestError with "too_short" → return empty text (treated as silence)
    ↓
2. Fallback: faster-whisper local model (base, CPU, int8)
   - Write to temp file
   - model.transcribe(str(tmp_path), beam_size=5, vad_filter=True, min_silence_duration_ms=300)
   - Return {text, language, engine: "local"}
    ↓
3. Final fallback: return {text: "", engine: "none"} — never crashes
```

### TTS — Which Engine, Which Voice

**Primary (OpenAI TTS, used when API key present):**
- **Endpoint:** `/api/v1/voice/synthesize` and `/api/v1/voice/synthesize-stream`
- **Model:** `tts-1` (real-time, lower latency)
- **Default voice:** `nova` (configurable per request; profile-mapped voices: work→`onyx`, chill→`shimmer`, focus→`echo`, professional→`alloy`)
- **Format:** MP3 via streaming; non-streamed returns full `audio/mpeg` response
- **Streaming:** `AsyncOpenAI` with `with_streaming_response.create()` — bytes piped chunk-by-chunk to frontend (MediaSource API)
- **Speed:** Clamped to OpenAI's range `[0.25, 4.0]`, default 1.0

**Fallback (pyttsx3/espeak-ng):**
- **File:** `voice/tts_service.py`
- **Engine:** `pyttsx3` wrapping `espeak-ng` on Linux
- **Rate:** 165 WPM; Volume: 0.9; picks first English voice from system
- **Format:** WAV (synchronous, blocking — see Performance section)
- **Thread safety:** Protected by a global `threading.Lock()`

### Where the Delay Happens (Voice Cycle Breakdown)

```
1. Recording              ~1-4s (user speaking)
2. Upload to backend      ~50-100ms (local network)
3. OpenAI STT API         ~800ms-2s (depends on audio length)
4. Intent routing         ~0ms (regex) to ~500ms (if GPT tool calling)
5. Tool execution         ~0ms (local tools) to ~5s (PowerShell/network)
6. GPT streaming (gpt-4o-mini) — first token ~300-500ms
7. TTS streaming start    ~150-300ms after first chunk
─────────────────────────────────────────────────────────────────
Typical total latency: 2-5 seconds from end of speech to audio response
```

The **single biggest delay source** is OpenAI STT API (step 3) + GPT first token (step 6) running sequentially. There is no parallelism between STT completion and LLM warm-up.

---

## 2. LLM USAGE — All OpenAI Model Calls

All calls use `gpt-4o-mini`. There is **no gpt-4o or Claude** call anywhere in the codebase.

| Call Site | File | Function | Model | Purpose |
|---|---|---|---|---|
| Intent classification (AI) | `command_service.py:81` | `classify_intent_ai()` | gpt-4o-mini | Tool name + params from tool definitions |
| Command response generation | `response_generator.py:108` | `_openai_spoken_response()` | gpt-4o-mini | Natural spoken reply (max 80 tokens) |
| English enforcement re-request | `response_generator.py:126` | `_openai_spoken_response()` | gpt-4o-mini | Re-request if non-English detected (second call) |
| General skill answer | `command_service.py:274` | `_run_general_skill()` | gpt-4o-mini (via ContentGenerator) | Answer general queries (max 150 tokens) |
| Email draft generation | `command_service.py:162` | `_run_email_skill()` | gpt-4o-mini | Write email body (max 400 tokens) |
| LinkedIn post generation | `command_service.py:194` | `_run_linkedin_skill()` | gpt-4o-mini | Write LinkedIn content |
| Reporting/summary | `command_service.py:234` | `_run_reporting_skill()` | gpt-4o-mini | Activity summary (max 200 tokens) |
| Voice streaming response | `routers/voice.py:1574` | `respond_stream/_generate()` | gpt-4o-mini | Main voice response (streaming, max not set) |
| Conversation bypass | `routers/voice.py:1590` | `_generate()` | gpt-4o-mini | Casual chat replies (max 80 tokens) |
| Email param extraction | `routers/voice.py:~2420` | `_generate()` | gpt-4o-mini | Extract to/subject/body from text (max 150 tokens) |
| Calendar event extraction | `routers/voice.py:~2450` | `_generate()` | gpt-4o-mini | Extract event fields (max 150 tokens) |
| Meeting summary | `services/meeting_service.py` | `summarize()` | gpt-4o-mini | Summarize meeting transcript |

### Redundant / Stacked API Calls

1. **Double-call on non-English detection:** If GPT responds in non-English, `response_generator.py` makes a *second* `gpt-4o-mini` call with forced English. This can happen on every voice turn when the user speaks Urdu/Roman Urdu — doubling TTS generation latency.

2. **Command service vs. voice router duplication:** When text enters through `/respond-stream` (main voice path), the command service's `classify_intent_ai()` is **NOT called** — the voice router does its own routing. But when text enters through `/api/v1/commands/submit`, the command service calls GPT for tool selection AND then calls GPT again for response generation. So the same command processed through two different paths can make different numbers of API calls.

3. **New OpenAI client per call:** Every call to `_openai_spoken_response()` instantiates `OpenAI(api_key=key)` fresh. No connection reuse. Minor overhead but avoidable.

---

## 3. INTENT ROUTING — How Commands Get Understood

### Architecture: Three Completely Separate Routing Systems

There are **three independent routers** that do not share state:

| Router | File | Used By | Method |
|---|---|---|---|
| `INTENT_PATTERNS` keyword list | `command_service.py:25-56` | `/commands` endpoint | `any(kw in text_lower for kw in keywords)` |
| Voice.py regex layers | `routers/voice.py:~1900-2500` | `/voice/respond-stream` | 15+ layers of compiled regexes |
| `IntentRouter` 4-tier | `services/intent_router.py` | Injected inside voice.py's layer -1 | Cache → Regex → Semantic → Fall-through |

### Voice Routing Layer Order (respond-stream, first match wins)

```
Layer -1:  Conversation bypass (_is_pure_conversation)    → gpt-4o-mini casual
Layer 0:   "remember that X"                              → memory store
Layer 0b:  "what do you remember"                         → memory read
Layer 0c:  Personality change ("be more casual")          → memory store
Layer 0d:  Screen context injection                        → augment system prompt
Layer 0e:  Chill mode trigger                              → open YouTube+Netflix tabs
Layer 0e2: Chill follow-up                                 → recommendations
Layer 0e3: Profile switch ("switch to boss mode")          → voice change
Layer 0e4: Morning mode ("good morning")                   → weather + calendar
Layer 0e5: Jarvis home mode ("I'm home")                   → system health greeting
Layer 0e6: Entertainment ("play something funny")          → YouTube search URL
Layer 0e7: Shutdown/restart with confirmation gate         → system control
Layer 0e8: Sleep/hibernate/lock/screenshot                 → system tools

[IntentRouter tier 1+2 shortcut runs here — cache + regex]

Layer -1:  WiFi panel, play media, named folder/file       → smart_open / open_wifi_panel
Layer 0:   Workflow trigger                                 → automation_workflow_service
Layer 0.3: Desktop type                                    → desktop_type
Layer 0.4: Desktop hotkey                                  → desktop_hotkey
Layer 0.45: Scroll                                         → desktop_scroll
Layer 0.5: "open this folder" (last action)                → open_directory
Layer 0.9: Startup/disk/process management                 → system tools
Layer 0.95: play media / named file/folder                 → smart_open
Layer 1:   "open/launch/start X" (open command)            → open_application / smart_open
Layer 1b:  Create folder                                   → create_folder
Layer 1c:  Create subfolders                               → create_subfolders
Layer 1d:  Browser navigate                                → browser_navigate
Layer 1e:  Browser click                                   → browser_click
Layer 1z:  disk_usage, date_time, battery, power_plan      → system tools (HIGH PRIORITY)
Layer 2:   System health keywords                          → system_health
Layer 3:   System info keywords                            → system_info
Layer 4:   Short follow-up phrase                          → replay last tool
Layer 5:   Explicit search prefix                          → search_web
Layer 5a0: Volume, IP, speed test                          → system tools
Layer 5a:  Wikipedia pattern (_WIKI_RE match)              → wiki_summary
Layer 5b:  Clipboard read/write                            → clipboard tools
Layer 5c:  Screen reading                                  → read_screen
Layer 5d:  Type text                                       → type_text
Layer 5e:  Window control                                  → min/max/close/switch
Layer 5f:  Reminder creation                               → reminders API
Layer 5g:  Gmail read/send                                 → Gmail tools
Layer 5h:  Calendar read/create                            → calendar tools
[No match] → pure gpt-4o-mini streaming response
```

### Bug: "Who built you" routes wrong sometimes

**Root cause:** The IDENTITY pattern check in `voice-core.ts` (line 94) intercepts "who built you" queries **on the client side** and returns "I was built by Tayyab Aziz" before any API call. This works correctly for the web frontend.

**When it fails:** If the text goes through the `/api/v1/commands/submit` endpoint (not respond-stream), it hits `_run_general_skill()` in `command_service.py` with system prompt: `"You are Xyron — a professional AI voice assistant for business productivity."` — no mention of who built it. GPT then answers from its training data ("I was created by Anthropic" or "I'm built on OpenAI's GPT").

**Evidence:** `command_service.py:276-284` — `_run_general_skill()` has no identity enforcement system prompt.

### Bug: "Play song on YouTube" doesn't play

**Root cause:** `_exec_search_youtube()` in `web_tools.py:47-58` returns:
```python
url = f"https://youtube.com/search?q={urllib.parse.quote(query)}"
return ToolResult(action_url=url, ...)
```
This opens a **YouTube search results page**, not the video player. The frontend (`useVoiceSession.ts:735`) does `window.open(actionUrl, '_blank', 'noopener')` which opens the search page. There is **no mechanism** to click the first result or start autoplay.

For the dedicated `youtubeQuery` path in `useVoiceSession.ts:1085-1109`, the code opens `youtube.com/search?q=...` as well. There's no YouTube Data API call, no video ID selection, and no `&autoplay=1` parameter (which wouldn't work on search pages anyway).

### Bug: Battery/system query sometimes goes to Wikipedia

**Root cause is historical** (now mostly fixed). The IntentRouter tier 2 includes a battery regex at `intent_router.py:170-171`:
```python
add(r'\b(?:battery|how\s+much\s+(?:battery|charge|power))\b', "get_battery_status")
```
And voice.py's Layer 1z also has `_BATTERY_RE`. Both should catch battery queries before they reach Layer 5a (Wikipedia). The Wikipedia guard `_WIKI_EXCLUDE_RE` also explicitly blocks `battery|charge|charging`.

**Remaining risk:** The IntentRouter tier 3 (sentence-transformer semantic classifier) loads asynchronously in a background thread. If a voice command arrives during the ~5-15s model loading window, tier 3 returns `None` and the query falls to tier 4. If the battery regex in Layer 1z also fails to match an unusual phrasing (e.g. "how's my laptop power?"), and `_WIKI_RE` matches it as a "what is" pattern, it could hit Wikipedia. The `_WIKI_EXCLUDE_RE` guard at `voice.py` contains "battery|charge|charging" which should prevent this — **but only if the guard is checked**. The code correctly checks: `if _WIKI_EXCLUDE_RE.search(text): return None` in `_extract_wiki_topic()`.

---

## 4. MEMORY SYSTEM — Full Picture

### What Persists vs. What is Lost on Restart

| Storage | Mechanism | Location | Persists? | Notes |
|---|---|---|---|---|
| User long-term facts | JSON file | `~/.ai-operator/memory.json` | **YES** | name, profession, location, interests, email, preferences; max 100 facts |
| Conversation history (episodic) | SQLite | `~/.ai-operator/episodes.db` | **YES** | Every turn with timestamp, tool_name, success |
| Tool usage patterns | SQLite (same DB) | `~/.ai-operator/episodes.db` | **YES** | Tool × hour frequency table for habit detection |
| Short-term session turns | In-memory `deque(maxlen=40)` | RAM | **NO** | Keyed by session_id; cleared on restart |
| Command queue/history | In-memory `OrderedDict(max=200)` | RAM | **NO** | Intentional for v1 |
| Last executed action | In-memory dict | RAM | **NO** | Used for "do it again", "open it" follow-ups |
| Personality style | JSON file (via `memory.json`) | `~/.ai-operator/memory.json` | **YES** | Stored as `personality_style` key |

### Memory Gaps

1. **Sessions reset silently:** The `session_id` is generated fresh per browser tab session. After restart, the episodic DB still has old turns but `memory_service._sessions` is empty, so the deque-based short-term history is always empty on restart. Only the last 5 turns from SQLite are injected into GPT context (via `episodic_memory.conversation_context(session_id, n=5)`).

2. **Multi-session isolation:** Two browser tabs have different `session_id`s, so short-term memory doesn't cross sessions. The long-term facts are shared globally.

3. **No automatic fact expiry:** Facts in `memory.json` are never expired. If the FIFO limit (100) is reached, oldest facts are silently dropped — including potentially more important ones.

4. **Last action lost on restart:** "Open it" / "create it in X" context (which relies on `memory_service._last_action`) is gone on restart.

### Auto-Extraction Rules (from `memory_service.py:187-284`)

Regex-based extraction runs on every user utterance:
- Name: `"my name is X"`, `"mera naam X hai"`, `"main X hun"`
- Profession/employer, location, work schedule, interests, email
- Last mentioned contact (for email/message targeting)
- Preferences (`"I prefer/always use/like to use X"`)

---

## 5. FULL STACK — All Versions

### Backend (Python)

| Component | Version (requirement) |
|---|---|
| Python | ≥ 3.9 |
| FastAPI | ≥ 0.115.0 |
| uvicorn | ≥ 0.32.0 (with standard extras) |
| pydantic | ≥ 2.9.0 |
| pydantic-settings | ≥ 2.6.0 |
| openai SDK | ≥ 1.0.0 (installed: 2.32.0 per CLAUDE.md) |
| faster-whisper | ≥ 1.1.0 |
| sounddevice | ≥ 0.5.1 |
| numpy | ≥ 1.26.0 |
| scipy | ≥ 1.13.0 |
| pyttsx3 | ≥ 2.90 |
| psutil | ≥ 5.9.0 |
| httpx | ≥ 0.27.0 |
| playwright | ≥ 1.44.0 |
| sentence-transformers | ≥ 3.0.0 |
| Whisper model | `base` (74MB, ~6x real-time speed on CPU with int8) |
| Sentence-transformer model | `all-MiniLM-L6-v2` (~22MB, ~80ms inference) |

### Web Dashboard

| Component | Version |
|---|---|
| Next.js | 15.5.14 (App Router) |
| React | 19.0.0 |
| React DOM | 19.0.0 |
| Framer Motion | ^12.38.0 |
| Tailwind CSS | ^3.4.17 |
| TypeScript | ^5.7.3 |
| Lucide React | ^0.511.0 |
| Port | 3001 |

### Desktop App (Electron)

| Component | Version |
|---|---|
| Electron | ^36.0.0 |
| React | ^18.3.1 |
| electron-vite | ^2.3.0 |
| TypeScript | yes |

### External APIs Called

| API | Purpose | Key Required |
|---|---|---|
| OpenAI Whisper (`whisper-1`) | STT — primary transcription | `OPENAI_API_KEY` |
| OpenAI TTS (`tts-1`) | Speech synthesis | `OPENAI_API_KEY` |
| OpenAI Chat (`gpt-4o-mini`) | Intent routing, response gen, drafts, streaming | `OPENAI_API_KEY` |
| Wikipedia REST API | Factual quick-answers | None |
| wttr.in | Morning mode weather | None |
| Gmail API (Google OAuth2) | Email read/send | OAuth credentials |
| Google Calendar API | Events | OAuth credentials |
| Odoo JSON-RPC | ERP/accounting | Credentials in `.secrets/` |
| WhatsApp Web (Playwright) | WA messages via browser automation | Browser session |
| Instagram Graph API | IG posts | OAuth credentials |
| LinkedIn API | Posts/profile | OAuth credentials |
| Ollama (localhost:11434) | Local LLM fallback (llama3) | None — local |

---

## 6. KNOWN BUGS — Code Evidence

### Bug 1: YouTube opens but song doesn't play

**File:** `backend/api/tools/web_tools.py:47-58`

```python
def _exec_search_youtube(params, ctx) -> ToolResult:
    query = params.get("query", "").strip()
    url = f"https://youtube.com/search?q={urllib.parse.quote(query)}"
    return ToolResult(
        success=True,
        spoken=f"Searching YouTube for {query}.",
        action_url=url,   # ← opens SEARCH PAGE, not video player
    )
```

**Frontend handler** (`useVoiceSession.ts:735`):
```typescript
if (actionUrl && typeof window !== 'undefined') window.open(actionUrl, '_blank', 'noopener')
```

**Result:** A new tab opens to `youtube.com/search?q=...`. This is a search results page. YouTube's autoplay does not fire on search pages. The user has to manually click a video.

**Fix needed:** Use the YouTube Data API v3 to search and retrieve the first video ID, then open `youtube.com/watch?v={videoId}&autoplay=1`. Alternatively, use `youtube.com/results?search_query=...&sp=EgIQAQ%253D%253D` (filter to videos) and attempt a headless click via the Playwright browser_tools.

### Bug 2: Whisper cold start (CLI path only)

**Two separate model caches exist:**

- `routers/voice.py:83`: `_local_whisper_model = None` → pre-warmed in background thread on startup
- `voice/whisper_service.py:12`: `_model = None` → lazy-loaded only on first CLI/`recorder.py` call

Pre-warm in `main.py:59-62` calls `_get_local_whisper_model` from `routers.voice`, which warms the HTTP path only. The CLI (`voice/cli.py` → `recorder.py` → `whisper_service.py`) still cold-starts (~3-8 seconds for the `base` model on first run). This is acceptable if the CLI is not the primary usage path.

### Bug 3: "Who built you" — wrong answer in command endpoint

**File:** `backend/api/services/command_service.py:276-284`

```python
def _run_general_skill(text: str) -> str:
    ai = _openai_chat(
        prompt=text,
        system=(
            "You are Xyron — a professional AI voice assistant for business productivity. "
            "Answer the user's request concisely and helpfully. "
            # ← NO identity instruction here
        ),
    )
```

GPT-4o-mini has training data that associates "Xyron" or similar assistants with OpenAI/Anthropic. Without an explicit "you were built by Tayyab Aziz" instruction in this system prompt, GPT may give the wrong creator attribution.

The **voice streaming path** (`_VOICE_SYSTEM_PROMPT`) does have the correct instruction:
> "You were NOT built by OpenAI — you use OpenAI APIs but Tayyab Aziz built you."

And the **frontend** (`voice-core.ts:94-97`) intercepts this pattern client-side before any API call — so the bug only manifests via the `/commands` endpoint or non-voice text submission.

### Bug 4: Language switches to Hindi/Urdu

**Root cause chain:**

1. OpenAI Whisper `whisper-1` detects the language of audio. If the user says anything in Urdu script, Whisper returns `language: "ur"`.
2. `routers/voice.py:154`: Language is accepted only if `"en"` or `"ur"`. So Urdu passes through.
3. Frontend sends `language: "ur"` in the `/respond-stream` request body.
4. `routers/voice.py:1558-1559`:
   ```python
   if body.language == "ur":
       system_content += "\n\nLANGUAGE OVERRIDE: The user is speaking Urdu. You MUST reply entirely in Urdu script"
   ```
5. GPT-4o-mini then intentionally replies in Urdu/Arabic script.

**This is by design for actual Urdu speakers**, but fails for bilingual users who mix Urdu words into English sentences. Whisper's language detection runs on the full audio — a predominantly English sentence with a few Urdu words might get tagged as `ur`, triggering full Urdu response.

**Secondary enforcement issue** (`response_generator.py:117-132`): The `_is_non_english()` check is only applied in `_openai_spoken_response()` (the non-streaming path). The streaming path in `respond_stream` has NO equivalent enforcement re-request mechanism. So a Urdu response from the streaming endpoint is never caught and re-requested.

---

## 7. PERFORMANCE BOTTLENECKS

### Slowest Parts of a Voice Command Cycle

```
1. OpenAI STT API (whisper-1): 800ms - 2s
   - Runs synchronously before routing starts
   - Cannot be overlapped with LLM warm-up

2. GPT-4o-mini first token: 300 - 700ms
   - After all routing decisions are made
   - Streaming helps UX (TTS starts with first sentence)

3. PowerShell tool execution: 1 - 8s
   - _ps() in system_tools.py uses subprocess.run() with blocking I/O
   - Called synchronously from async FastAPI handlers (blocks event loop thread)
   - Battery query: up to 8s timeout if psutil fails and PowerShell fallback runs

4. sentence-transformers load time: 5 - 15s on first startup
   - Runs in daemon thread; new requests before it finishes skip tier 3

5. pyttsx3 TTS (fallback only): 2 - 10s
   - synthesize_speech() holds _lock for entire synthesis duration
   - Single-threaded: only one TTS synthesis can run at a time globally
```

### Blocking Calls That Should Be Async

| Call | File | Issue |
|---|---|---|
| `subprocess.run(cmd, ...)` | `system_tools.py:_ps()` | Runs blocking PowerShell in async FastAPI context; ties up a thread from uvicorn's thread pool |
| `_engine.runAndWait()` | `tts_service.py:107` | Blocking pyttsx3 synthesis inside threading.Lock — blocks its entire thread for synthesis duration |
| `_req.urlopen(r, timeout=5)` | `web_tools.py:73` | Blocking urllib call inside async context for Wikipedia lookup |
| `record_until_silence()` | `audio_utils.py:27` | Blocking sounddevice stream — acceptable in CLI only |
| `sd.wait()` | `audio_utils.py:24` | Blocking fixed-duration recording |

### Repeated Work That Could Be Cached

| Work | Current | Cost | Fix |
|---|---|---|---|
| `OpenAI(api_key=key)` instantiation | Per-call in response_generator.py | Minor overhead | Module-level singleton client |
| `_get_local_whisper_model()` | Already singleton | n/a | — |
| System info (`_get_windows_os`, `_get_cpu_name`) | Called per-request | subprocess calls | Cache with 60s TTL |
| Wikipedia summaries | Fetched fresh every time | Network + latency | LRU cache with 1h TTL |
| `memory_service.get_context_string()` | Rebuilt per-turn from facts dict | Minor | Cache until facts change |
| Sentence-transformer embeddings | Pre-computed at startup | Already cached | — |
| PowerShell `$b = Get-CimInstance Win32_Battery` | Per battery query | WMI call (~1-2s) | Cache 30s TTL |

### Memory and Concurrency Notes

- The `ThreadPoolExecutor(max_workers=4)` in `command_service.py:397` limits concurrent command processing to 4. Under high load, commands queue up.
- The `ToolRegistry.execute()` has no timeout. A hanging PowerShell subprocess will hold a thread indefinitely until the subprocess's own timeout fires.
- `episodic_memory._lock` is a threading lock used with SQLite connections. Each write opens a new connection; there's no connection pooling. Under high traffic, SQLite write contention is possible.
- `synthesize_speech()` holds `_lock` globally — if two TTS requests happen simultaneously, the second one blocks until the first completes. This is a single-threaded TTS bottleneck.

---

## Summary Table

| Area | Status | Severity |
|---|---|---|
| YouTube autoplay | Opens search page only — no playback | High |
| "Who built you" via /commands | Wrong answer — no identity in system prompt | Medium |
| Language switching (Urdu) | By design but no streaming enforcement | Medium |
| Whisper cold start (CLI) | CLI path still lazy-loads | Low |
| PowerShell blocking calls | Ties up async event loop threads | Medium |
| pyttsx3 global lock | Serializes all local TTS synthesis | Medium |
| Wikipedia blocking call | `urllib.urlopen` in async context | Low |
| Double GPT call on non-English | Extra latency per non-English turn | Medium |
| No OpenAI client reuse | Minor connection overhead per call | Low |
| Battery WMI cold call | Up to 2s per query — no cache | Low |
| Command store lost on restart | Intentional v1 design | Known |
| Short-term memory lost on restart | By design; SQLite episodic fills in last 5 turns | Known |
