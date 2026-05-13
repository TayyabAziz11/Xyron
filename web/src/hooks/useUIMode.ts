import { useMemo } from 'react'
import type { CognitiveState } from './useCognitiveState'
import type { EnvironmentStatus } from './useEnvironment'

export type UIMode = 'default' | 'focus' | 'calm' | 'overdrive' | 'sentinel'

export function useUIMode(
  cogState: CognitiveState | null,
  env: EnvironmentStatus | null,
): UIMode {
  return useMemo(() => {
    if (!cogState || !env) return 'default'

    // Priority 1 — sentinel always wins
    if (cogState.active_ui_mode === 'sentinel') return 'sentinel'

    // Priority 2 — overdrive: CPU stress or high-arousal emotion
    if (
      env.cpu_percent > 85 ||
      cogState.last_user_emotion === 'stressed' ||
      cogState.last_user_emotion === 'excited'
    ) return 'overdrive'

    // Priority 3 — calm: low-energy emotion
    if (
      cogState.last_user_emotion === 'tired' ||
      cogState.last_user_emotion === 'sad'
    ) return 'calm'

    // Priority 4 — focus: code_mode OR focus goal
    if (cogState.code_mode || cogState.active_goal?.toLowerCase().includes('focus')) {
      return 'focus'
    }

    return 'default'
  }, [cogState, env])
}
