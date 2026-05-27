# Migration Checklist — Electron → Tauri
_Generated: 2026-05-18_

Use this as the step-by-step execution guide. Complete each phase in order. Do NOT move to the next phase until all items are checked.

---

## Phase 0 — Prerequisites
- [ ] Rust toolchain installed: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- [ ] Tauri CLI installed: `cargo install tauri-cli --version "^2"` OR `npm install -g @tauri-apps/cli@next`
- [ ] WebView2 (Windows) / WebKitGTK (Linux): `sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev`
- [ ] Verify: `cargo tauri info` shows no missing dependencies
- [ ] Git branch created: `git checkout -b feat/tauri-migration`

---

## Phase 1 — Strip Electron From desktop-app/

### Remove files
- [ ] Delete `desktop-app/src/main/index.ts`
- [ ] Delete `desktop-app/src/preload/index.ts`
- [ ] Delete `desktop-app/electron.vite.config.ts`

### Update package.json
- [ ] Remove all Electron dependencies:
  ```
  electron, electron-builder, electron-vite
  @electron-toolkit/utils, @electron-toolkit/tsconfig
  cross-env
  ```
- [ ] Remove `build` section (electron-builder config)
- [ ] Remove `postinstall: electron-builder install-app-deps`
- [ ] Remove `dev:wsl` and `dev` scripts that reference `electron-vite`
- [ ] Keep: `react`, `react-dom`, `react-router-dom`, `framer-motion`, `lucide-react`, `typescript`

### Clean node_modules
- [ ] `rm -rf desktop-app/node_modules desktop-app/package-lock.json`
- [ ] Remove `desktop-app/out/` if present

### Remove IPC references from renderer hooks
- [ ] Search all `window.electronAPI` calls in `desktop-app/src/renderer/`
- [ ] Replace with direct `fetch('http://localhost:8000/...')` or import from `shared/lib/api.ts`
- [ ] Remove all `ipcRenderer.invoke` references
- [ ] Remove `ElectronAPI` type declarations from renderer code

---

## Phase 2 — Create shared/ Package

### Scaffold
- [ ] Create `shared/` at repo root
- [ ] Create `shared/package.json` (name: `@xyron/shared`, private: true)
- [ ] Create `shared/tsconfig.json` extending web tsconfig patterns

### Copy components from web/src/
- [ ] `cp -r web/src/components/ shared/components/`
- [ ] `cp -r web/src/hooks/ shared/hooks/`
- [ ] `cp -r web/src/lib/ shared/lib/`
- [ ] `cp -r web/src/state/ shared/state/`
- [ ] `cp -r web/src/contexts/ shared/contexts/`
- [ ] `cp -r web/src/config/ shared/config/`
- [ ] `cp web/src/app/globals.css shared/styles/globals.css`
- [ ] `cp web/src/styles/ui-modes.css shared/styles/ui-modes.css`

### Fix imports in shared/
- [ ] Replace all `@/` path aliases → relative paths (e.g. `@/lib/api` → `../lib/api`)
  - OR configure path alias in shared tsconfig: `"@": ["./"]`
- [ ] Remove `'use client'` directives (not needed outside Next.js)
- [ ] Verify no `next/` imports in shared components (e.g. `next/image`, `next/font`)
  - Replace `next/image` → `<img>` in shared components
  - Replace `next/link` → `react-router-dom <Link>` or `<a>`

### Verify shared compiles
- [ ] `cd shared && tsc --noEmit`

---

## Phase 3 — Initialize Tauri in desktop-app/

### Scaffold Tauri
```bash
cd desktop-app
cargo tauri init
```
Answer prompts:
- App name: `Xyron`
- Window title: `Xyron`
- Web assets dist dir: `../dist`
- Dev server URL: `http://localhost:1420`
- Frontend dev command: `npm run dev:frontend`
- Frontend build command: `npm run build:frontend`

- [ ] Verify `desktop-app/src-tauri/` was created with:
  - `Cargo.toml`
  - `tauri.conf.json`
  - `src/main.rs`
  - `src/lib.rs`
  - `capabilities/default.json`
  - `icons/` (copy from assets/)

### Configure tauri.conf.json
- [ ] Set `bundle.identifier` = `com.tayyabaziz.xyron`
- [ ] Set window `width: 1280`, `height: 800`, `minWidth: 900`, `minHeight: 600`
- [ ] Set `backgroundColor: "#08080f"`
- [ ] Set `decorations: true`
- [ ] Configure `updater.endpoints` (or disable for now)

### Add Tauri plugins to Cargo.toml
- [ ] Add all plugins listed in `docs/tauri_plugins_used.md` → `[dependencies]`
- [ ] Run `cargo build` inside `src-tauri/` to verify deps resolve

### Register plugins in main.rs
- [ ] Add all `.plugin(tauri_plugin_*.init())` calls to `Builder::default()`
- [ ] Add `invoke_handler` with all Tauri commands

---

## Phase 4 — Implement Tauri Rust Commands

### src-tauri/src/lib.rs
- [ ] `launch_application(command: String)` — WSL2 detection + spawn cmd.exe/powershell.exe
- [ ] `take_screenshot()` → returns `Option<String>` (base64 PNG)
- [ ] `get_system_info()` → runs PowerShell, returns pipe-delimited string
- [ ] `start_backend(resource_dir: String)` → spawn python3 uvicorn
- [ ] `stop_backend()` → kill backend child process
- [ ] `open_url(url: String)` → platform-specific
- [ ] `copy_to_clipboard(text: String)` → via clipboard plugin

### src-tauri/src/backend.rs
- [ ] `read_dot_env(dir: &Path)` → parse backend/.env
- [ ] `BackendManager` struct with `start()`, `stop()`, `is_running()`
- [ ] Check if backend already running before starting (HTTP health check)

### Setup closure (main.rs)
- [ ] System tray creation with menu (Show/Hide, Quit)
- [ ] Global shortcut registration: `Alt+Space`, `Ctrl+Shift+Space`, `Ctrl+Shift+Esc`
- [ ] Backend auto-start call
- [ ] Permission grant for mic/media (Tauri handles this via capabilities JSON)

### capabilities/default.json
- [ ] Add all permissions listed in `docs/tauri_plugins_used.md`

---

## Phase 5 — Update Frontend Stack

### Install new dependencies
```bash
cd desktop-app
npm install @tauri-apps/api @tauri-apps/plugin-fs @tauri-apps/plugin-shell \
  @tauri-apps/plugin-process @tauri-apps/plugin-notification \
  @tauri-apps/plugin-dialog @tauri-apps/plugin-global-shortcut \
  @tauri-apps/plugin-clipboard-manager @tauri-apps/plugin-window-state \
  @tauri-apps/plugin-store \
  @ricky0123/vad-react @ricky0123/vad-web \
  zustand @tanstack/react-query \
  clsx tailwind-merge

npm install -D @tauri-apps/cli @tauri-apps/vite-plugin @vitejs/plugin-react
```

### Version upgrades
- [ ] `react` → `19.0.0`
- [ ] `react-dom` → `19.0.0`
- [ ] `framer-motion` → `^12`
- [ ] `lucide-react` → `^0.511`

### Create vite.config.ts
- [ ] `@vitejs/plugin-react` + `@tauri-apps/vite-plugin`
- [ ] `server.port = 1420`
- [ ] Path alias `@` → `./src`
- [ ] Path alias `@shared` → `../shared`

### Update tailwind.config.ts
- [ ] Copy color tokens from `web/tailwind.config.ts` (brand, surface, bg, border, etc.)
- [ ] Set `content` to include `src/**` AND `../shared/**`

### Update index.html
- [ ] Point to `src/main.tsx`
- [ ] Add CSP meta tag allowing `localhost:8000`

---

## Phase 6 — Rebuild Desktop App Structure

### desktop-app/src/main.tsx
- [ ] `ReactDOM.createRoot` → render `<App />`
- [ ] Wrap with `QueryClientProvider` (TanStack Query)
- [ ] Import `@shared/styles/globals.css`
- [ ] Import `@shared/styles/ui-modes.css`

### desktop-app/src/App.tsx
- [ ] `BrowserRouter` → 9 routes matching web app
- [ ] Wrap with `UIModeProvider` (from shared)
- [ ] Import `AppShell` from shared
- [ ] All page components lazy-loaded with `React.lazy()`
- [ ] Global keyboard nav (1–9 key shortcuts)

### Desktop page wrappers (desktop-app/src/pages/)
Each page is a thin wrapper that imports the shared component:

- [ ] `Dashboard.tsx` — imports shared Dashboard, adds Tauri-specific data source if needed
- [ ] `CommandCenter.tsx` — imports shared CommandCenter page content
- [ ] `ActivityTimeline.tsx`
- [ ] `Approvals.tsx`
- [ ] `History.tsx`
- [ ] `Integrations.tsx`
- [ ] `Settings.tsx`
- [ ] `Stats.tsx`
- [ ] `Workflows.tsx`

### Desktop-only components (desktop-app/src/components/)
- [ ] `TitleBar.tsx` — custom title bar with native window controls (minimize/maximize/close via Tauri window API)
- [ ] `TrayIndicator.tsx` — tray status in UI if needed
- [ ] `BackendStatus.tsx` — shows backend running/stopped, restart button
- [ ] `AlwaysOnTopOrb.tsx` — floating orb in separate Tauri window (Phase 2 feature)
- [ ] `TakeoverDesktopEnhancement.tsx` — wraps TakeoverOrchestrator, adds native notification + window state

### Desktop-only hooks (desktop-app/src/hooks/)
- [ ] `useNativeShortcuts.ts` — listens for Tauri global shortcut events
- [ ] `useWindowControls.ts` — minimize/maximize/close via `@tauri-apps/api/window`
- [ ] `useNativeNotification.ts` — wraps `tauri-plugin-notification`
- [ ] `useBackendManager.ts` — start/stop/health-check Python backend
- [ ] `useDesktopStore.ts` — persistent settings via `tauri-plugin-store`

---

## Phase 7 — Voice Pipeline Integration

- [ ] `useVoiceSession` from shared works in Tauri WebView (test VAD init)
- [ ] `useWakeWord` WebSocket connects to `ws://localhost:8000/api/v1/voice/ws`
- [ ] Microphone permission granted via `capabilities/default.json`
- [ ] `PULSE_SERVER` set in `dev:wsl` npm script for WSL2 audio
- [ ] Audio playback (TTS) works in WebView
- [ ] Waveform visualization renders correctly

---

## Phase 8 — Takeover Mode Desktop Integration

- [ ] `useTakeoverMode` hook imported from shared — state machine works
- [ ] `TakeoverOrchestrator` renders all phases correctly in Tauri WebView
- [ ] All 5 phase components render with animations
- [ ] All 7 HUD components render
- [ ] Matrix rain canvas performs acceptably (60fps target)
- [ ] Desktop enhancement: native notification fires on takeover activate
- [ ] Desktop enhancement: window goes always-on-top during takeover
- [ ] Emergency stop shortcut `Ctrl+Shift+Esc` deactivates takeover
- [ ] Takeover deactivation restores window state

---

## Phase 9 — Style & Theme Verification

- [ ] Cyberpunk dark theme renders correctly (`#08080f` background)
- [ ] All Tailwind color tokens resolve (brand, surface, bg, border, card, etc.)
- [ ] `cyber-panel` CSS class applies correctly
- [ ] UI mode badge visible in bottom-right
- [ ] UI mode transitions (focus/calm/overdrive/sentinel) work
- [ ] Framer Motion animations run at 60fps
- [ ] No FOUC (flash of unstyled content) on startup

---

## Phase 10 — API Parity Verification

Test each endpoint from the desktop app:

- [ ] GET `/api/v1/health` — returns 200
- [ ] GET `/api/v1/status` — returns system status
- [ ] POST `/api/v1/commands` — submits command, receives result
- [ ] GET `/api/v1/approvals` — lists pending approvals
- [ ] POST `/api/v1/approvals/:id/approve` — approval works
- [ ] GET `/api/v1/activity` — activity feed loads
- [ ] GET `/api/v1/integrations` — integrations list loads
- [ ] GET `/api/v1/workflows` — workflows list loads
- [ ] GET `/api/v1/drafts` — drafts load
- [ ] POST `/api/v1/voice/transcribe` — voice transcription works
- [ ] GET `/api/v1/cognition/state` — cognitive state poll works
- [ ] WS `ws://localhost:8000/api/v1/voice/ws` — WebSocket connects

---

## Phase 11 — Desktop-Native Feature Verification

- [ ] System tray appears on startup
- [ ] Tray → Show/Hide works
- [ ] Tray → Quit exits cleanly
- [ ] `Alt+Space` shows/hides window
- [ ] `Ctrl+Shift+Space` toggles voice session
- [ ] Native notification fires on takeover activation
- [ ] Backend auto-starts if not running
- [ ] Backend shutdown on app quit
- [ ] Window state (size/position) persists across restarts
- [ ] Clipboard write works
- [ ] `launch_application` opens Windows app from WSL2

---

## Phase 12 — Performance Verification

- [ ] `htop` / Task Manager: RAM < 350MB at idle
- [ ] Cold start to interactive: < 2 seconds (measure 5x, take median)
- [ ] Route switch time < 100ms (React DevTools profiler)
- [ ] Voice wake → audio response < 1200ms
- [ ] No memory leaks after 10 minutes of use (heap snapshot before/after)
- [ ] All page routes code-split (check Vite bundle output)
- [ ] `tauri build` produces working binary

---

## Phase 13 — Final Validation

### Compare routes
- [ ] `/` (Dashboard) — visually matches web
- [ ] `/commands` — all sub-panels present
- [ ] `/activity` — feed matches
- [ ] `/approvals` — approval flow works end-to-end
- [ ] `/history` — history loads and is searchable
- [ ] `/integrations` — integration cards render
- [ ] `/settings` — settings save/load correctly
- [ ] `/stats` — stats charts render
- [ ] `/workflows` — workflow list renders

### Compare components (spot-check)
- [ ] `TakeoverOrchestrator` — all 5 phases animate correctly
- [ ] `BrainPanel` — renders in dashboard
- [ ] `ConversationThread` — chat bubbles render correctly
- [ ] `PassiveHUD` — ambient HUD visible
- [ ] `ProactiveToast` — toast appears on proactive event

### Compare hooks (integration test)
- [ ] `useCognitiveState` — polls and returns data
- [ ] `useEmotionState` — SSE stream connects
- [ ] `useTakeoverMode` — phase state machine transitions correctly
- [ ] `useVoiceSession` — full voice flow works
- [ ] `useWakeWord` — wake detection triggers session

### Compare settings
- [ ] Settings page in desktop matches web settings page
- [ ] Profile switching works

### Compare voice features
- [ ] Wake word detection
- [ ] Voice session start/stop
- [ ] TTS playback
- [ ] Conversation thread scrolling
- [ ] Follow-up chip appears after AI response

---

## Cleanup

- [ ] Delete `desktop-app/node_modules/.cache`
- [ ] Delete any remaining `*.electron.*` files
- [ ] Remove `cross-env` from package.json if still present
- [ ] Remove any `window.electronAPI` type declarations
- [ ] Run `tsc --noEmit` — zero errors
- [ ] Run `cargo clippy` in `src-tauri/` — zero warnings
- [ ] Final `git status` — no unintended files staged
- [ ] Update `SETUP.md` with new `tauri dev` / `tauri build` commands
- [ ] Update `CLAUDE.md` with Tauri dev instructions

---

## Branch & PR

- [ ] All changes on `feat/tauri-migration` branch
- [ ] Create PR to `main` with this checklist as body
- [ ] Do NOT merge until all Phase 13 checks pass
