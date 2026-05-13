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

    if (cogState.active_ui_mode === 'sentinel') return 'sentinel'

    if (
      env.cpu_percent > 85 ||
      cogState.last_user_emotion === 'stressed' ||
      cogState.last_user_emotion === 'excited'
    ) return 'overdrive'

    if (
      cogState.last_user_emotion === 'tired' ||
      cogState.last_user_emotion === 'sad'
    ) return 'calm'

    if (cogState.active_goal?.toLowerCase().includes('focus')) return 'focus'

    return 'default'
  }, [cogState, env])
}
