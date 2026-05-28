import { useEffect, useRef, useState } from 'react'
import { normalizeArray } from '@desktop/lib/safeData'
import {
  dashboardApi,
  type HealthStatus, type BrainStatus, type MetricsData,
  type CognitionData, type CommandItem, type ApprovalItem,
} from '@desktop/services/dashboardApi'

export interface DashboardState {
  health:     HealthStatus | null
  brain:      BrainStatus | null
  metrics:    MetricsData | null
  cognition:  CognitionData | null
  commands:   CommandItem[]
  approvals:  ApprovalItem[]
  online:     boolean
  lastFetch:  number
}

const INITIAL: DashboardState = {
  health: null, brain: null, metrics: null, cognition: null,
  commands: [], approvals: [], online: false, lastFetch: 0,
}

// Visibility-aware polling intervals:
//   Visible   — fast: 8s, slow: 20s  (dashboard is being watched)
//   Hidden    — fast: 30s, slow: 60s (window minimized or covered)
const FAST_MS_VISIBLE  = 8_000
const SLOW_MS_VISIBLE  = 20_000
const FAST_MS_HIDDEN   = 30_000
const SLOW_MS_HIDDEN   = 60_000

export function useDashboardStatus() {
  const [state, setState] = useState<DashboardState>(INITIAL)
  const fastRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const slowRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const onlineRef  = useRef(false)  // track without triggering re-renders on each ping

  useEffect(() => {
    let alive = true

    const fast = async () => {
      try {
        const [health, metrics, cognition] = await Promise.allSettled([
          dashboardApi.health(),
          dashboardApi.metrics(),
          dashboardApi.cognition(),
        ])
        if (!alive) return
        const nowOnline = health.status === 'fulfilled'
        // Only log network state change
        if (nowOnline !== onlineRef.current) {
          console.log(nowOnline ? '[NETWORK_ONLINE] backend reachable' : '[NETWORK_OFFLINE] backend unreachable')
          onlineRef.current = nowOnline
        }
        setState(prev => ({
          ...prev,
          health:    health.status    === 'fulfilled' ? health.value    : prev.health,
          metrics:   metrics.status   === 'fulfilled' ? metrics.value   : prev.metrics,
          cognition: cognition.status === 'fulfilled' ? cognition.value : prev.cognition,
          online:    nowOnline,
          lastFetch: Date.now(),
        }))
      } catch { /* keep last state */ }
    }

    const slow = async () => {
      if (!onlineRef.current) return  // skip heavy queries when offline
      try {
        const [brain, commands, approvals] = await Promise.allSettled([
          dashboardApi.brain(),
          dashboardApi.commands(),
          dashboardApi.approvals(),
        ])
        if (!alive) return
        setState(prev => ({
          ...prev,
          brain: brain.status === 'fulfilled' ? brain.value : prev.brain,
          commands:  normalizeArray<CommandItem>(
            commands.status === 'fulfilled' ? commands.value : prev.commands
          ),
          approvals: normalizeArray<ApprovalItem>(
            approvals.status === 'fulfilled' ? approvals.value : prev.approvals
          ),
        }))
      } catch { /* keep last state */ }
    }

    const getIntervals = () => ({
      fast: document.visibilityState === 'hidden' ? FAST_MS_HIDDEN : FAST_MS_VISIBLE,
      slow: document.visibilityState === 'hidden' ? SLOW_MS_HIDDEN : SLOW_MS_VISIBLE,
    })

    const restart = () => {
      if (!alive) return
      if (fastRef.current) clearInterval(fastRef.current)
      if (slowRef.current) clearInterval(slowRef.current)
      const { fast: fMs, slow: sMs } = getIntervals()
      fastRef.current = setInterval(fast, fMs)
      slowRef.current = setInterval(slow, sMs)
    }

    fast()
    slow()
    restart()
    document.addEventListener('visibilitychange', restart)

    return () => {
      alive = false
      if (fastRef.current) clearInterval(fastRef.current)
      if (slowRef.current) clearInterval(slowRef.current)
      document.removeEventListener('visibilitychange', restart)
    }
  }, [])

  return state
}
