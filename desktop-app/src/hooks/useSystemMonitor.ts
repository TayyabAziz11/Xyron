/**
 * useSystemMonitor — real-time system metrics via WebSocket.
 *
 * Connects to ws://localhost:8000/api/v1/monitor/ws.
 * On connect the server sends a full snapshot with 60s history deques.
 * After that it sends live `metrics` frames at ~1 s intervals.
 *
 * Falls back to REST GET /api/v1/monitor/snapshot if WS fails (e.g. backend down).
 * Reconnects with exponential backoff (1s → 2s → 4s → 8s, capped at 30s).
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import type { MetricsData } from '@desktop/services/dashboardApi'

const WS_URL        = 'ws://localhost:8000/api/v1/monitor/ws'
const REST_URL      = 'http://localhost:8000/api/v1/monitor/snapshot'
const HISTORY_LEN   = 60
const REST_FALLBACK_MS = 5_000  // poll REST every 5s when WS is down

export interface MonitorState {
  metrics:    MetricsData | null
  history:    Record<string, number[]>  // metric → last 60 values
  connected:  boolean
}

const EMPTY_HISTORY: Record<string, number[]> = {
  cpu: [], ram: [], gpu: [], net_up: [], net_down: [],
}

function appendHistory(prev: Record<string, number[]>, snap: MetricsData): Record<string, number[]> {
  const push = (arr: number[], v: number | null | undefined) =>
    v == null ? arr : [...arr.slice(-(HISTORY_LEN - 1)), v]

  return {
    cpu:      push(prev.cpu,      snap.cpu_pct),
    ram:      push(prev.ram,      snap.ram_pct),
    gpu:      push(prev.gpu,      snap.gpu_pct),
    net_up:   push(prev.net_up,   snap.net_up_kbs),
    net_down: push(prev.net_down, snap.net_down_kbs),
  }
}

export function useSystemMonitor(): MonitorState {
  const [state, setState] = useState<MonitorState>({
    metrics: null, history: EMPTY_HISTORY, connected: false,
  })

  const wsRef         = useRef<WebSocket | null>(null)
  const retryMs       = useRef(1_000)
  const retryTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const restTimer     = useRef<ReturnType<typeof setInterval> | null>(null)
  const aliveRef      = useRef(true)

  const clearRestFallback = useCallback(() => {
    if (restTimer.current) { clearInterval(restTimer.current); restTimer.current = null }
  }, [])

  const startRestFallback = useCallback(() => {
    if (restTimer.current) return
    const poll = async () => {
      if (!aliveRef.current) return
      try {
        const r = await fetch(REST_URL, { signal: AbortSignal.timeout(3_000) })
        if (!r.ok) return
        const body = await r.json() as { success: boolean; data: MetricsData }
        if (!body.success || !body.data) return
        setState(prev => ({
          ...prev,
          metrics: body.data,
          history: appendHistory(prev.history, body.data),
          connected: false,
        }))
      } catch { /* backend offline — keep last state */ }
    }
    poll()
    restTimer.current = setInterval(poll, REST_FALLBACK_MS)
  }, [])

  const connect = useCallback(() => {
    if (!aliveRef.current) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!aliveRef.current) { ws.close(); return }
      retryMs.current = 1_000
      clearRestFallback()
      setState(prev => ({ ...prev, connected: true }))
    }

    ws.onmessage = (ev) => {
      if (!aliveRef.current) return
      try {
        const msg = JSON.parse(ev.data as string) as {
          type: string; data?: MetricsData & { history?: Record<string, number[]> }
        }
        if (msg.type === 'ping') return
        if (!msg.data) return

        const snap = msg.data

        setState(prev => {
          let newHistory = prev.history
          if (msg.type === 'snapshot' && snap.history) {
            // Server sent full deque history on connect — use it directly
            newHistory = {
              cpu:      Array.isArray(snap.history.cpu)      ? snap.history.cpu      : [],
              ram:      Array.isArray(snap.history.ram)      ? snap.history.ram      : [],
              gpu:      Array.isArray(snap.history.gpu)      ? snap.history.gpu      : [],
              net_up:   Array.isArray(snap.history.net_up)   ? snap.history.net_up   : [],
              net_down: Array.isArray(snap.history.net_down) ? snap.history.net_down : [],
            }
          } else {
            newHistory = appendHistory(prev.history, snap)
          }
          return { ...prev, metrics: snap, history: newHistory, connected: true }
        })
      } catch { /* malformed frame — ignore */ }
    }

    ws.onerror = () => { /* onclose will fire next */ }

    ws.onclose = () => {
      if (!aliveRef.current) return
      setState(prev => ({ ...prev, connected: false }))
      startRestFallback()
      // Exponential backoff, cap at 30s
      retryTimer.current = setTimeout(() => {
        retryMs.current = Math.min(retryMs.current * 2, 30_000)
        connect()
      }, retryMs.current)
    }
  }, [clearRestFallback, startRestFallback])

  useEffect(() => {
    aliveRef.current = true
    connect()

    return () => {
      aliveRef.current = false
      if (retryTimer.current) clearTimeout(retryTimer.current)
      clearRestFallback()
      if (wsRef.current) wsRef.current.close()
    }
  }, [connect, clearRestFallback])

  return state
}
