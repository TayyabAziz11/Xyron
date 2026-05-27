

import { useEffect, useRef, useState } from 'react'

export interface EnvironmentStatus {
  cpu_percent: number
  ram_percent: number
  battery_percent: number | null
  battery_charging: boolean | null
  active_window: string
  timestamp: number
}

export function useEnvironment(): EnvironmentStatus | null {
  const [data, setData] = useState<EnvironmentStatus | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/v1/environment/status')
        if (!res.ok) return
        const json: EnvironmentStatus = await res.json()
        if (!cancelled) setData(json)
      } catch (e) {
        console.log('[useEnvironment] fetch error:', e)
      }
    }

    poll()
    timerRef.current = setInterval(poll, 30_000)
    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  return data
}
