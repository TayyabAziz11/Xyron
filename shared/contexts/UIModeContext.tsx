

import { createContext, useContext } from 'react'
import { useCognitiveState } from '@/hooks/useCognitiveState'
import { useEnvironment } from '@/hooks/useEnvironment'
import { useUIMode as deriveUIMode, type UIMode } from '@/hooks/useUIMode'

interface UIModeContextValue {
  mode: UIMode
}

const UIModeContext = createContext<UIModeContextValue>({ mode: 'default' })

export function UIModeProvider({ children }: { children: React.ReactNode }) {
  const cogState = useCognitiveState()
  const env = useEnvironment()
  const mode = deriveUIMode(cogState, env)

  return (
    <UIModeContext.Provider value={{ mode }}>
      <div data-ui-mode={mode}>
        {children}
      </div>
    </UIModeContext.Provider>
  )
}

export function useUIMode(): UIMode {
  return useContext(UIModeContext).mode
}
