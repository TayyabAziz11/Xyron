'use client'

import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { SystemMetrics } from '@/lib/types'

export function useSystemMetrics(intervalMs = 2000) {
  const [data, setData] = useState<SystemMetrics | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const fetch = async () => {
      try {
        const m = await api.system.metrics()
        if (!cancelled) { setData(m); setError(null) }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)))
      }
    }

    fetch()
    timerRef.current = setInterval(fetch, intervalMs)
    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [intervalMs])

  return { data, error }
}
