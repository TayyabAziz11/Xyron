import { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, shell } from 'electron'
import * as path from 'path'
import * as http from 'http'
import { exec } from 'child_process'

// WSL2: use explorer.exe to open URLs in the Windows default browser
function openUrl(url: string): void {
  if (process.platform === 'linux') {
    // wslview (from wslu) is preferred; fall back to explorer.exe
    exec(`wslview "${url}" 2>/dev/null || explorer.exe "${url}" 2>/dev/null`, (err) => {
      if (err) console.warn('[AI Operator] Could not open URL in browser:', url)
    })
  } else {
    shell.openExternal(url).catch(console.warn)
  }
}

const API_BASE = 'http://localhost:8000'
let tray: Tray | null = null
let assistantWindow: BrowserWindow | null = null
let isWindowVisible = false

// Prevent silent crashes from killing the process
process.on('uncaughtException', (err) => {
  console.error('[AI Operator] Uncaught exception:', err.message)
})

// ── API helpers ────────────────────────────────────────────────────────────────
function apiGet(endpoint: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    http.get(`${API_BASE}${endpoint}`, res => {
      let data = ''
      res.on('data', (c: string) => { data += c })
      res.on('end', () => { try { resolve(JSON.parse(data)) } catch { resolve({}) } })
    }).on('error', reject)
  })
}

function apiPost(endpoint: string, body: object): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body)
    const opts: http.RequestOptions = {
      hostname: 'localhost',
      port: 8000,
      path: endpoint,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
    }
    const req = http.request(opts, res => {
      let data = ''
      res.on('data', (c: string) => { data += c })
      res.on('end', () => { try { resolve(JSON.parse(data)) } catch { resolve({}) } })
    })
    req.on('error', reject)
    req.write(payload)
    req.end()
  })
}

// ── Assistant Window ───────────────────────────────────────────────────────────
function createAssistantWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 420,
    height: 560,
    // On WSL2/Linux without tray, show the window directly
    show: process.platform === 'linux',
    frame: process.platform === 'linux', // frameless only works reliably on macOS/Windows
    resizable: true,
    alwaysOnTop: false,
    skipTaskbar: false,
    backgroundColor: '#08080f',
    title: 'AI Operator',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  const htmlPath = path.join(__dirname, '..', 'src', 'renderer', 'index.html')
  win.loadFile(htmlPath).catch(err => {
    console.error('[AI Operator] Failed to load renderer:', err.message)
  })

  win.on('ready-to-show', () => {
    console.log('[AI Operator] Window ready')
    win.show()
    win.focus()
    isWindowVisible = true
  })

  win.on('closed', () => {
    assistantWindow = null
    isWindowVisible = false
  })

  return win
}

function toggleAssistant(): void {
  if (!assistantWindow) {
    assistantWindow = createAssistantWindow()
    return
  }

  if (isWindowVisible) {
    assistantWindow.hide()
    isWindowVisible = false
  } else {
    const { screen } = require('electron') as typeof import('electron')
    const display = screen.getPrimaryDisplay()
    const { width, height } = display.workAreaSize
    const winBounds = assistantWindow.getBounds()

    assistantWindow.setPosition(
      Math.round(width / 2 - winBounds.width / 2),
      Math.round(height - winBounds.height - 80),
      false,
    )
    assistantWindow.show()
    assistantWindow.focus()
    isWindowVisible = true
  }
}

// ── Tray (optional — not supported on all Linux/WSL2 setups) ──────────────────
function tryCreateTray(): Tray | null {
  try {
    // Minimal 1x1 purple PNG (base64) — fallback when no icon file
    const FALLBACK_ICON_B64 =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    const iconPath = path.join(__dirname, '..', 'assets', 'tray-icon.png')
    let icon: Electron.NativeImage

    try {
      icon = nativeImage.createFromPath(iconPath)
      if (icon.isEmpty()) throw new Error('empty')
    } catch {
      icon = nativeImage.createFromBuffer(Buffer.from(FALLBACK_ICON_B64, 'base64'))
    }

    const t = new Tray(icon)
    t.setToolTip('AI Operator')

    const menu = Menu.buildFromTemplate([
      { label: 'AI Operator', enabled: false },
      { type: 'separator' },
      { label: 'Open Assistant', click: toggleAssistant },
      { label: 'Open Dashboard', click: () => openUrl('http://localhost:3001/app/dashboard') },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ])
    t.setContextMenu(menu)
    t.on('click', toggleAssistant)
    console.log('[AI Operator] System tray created')
    return t
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    console.warn('[AI Operator] Tray not available (WSL2/headless):', msg)
    return null
  }
}

// ── IPC Handlers ──────────────────────────────────────────────────────────────
ipcMain.handle('submit-command', async (_event, text: string, source = 'desktop') => {
  try { return await apiPost('/api/v1/commands', { text, source }) }
  catch { return null }
})

ipcMain.handle('get-commands', async () => {
  try { return await apiGet('/api/v1/commands') }
  catch { return null }
})

ipcMain.handle('get-command', async (_event, id: string) => {
  try { return await apiGet(`/api/v1/commands/${id}`) }
  catch { return null }
})

ipcMain.handle('get-health', async () => {
  try { return await apiGet('/api/v1/health') }
  catch { return null }
})

ipcMain.handle('get-integrations', async () => {
  try { return await apiGet('/api/v1/integrations') }
  catch { return null }
})

ipcMain.handle('get-activity', async () => {
  try { return await apiGet('/api/v1/activity?limit=5') }
  catch { return null }
})

ipcMain.handle('hide-window', () => {
  assistantWindow?.hide()
  isWindowVisible = false
})

ipcMain.handle('open-dashboard', () => {
  openUrl('http://localhost:3001/app/dashboard')
})

ipcMain.handle('open-url', (_event, url: string) => {
  openUrl(url)
})

ipcMain.handle('copy-text', async (_event, text: string) => {
  const { clipboard } = require('electron') as typeof import('electron')
  clipboard.writeText(text)
  return true
})

// ── App lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  console.log('[AI Operator] App ready, platform:', process.platform)

  // Create window first — always works
  assistantWindow = createAssistantWindow()

  // Tray is best-effort — fails silently on WSL2
  tray = tryCreateTray()

  // Register global hotkey
  const hotkeys = ['Alt+Space', 'CommandOrControl+Shift+Space', 'F12']
  let registered = false
  for (const key of hotkeys) {
    try {
      if (globalShortcut.register(key, toggleAssistant)) {
        console.log(`[AI Operator] Hotkey registered: ${key}`)
        registered = true
        break
      }
    } catch { /* try next */ }
  }
  if (!registered) console.warn('[AI Operator] No hotkey could be registered — use the window directly')

  if (process.platform === 'darwin' && app.dock) app.dock.hide()
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
})

app.on('window-all-closed', () => {
  // On macOS/Linux keep running; on Windows quit if no tray
  if (process.platform !== 'darwin' && !tray) {
    app.quit()
  }
})

app.on('activate', () => {
  // macOS: re-open window when dock icon clicked
  if (!assistantWindow) assistantWindow = createAssistantWindow()
})
