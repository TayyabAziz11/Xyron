// Startup orchestrator — runs once on app boot.
// Each phase is independent and logs to startupStore.
import { useStartupStore } from '@desktop/stores/startupStore'

async function checkBackend(): Promise<boolean> {
  try {
    const r = await fetch('http://localhost:8000/api/v1/health', {
      signal: AbortSignal.timeout(4000),
    })
    return r.ok
  } catch {
    return false
  }
}

async function checkWakeWS(): Promise<boolean> {
  return new Promise<boolean>(resolve => {
    try {
      const ws = new WebSocket('ws://localhost:8000/api/v1/voice/ws/wake')
      ws.onopen  = () => { ws.close(); resolve(true) }
      ws.onerror = () => resolve(false)
      setTimeout(() => { try { ws.close() } catch {} resolve(false) }, 3000)
    } catch {
      resolve(false)
    }
  })
}

export async function runStartupSequence(): Promise<void> {
  const { log, setReady, setError } = useStartupStore.getState()

  try {
    log('APP_BOOT', 'Xyron OS initializing')

    // Auth restore — handled by Clerk externally; we just mark it
    log('AUTH_RESTORE', 'Restoring session from Clerk')

    // Tier
    const stored = localStorage.getItem('xyron-subscription')
    const tier = stored ? (JSON.parse(stored)?.state?.tier ?? 'free') : 'free'
    log('TIER_RESOLVED', `Subscription tier: ${tier.toUpperCase()}`)

    // Voice
    try {
      await navigator.mediaDevices.enumerateDevices()
      const hasAudio = (await navigator.mediaDevices.enumerateDevices())
        .some(d => d.kind === 'audioinput')
      log('VOICE_READY', hasAudio ? 'Audio input detected' : 'No audio input found', hasAudio)
    } catch {
      log('VOICE_READY', 'Audio enumeration unavailable', false)
    }

    // Backend
    const backendOk = await checkBackend()
    log('BACKEND_READY', backendOk ? 'Backend online at :8000' : 'Backend offline — running in degraded mode', backendOk)

    // WS session
    if (backendOk) {
      const wsOk = await checkWakeWS()
      log('SESSION_READY', wsOk ? 'Wake-word WebSocket ready' : 'Wake WS not available', wsOk)
    } else {
      log('SESSION_READY', 'Skipped — backend offline', false)
    }

    setReady()
  } catch (err) {
    setError(String(err))
  }
}
