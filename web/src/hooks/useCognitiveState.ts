'use client'

import { useEffect, useRef, useState } from 'react'

export interface CognitiveState {
  attention: 'IDLE' | 'LISTENING' | 'PROCESSING' | 'SPEAKING' | 'FOCUSED'
  mood_bias: 'neutral' | 'alert' | 'calm' | 'stressed'
  last_user_emotion: string
  emotion_intensity: number
  active_goal: string | null
  current_task: string | null
  context_summary: string
  active_ui_mode: string
  turn_count: number
  last_updated: number
  code_mode: boolean
  active_project: string | null
  active_file: string | null
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
