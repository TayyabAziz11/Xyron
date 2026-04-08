import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('aiOperator', {
  submitCommand: (text: string) => ipcRenderer.invoke('submit-command', text),
  getCommands: () => ipcRenderer.invoke('get-commands'),
  getHealth: () => ipcRenderer.invoke('get-health'),
  hideWindow: () => ipcRenderer.invoke('hide-window'),
  openDashboard: () => ipcRenderer.invoke('open-dashboard'),
})
