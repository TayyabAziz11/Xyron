/**
 * useAudioHealth — polls /api/v1/audio/health before allowing a voice session.
 *
 * Returns the latest health result plus helpers to retry the check and trigger
 * soft recovery.  Never auto-suggests wsl --shutdown unless the backend
 * explicitly says PulseServer is missing/unrecoverable.
 */
import { useState, useEffect, useCallback, useRef } from 'react'

export interface AudioHealthState {
  checked:             boolean
  ok:                  boolean
  pulseServerExists:   boolean
  sourcesCount:        number
  sinksCount:          number
  recoveryAvailable:   boolean
  recoveryAttempted:   boolean
  suggestedAction:     string | null
  detail:              string
  checking:            boolean
  recovering:          boolean
}

const _initial: AudioHealthState = {
  checked:           false,
  ok:                false,
  pulseServerExists: false,
  sourcesCount:      0,
  sinksCount:        0,
  recoveryAvailable: false,
  recoveryAttempted: false,
  suggestedAction:   null,
  detail:            '',
  checking:          false,
  recovering:        false,
}

function _ts() { return new Date().toISOString() }

// Shape returned by /api/v1/audio/health
interface _HealthPayload {
  ok:                 boolean
  pulse_server_exists:boolean
  sources_count:      number
  sinks_count:        number
  recovery_available: boolean
  recovery_attempted: boolean
  suggested_action:   string | null
  detail:             string
}

export function useAudioHealth() {
  const [state, setState] = useState<AudioHealthState>(_initial)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const _apply = useCallback((payload: _HealthPayload, extras: Partial<AudioHealthState> = {}) => {
    if (!mountedRef.current) return
    setState({
      checked:           true,
      ok:                payload.ok,
      pulseServerExists: payload.pulse_server_exists,
      sourcesCount:      payload.sources_count,
      sinksCount:        payload.sinks_count,
      recoveryAvailable: payload.recovery_available,
      recoveryAttempted: payload.recovery_attempted,
      suggestedAction:   payload.suggested_action ?? null,
      detail:            payload.detail ?? '',
      checking:          false,
      recovering:        false,
      ...extras,
    })
  }, [])

  const check = useCallback(async () => {
    if (!mountedRef.current) return
    setState(s => ({ ...s, checking: true }))
    console.log(`[AUDIO_HEALTH_FRONTEND_CHECK] ${_ts()} fetching /api/v1/audio/health`)
    try {
      const r = await fetch('/api/v1/audio/health')
      const body = await r.json() as _HealthPayload
      if (body.ok) {
        console.log(`[AUDIO_HEALTH_FRONTEND_OK] ${_ts()} sources=${body.sources_count} sinks=${body.sinks_count}`)
      } else {
        console.warn(`[AUDIO_HEALTH_FRONTEND_FAIL] ${_ts()} detail=${body.detail} suggested=${body.suggested_action}`)
      }
      _apply(body)
    } catch (err) {
      console.warn(`[AUDIO_HEALTH_FRONTEND_FAIL] ${_ts()} fetch error: ${err}`)
      if (!mountedRef.current) return
      setState(s => ({
        ...s,
        checked:  true,
        ok:       false,
        checking: false,
        detail:   String(err),
        suggestedAction: 'Could not reach backend. Make sure Xyron backend is running.',
      }))
    }
  }, [_apply])

  const retry = useCallback(() => {
    console.log(`[AUDIO_HEALTH_RETRY_CLICKED] ${_ts()}`)
    void check()
  }, [check])

  const recover = useCallback(async () => {
    if (!mountedRef.current) return
    setState(s => ({ ...s, recovering: true }))
    console.log(`[AUDIO_HEALTH_FRONTEND_CHECK] ${_ts()} posting /api/v1/audio/recover`)
    try {
      const r = await fetch('/api/v1/audio/recover', { method: 'POST' })
      const body = await r.json() as _HealthPayload
      if (body.ok) {
        console.log(`[AUDIO_HEALTH_FRONTEND_OK] ${_ts()} recovery succeeded`)
      } else {
        console.warn(`[AUDIO_HEALTH_FRONTEND_FAIL] ${_ts()} recovery failed detail=${body.detail}`)
      }
      _apply(body, { recoveryAttempted: true })
    } catch (err) {
      console.warn(`[AUDIO_HEALTH_FRONTEND_FAIL] ${_ts()} recover error: ${err}`)
      if (!mountedRef.current) return
      setState(s => ({ ...s, recovering: false, recoveryAttempted: true }))
    }
  }, [_apply])

  // Run once on mount
  useEffect(() => { void check() }, [check])

  return { ...state, check, retry, recover }
}
