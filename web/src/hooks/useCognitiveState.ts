'use client'

import { useEffect, useRef, useState } from 'react'

export interface CognitiveState {
  attention: 'IDLE' | 'LISTENING' | 'PROCESSING' | 'SPEAKING'
  last_user_emotion: string   // neutral | happy | laughing | joyful | excited | stressed | tired | sad | curious | focused | surprised | bored
  emotion_intensity: number   // 0.0 – 1.0
  active_goal: string | null
  current_task: string | null
  active_ui_mode: string
}

export function useCognitiveState(): CognitiveState | null {
  const [data, setData] = useState<CognitiveState | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const res = await fetch('http://localhost:8000/api/v1/cognition/state')
        if (!res.ok) return
        const json: CognitiveState = await res.json()
        if (!cancelled) setData(json)
      } catch {
        // silent
      }
    }

    poll()
    timerRef.current = setInterval(poll, 500)
    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  return data
}
