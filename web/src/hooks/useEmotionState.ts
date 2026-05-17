'use client'

/**
 * useEmotionState — Phase 7 reactive UI hook.
 *
 * Polls /api/v1/cognition/mood (500ms) and /api/v1/cognition/state for
 * emotion_label and emotion_energy. Merges both into a single EmotionState
 * object for orb animations, CSS variables, and Framer Motion variants.
 *
 * Falls back to DEFAULT_EMOTION_STATE while loading or on error.
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import {
  DEFAULT_EMOTION_STATE,
  type EmotionState,
  type MoodStateLabel,
  type EmotionLabel,
  type MoodTheme,
} from '@/state/emotionState'

interface MoodAPIResponse {
  state:    string
  theme:    { primary: string; glow: string; pulse: string }
  held_sec: number
}

interface CognitionAPIResponse {
  emotion_label:  string
  emotion_energy: number
  mood_state:     string
}

const API_BASE = 'http://localhost:8000/api/v1/cognition'
const POLL_MS  = 500

const AUDIENCE_MODE_DURATION_MS = 35_000

export function useEmotionState(): EmotionState {
  const [state, setState]         = useState<EmotionState>(DEFAULT_EMOTION_STATE)
  const timerRef                  = useRef<ReturnType<typeof setInterval> | null>(null)
  const cancelledRef              = useRef(false)
  const audienceModeRef           = useRef(false)
  const audienceTimerRef          = useRef<ReturnType<typeof setTimeout> | null>(null)

  const poll = useCallback(async () => {
    if (cancelledRef.current) return
    // Suppress polling while audience mode override is active
    if (audienceModeRef.current) return
    try {
      const [moodRes, cogRes] = await Promise.all([
        fetch(`${API_BASE}/mood`),
        fetch(`${API_BASE}/state`),
      ])

      if (!moodRes.ok || !cogRes.ok) return

      const mood: MoodAPIResponse     = await moodRes.json()
      const cog:  CognitionAPIResponse = await cogRes.json()

      if (cancelledRef.current || audienceModeRef.current) return

      setState({
        mood_state:     (mood.state as MoodStateLabel) || 'CALM',
        theme: {
          primary: mood.theme?.primary || DEFAULT_EMOTION_STATE.theme.primary,
          glow:    mood.theme?.glow    || DEFAULT_EMOTION_STATE.theme.glow,
          pulse:   (mood.theme?.pulse  || 'slow') as MoodTheme['pulse'],
        },
        held_sec:       mood.held_sec       ?? 0,
        emotion_label:  (cog.emotion_label  as EmotionLabel) || 'calmness',
        emotion_energy: cog.emotion_energy  ?? 0.5,
      })
    } catch {
      // silent — keep last known state
    }
  }, [])

  // Audience mode override: listen for custom event, lock state for 35s
  useEffect(() => {
    const onAudienceMode = () => {
      audienceModeRef.current = true
      setState(prev => ({
        ...prev,
        mood_state:     'AUDIENCE_MODE',
        emotion_label:  'pride',
        emotion_energy: 0.9,
        theme: {
          primary: '#7c4dff',
          glow:    'rgba(124,77,255,0.45)',
          pulse:   'steady',
        },
      }))
      if (audienceTimerRef.current) clearTimeout(audienceTimerRef.current)
      audienceTimerRef.current = setTimeout(() => {
        audienceModeRef.current = false
        // Resume polling — next tick will fetch real state
        poll()
      }, AUDIENCE_MODE_DURATION_MS)
    }

    window.addEventListener('xyron:audience-mode', onAudienceMode)
    return () => {
      window.removeEventListener('xyron:audience-mode', onAudienceMode)
      if (audienceTimerRef.current) clearTimeout(audienceTimerRef.current)
    }
  }, [poll])

  useEffect(() => {
    cancelledRef.current = false
    poll()
    timerRef.current = setInterval(poll, POLL_MS)
    return () => {
      cancelledRef.current = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [poll])

  return state
}
