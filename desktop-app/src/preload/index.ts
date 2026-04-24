import { contextBridge, ipcRenderer } from 'electron'

// ── Expose safe IPC API to the renderer ────────────────────────────────────────
contextBridge.exposeInMainWorld('electronAPI', {
  // Commands
  submitCommand:   (text: string, source?: string) => ipcRenderer.invoke('submit-command', text, source),
  getCommands:     ()                               => ipcRenderer.invoke('get-commands'),
  getCommand:      (id: string)                     => ipcRenderer.invoke('get-command', id),

  // Activity
  getActivity:     ()                               => ipcRenderer.invoke('get-activity'),

  // System
  getHealth:       ()                               => ipcRenderer.invoke('get-health'),
  getIntegrations: ()                               => ipcRenderer.invoke('get-integrations'),
  getBackendStatus:()                               => ipcRenderer.invoke('get-backend-status'),

  // Approvals
  approveCommand:  (id: string)                     => ipcRenderer.invoke('approve-command', id),
  rejectCommand:   (id: string)                     => ipcRenderer.invoke('reject-command', id),

  // Shell / utility
  openUrl:      (url: string)     => ipcRenderer.invoke('open-url', url),
  copyText:     (text: string)    => ipcRenderer.invoke('copy-text', text),
  launchApp:    (command: string) => ipcRenderer.invoke('launch-app', command),
  getSystemInfo: ()               => ipcRenderer.invoke('get-system-info'),

  // Window controls
  minimize: () => ipcRenderer.invoke('minimize-window'),
  maximize: () => ipcRenderer.invoke('maximize-window'),
  close:    () => ipcRenderer.invoke('close-window'),

  // Desktop automation — instant PowerShell execution
  automateType:   (text: string) => ipcRenderer.invoke('automate-type', text),
  automateHotkey: (keys: string) => ipcRenderer.invoke('automate-hotkey', keys),
  takeScreenshot: ()             => ipcRenderer.invoke('take-screenshot'),
})

// Type augmentation — gives TypeScript callers proper types in the renderer
export type ElectronAPI = {
  submitCommand:    (text: string, source?: string) => Promise<unknown>
  getCommands:      ()                              => Promise<unknown>
  getCommand:       (id: string)                    => Promise<unknown>
  getActivity:      ()                              => Promise<unknown>
  getHealth:        ()                              => Promise<unknown>
  getIntegrations:  ()                              => Promise<unknown>
  getBackendStatus: ()                              => Promise<{ running: boolean; status?: string }>
  approveCommand:   (id: string)                    => Promise<unknown>
  rejectCommand:    (id: string)                    => Promise<unknown>
  openUrl:       (url: string)     => Promise<void>
  copyText:      (text: string)    => Promise<boolean>
  launchApp:     (command: string) => Promise<void>
  getSystemInfo: ()                => Promise<string | null>
  minimize:   ()             => Promise<void>
  maximize:   ()             => Promise<void>
  close:      ()             => Promise<void>
  automateType:   (text: string) => Promise<boolean>
  automateHotkey: (keys: string) => Promise<boolean>
  takeScreenshot: ()             => Promise<string | null>
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
