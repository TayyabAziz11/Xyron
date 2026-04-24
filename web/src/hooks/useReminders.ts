'use client'

/**
 * useReminders — polls the backend every 5 seconds for fired reminders,
 * then speaks them aloud using the TTS pipeline.
 *
 * Designed to run continuously whenever the session is active.
 */

import { useEffect, useRef } from 'react'
import { getApiBase } from '@/lib/voice-core'

interface FiredReminder {
  id:      string
  message: string
  fire_at: string
}

interface UseRemindersOpts {
  /** Whether the voice session is active (polling only runs when true). */
  active:     boolean
  /** TTS voice name from user settings. */
  voice:      string
  speed:      number
  volume:     number
  /** Called with the spoken text when a reminder fires. */
  onFire:     (message: string) => void
}

const POLL_INTERVAL = 5_000   // 5 seconds

export function useReminders({ active, voice, speed, volume, onFire }: UseRemindersOpts) {
  const onFireRef = useRef(onFire)
  onFireRef.current = onFire

  useEffect(() => {
    if (!active) return

    const API_BASE = getApiBase()

    const speak = async (text: string) => {
      try {
        const resp = await fetch(`${API_BASE}/api/v1/voice/synthesize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, voice, speed }),
        })
        if (!resp.ok) return
        const url = URL.createObjectURL(await resp.blob())
        const audio = new Audio(url)
        audio.volume = volume
        audio.onended = () => URL.revokeObjectURL(url)
        audio.play().catch(() => {})
      } catch { /* silent fail — reminder fires even if TTS fails */ }
    }

    const poll = async () => {
      try {
        const resp = await fetch(`${API_BASE}/api/v1/reminders/fired`)
        if (!resp.ok) return
        const data = await resp.json() as { fired?: FiredReminder[] }
        for (const reminder of data.fired ?? []) {
          const message = `Reminder: ${reminder.message}`
          onFireRef.current(message)
          await speak(message)
        }
      } catch { /* network errors ignored */ }
    }

    const interval = setInterval(poll, POLL_INTERVAL)
    // Also poll immediately on mount
    poll()

    return () => clearInterval(interval)
  }, [active, voice, speed, volume])
}
