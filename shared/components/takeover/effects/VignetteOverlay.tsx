

import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

export default function VignetteOverlay({ phase }: Props) {
  const isIdle = phase === 'idle'

  // Red pulse intensity by phase
  const borderIntensity: Partial<Record<TakeoverPhase, string>> = {
    command:    '0 0 0 2px rgba(180,0,0,0.4) inset, 0 0 60px rgba(200,0,0,0.12) inset',
    breach:     '0 0 0 3px rgba(220,0,0,0.7) inset, 0 0 80px rgba(255,0,0,0.20) inset',
    root:       '0 0 0 4px rgba(255,0,0,0.9) inset, 0 0 100px rgba(255,0,0,0.28) inset',
    sync:       '0 0 0 2px rgba(0,160,220,0.35) inset, 0 0 60px rgba(0,150,200,0.08) inset',
    activation: '0 0 0 2px rgba(180,0,0,0.3) inset, 0 0 50px rgba(200,0,0,0.10) inset',
    hud:        '0 0 0 1px rgba(0,160,200,0.15) inset',
    active:     '0 0 0 1px rgba(0,160,200,0.10) inset',
    standdown:  '0 0 0 3px rgba(200,0,0,0.5) inset, 0 0 80px rgba(200,0,0,0.15) inset',
  }

  return (
    <AnimatePresence>
      {!isIdle && (
        <motion.div
          key="vignette"
          className="fixed inset-0 z-[56] pointer-events-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.5 }}
          style={{
            background:
              'radial-gradient(ellipse 80% 70% at 50% 50%, transparent 40%, rgba(0,0,0,0.7) 100%)',
            boxShadow: borderIntensity[phase] ?? 'none',
            transition: 'box-shadow 0.4s ease',
          }}
        />
      )}
    </AnimatePresence>
  )
}
