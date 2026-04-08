import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('aiOperator', {
  // Core
  submitCommand: (text: string, source?: string) =>
    ipcRenderer.invoke('submit-command', text, source),
  getCommands: () => ipcRenderer.invoke('get-commands'),
  getCommand: (id: string) => ipcRenderer.invoke('get-command', id),
  getHealth: () => ipcRenderer.invoke('get-health'),

  // Integrations & activity
  getIntegrations: () => ipcRenderer.invoke('get-integrations'),
  getActivity: () => ipcRenderer.invoke('get-activity'),

  // Window
  hideWindow: () => ipcRenderer.invoke('hide-window'),
  openDashboard: () => ipcRenderer.invoke('open-dashboard'),
  openUrl: (url: string) => ipcRenderer.invoke('open-url', url),

  // Clipboard
  copyText: (text: string) => ipcRenderer.invoke('copy-text', text),

  // Platform info
  platform: process.platform,
})
