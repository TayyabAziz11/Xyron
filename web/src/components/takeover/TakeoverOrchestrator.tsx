'use client'

import { AnimatePresence } from 'framer-motion'
import MatrixRain      from './effects/MatrixRain'
import VignetteOverlay from './effects/VignetteOverlay'
import NoiseOverlay    from './effects/NoiseOverlay'
import CommandPhase    from './phases/CommandPhase'
import BreachPhase     from './phases/BreachPhase'
import RootPhase       from './phases/RootPhase'
import SyncPhase       from './phases/SyncPhase'
import ActivationPhase from './phases/ActivationPhase'
import HudLayout       from './hud/HudLayout'
import ControlLevel    from './hud/ControlLevel'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

interface Props {
  phase:         TakeoverPhase
  systemLine:    string
  controlLevel:  number
  showDirective: boolean
  onClose:       () => void
}

export default function TakeoverOrchestrator({ phase, systemLine, controlLevel, showDirective, onClose }: Props) {
  if (phase === 'idle') return null

  const showEffects = phase !== 'standdown'

  return (
    <>
      {/* Always-on ambient effects while active */}
      <MatrixRain      phase={phase} />
      <VignetteOverlay phase={phase} />
      {showEffects && <NoiseOverlay />}

      {/* Live control level badge — visible across all phases */}
      <ControlLevel phase={phase} controlLevel={controlLevel} />

      {/* Phase-gated content */}
      <AnimatePresence mode="wait">
        {phase === 'command'    && <CommandPhase    key="command"    phase={phase} />}
        {phase === 'breach'     && <BreachPhase     key="breach"     phase={phase} />}
        {phase === 'root'       && <RootPhase       key="root"       phase={phase} />}
        {phase === 'sync'       && <SyncPhase       key="sync"       phase={phase} />}
        {phase === 'activation' && <ActivationPhase key="activation" phase={phase} systemLine={systemLine} />}
      </AnimatePresence>

      {/* HUD persists across hud + active phases */}
      <HudLayout
        phase={phase}
        systemLine={systemLine}
        showDirective={showDirective}
        onClose={onClose}
      />
    </>
  )
}
