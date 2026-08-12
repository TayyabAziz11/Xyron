import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useVoiceWS } from '@/hooks/useVoiceWS'
import { useWakeWord } from '@/hooks/useWakeWord'

type VoiceSession = ReturnType<typeof useVoiceWS>

interface VoiceRuntimeValue extends VoiceSession {
  wakeSupported: boolean
  wakeListening: boolean
}

const VoiceRuntimeContext = createContext<VoiceRuntimeValue | null>(null)

// Module-level singleton guard. React StrictMode double-invokes effects in
// dev (mount → cleanup → mount) for the SAME provider instance — that
// sequence is safe because cleanup releases the slot before the second
// mount claims it. What this guards against is a genuine second
// VoiceRuntimeProvider appearing in the tree at the same time (e.g. a
// future refactor that accidentally nests it twice) — the second instance
// sees the slot already held and never enables its own wake pipeline,
// so it can never open a second live wake/session socket.
let _activeInstance: symbol | null = null

export function VoiceRuntimeProvider({ children }: { children: ReactNode }) {
  const session    = useVoiceWS()
  const sessionRef = useRef(session)
  sessionRef.current = session

  // wakeActivate is the single, stable entry point from a wake event (or a
  // manual activation call) into the session lifecycle. Moved here from
  // CommandCenter.tsx so it's owned by the runtime, not any one page.
  const wakeActivate = useCallback(() => {
    const s = sessionRef.current
    if (!s.isActive) s.startSession()
    else if (s.state === 'idle' || s.state === 'listening') s.startListening()
  }, [])

  const [wakeEnabled, setWakeEnabled] = useState(false)
  const isOwnerRef = useRef(false)

  useEffect(() => {
    if (_activeInstance !== null) {
      console.warn('[DUPLICATE_VOICE_RUNTIME_BLOCKED]')
      isOwnerRef.current = false
      return
    }
    const mySlot = Symbol('voice-runtime-instance')
    _activeInstance = mySlot
    isOwnerRef.current = true
    console.log('[GLOBAL_VOICE_RUNTIME_MOUNTED]')

    // Delay wake-word startup by 1.5s so the page renders fully before
    // getUserMedia fires — prevents WebKit2GTK freezing the Tauri renderer
    // on the first paint after login (same rationale as the previous
    // per-page delay in CommandCenter.tsx).
    const t = setTimeout(() => {
      if (_activeInstance === mySlot) setWakeEnabled(true)
    }, 1500)

    return () => {
      clearTimeout(t)
      if (_activeInstance === mySlot) {
        _activeInstance = null
        isOwnerRef.current = false
        console.log('[GLOBAL_VOICE_RUNTIME_UNMOUNTED]')
      }
    }
  }, [])

  // A blocked duplicate never enables its own wake pipeline — it renders
  // the same context shape (so consumers don't crash) but stays inert.
  const effectiveWakeEnabled = wakeEnabled && isOwnerRef.current

  const { supported: wakeSupported, listening: wakeListening } = useWakeWord(wakeActivate, effectiveWakeEnabled)

  const value: VoiceRuntimeValue = { ...session, wakeSupported, wakeListening }

  return (
    <VoiceRuntimeContext.Provider value={value}>
      {children}
    </VoiceRuntimeContext.Provider>
  )
}

export function useVoiceRuntime(): VoiceRuntimeValue {
  const ctx = useContext(VoiceRuntimeContext)
  if (!ctx) {
    throw new Error('useVoiceRuntime() must be used within <VoiceRuntimeProvider>')
  }
  return ctx
}
