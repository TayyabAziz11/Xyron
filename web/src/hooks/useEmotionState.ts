'use client'

import { useEffect, useRef, useState } from 'react'

export interface EmotionState {
  mood_state: string
}

const DEFAULT: EmotionState = { mood_state: 'CALM' }

export function useEmotionState(): EmotionState {
  const [data, setData] = useState<EmotionState>(DEFAULT)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/v1/cognition/state')
        if (!res.ok) return
        const json = await res.json()
        const mood_state: string = json.mood_state ?? 'CALM'
        if (!cancelled) setData({ mood_state })
      } catch {
        // silent — keep last known state
      }
    }

    poll()
    timerRef.current = setInterval(poll, 1000)
    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  return data
}
