# Tauri Architecture — Xyron Desktop
_Generated: 2026-05-18_

## Overview

Xyron desktop is rebuilt on **Tauri v2** with a shared frontend package consumed by the desktop app. The Electron main process and preload bridge are completely removed. All API calls go directly to the FastAPI backend at `http://localhost:8000` — identical to how the web app works.

---

## Repository Structure

```
Xyron/
├── shared/                         # Shared frontend package
│   ├── components/                 # All React components (copied from web/src/components/)
│   │   ├── layout/
│   │   ├── dashboard/
│   │   ├── takeover/
│   │   ├── voice/
│   │   ├── ambient/
│   │   ├── system/
│   │   ├── command/
│   │   ├── tasks/
│   │   ├── code/
│   │   ├── im-home/
│   │   ├── ui/
│   │   ├── activity/
│   │   ├── approvals/
│   │   ├── integrations/
│   │   └── workflows/
│   ├── hooks/                      # All hooks (copied from web/src/hooks/)
│   ├── state/                      # emotionState.ts, Zustand stores
│   ├── contexts/                   # UIModeContext, etc.
│   ├── config/                     # emotions.ts
│   ├── lib/                        # api.ts, types.ts, utils.ts, voice-core.ts
│   └── styles/                     # globals.css, ui-modes.css
│
├── web/                            # Next.js 15 web app (unchanged)
│   └── src/
│       └── app/                    # App Router pages
│
├── desktop-app/                    # Tauri v2 desktop
│   ├── src/                        # React frontend (Vite SPA)
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── pages/                  # Desktop page wrappers
│   │   ├── components/             # Desktop-only components (tray, overlays, etc.)
│   │   ├── hooks/                  # Desktop-only hooks (useNativeNotification, etc.)
│   │   └── styles/
│   ├── src-tauri/                  # Tauri Rust backend
│   │   ├── src/
│   │   │   ├── main.rs             # Tauri app entry
│   │   │   ├── lib.rs              # Command implementations
│   │   │   ├── backend.rs          # Python backend auto-start
│   │   │   └── shell.rs            # PowerShell/WSL2 bridge
│   │   ├── Cargo.toml
│   │   ├── tauri.conf.json
│   │   └── capabilities/
│   │       └── default.json        # Tauri permission grants
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   └── package.json
│
└── backend/                        # FastAPI Python backend (unchanged)
```

---

## Frontend Architecture

### SPA Routing (React Router v6)
The desktop is a Vite SPA — no Next.js. Same routing model as the old Electron renderer, but with full parity of web routes:

```typescript
// desktop-app/src/App.tsx
<Routes>
  <Route path="/"             element={<Dashboard />} />
  <Route path="/commands"     element={<CommandCenter />} />
  <Route path="/activity"     element={<ActivityTimeline />} />
  <Route path="/approvals"    element={<Approvals />} />
  <Route path="/history"      element={<History />} />
  <Route path="/integrations" element={<Integrations />} />
  <Route path="/settings"     element={<Settings />} />
  <Route path="/stats"        element={<Stats />} />
  <Route path="/workflows"    element={<Workflows />} />
</Routes>
```

Pages import components from `shared/` — no duplication.

### State Management
| Layer | Tool | Scope |
|---|---|---|
| Server state | TanStack Query v5 | API fetch/cache/refetch |
| Global UI state | Zustand | emotion, UI mode, takeover phase |
| Component state | React useState/useReducer | Local |
| Shared context | React Context | UIModeContext, etc. |

### API Layer
All API calls use the same `lib/api.ts` from `shared/`. The `API_BASE` resolves to:
- `http://localhost:8000` in Tauri (hardcoded for desktop)
- `''` (relative) in Next.js web app (server-side proxied)

No IPC, no contextBridge. Direct HTTP — same as the web app.

### Tauri Invocations
Only used for things that genuinely need OS access. Tauri commands are called from hooks in `desktop-app/src/hooks/`:

```typescript
import { invoke } from '@tauri-apps/api/core'

// Examples
await invoke('launch_application', { command: 'notepad.exe' })
await invoke('take_screenshot')
await invoke('get_system_info')
```

---

## Rust Backend (src-tauri/)

### main.rs — App Builder
```rust
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_global_shortcut::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::init())
        .plugin(tauri_plugin_window_state::init())
        .plugin(tauri_plugin_store::init())
        .invoke_handler(tauri::generate_handler![
            launch_application,
            take_screenshot,
            get_system_info,
            start_backend,
            stop_backend,
            open_url,
            copy_to_clipboard,
        ])
        .setup(|app| {
            // System tray setup
            // Backend auto-start
            // Global shortcut registration
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Xyron");
}
```

### Tauri Commands
| Command | Replaces | Implementation |
|---|---|---|
| `launch_application` | IPC `launch-app` | `tauri_plugin_shell` + WSL2 detection |
| `take_screenshot` | IPC `take-screenshot` | `tauri_plugin_fs` + platform screenshot |
| `get_system_info` | IPC `get-system-info` | `tauri_plugin_shell` → PowerShell |
| `start_backend` | Electron `startBackend()` | `tauri_plugin_shell` → `python3 -m uvicorn` |
| `stop_backend` | Electron `backendProcess.kill()` | Process handle kill |
| `open_url` | IPC `open-url` + `shell.openExternal` | `tauri_plugin_shell` open |
| `copy_to_clipboard` | IPC `copy-text` | `tauri_plugin_clipboard_manager` |

### Window Controls (no IPC needed)
```typescript
import { getCurrentWindow } from '@tauri-apps/api/window'
const win = getCurrentWindow()
win.minimize()
win.maximize()
win.close()
```

---

## System Tray
Implemented in Rust setup closure using `tauri_plugin_tray` (if needed) or via Tauri's built-in tray API:

```rust
let tray = TrayIconBuilder::new()
    .icon(app.default_window_icon().unwrap().clone())
    .menu(&menu)
    .on_menu_event(...)
    .build(app)?;
```

---

## Global Shortcuts
```rust
app.global_shortcut().register("Alt+Space", || {
    // toggle main window visibility
})?;
```

Mapped in `tauri.conf.json` capabilities.

---

## Window Configuration (tauri.conf.json)
```json
{
  "app": {
    "windows": [{
      "title": "Xyron",
      "width": 1280,
      "height": 800,
      "minWidth": 900,
      "minHeight": 600,
      "decorations": true,
      "transparent": false,
      "resizable": true,
      "center": true
    }]
  },
  "bundle": {
    "identifier": "com.tayyabaziz.xyron",
    "productName": "Xyron",
    "icon": ["icons/icon.png", "icons/icon.ico"]
  }
}
```

---

## Backend Auto-Start
```rust
// backend.rs
pub fn start_backend(app_handle: &AppHandle) -> Result<Child> {
    let backend_dir = if cfg!(debug_assertions) {
        // dev: relative path to backend/
    } else {
        // prod: app resource path
        app_handle.path().resource_dir()?.join("backend")
    };
    
    Command::new("python3")
        .args(["-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"])
        .current_dir(&backend_dir)
        .spawn()
}
```

---

## Audio Pipeline
The desktop app is the primary runtime for voice. Audio handling:
- **VAD**: `@ricky0123/vad-react` (same as web) — runs in browser context within WebView
- **WebSocket**: Direct WS to `ws://localhost:8000/api/v1/voice/ws` — no Electron IPC needed
- **TTS playback**: Web Audio API in WebView — same as web
- **Wake word**: `useWakeWord` hook → WS → backend

WSL2 audio: set `PULSE_SERVER=unix:/mnt/wslg/PulseServer` as environment variable before spawning the Tauri process (handled by `dev:wsl` script).

---

## Permissions (capabilities/default.json)
```json
{
  "identifier": "default",
  "description": "Default Xyron capabilities",
  "platforms": ["linux", "windows", "macOS"],
  "permissions": [
    "core:default",
    "shell:allow-execute",
    "shell:allow-open",
    "fs:allow-read-dir",
    "fs:allow-write-file",
    "notification:default",
    "clipboard-manager:allow-read",
    "clipboard-manager:allow-write",
    "dialog:default",
    "process:default",
    "global-shortcut:allow-register",
    "global-shortcut:allow-unregister-all",
    "window-state:default"
  ]
}
```

---

## Performance Targets
| Metric | Target | Strategy |
|---|---|---|
| RAM idle | < 350 MB | Tauri WebView2/WKWebView vs Electron Chromium (saves ~200MB) |
| Startup | < 2 sec | Rust startup + Vite pre-bundled SPA |
| Window open | Instant | No second Chromium process |
| Route switch | < 100ms | React Router + lazy() + Suspense |
| Voice latency | < 300ms | Direct WS, no IPC hop |

### Code Splitting
```typescript
const CommandCenter = lazy(() => import('./pages/CommandCenter'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
// etc.
```

---

## WSL2-Specific Notes
- Desktop still runs on Linux (WSL2) during development
- Rust process spawns `python3` (Linux) for backend
- For Windows automation: detect WSL2 via `/proc/version`, then spawn `powershell.exe` or `cmd.exe` directly using `tauri_plugin_shell` with `shell: false`
- Audio: `TAURI_PULSE_SERVER` env var or launch wrapper script
