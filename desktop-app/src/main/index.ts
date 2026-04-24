import {
  app,
  BrowserWindow,
  Tray,
  Menu,
  globalShortcut,
  ipcMain,
  nativeImage,
  shell,
  session,
  clipboard,
} from 'electron'
import * as path from 'path'
import * as fs from 'fs'
import { exec, spawn, ChildProcess } from 'child_process'
import * as http from 'http'
import { is } from '@electron-toolkit/utils'

// ── Read key=value pairs from a .env file ─────────────────────────────────────
function readDotEnv(dir: string): Record<string, string> {
  const envPath = path.join(dir, '.env')
  try {
    const lines = fs.readFileSync(envPath, 'utf-8').split('\n')
    const out: Record<string, string> = {}
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eqIdx = trimmed.indexOf('=')
      if (eqIdx < 1) continue
      const key = trimmed.slice(0, eqIdx).trim()
      const val = trimmed.slice(eqIdx + 1).trim()
      if (key && val) out[key] = val
    }
    return out
  } catch {
    return {}
  }
}

// ── WSL2 / Linux audio routing ─────────────────────────────────────────────────
if (process.platform === 'linux') {
  process.env.PULSE_SERVER = process.env.PULSE_SERVER || 'unix:/mnt/wslg/PulseServer'
  app.commandLine.appendSwitch('disable-features', 'WebRtcHideLocalIpsWithMdns,MediaSessionService,GlobalMediaControls')
  app.commandLine.appendSwitch('use-fake-ui-for-media-stream', 'false')
  process.env.LIBASOUND_THREAD_SAFE = '0'
}

app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-dev-shm-usage')
// Enable Web Speech API in Electron (needed for speech recognition in renderer)
app.commandLine.appendSwitch('enable-features', 'SpeechRecognition,AudioCapture')
app.commandLine.appendSwitch('enable-speech-dispatcher')

const API_BASE = 'http://localhost:8000'
let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let backendProcess: ChildProcess | null = null

process.on('uncaughtException', (err) => {
  console.error('[Main] Uncaught exception:', err.message)
})

// ── Cross-platform runtime detection ──────────────────────────────────────────
const _IS_WSL = process.platform === 'linux' && (() => {
  try { return fs.readFileSync('/proc/version', 'utf8').toLowerCase().includes('microsoft') } catch { return false }
})()

const _CMDEXE: string | null = (() => {
  if (!_IS_WSL) return null
  for (const p of [
    '/mnt/c/Windows/System32/cmd.exe',
    '/mnt/c/WINDOWS/System32/cmd.exe',
    '/mnt/c/WINDOWS/system32/cmd.exe',
  ]) { if (fs.existsSync(p)) return p }
  return null
})()

/** Convert a Windows path (C:\...) to WSL path (/mnt/c/...) */
function winToWsl(p: string): string {
  return p.replace(/^([A-Za-z]):[\\\/]/, (_, d) => `/mnt/${d.toLowerCase()}/`).replace(/\\/g, '/')
}

/** Extract the target path/URL from a Windows shell command */
function extractWinTarget(cmd: string): string | null {
  // start "" "path"  →  path
  const m1 = cmd.match(/start\s+"[^"]*"\s+"([^"]+)"/)
  if (m1) return m1[1]
  // start url  →  url
  const m2 = cmd.match(/^start\s+(\S.+)$/)
  if (m2) return m2[1].trim()
  // explorer path  →  path
  const m3 = cmd.match(/^explorer\s+(.+)$/)
  if (m3) return m3[1].trim()
  return null
}

/**
 * Spawn a Windows binary from WSL2 WITHOUT going through /bin/sh.
 * exec() wraps commands in `/bin/sh -c "..."` — /bin/sh cannot execute
 * Windows PE binaries. spawn() with shell:false calls execve() directly,
 * which WSL2's binfmt_misc handles transparently for .exe files.
 */
function wslSpawn(args: string[]): void {
  if (!_CMDEXE) { console.warn('[Main] wslSpawn: no cmd.exe found'); return }
  const child = spawn(_CMDEXE, args, { shell: false, stdio: 'ignore', detached: true })
  child.on('error', (err) => console.warn('[Main] wslSpawn error:', err.message))
  child.unref()
}

/** Spawn powershell.exe directly (no /bin/sh wrapper). */
function wslPowershell(psArgs: string[]): void {
  const psExe = _IS_WSL ? 'powershell.exe' : 'powershell'
  const child = spawn(psExe, psArgs, { shell: false, stdio: 'ignore', detached: true })
  child.on('error', (err) => console.warn('[Main] wslPowershell error:', err.message))
  child.unref()
}

/** Run a Windows shell command cross-platform */
function winExec(command: string): void {
  if (process.platform === 'win32') {
    exec(command, (err) => { if (err) console.warn('[Main] exec error:', err.message) })
    return
  }
  if (_IS_WSL && _CMDEXE) {
    // ✅ Use spawn (not exec) — /bin/sh cannot run Windows PE binaries
    wslSpawn(['/c', command])
    return
  }
  // Pure Linux or macOS — extract target and open natively
  const target = extractWinTarget(command)
  if (!target) { console.warn('[Main] Could not extract target from:', command); return }
  const isWinPath = /^[A-Za-z]:[\\\/]/.test(target)
  const localTarget = (isWinPath && _IS_WSL) ? winToWsl(target) : target
  const opener = process.platform === 'darwin' ? 'open' : 'xdg-open'
  exec(`${opener} "${localTarget.replace(/"/g, '\\"')}"`, (err) => {
    if (err) console.warn('[Main] open error:', err.message)
  })
}

// ── URL opener ────────────────────────────────────────────────────────────────
function openUrl(url: string): void {
  if (process.platform === 'win32') {
    shell.openExternal(url).catch(console.warn)
  } else if (_IS_WSL && _CMDEXE) {
    // ✅ spawn cmd.exe directly — no /bin/sh wrapper
    wslSpawn(['/c', 'start', '', url])
  } else if (process.platform === 'darwin') {
    exec(`open "${url.replace(/"/g, '\\"')}"`)
  } else {
    exec(`xdg-open "${url.replace(/"/g, '\\"')}" 2>/dev/null || wslview "${url.replace(/"/g, '\\"')}"`)
  }
}

// ── Backend HTTP helpers ───────────────────────────────────────────────────────
function apiGet(endpoint: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    http.get(`${API_BASE}${endpoint}`, (res) => {
      let data = ''
      res.on('data', (c: string) => { data += c })
      res.on('end', () => { try { resolve(JSON.parse(data)) } catch { resolve({}) } })
    }).on('error', reject)
  })
}

function apiPost(endpoint: string, body: object): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body)
    const req = http.request(
      {
        hostname: 'localhost', port: 8000, path: endpoint,
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) },
      },
      (res) => {
        let data = ''
        res.on('data', (c: string) => { data += c })
        res.on('end', () => { try { resolve(JSON.parse(data)) } catch { resolve({}) } })
      },
    )
    req.on('error', reject)
    req.write(payload)
    req.end()
  })
}

// ── Backend auto-start (optional) ─────────────────────────────────────────────
function startBackend(): void {
  // Only attempt if backend is not already running
  http.get(`${API_BASE}/api/v1/health`, (res) => {
    if (res.statusCode === 200) {
      console.log('[Main] Backend already running — skipping auto-start')
    }
  }).on('error', () => {
    // Backend not running — try to start it
    const backendDir = is.dev
      ? path.join(__dirname, '../../..', 'backend')
      : path.join(process.resourcesPath, 'backend')

    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3'
    const uvicornArgs = ['-m', 'uvicorn', 'api.main:app', '--host', '0.0.0.0', '--port', '8000', '--reload']

    console.log('[Main] Starting backend from:', backendDir)

    // Read .env file from backendDir and merge into env so the key is
    // always present even if OPENAI_API_KEY is empty in the shell env.
    const dotEnvVars = readDotEnv(backendDir)
    const backendEnv: NodeJS.ProcessEnv = { ...process.env }
    for (const [k, v] of Object.entries(dotEnvVars)) {
      if (v) backendEnv[k] = v  // .env value wins over empty shell var
    }

    backendProcess = spawn(pythonCmd, uvicornArgs, {
      cwd: backendDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: backendEnv,
    })

    backendProcess.stdout?.on('data', (d: Buffer) => {
      const line = d.toString().trim()
      if (line) console.log('[Backend]', line)
    })

    backendProcess.stderr?.on('data', (d: Buffer) => {
      const line = d.toString().trim()
      if (line && !line.includes('INFO') && !line.includes('WARNING')) {
        console.error('[Backend]', line)
      }
    })

    backendProcess.on('exit', (code) => {
      console.log('[Main] Backend exited with code:', code)
      backendProcess = null
    })
  })
}

// ── Main window ────────────────────────────────────────────────────────────────
function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#08080f',
    title: 'Xyron',
    frame: true,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // Disable CORS for the renderer — this app only talks to its own localhost
      // backend, so browser same-origin policy adds no security value here.
      webSecurity: false,
    },
  })

  // Load renderer
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
    win.webContents.openDevTools({ mode: 'detach' })
  } else {
    win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }

  win.on('ready-to-show', () => {
    win.show()
    win.focus()
  })

  win.on('closed', () => {
    mainWindow = null
  })

  // Open external links in system browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    openUrl(url)
    return { action: 'deny' }
  })

  return win
}

// ── Tray ───────────────────────────────────────────────────────────────────────
function tryCreateTray(): Tray | null {
  try {
    const FALLBACK_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    const iconPath = path.join(__dirname, '../../assets/icon.png')
    let icon: Electron.NativeImage
    try {
      icon = nativeImage.createFromPath(iconPath)
      if (icon.isEmpty()) throw new Error('empty')
    } catch {
      icon = nativeImage.createFromBuffer(Buffer.from(FALLBACK_B64, 'base64'))
    }

    const t = new Tray(icon)
    t.setToolTip('Xyron')
    t.setContextMenu(Menu.buildFromTemplate([
      { label: 'Xyron', enabled: false },
      { type: 'separator' },
      { label: 'Show / Hide', click: () => {
          if (mainWindow?.isVisible()) mainWindow.hide()
          else { mainWindow?.show(); mainWindow?.focus() }
        }
      },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]))
    t.on('double-click', () => { mainWindow?.show(); mainWindow?.focus() })
    return t
  } catch (err) {
    console.warn('[Main] Tray unavailable:', (err as Error).message)
    return null
  }
}

// ── IPC handlers ───────────────────────────────────────────────────────────────
function registerIpcHandlers(): void {
  ipcMain.handle('submit-command', async (_e, text: string, source = 'desktop') => {
    try { return await apiPost('/api/v1/commands', { text, source }) }
    catch { return null }
  })

  ipcMain.handle('get-commands', async () => {
    try { return await apiGet('/api/v1/commands?limit=20') }
    catch { return null }
  })

  ipcMain.handle('get-command', async (_e, id: string) => {
    try { return await apiGet(`/api/v1/commands/${id}`) }
    catch { return null }
  })

  ipcMain.handle('get-activity', async () => {
    try { return await apiGet('/api/v1/activity?limit=20') }
    catch { return null }
  })

  ipcMain.handle('get-health', async () => {
    try { return await apiGet('/api/v1/health') }
    catch { return { error: true } }
  })

  ipcMain.handle('get-integrations', async () => {
    try { return await apiGet('/api/v1/integrations') }
    catch { return null }
  })

  ipcMain.handle('approve-command', async (_e, id: string) => {
    try { return await apiPost(`/api/v1/approvals/${id}/approve`, {}) }
    catch { return null }
  })

  ipcMain.handle('reject-command', async (_e, id: string) => {
    try { return await apiPost(`/api/v1/approvals/${id}/reject`, {}) }
    catch { return null }
  })

  ipcMain.handle('open-url', (_e, url: string) => {
    openUrl(url)
  })

  ipcMain.handle('get-system-info', async () => {
    if (process.platform !== 'linux' && process.platform !== 'win32') return null
    return new Promise<string | null>((resolve) => {
      // PowerShell script that returns pipe-delimited: OS|Build|CPU|Cores|Threads|RAMgb
      const psLines = [
        '$os  = Get-CimInstance Win32_OperatingSystem',
        '$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1',
        '$ram = [math]::Round($os.TotalVisibleMemorySize/1MB, 1)',
        'Write-Output "$($os.Caption)|$($os.BuildNumber)|$($cpu.Name)|$($cpu.NumberOfCores)|$($cpu.NumberOfLogicalProcessors)|${ram}GB"',
      ].join('; ')
      // Encode as UTF-16LE base64 to avoid shell-escaping issues
      const encoded = Buffer.from(psLines, 'utf16le').toString('base64')
      // ✅ spawn powershell.exe directly — exec() wraps in /bin/sh which can't run PE binaries
      const psExe = process.platform === 'linux' ? 'powershell.exe' : 'powershell'
      let stdout = ''
      const child = spawn(psExe, ['-NoProfile', '-EncodedCommand', encoded], {
        shell: false, stdio: ['ignore', 'pipe', 'pipe'],
      })
      child.stdout?.on('data', (d: Buffer) => { stdout += d.toString() })
      child.on('error', (err) => { console.warn('[Main] get-system-info error:', err.message); resolve(null) })
      child.on('close', (code) => {
        if (code !== 0) { console.warn('[Main] get-system-info exit', code); resolve(null) }
        else resolve(stdout.trim())
      })
      setTimeout(() => { try { child.kill() } catch {} resolve(null) }, 12000)
    })
  })

  ipcMain.handle('launch-app', (_e, command: string) => {
    if (typeof command !== 'string' || command.length > 600) return
    // Block shell injection characters only — allow Unicode paths (Urdu, Arabic, etc.)
    // Dangerous: backtick, $(), semicolon outside quotes, pipe outside start, &&, ||
    if (/[`$;&]|&&|\|\|/.test(command)) {
      console.warn('[Main] launch-app: blocked unsafe command:', command)
      return
    }
    console.log('[Main] launch-app:', command)
    winExec(command)
  })

  ipcMain.handle('copy-text', (_e, text: string) => {
    clipboard.writeText(text)
    return true
  })

  ipcMain.handle('get-backend-status', async () => {
    try {
      const r = await apiGet('/api/v1/health') as { status?: string }
      return { running: true, status: r?.status ?? 'ok' }
    } catch {
      return { running: false }
    }
  })

  ipcMain.handle('minimize-window', () => mainWindow?.minimize())
  ipcMain.handle('maximize-window', () => {
    if (mainWindow?.isMaximized()) mainWindow.unmaximize()
    else mainWindow?.maximize()
  })
  ipcMain.handle('close-window', () => mainWindow?.close())

  // ── Desktop automation — PowerShell bridge ────────────────────────────────
  // The backend calls these via HTTP when it needs Electron-side execution.
  // Primary automation path is backend → powershell.exe directly (WSL2).
  // These handlers are the renderer-facing fallback / UI feedback channel.

  ipcMain.handle('automate-type', (_e, text: string) => {
    if (!text || typeof text !== 'string') return false
    const escaped = text.replace(/'/g, "''")
    const script = `Add-Type -AssemblyName System.Windows.Forms; Start-Sleep -Milliseconds 200; [System.Windows.Forms.SendKeys]::SendWait('${escaped}')`
    // ✅ spawn powershell.exe directly — no /bin/sh wrapper
    wslPowershell(['-NoProfile', '-NonInteractive', '-Command', script])
    return true
  })

  ipcMain.handle('automate-hotkey', (_e, keys: string) => {
    if (!keys || typeof keys !== 'string') return false
    const keyMap: Record<string, string> = {
      'ctrl+c': '^c', 'ctrl+v': '^v', 'ctrl+z': '^z', 'ctrl+y': '^y',
      'ctrl+a': '^a', 'ctrl+s': '^s', 'ctrl+w': '^w', 'ctrl+t': '^t',
      'alt+tab': '%{TAB}', 'alt+f4': '%{F4}', 'enter': '{ENTER}',
      'escape': '{ESCAPE}', 'tab': '{TAB}', 'f5': '{F5}',
    }
    const sendKeys = keyMap[keys.toLowerCase()] ?? keys
    const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('${sendKeys}')`
    // ✅ spawn powershell.exe directly — no /bin/sh wrapper
    wslPowershell(['-NoProfile', '-NonInteractive', '-Command', script])
    return true
  })

  // Take a screenshot using Electron's desktopCapturer — returns base64 PNG
  ipcMain.handle('take-screenshot', async () => {
    try {
      const { desktopCapturer } = await import('electron')
      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: { width: 1920, height: 1080 },
      })
      if (sources.length === 0) return null
      const img = sources[0].thumbnail
      return img.toPNG().toString('base64')
    } catch (err) {
      console.warn('[Main] take-screenshot failed:', err)
      return null
    }
  })
}

// ── App lifecycle ──────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  console.log('[Main] Xyron starting — platform:', process.platform)

  // Grant mic / media / speech permissions without browser prompts
  const ALLOWED_PERMISSIONS = ['media', 'mediaKeySystem', 'clipboard-read', 'notifications', 'speech-recognition', 'microphone']
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(ALLOWED_PERMISSIONS.includes(permission))
  })
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return ALLOWED_PERMISSIONS.includes(permission)
  })

  registerIpcHandlers()
  startBackend()

  mainWindow = createMainWindow()
  tray = tryCreateTray()

  // Global hotkey to toggle visibility
  const hotkeys = ['Alt+Space', 'CommandOrControl+Shift+Space', 'F12']
  for (const key of hotkeys) {
    try {
      if (globalShortcut.register(key, () => {
        if (mainWindow?.isVisible()) mainWindow.hide()
        else { mainWindow?.show(); mainWindow?.focus() }
      })) {
        console.log('[Main] Hotkey registered:', key)
        break
      }
    } catch { /* try next */ }
  }

  if (process.platform === 'darwin' && app.dock) app.dock.hide()
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (backendProcess) {
    console.log('[Main] Stopping backend…')
    backendProcess.kill('SIGTERM')
    backendProcess = null
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && !tray) app.quit()
})

app.on('activate', () => {
  if (!mainWindow) mainWindow = createMainWindow()
  else { mainWindow.show(); mainWindow.focus() }
})
