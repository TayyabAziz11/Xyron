# Tauri Plugins — Xyron Desktop
_Generated: 2026-05-18_

## Plugin Registry

All plugins are Tauri v2 official plugins from `https://github.com/tauri-apps/plugins-workspace`.

### Cargo.toml (src-tauri/Cargo.toml)
```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon", "image-png"] }
tauri-plugin-fs = "2"
tauri-plugin-shell = "2"
tauri-plugin-process = "2"
tauri-plugin-notification = "2"
tauri-plugin-dialog = "2"
tauri-plugin-global-shortcut = "2"
tauri-plugin-updater = "2"
tauri-plugin-clipboard-manager = "2"
tauri-plugin-window-state = "2"
tauri-plugin-store = "2"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### package.json (desktop-app/package.json) — npm side
```json
{
  "dependencies": {
    "@tauri-apps/api": "^2",
    "@tauri-apps/plugin-fs": "^2",
    "@tauri-apps/plugin-shell": "^2",
    "@tauri-apps/plugin-process": "^2",
    "@tauri-apps/plugin-notification": "^2",
    "@tauri-apps/plugin-dialog": "^2",
    "@tauri-apps/plugin-global-shortcut": "^2",
    "@tauri-apps/plugin-updater": "^2",
    "@tauri-apps/plugin-clipboard-manager": "^2",
    "@tauri-apps/plugin-window-state": "^2",
    "@tauri-apps/plugin-store": "^2"
  }
}
```

---

## Plugin Details

### `tauri-plugin-fs` — Filesystem
**Replaces:** `fs.readFileSync` / `fs.writeFileSync` in Electron main process  
**Used for:** Reading `.env` file on startup, writing local cache/logs, screenshot save  
**JS API:**
```typescript
import { readTextFile, writeTextFile } from '@tauri-apps/plugin-fs'
const contents = await readTextFile('backend/.env', { baseDir: BaseDirectory.Resource })
```
**Capabilities required:** `fs:allow-read-dir`, `fs:allow-write-file`

---

### `tauri-plugin-shell` — Shell Execution
**Replaces:** `child_process.spawn` / `exec` in Electron main, PowerShell bridge  
**Used for:**
- Backend Python auto-start (`python3 -m uvicorn ...`)
- PowerShell automation (`powershell.exe -Command ...`)
- WSL2 → Windows bridge (`cmd.exe /c start ...`)
- `xdg-open` / `open` for URLs on Linux/Mac
**JS API (for desktop hooks):**
```typescript
import { Command } from '@tauri-apps/plugin-shell'
const output = await Command.create('python3', ['-m', 'uvicorn', ...]).execute()
```
**Rust API (for Tauri commands):**
```rust
use tauri_plugin_shell::ShellExt;
app.shell().command("python3").args(["-m", "uvicorn"]).spawn()?;
```
**Capabilities required:** `shell:allow-execute`, `shell:allow-open`

---

### `tauri-plugin-process` — Process Management
**Replaces:** `process.exit()`, app quit from tray menu  
**Used for:** Clean shutdown, restart after update  
**JS API:**
```typescript
import { exit, relaunch } from '@tauri-apps/plugin-process'
await exit(0)
await relaunch()  // after auto-update install
```

---

### `tauri-plugin-notification` — Native Notifications
**Replaces:** Electron `new Notification()` / `BrowserWindow.webContents.send`  
**Used for:**
- Takeover mode activation/deactivation alerts
- Proactive suggestion notifications
- Backend status alerts
**JS API:**
```typescript
import { sendNotification } from '@tauri-apps/plugin-notification'
sendNotification({ title: 'Xyron', body: 'Takeover mode activated' })
```

---

### `tauri-plugin-dialog` — Native Dialogs
**Replaces:** Browser `confirm()` / `alert()`  
**Used for:** Dangerous action confirmations (before executing risky commands)  
**JS API:**
```typescript
import { confirm } from '@tauri-apps/plugin-dialog'
const yes = await confirm('Execute this command?', { title: 'Xyron', kind: 'warning' })
```

---

### `tauri-plugin-global-shortcut` — System Hotkeys
**Replaces:** `electron.globalShortcut.register()`  
**Used for:**
- `Alt+Space` → show/hide main window
- `Ctrl+Shift+Space` → toggle voice session
- `Ctrl+Shift+Esc` → emergency stop takeover
- `F12` → dev tools (dev mode only)
**Rust registration (in setup):**
```rust
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};
app.global_shortcut().register(
    Shortcut::new(Some(Modifiers::ALT), Code::Space),
    |_app, _shortcut, event| { /* toggle window */ }
)?;
```

---

### `tauri-plugin-updater` — Auto-Updater
**Replaces:** `electron-updater`  
**Used for:** Silent background update checks and installs  
**JS API:**
```typescript
import { check } from '@tauri-apps/plugin-updater'
const update = await check()
if (update?.available) {
  await update.downloadAndInstall()
}
```
**Requires:** Update server endpoint configured in `tauri.conf.json`

---

### `tauri-plugin-clipboard-manager` — Clipboard
**Replaces:** Electron `clipboard.writeText()` / IPC `copy-text`  
**Used for:** Copying command results, code snippets, AI responses  
**JS API:**
```typescript
import { writeText, readText } from '@tauri-apps/plugin-clipboard-manager'
await writeText('copied content')
const text = await readText()
```

---

### `tauri-plugin-window-state` — Window State Persistence
**Replaces:** Manual window position/size save in Electron  
**Used for:** Remembering window size/position across restarts  
**Rust setup:**
```rust
.plugin(tauri_plugin_window_state::Builder::default().build())
```
Auto-saves and restores on next launch — no JS code needed.

---

### `tauri-plugin-store` — Persistent Key-Value Store
**Replaces:** `localStorage` (which is wiped on app data clear) and IPC-based settings storage  
**Used for:** Offline settings cache, assistant profile, last-used macros, feature flags  
**JS API:**
```typescript
import { load } from '@tauri-apps/plugin-store'
const store = await load('settings.json', { autoSave: true })
await store.set('theme', 'dark')
const theme = await store.get<string>('theme')
```

---

## Optional / Future Plugins

| Plugin | Use Case | Priority |
|---|---|---|
| `tauri-plugin-autostart` | Launch Xyron on system startup | P3 |
| `tauri-plugin-single-instance` | Prevent multiple app instances | P2 |
| `tauri-plugin-positioner` | Snap tray popover to screen corner | P2 |
| `window-vibrancy` (crate) | Native blur behind main window | P2 |
| `tauri-plugin-log` | Structured logging to file | P2 |
| `tauri-plugin-deep-link` | Handle `xyron://` URL scheme | P3 |

---

## What Is NOT Needed

| Electron Package | Tauri Equivalent | Notes |
|---|---|---|
| `electron-builder` | Tauri bundler (built-in) | `cargo tauri build` handles all platforms |
| `electron-updater` | `tauri-plugin-updater` | |
| `@electron-toolkit/utils` | Tauri utils / `tauri::AppHandle` | |
| `@electron-toolkit/tsconfig` | Standard tsconfig | |
| `cross-env` | Not needed | Tauri handles platform env |
| `7zip-bin` | Not needed | Tauri uses platform-native bundler |
| `electron-vite` | Vite (plain) | Direct `@vitejs/plugin-react` + Tauri Vite plugin |
| `react-router-dom` hash router | Same — keep | SPA routing in WebView |

---

## Tauri Vite Plugin
Required for development HMR and production build integration:

```bash
npm install --save-dev @tauri-apps/cli@next
npm install --save-dev @tauri-apps/vite-plugin
```

**vite.config.ts:**
```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { tauri } from '@tauri-apps/vite-plugin'

export default defineConfig({
  plugins: [react(), tauri()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
})
```

**package.json scripts:**
```json
{
  "scripts": {
    "dev": "tauri dev",
    "dev:wsl": "PULSE_SERVER=unix:/mnt/wslg/PulseServer tauri dev",
    "build": "tauri build",
    "build:debug": "tauri build --debug",
    "type-check": "tsc --noEmit"
  }
}
```
