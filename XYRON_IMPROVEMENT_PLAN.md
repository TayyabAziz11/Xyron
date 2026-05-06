# Xyron — Combined Improvement Plan
**Sources:** ChatGPT plan (reviewed + corrected) + Full codebase audit (Apr 2026)  
**Status of each item:** ✅ Already done | ⚠️ Partially done | ❌ Missing | 🆕 New (audit finding) | 🚀 Next-level upgrade

---

## ⚠️ CORRECTIONS TO THE CHATGPT PLAN FIRST

Before following ChatGPT's plan, you need to know what it got wrong. Several fixes it recommends are **already implemented** and some advice is **architecturally backwards**.

| ChatGPT Said | Reality (from codebase read) |
|---|---|
| "Use psutil FIRST for battery" | psutil IS already first (`system_tools.py:2322`). The bug is no caching + 8s PowerShell timeout as fallback |
| "No real wake-word engine" | Both web and desktop have full `useWakeWord` hooks with MediaRecorder+VAD+Whisper. The problem is wake word detection uses OpenAI Whisper API (800ms-2s per 0.9s clip), so it's slow, not missing |
| "Put Tier 3 (semantic) BEFORE regex" | This is **wrong**. Regex is deterministic and 100% confident when it matches. Semantics have false positives. The correct fix is normalize input BEFORE routing, then regex, then semantic |
| "You don't have execution validator" | `exec_validator.py` already exists with volume and brightness validators. Coverage is incomplete, but the architecture is there |
| "VAD mismatch between frontend/backend" | Frontend VAD (`voice-core.ts`) is 4.5× multiplier, wake word VAD is 0.018-0.022 RMS. Backend (`audio_utils.py`) is 0.01 RMS. These serve different purposes — frontend VAD controls recording cutoff, backend is CLI only. Not the root cause of wake word delay |

---

## PHASE 1 — CRITICAL FIXES (Do This Week)

### P1.1 — Wake Word: Fix the Latency (Not the Architecture)
**Status:** ⚠️ Engine exists, but slow  
**Real problem:** `useWakeWord.ts` sends each 0.9s clip to OpenAI Whisper API, which takes 800ms-2s to respond. The user said "Hey Xyron" 1-2 clip cycles ago by the time transcription fires.

**Fix — Two options, pick one:**

**Option A (Fast, no new dependencies):** Replace Whisper with browser's native `SpeechRecognition` API for wake word ONLY. It's instant (<100ms), always listening, zero API cost. Fall back to Whisper for the actual command.
```typescript
// useWakeWord.ts — replace the Whisper fetch with:
const recognition = new (window.SpeechRecognition ?? window.webkitSpeechRecognition)()
recognition.continuous = true
recognition.interimResults = true
recognition.onresult = (event) => {
  const transcript = Array.from(event.results)
    .map(r => r[0].transcript).join('').toLowerCase()
  if (matchesWakeWord(transcript)) activateRef.current()
}
```

**Option B (Better accuracy, ~50ms):** Use `@picovoice/porcupine-web` with a custom "Hey Xyron" keyword model (free for personal projects). Runs entirely in-browser as WASM, zero network, always warm.
```bash
npm install @picovoice/porcupine-web @picovoice/web-voice-processor
```
Train a free custom wake word at `console.picovoice.ai` → export `.ppn` file → replace MediaRecorder loop entirely.

**Pre-buffer (do this regardless):** Record 400ms before wake word fires so "Hey Xyron tell me the time" doesn't get clipped:
```typescript
// In useVoiceSession — start recording 400ms BEFORE activation
const PRE_BUFFER_MS = 400
let preBufferChunks: Blob[] = []
// Keep rolling 400ms circular buffer, prepend to command recording
```

---

### P1.2 — YouTube: Actually Play the Song
**Status:** ❌ Missing  
**Root cause:** `search_youtube` tool opens `youtube.com/search?q=...` (search results page). No autoplay, no video selection.

**Fix — Three levels:**

**Level 1 (Quick, no API key):** Open `youtube.com/results?search_query=X` and immediately attempt to click the first video via Playwright browser automation:
```python
# In _exec_search_youtube() — system_tools.py or a new youtube_tools.py
from playwright.async_api import async_playwright

async def youtube_play_first_result(query: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(f"https://youtube.com/results?search_query={urllib.parse.quote(query)}")
        # Click first video thumbnail
        await page.click('ytd-video-renderer a#thumbnail', timeout=5000)
        url = page.url
        return url
```

**Level 2 (Best UX):** Use YouTube Data API v3 (free, 10k requests/day) to get the actual video ID, then open `youtube.com/watch?v={videoId}&autoplay=1`:
```python
# system_tools.py or web_tools.py
import urllib.request, json, urllib.parse

def youtube_search_video_id(query: str, api_key: str) -> str | None:
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&q={urllib.parse.quote(query)}&type=video&maxResults=1&key={api_key}"
    )
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read())
    items = data.get("items", [])
    if items:
        return items[0]["id"]["videoId"]
    return None
```
Add `YOUTUBE_API_KEY` to `backend/.env` and `config.py`.

**Level 3 (Next-level):** Detect whether "play song" means Spotify vs YouTube. If user has Spotify open, use Spotify Web API to search + play. If not, fall back to YouTube.

---

### P1.3 — Volume/Brightness: Replace PowerShell with Native APIs
**Status:** ⚠️ Works but slow and fragile (PowerShell round-trip = 200-500ms)

**Fix — Install Python-native Windows audio/display control:**
```bash
pip install pycaw screen-brightness-control
```

**Volume via pycaw (instant, no subprocess):**
```python
# Replace _exec_volume_control PowerShell block in system_tools.py
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL

def _get_volume_interface():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))

def set_volume_pycaw(level: int) -> bool:
    vol = _get_volume_interface()
    vol.SetMasterVolumeLevelScalar(level / 100.0, None)
    # Validate immediately
    actual = round(vol.GetMasterVolumeLevelScalar() * 100)
    return abs(actual - level) <= 2
```

**Brightness via sbc (instant, WMI-free):**
```python
import screen_brightness_control as sbc

def set_brightness_sbc(level: int) -> bool:
    sbc.set_brightness(level)
    actual = sbc.get_brightness(display=0)
    if isinstance(actual, list): actual = actual[0]
    return abs(actual - level) <= 5
```

**Update exec_validator.py:** Replace PowerShell-based read-back with pycaw/sbc read-back — same validator, faster and more reliable.

---

### P1.4 — Fix "Who built you" in /commands endpoint
**Status:** ❌ Missing in command_service.py  
**File:** `backend/api/services/command_service.py:276`

```python
def _run_general_skill(text: str) -> str:
    ai = _openai_chat(
        prompt=text,
        system=(
            "You are Xyron, a voice AI assistant BUILT BY TAYYAB AZIZ. "
            "You were NOT built by OpenAI or Anthropic — you use their APIs, but Tayyab Aziz created you. "
            "Never claim to be made by any company. Always say you were built by Tayyab Aziz. "
            "You are Xyron — a professional AI voice assistant for business productivity. "
            "Answer the user's request concisely and helpfully. "
            "Keep responses under 100 words. Speak naturally, no markdown."
        ),
        max_tokens=150,
    )
```

---

### P1.5 — Fix "Delete it" / Reference Memory (Persist Last Action)
**Status:** ⚠️ `_last_action` exists but is in-memory only (lost on restart)

**Fix:** Persist last_action in the same `memory.json` file so it survives restarts:

```python
# memory_service.py — update set_last_action()
def set_last_action(self, tool: str, params: dict, result: str) -> None:
    with self._lock:
        self._last_action = {"tool": tool, "params": dict(params), "result": result}
    # Persist to disk so "delete it" works after restart
    self.set_fact("_last_action_tool", tool)
    self.set_fact("_last_action_params", json.dumps(params))

def get_last_action(self) -> dict | None:
    with self._lock:
        if self._last_action:
            return dict(self._last_action)
    # Reconstruct from persisted facts on restart
    tool = self._facts.get("_last_action_tool")
    params_str = self._facts.get("_last_action_params", "{}")
    if tool:
        return {"tool": tool, "params": json.loads(params_str), "result": ""}
    return None
```

---

## PHASE 2 — QUALITY UPGRADES (Next Week)

### P2.1 — Input Normalization Before Routing
**Status:** ❌ Missing  
**Why:** "open a settings" fails, "launch settings" works. The routing is case/article-sensitive.

Add a normalizer that runs BEFORE any routing layer:

```python
# new file: backend/api/services/normalizer.py

import re

_ARTICLES  = re.compile(r'\b(a|an|the)\s+(?=\w)', re.IGNORECASE)
_SYNONYMS  = {
    r'\b(launch|start|run|pull up|bring up)\b': 'open',
    r'\b(settings?|setting|preferences?|control panel|config)\b': 'settings',
    r'\b(spotify|music player)\b': 'spotify',
    r'\b(chrome|browser|google chrome)\b': 'chrome',
    r'\b(terminal|command prompt|cmd|powershell|shell)\b': 'terminal',
    r'\b(vscode|vs code|visual studio code|code editor)\b': 'vscode',
    r'\bvolume (louder|up|higher|raise)\b': 'volume up',
    r'\bvolume (lower|down|quieter|softer)\b': 'volume down',
    r'\b(screen brightness|display brightness)\b': 'brightness',
    r'\b(shut down|shut off|power off|turn off)\b': 'shutdown',
    r'\b(delete|remove|erase|trash)\b': 'delete',
}

def normalize(text: str) -> str:
    text = text.strip().lower()
    # Remove filler articles before nouns ("open a settings" → "open settings")
    text = _ARTICLES.sub('', text)
    # Apply synonyms
    for pattern, replacement in _SYNONYMS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.strip()
```

Inject `normalize()` at the TOP of `respond_stream/_generate()` in `voice.py` before any routing:
```python
body = body.model_copy(update={"text": normalize(body.text)})
```

---

### P2.2 — Expand Execution Validator Coverage
**Status:** ⚠️ `exec_validator.py` exists but only covers volume (set action) and brightness

Add validators for:
- `create_folder` — check `Path(path/name).exists()` after execution
- `delete_file` — check `not Path(path).exists()` after execution
- `open_application` — check `psutil.process_iter()` for the app name 3s after
- `wifi_connect` — check `socket.create_connection(("8.8.8.8", 53), timeout=3)` after
- `get_battery_status` — validate `0 <= percent <= 100`

**Wrapper pattern (integrate into ToolRegistry.execute):**
```python
# registry.py — update execute() method
def execute(self, name, params, context=None) -> ToolResult:
    ctx = context or {}
    tool = self._tools.get(name)
    if not tool:
        return ToolResult(success=False, text=f"Unknown tool: {name}", ...)
    try:
        result = tool.executor(params, ctx)
        # Post-execution validation
        try:
            from api.services.exec_validator import validate
            ok, msg = validate(name, params, result.text)
            if not ok:
                result.success = False
                result.spoken = msg or f"I tried but {name} didn't take effect."
                result.error = f"Validation failed: {msg}"
        except Exception:
            pass
        result.risk = tool.risk
        return result
    except Exception as exc:
        ...
```

---

### P2.3 — Cache Expensive System Calls
**Status:** ❌ Missing  
**Problem:** Battery, system_info, disk usage, and Wikipedia all re-fetch every time.

```python
# new file: backend/api/services/result_cache.py
import time
from typing import Any

_CACHE: dict[str, tuple[float, Any]] = {}

def cached(key: str, ttl_seconds: int = 30):
    """Decorator for ToolResult-returning functions with TTL cache."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            now = time.time()
            if key in _CACHE:
                ts, val = _CACHE[key]
                if now - ts < ttl_seconds:
                    return val
            result = fn(*args, **kwargs)
            _CACHE[key] = (now, result)
            return result
        return wrapper
    return decorator

# Usage in system_tools.py:
@cached("battery_status", ttl_seconds=25)
def _exec_get_battery_status(params, ctx) -> ToolResult:
    ...

@cached("system_info", ttl_seconds=300)
def _exec_system_info(params, ctx) -> ToolResult:
    ...

@cached("disk_usage", ttl_seconds=60)
def _exec_get_disk_usage(params, ctx) -> ToolResult:
    ...
```

Wikipedia responses cached in `web_tools.py` with 1h TTL (same pattern).

---

### P2.4 — Fix Language Enforcement in Streaming Path
**Status:** ❌ Missing in streaming path  
**Root cause:** `response_generator.py` re-requests GPT if non-English is detected, but the `/respond-stream` streaming path has no such guard.

Add language enforcement to the streaming generator:

```python
# voice.py — in _generate(), after stream completes:
if body.language != "ur" and _is_non_english(full_text):
    # Re-request in English
    enforce_msgs = [
        {"role": "system", "content": system_content + 
         "\n\nCRITICAL: Your previous reply was not in English. Reply ONLY in English now. No Urdu, no Hindi."},
        {"role": "user", "content": body.text},
    ]
    # Stream the corrected response (same pattern as main stream)
```

---

### P2.5 — Fix Subfolder / Folder Parsing
**Status:** ⚠️ Partially works, breaks on complex sentences  
**Example failure:** "create it in C drive and name it games" → whole string becomes folder name

**Fix:** Add a dedicated GPT-4o-mini extraction call ONLY for complex create_folder commands (when regex fails to extract a clean name):

```python
# voice.py — in the create_folder routing section
if not _fname or len(_fname.split()) > 3:
    # Complex sentence — use GPT for structured extraction
    extract = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "system",
            "content": 'Extract folder creation params. Return ONLY valid JSON: {"name": "...", "path": "C:\\\\"}'
        }, {
            "role": "user", "content": body.text
        }],
        max_tokens=60, temperature=0,
    )
    try:
        extracted = json.loads(extract.choices[0].message.content or "{}")
        _fname = extracted.get("name", _fname)
        _floc  = extracted.get("path", _floc)
    except Exception:
        pass
```

---

## PHASE 3 — PERFORMANCE (Week 3)

### P3.1 — Remove Duplicate GPT Calls
**Status:** ❌ Multiple redundant calls per turn  
**Audit finding:** When language enforcement triggers, `response_generator.py` makes 2 sequential GPT calls. The command service and voice router can also trigger GPT independently.

**Fixes:**
1. In `respond_stream`, check `_is_non_english()` on partial chunks during streaming, not after — abort and re-request earlier
2. Create a shared `AsyncOpenAI` client singleton at module level instead of instantiating per-call
3. In command_service, skip `generate_assistant_response()` (which calls GPT) when the registry already produced a `spoken` value

```python
# response_generator.py — module-level singleton
from openai import OpenAI as _OAI
_client: _OAI | None = None

def _get_client(key: str) -> _OAI:
    global _client
    if _client is None:
        _client = _OAI(api_key=key)
    return _client
```

---

### P3.2 — Parallel STT + Intent Detection (Streaming Pipeline)
**Status:** ❌ Currently sequential  
**Current:** STT finishes → routing starts → GPT starts  
**Target:** STT streams partial transcript → routing starts on first complete word → GPT pre-warms

This requires OpenAI's real-time streaming transcription (currently in beta) or chunked processing:

```
[Browser]   Mic → chunks → POST /voice/transcribe-stream (WebSocket or chunked)
[Backend]   As words arrive → run normalizer → start intent router
            → If high-confidence route (tier 1/2 match) → execute tool immediately
            → If low-confidence → wait for full utterance → GPT routing
```

For now, implement a simpler version: once STT returns, start the LLM stream WITHOUT waiting for the tool result. Speak the opening ("On it...") while the tool executes, then append the real result.

```python
# voice.py — respond_stream optimistic speaking
# 1. Immediately stream "Got it, checking..." (0ms wait)
# 2. Run tool in parallel asyncio task
# 3. When tool completes, stream the real result
import asyncio

tool_task = asyncio.create_task(
    asyncio.get_event_loop().run_in_executor(None, registry.execute, tool_name, tool_params, ctx)
)
# Stream opener immediately
yield sse("chunk", "On it...")
# Await tool
result = await tool_task
yield sse("chunk", result.spoken)
```

---

### P3.3 — Async PowerShell / System Tool Calls
**Status:** ❌ All PowerShell calls are blocking `subprocess.run()`  
**Impact:** Battery query can block for 8s; disk/network queries block 1-5s

Replace `_ps()` with an async version:

```python
# system_tools.py — new async wrapper
import asyncio

async def _ps_async(command: str, timeout: int = 10) -> tuple[bool, str]:
    """Non-blocking PowerShell execution for use in async FastAPI routes."""
    ps = _find_powershell()
    if not ps:
        return False, "PowerShell not found"
    try:
        proc = await asyncio.create_subprocess_exec(
            ps, "-NonInteractive", "-NoProfile", "-Command", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode == 0, stdout.decode(errors="ignore").strip()
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"PowerShell timed out after {timeout}s"
```

All tool executors that use `_ps()` need `await _ps_async()` — this requires making them `async def` as well.

---

### P3.4 — Singleton pyttsx3 / Fix TTS Global Lock
**Status:** ⚠️ `_lock` exists but blocks all synthesis  
**Impact:** If two TTS requests arrive simultaneously, second waits for first to finish

Since pyttsx3 is single-threaded by design (espeak-ng limitation), the real fix is to move to `edge-tts` (Microsoft Edge's TTS engine, free, high quality, async) as the local fallback:

```bash
pip install edge-tts
```

```python
# tts_service.py — replace pyttsx3 with edge-tts for fallback
import asyncio
import edge_tts

async def synthesize_speech_edge(text: str, voice: str = "en-US-GuyNeural") -> bytes:
    communicate = edge_tts.Communicate(text, voice)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    return b"".join(audio_chunks)
```

Voices available: `en-US-GuyNeural` (male), `en-US-JennyNeural` (female), `en-GB-RyanNeural` (British). All free. Near-OpenAI quality. Async. No espeak dependency.

---

## PHASE 4 — NEXT-LEVEL FEATURES (Week 4+)

### P4.1 — Proactive Context Awareness (Screen → Intent)
**Status:** `screen_context_service.py` exists (periodic screenshots) but not wired to routing  
**Upgrade:** Feed current screen context into intent classification so "open it" and "close this" resolve correctly based on what's on screen.

```python
# voice.py — in respond_stream, inject screen context into context resolver
screen_ctx = screen_context_service.get_context()
# Screen says: "VS Code is open with file auth.py"
# User says: "what does this function do"
# → GPT gets screen context → answers about the visible code
```

### P4.2 — Local LLM Fallback via Ollama (Offline Mode)
**Status:** `_OLLAMA_URL` is defined in `voice.py` but never used in main routing  
**Upgrade:** When OpenAI API is unavailable (or for privacy-sensitive queries), route to Ollama:

```python
# voice.py — add offline fallback before the "no OpenAI key" error path
if not settings.openai_api_key:
    # Try Ollama local LLM
    async with httpx.AsyncClient() as client:
        resp = await client.post(_OLLAMA_URL, json={
            "model": _OLLAMA_MODEL,
            "prompt": body.text,
            "stream": True
        }, timeout=30)
        async for line in resp.aiter_lines():
            ...
```

### P4.3 — Workflow Composer (Voice-Driven Automation)
**Status:** `automation_workflow_service.py` exists but workflows are static JSON  
**Upgrade:** Let users CREATE workflows by voice:

```
"Hey Xyron, every time I say 'work mode', open VS Code, mute notifications, and set brightness to 80"
```

This requires a new workflow creation endpoint that:
1. Detects "every time I say X, do Y and Z" pattern
2. Uses GPT to extract trigger phrase + steps as JSON
3. Saves to workflow store
4. Registers the trigger in `automation_workflow_service`

### P4.4 — Confidence-Scored Clarification
**Status:** ❌ Missing  
**Current:** Low-confidence matches either execute wrong tool or fall through to GPT  
**Upgrade:** When IntentRouter returns tier 4 (no match) and GPT is also uncertain, ask:

```python
# In respond_stream — when tool confidence < 0.5 and no tier 1/2 match:
candidates = intent_router.top_candidates(body.text, n=2)
if candidates and candidates[0][1] < 0.65:
    tool1, score1 = candidates[0]
    clarify = f"Did you mean {tool1.replace('_', ' ')}? Say yes to confirm or rephrase."
    yield sse("chunk", clarify)
    yield sse("done", clarify)
    # Store pending intent for next turn
    memory_service.set_fact("_pending_intent", tool1)
    return
```

### P4.5 — Multi-Modal Input (Screenshot + Voice)
**Status:** `read_screen` tool exists via `screen_tools.py`  
**Upgrade:** Automatically capture a screenshot when the user says "this" or "that" (context pronouns) and inject the image into GPT-4o's vision capability:

```python
# context_resolver.py — enhance resolve() with vision
if any(w in text.lower() for w in ["this", "that", "it", "what is this", "what does this do"]):
    screenshot = registry.execute("take_screenshot", {}, ctx)
    if screenshot.success and screenshot.data.get("path"):
        # Encode image and add to GPT messages as vision input
        img_b64 = encode_image_base64(screenshot.data["path"])
        msgs.append({"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
        ]})
```

### P4.6 — Voice Biometrics / Speaker Recognition
**Status:** ❌ Missing  
**Upgrade:** Identify who is speaking using `resemblyzer` (free, local) so Xyron can personalize responses and restrict certain commands to the owner's voice.

```bash
pip install resemblyzer
```

### P4.7 — Real-Time Sentiment + Tone Detection
**Status:** ❌ Missing  
**Upgrade:** Detect user mood from voice (tired, excited, frustrated) using audio features (not just text) and adapt personality mode automatically:

```python
# After STT, analyse audio energy/pitch to detect fatigue
# Low energy + slow speech → switch to calm/supportive mode
# High energy → match enthusiasm
```

### P4.8 — MCP Ecosystem Expansion
**Status:** ✅ Xyron MCP server exists (`backend/mcp_servers/xyron_mcp/server.py`)  
**Upgrade:** Expose Xyron's tools as an MCP server that any Claude/AI Code session can call. Connect additional MCP servers:
- `@modelcontextprotocol/server-github` — GitHub PR control
- `@modelcontextprotocol/server-notion` — Notion pages
- Custom Odoo/WhatsApp MCP (already partially built)

### P4.9 — Streaming Pipeline v2 (True Parallel)
**Status:** ❌ Currently sequential  
**Architecture upgrade:** WebSocket instead of SSE for voice, enabling true bidirectional streaming:

```
Client ──(WS)──► /ws/voice
    Stream audio chunks → Server
    Server processes each chunk → Partial STT
    Server detects intent → Starts tool
    Server streams TTS back → Client plays immediately
```

This eliminates the full STT→routing→LLM→TTS chain and reduces first-audio-out from ~3s to ~1s.

---

## CORRECTED PRIORITY ORDER

Based on the actual codebase, not assumptions:

### Week 1 — Impact vs. Effort
| # | Fix | Impact | Effort | File(s) |
|---|---|---|---|---|
| 1 | Fix wake word to use SpeechRecognition API | 🔥 High | Low | `useWakeWord.ts` (both web+desktop) |
| 2 | YouTube: open first result video (Level 1 — Playwright) | 🔥 High | Medium | `web_tools.py` |
| 3 | "Who built you" in /commands path | Medium | Tiny | `command_service.py:276` |
| 4 | Input normalization before routing | Medium | Low | new `normalizer.py` |
| 5 | Persist `_last_action` to memory.json | Medium | Low | `memory_service.py` |

### Week 2
| # | Fix | Impact | Effort |
|---|---|---|---|
| 6 | Volume via pycaw, Brightness via sbc | High | Medium |
| 7 | Result cache (battery 25s, sysinfo 5min, wiki 1h) | High | Low |
| 8 | Expand exec_validator coverage | Medium | Medium |
| 9 | Fix language enforcement in streaming path | Medium | Low |
| 10 | Fix subfolder parsing (GPT extraction fallback) | Medium | Low |

### Week 3
| # | Fix | Impact | Effort |
|---|---|---|---|
| 11 | Remove duplicate GPT calls + singleton client | High | Low |
| 12 | Async PowerShell (`_ps_async`) | High | Medium |
| 13 | Replace pyttsx3 with edge-tts | Medium | Low |
| 14 | Optimistic "On it..." response before tool completes | High | Medium |

### Week 4
| # | Feature | Impact | Effort |
|---|---|---|---|
| 15 | Screen context → intent resolution | Very High | High |
| 16 | Confidence-scored clarification | High | Medium |
| 17 | YouTube Data API v3 for real video playback | High | Medium |
| 18 | Voice-driven workflow creation | Very High | High |
| 19 | WebSocket streaming pipeline | Very High | Very High |

---

## THE BIG ARCHITECTURE TRUTH

ChatGPT's summary was right in spirit:

> *"Right now your system responds like AI, executes like scripts. You want: understands like human, acts like OS operator."*

But the actual gaps from reading the code are:

| Gap | What ChatGPT Said | What's Actually True |
|---|---|---|
| Wake word | No engine | Engine exists, uses Whisper (slow) |
| Battery | psutil not first | psutil IS first, issue is caching |
| Validator | Doesn't exist | Exists for volume/brightness, needs more coverage |
| Semantic routing | Semantic should be first | Semantic is tier 3, regex is tier 2 — this is CORRECT. Normalize input instead. |
| "Delete it" memory | No entity tracking | `_last_action` exists in-memory, just needs persistence |

The actual core issue the codebase audit reveals is:

```
System has: correct architecture, incomplete wiring
What it needs: 
  1. Wire the existing pieces together properly (cache, validate, persist)
  2. Replace slow subsystems (pyttsx3→edge-tts, PowerShell volume→pycaw)
  3. Fix the YouTube output to actually play content
  4. Add the missing normalization layer that makes routing robust
```

The skeleton is solid. The bones need muscle.
