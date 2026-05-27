import { create } from 'zustand'

export type StartupPhase =
  | 'APP_BOOT'
  | 'AUTH_RESTORE'
  | 'TIER_RESOLVED'
  | 'VOICE_READY'
  | 'BACKEND_READY'
  | 'SESSION_READY'
  | 'COMPLETE'
  | 'ERROR'

export interface StartupLog {
  phase: StartupPhase
  message: string
  ts: number
  ok: boolean
}

interface StartupState {
  phase: StartupPhase
  logs: StartupLog[]
  ready: boolean
  error: string | null

  setPhase: (phase: StartupPhase) => void
  log: (phase: StartupPhase, message: string, ok?: boolean) => void
  setReady: () => void
  setError: (msg: string) => void
  reset: () => void
}

export const useStartupStore = create<StartupState>((set, get) => ({
  phase: 'APP_BOOT',
  logs: [],
  ready: false,
  error: null,

  setPhase: (phase) => set({ phase }),

  log: (phase, message, ok = true) => {
    const entry: StartupLog = { phase, message, ts: Date.now(), ok }
    console.log(`[${phase}] ${message}`)
    set({ logs: [...get().logs, entry], phase })
  },

  setReady: () => set({ ready: true, phase: 'COMPLETE' }),
  setError: (error) => set({ error, phase: 'ERROR' }),
  reset: () => set({ phase: 'APP_BOOT', logs: [], ready: false, error: null }),
}))
