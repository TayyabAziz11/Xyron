
import { useMemo } from 'react'
import { useCognitiveState } from './useCognitiveState'

export interface EmotionState {
  mood_state: string
}

const DEFAULT: EmotionState = { mood_state: 'CALM' }

// Derives from useCognitiveState instead of making a redundant 5-second poll
// to the same /cognition/state endpoint. One shared fetch, zero duplication.
export function useEmotionState(): EmotionState {
  const cogState = useCognitiveState()
  return useMemo(
    () => (cogState ? { mood_state: cogState.mood_state ?? 'CALM' } : DEFAULT),
    [cogState?.mood_state],
  )
}
