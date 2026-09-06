# Xyron — Offline-First Desktop Voice Assistant for Pakistan

**No internet? No problem.**

## The Problem

Pakistan runs on load shedding, throttled and expensive mobile data, and voice assistants that assume you speak English. Siri, Alexa, and Google Assistant all go silent the moment your connection does — and none of them understand "Chrome kholo" or "awaz kam karo" natively. Xyron is built the other way around: **local first, cloud only when it genuinely has to be.**

## Offline Mode — the headline feature

**84 of Xyron's 116 registered voice commands run entirely on-device — zero internet required.** No API call, no cloud round-trip, no failure when the signal drops. Verified directly against the live tool registry (`backend/api/tools/registry.py`), not estimated.

| | Count | Behavior without internet |
|---|---|---|
| **Fully offline** | **84** | Work exactly the same with WiFi off |
| Hybrid (local-first, graceful fallback) | 4 | Degrade gracefully — see [Honest Cloud Disclosure](#honest-cloud-disclosure) below |
| Online-only | 26 | Need a live connection by nature (web search, Gmail, WhatsApp, browser automation, Store installs, AI screen reading, speed test) |
| Workflow-dependent | 1 (`run_workflow`) | Depends which of the 7 built-in workflows is triggered — see [Modes](#modes) |
| Duplicate/utility | 1 (`install_store_app_exec`) | Counted under online-only above |

### The 84 offline commands, by category

Every row below is a real registered tool (name in the intent router), pulled live from the tool registry — not hand-picked.

**App Control** — `api/tools/system_tools.py`, `api/tools/core_tools.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Open Chrome" / "Chrome kholo" | `open_application` |
| "Close Chrome" / "Chrome band karo" | `kill_app` |
| "What apps are running?" | `get_running_apps` |
| "Kill Notepad" | `kill_process` |
| "List processes" | `list_processes` |
| "Find [app]" | `find_app` |
| "Launch [app]" | `launch_app` |
| "Disable Spotify from startup" | `disable_startup_app` |
| "What starts on boot?" | `get_startup_apps` |
| "Xyron takeover" / "focus mode" | `takeover_mode` |

**File Management** — `api/tools/system_tools.py`, `api/tools/core_tools.py`, `api/tools/file_organizer.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Open E drive" / "E drive kholo" | `open_directory` / `open_drive` |
| "What's in Downloads?" | `list_directory` |
| "Search for [file]" | `search_files` / `find_file` |
| "Open my course folder" (fuzzy match) | `smart_open` |
| "Create folder X" | `create_folder` / `create_subfolders` |
| "Move X to Y" | `move_file` |
| "Copy X to Y" | `copy_file` |
| "Rename X to Y" | `rename_file` |
| "Delete X" | `delete_file` |
| "Write to file X" | `write_file` |
| "Open the file X" | `open_file` |
| "Organize my Downloads" | `organize_files` |
| "Undo that" / "put everything back" | `undo_organize_files` |
| "Which drives do I have?" | `detect_drives` / `drive_exists` |

**System & Power** — `api/tools/system_tools.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Sleep" / "hibernate" | `sleep_system` / `hibernate_system` |
| "Lock the screen" | `lock_system` |
| "Shut down" / "restart" | `shutdown_system` / `restart_system` |
| "Shutdown in 30 minutes" | `schedule_shutdown` |
| "System info" / "specs" | `system_info` |
| "CPU/RAM/disk usage" | `system_health` / `system_health_check` |
| "How much space do I have?" | `get_disk_usage` |
| "System uptime" | `get_uptime` |
| "Battery level" | `get_battery_status` |
| "Switch to performance mode" | `set_power_plan` |
| "Empty the recycle bin" / "kachra khali karo" | `empty_recycle_bin` |
| "How big is temp folder?" / "clear temp files" | `get_temp_files_size` / `clear_temp_files` |
| "Run disk cleanup" | `run_disk_cleanup` |
| "Flush DNS" | `flush_dns` |

**Display, Audio & Settings** — `api/tools/system_tools.py`, `api/tools/core_tools.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Volume up/down" / "awaaz kam karo", "volume barhao" | `volume_control` / `set_volume` / `volume_up` / `volume_down` |
| "Mute" / "unmute" | `mute_unmute` |
| "What's the volume?" | `get_volume` |
| "Pause the song" / "gana band karo" | `media_control` |
| "List audio devices" / "switch to headphones" | `list_audio_devices` / `set_default_audio` |
| "Brightness up/down" / "brightness barhao" | `brightness_control` / `set_brightness` / `get_brightness` |
| "Set resolution to 1920x1080" | `set_display_resolution` |
| "Set refresh rate to 144hz" | `set_refresh_rate` |
| "Open display settings" / "display settings kholo" | `open_system_settings` |
| "Create new virtual desktop" | `virtual_desktop_create` / `virtual_desktop_switch` |

**WiFi & Local Network** — `api/tools/system_tools.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Show WiFi networks" | `wifi_list` / `open_wifi_panel` |
| "Connect to [network]" | `wifi_connect` |
| "WiFi off" / "wifi off karo" | `wifi_disconnect` |

**Window & Desktop Control** — `api/tools/screen_tools.py`, `api/tools/automation_tools.py`
| Command (English / Roman Urdu) | Tool |
|---|---|
| "Minimize" / "maximize" | `minimize_window` / `maximize_window` |
| "Close this window" | `close_window` |
| "Switch to [app]" | `switch_window` |
| "Type this: ..." | `type_text` / `desktop_type` |
| "Read clipboard" / "clear clipboard" | `read_clipboard` / `write_clipboard` / `clear_clipboard` |
| "Take a screenshot" | `take_screenshot` / `desktop_screenshot` |
| "Press ctrl+c" | `desktop_hotkey` |
| "Scroll down" | `desktop_scroll` |
| "Click at X,Y" | `desktop_click` |
| "Bring [app] to front" | `desktop_focus_app` |

**Utility**
| Command | Tool |
|---|---|
| "What time is it?" | `get_date_time` |
| "List pending approvals" | `list_approvals` |

## Modes

Xyron has exactly **2 real modes** — both defined in code, both trigger on the phrase "focus mode" (an intentional overlap in the intent router worth knowing about, not two separate features accidentally colliding):

| Mode | What it does | Offline? | Defined in |
|---|---|---|---|
| **Work Mode** | Opens VS Code, then opens your GitHub profile page | Partial — VS Code launch is offline, the GitHub tab needs internet | `backend/workflows/work_mode.json` |
| **Takeover Mode** | Opens VS Code + triggers a cinematic UI activation sequence in the frontend | Yes — fully offline | `backend/api/tools/system_tools.py` (`_exec_takeover_mode`) |

Beyond these, Xyron ships **7 pre-built workflows** (macros, not modes) in `backend/workflows/*.json` — `work_mode`, plus 6 that are all browser-based and therefore online-only: `github_search`, `gmail_compose`, `google_maps`, `google_search`, `whatsapp_message`, `youtube_search`.

## Roman Urdu Support

The intent router (`backend/api/services/intent_router.py`) compiles **191 Tier-2 regex rules** at runtime. Of those, **27 explicitly encode Roman Urdu vocabulary** (`kholo`, `karo`, `chalao`, `dikhao`, `band`, `khali`, `barhao`, `ghata`, `wapis`, `dobara`, `kachra`, etc.) — **19 unique templates** (6 of the 27 are exact duplicates from two identical settings-keyword lists in the source, a known cleanup item, not a feature).

| Roman Urdu pattern | English equivalent | Tool |
|---|---|---|
| "gana rok do / roko / band karo" | "pause/stop the song" | `media_control` |
| "dobara / wapis chalao" | "play again" | `media_control` |
| "[wifi\|network\|bluetooth\|display\|sound\|updates] kholo/chalao/on karo/dikhao" | "open [X] settings" (×6 pages) | `open_system_settings` |
| "X kholo / khol do / chalao / open karo / start karo / launch karo" | "open X" (generic app launcher) | `open_application` |
| "wifi on karo" / "wifi off karo" | "turn wifi on/off" | `open_wifi_panel` / `wifi_disconnect` |
| "volume/awaaz barhao" / "kam karo" | "increase/decrease volume" | `volume_control` |
| "brightness barhao" / "ghata do" | "increase/decrease brightness" | `brightness_control` |
| "youtube pe X chalao" | "play X on YouTube" | `search_youtube` |
| "X dikhao / dikha do" | "search for X" | `search_web` |
| "recycle bin / kachra khali karo" | "empty the recycle bin" | `empty_recycle_bin` |

This sits above **Tier 1** (`mixed_language_engine.py` — deterministic code-switch detection) and below **Tier 4** (`local_comprehension.py` — local Ollama model for anything the deterministic tiers miss), so Roman Urdu, Urdu script, and English/Urdu code-mixing are all first-class, not bolted on.

## Honest Cloud Disclosure

We'd rather tell you exactly what leaves your machine than claim "fully offline" and be wrong.

**Goes to the cloud, and why:**
- **Web search, YouTube, Wikipedia, Gmail, Google Calendar, browser automation, WhatsApp Web, Microsoft Store installs** (26 tools) — these features are inherently internet-dependent; there's no local substitute for "search Google" or "read my Gmail"
- **AI screen reading** (`read_screen`) — sends a screenshot to GPT-4o Vision; hard-requires an OpenAI key, no local vision fallback exists yet
- **Roman Urdu TTS voice output** — Edge-TTS calls a Microsoft cloud endpoint for the native `ur-PK-AsadNeural`/`ur-PK-UzmaNeural` voices; English TTS stays fully local via Kokoro
- **Intent classification (Tier 3) and general conversation** — `gpt-4o-mini` is the primary brain when the fast deterministic tiers (regex, embeddings) don't confidently match; falls back to a local Ollama model (`qwen2.5:1.5b`) when OpenAI is unavailable
- **`compose_email` / `create_post` / `get_summary`** — try OpenAI first; if unreachable, they don't fail, but they fall back to a generic static template rather than real generated content

**Stays on your machine, always:**
- Speech-to-text (`faster-whisper`, local model, GPU or INT8 CPU)
- English TTS (Kokoro)
- All 84 offline commands above — file operations, app control, system/power management, display/audio settings, WiFi, window control
- Wake-word detection
- Short-term conversation memory and the episodic SQLite log

**Why this matters for privacy, not just uptime:** every one of those 84 offline commands never touches a network socket — no telemetry, no transcript leaves the device to run them. The approval gate (below) is a second, independent layer of protection on top of that: even for the online/AI-assisted paths, nothing destructive executes unreviewed.

## Architecture — 3-tier offline-first routing

```
🎤 Mic → Wake Word (local) → STT (faster-whisper, local)
       → Tier 1: mixed_language_engine (deterministic, local)
       → Tier 2: intent_router regex (191 rules, local, <1ms)
       → Tier 3: sentence-embedding classifier (local)
       → Tier 4: local_comprehension / Ollama (local) — only if Tiers 1–3 miss
       → [cloud fallback: gpt-4o-mini] — only if local tiers are not confident
       → Agents / Tools (84 offline / 26 online / 4 hybrid)
       → TTS: Kokoro (local, EN) or Edge-TTS (cloud, UR)
       → 🔊 Audio
```

Cloud is the *last* resort in the routing order, not the first — a command only reaches OpenAI if four local tiers all failed to confidently resolve it.

The system runs as three independently deployable layers that talk only over HTTP:

```
desktop-app (Tauri + React)  ─┐
web (Next.js dashboard)      ─┼──▶  backend (FastAPI)  ──▶  external APIs / OS (only when needed)
```

| Layer | Responsibility | Stack |
|---|---|---|
| **Backend** | Voice pipeline, intent routing, agent orchestration, perception, tool execution | Python 3.10+, FastAPI |
| **Web Dashboard** | Command center, activity timeline, approvals queue, stats, integrations | Next.js 15, React 19, TypeScript, Tailwind |
| **Desktop App** | System-tray presence, global wake-word listening, native automation bridge | Tauri 2 (Rust shell) + React renderer |

**116 registered tool handlers across 15 modules**, dispatched by **5 agent packages spanning 87 Python modules** (`coordinator`, `browser_agent`, `coding_agent`, `automation_agent`, `personality`) — a `CoordinatorAgent` builds a task graph and delegates instead of one monolithic prompt trying to do everything.

For deep-dives, see [`Docs/architecture/`](Docs/architecture/) — system overview, voice pipeline, perception engine, world state, planning engine, tool orchestrator, memory system, filesystem intelligence, database schemas, and project structure.

## Tech Stack

| Component | Technology | Offline? |
|---|---|---|
| Backend API | Python, FastAPI | — |
| Speech-to-text | faster-whisper (local, GPU/INT8) | ✅ Local |
| Text-to-speech (English) | Kokoro | ✅ Local |
| Text-to-speech (Urdu) | Edge-TTS (`ur-PK-AsadNeural`/`ur-PK-UzmaNeural`) | ☁️ Cloud |
| LLM (cloud) | OpenAI `gpt-4o-mini` — intent classification, response generation, screen reading | ☁️ Cloud |
| LLM (local fallback) | Ollama `qwen2.5:1.5b` | ✅ Local |
| Intent routing | Regex/keyword (191 rules) → sentence-transformer embeddings → LLM fallback | Mostly local |
| Web dashboard | Next.js 15, React 19, Tailwind, Framer Motion | — |
| Desktop shell | Tauri 2 (Rust) + React | — |
| Browser automation | Playwright (CDP) | ☁️ Needs internet by nature |
| WhatsApp integration | Baileys / open-wa (Node.js sidecar) | ☁️ Needs internet by nature |
| Memory | SQLite (episodic log) + JSON fact store | ✅ Local |

## Security

- **Offline = privacy** — the 84 fully-offline commands never open a network socket; nothing about your file system, running apps, or system state leaves the machine to execute them
- **Human-in-the-loop approval gate** — any action classified as risky (deletes, sends, installs, shutdowns) writes a plan to `Pending_Approval/` and halts; nothing executes until a human moves it to `Approved/` via the dashboard — this applies regardless of whether the triggering command was local or cloud-routed
- **Typed tool schemas** — all 116 tools are registered with explicit JSON-schema parameter definitions (`backend/api/tools/registry.py`); the LLM can only call what's registered, with the shape it's registered with — no arbitrary code execution from a model response
- **Risk-tiered tools** — every tool carries a `risk` level (`low`/`medium`/`high`); 6 are `high` risk (delete, shutdown, restart, WhatsApp send/reply/file) and route through stricter confirmation

## Getting Started

### Prerequisites

| Tool | Why |
|---|---|
| Python 3.10+ | Backend runtime |
| Node.js 18+ / npm | Web dashboard, desktop app, WhatsApp sidecar |
| Rust toolchain | Building the Tauri desktop shell |
| WSL2 (Windows) | Backend and voice pipeline target WSL2; `PULSE_SERVER` wiring assumes it |
| `espeak-ng` | Local TTS fallback (`sudo apt-get install espeak-ng`) |
| OpenAI API key (optional) | Only needed for the 26 online-only tools + cloud LLM fallback — the 84 offline commands work without one |

### 1. Clone

```bash
git clone https://github.com/TayyabAziz11/Xyron.git
cd Xyron
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # OPENAI_API_KEY is optional — offline commands work without it
python3 -m uvicorn api.main:app --reload --port 8000
```

Config is read from `backend/.env` by absolute path — your working directory doesn't matter.

### 3. Web Dashboard

```bash
cd web
npm install
cp .env.local.example .env.local
npm run dev        # http://localhost:3001
```

### 4. Desktop App (optional)

```bash
cd desktop-app
npm install
cp .env.example .env       # optional — Clerk auth; without it the app runs in dev-auth mode
npm run dev:wsl            # WSL2 — wires PULSE_SERVER for audio
# or
npm run dev                # native Linux/Mac
```

### 5. WhatsApp integration (optional, requires internet)

```bash
cd backend/integrations/whatsapp/sidecar
npm install
cp .env.example .env       # generate a real WA_SIDECAR_API_KEY
node server.js
```

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/                        # full suite
pytest tests/test_wa_identity.py     # single file
```

## Built for Alibaba Cloud AI Hackathon Pakistan 2026

Xyron started as a general-purpose voice operator; during the hackathon we sharpened its focus around offline-first reliability and native Roman Urdu / Urdu support for Pakistani users. Added during the hackathon build phase:

| Feature | What changed | Offline? |
|---|---|---|
| Roman Urdu / Urdu-script command routing | 27 regex rules added to `intent_router.py`, plus `mixed_language_engine.py` and `ml_normalizer.py` for code-switched input | ✅ Yes |
| Native Pakistani Urdu TTS | Edge-TTS integration (`voice/edge_tts_service.py`), auto-selected by detected response language | ☁️ Cloud |
| WhatsApp integration | Baileys + open-wa sidecar (`backend/integrations/whatsapp/sidecar/`), identity resolution, voice-driven send/reply/read (`api/integrations/whatsapp/`, `api/tools/whatsapp_tools.py`) | ☁️ Cloud |
| File organizer with undo | Plan → confirm → execute → undo workflow (`api/tools/file_organizer.py`) | ✅ Yes |
| Business-automation reporting | Accounting audit + weekly CEO briefing skills (mock-mode) | ✅ Yes |
| STT benchmarking suite | Accuracy/latency comparison across Whisper model sizes for low-spec hardware (`scripts/bench_stt_*.py`) | ✅ Yes |
| Wake-word/STT/TTS reliability fixes | Thread-pool contention, dead-end Ollama retries, STT retry regression, slow-hardware wake/app-launch fixes | ✅ Yes |

## Team

- **Muhammad Qasim** — Backend, Memory, Documentation
- **Tayyab Aziz** — Voice Engine, Tools, Orchestration, UI — Lead Developer

## Links

- GitHub: [TayyabAziz11/Xyron](https://github.com/TayyabAziz11/Xyron)
- Instagram: [@xyron_ai](https://instagram.com/xyron_ai)

## Contributing

This repo protects `main` — all work happens on feature branches:

```bash
git checkout main && git pull origin main
git checkout -b feat/your-feature
# ...
git push origin feat/your-feature
gh pr create --base main --head feat/your-feature
```

See [`CLAUDE.md`](./CLAUDE.md) for the full development guide (config reference, intent-routing internals, key files).

## License

See [LICENSE](./LICENSE).
