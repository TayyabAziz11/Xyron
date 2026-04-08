import { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, nativeImage, shell } from 'electron'
import * as path from 'path'
import * as http from 'http'

const API_BASE = 'http://localhost:8000'
let tray: Tray | null = null
let assistantWindow: BrowserWindow | null = null
let isWindowVisible = false

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
    show: false,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    transparent: false,
    backgroundColor: '#08080f',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  win.loadFile(path.join(__dirname, '..', 'src', 'renderer', 'index.html'))

  win.on('blur', () => {
    if (!win.webContents.isDevToolsOpened()) {
      win.hide()
      isWindowVisible = false
    }
  })

  return win
}

function toggleAssistant(): void {
  if (!assistantWindow) return

  if (isWindowVisible) {
    assistantWindow.hide()
    isWindowVisible = false
    return
  }

  // Position near bottom center of primary display
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

// ── Tray ───────────────────────────────────────────────────────────────────────
function createTray(): Tray {
  const iconPath = path.join(__dirname, '..', 'assets', 'tray-icon.png')
  let icon: Electron.NativeImage
  try {
    icon = nativeImage.createFromPath(iconPath)
    if (icon.isEmpty()) throw new Error('empty icon')
  } catch {
    icon = nativeImage.createEmpty()
  }

  const t = new Tray(icon)
  t.setToolTip('AI Operator')

  const menu = Menu.buildFromTemplate([
    { label: 'AI Operator', enabled: false },
    { type: 'separator' },
    { label: 'Open Assistant  (Alt+Space)', click: toggleAssistant },
    {
      label: 'Open Dashboard',
      click: () => shell.openExternal('http://localhost:3001/app/dashboard'),
    },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() },
  ])

  t.setContextMenu(menu)
  t.on('click', toggleAssistant)

  return t
}

// ── IPC Handlers ──────────────────────────────────────────────────────────────
// All handlers catch network errors silently — the renderer shows "offline" state.
ipcMain.handle('submit-command', async (_event, text: string) => {
  try { return await apiPost('/api/v1/commands', { text, source: 'desktop' }) }
  catch { return null }
})

ipcMain.handle('get-commands', async () => {
  try { return await apiGet('/api/v1/commands') }
  catch { return null }
})

ipcMain.handle('get-health', async () => {
  try { return await apiGet('/api/v1/health') }
  catch { return null }
})

ipcMain.handle('hide-window', () => {
  assistantWindow?.hide()
  isWindowVisible = false
})

ipcMain.handle('open-dashboard', () => {
  shell.openExternal('http://localhost:3001/app/dashboard')
})

// ── App lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  tray = createTray()
  assistantWindow = createAssistantWindow()

  // Global hotkey: Alt+Space (fallback to Ctrl+Shift+Space)
  const registered = globalShortcut.register('Alt+Space', toggleAssistant)
  if (!registered) {
    console.warn('Failed to register Alt+Space — trying Ctrl+Shift+Space fallback')
    globalShortcut.register('CommandOrControl+Shift+Space', toggleAssistant)
  }

  // Hide dock icon on macOS — lives in tray only
  if (process.platform === 'darwin' && app.dock) {
    app.dock.hide()
  }
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
})

// Keep running in tray when all windows closed
app.on('window-all-closed', () => {
  // Do not quit — keep alive in system tray
})
