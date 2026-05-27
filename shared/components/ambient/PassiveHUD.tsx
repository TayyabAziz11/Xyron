

import { motion, AnimatePresence } from 'framer-motion'
import type { CognitiveState } from '@/hooks/useCognitiveState'
import { getEmotion, ATTENTION_COLORS } from '@/config/emotions'

const ATTENTION_LABELS: Record<string, string> = {
  IDLE:       'Idle',
  LISTENING:  'Listening',
  PROCESSING: 'Processing',
  SPEAKING:   'Speaking',
}

interface PassiveHUDProps {
  cognitiveState: CognitiveState | null
}

export default function PassiveHUD({ cognitiveState }: PassiveHUDProps) {
  const attention = cognitiveState?.attention ?? 'IDLE'
  const emotion   = cognitiveState?.last_user_emotion ?? 'neutral'
  const intensity = cognitiveState?.emotion_intensity ?? 0.5
  const isIdle    = attention === 'IDLE'

  const emotionCfg   = getEmotion(emotion)
  const attnColor    = ATTENTION_COLORS[attention] ?? '#475569'

  return (
    <motion.div
      layout
      className="relative flex items-center justify-center overflow-hidden border-b border-white/5 bg-black/75 backdrop-blur-md"
      animate={{ height: isIdle ? 12 : 38 }}
      transition={{ duration: 0.35, ease: 'easeInOut' }}
      style={{
        boxShadow: isIdle ? 'none' : `0 1px 0 ${emotionCfg.glow}`,
      }}
    >
      {/* Subtle emotion color wash behind content */}
      {!isIdle && (
        <motion.div
          className="pointer-events-none absolute inset-0"
          style={{ background: `linear-gradient(90deg, transparent 0%, ${emotionCfg.color}06 50%, transparent 100%)` }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        />
      )}

      <AnimatePresence mode="wait">
        {isIdle ? (
          <motion.div
            key="dot"
            className="h-2 w-2 rounded-full"
            style={{ background: '#1e293b', boxShadow: '0 0 4px rgba(255,255,255,0.1)' }}
            initial={{ opacity: 0, scale: 0 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0 }}
            transition={{ duration: 0.2 }}
          />
        ) : (
          <motion.div
            key="expanded"
            className="relative flex w-full items-center justify-between px-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
          >
            {/* ── Left: Brand ── */}
            <div className="flex items-center gap-2 min-w-0">
              <span
                className="font-mono text-[9px] font-bold tracking-[0.25em]"
                style={{ color: emotionCfg.color, textShadow: `0 0 8px ${emotionCfg.glow}` }}
              >
                XYRON
              </span>
            </div>

            {/* ── Center: Attention + Emotion ── */}
            <div className="flex items-center gap-4">
              {/* Attention */}
              <div className="flex items-center gap-1.5">
                <motion.div
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: attnColor, boxShadow: `0 0 6px ${attnColor}` }}
                  animate={{ opacity: [1, 0.4, 1] }}
                  transition={{ duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
                />
                <span className="font-mono text-[9px] tracking-widest" style={{ color: attnColor }}>
                  {ATTENTION_LABELS[attention] ?? attention}
                </span>
              </div>

              <div className="h-3 w-px bg-white/10" />

              {/* Emotion */}
              <div className="flex items-center gap-1.5">
                <motion.span
                  key={emotion}
                  className="text-sm leading-none"
                  initial={{ scale: 0.5, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 20 }}
                >
                  {emotionCfg.emoji}
                </motion.span>
                <motion.span
                  key={`label-${emotion}`}
                  className="font-mono text-[9px] font-bold tracking-widest"
                  style={{ color: emotionCfg.color }}
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  {emotionCfg.label.toUpperCase()}
                </motion.span>

                {/* Intensity bar */}
                <div className="flex h-1 w-14 overflow-hidden rounded-full bg-white/10">
                  <motion.div
                    className="h-full rounded-full"
                    style={{ background: emotionCfg.color, boxShadow: `0 0 4px ${emotionCfg.glow}` }}
                    animate={{ width: `${intensity * 100}%` }}
                    transition={{ duration: 0.6, ease: 'easeOut' }}
                  />
                </div>
              </div>
            </div>

            {/* ── Right: Goal + Task ── */}
            <div className="flex items-center gap-3 min-w-0">
              {cognitiveState?.active_goal && (
                <motion.span
                  className="font-mono text-[9px] text-white/35 truncate max-w-[140px]"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  Goal: <span className="text-white/65">{cognitiveState.active_goal}</span>
                </motion.span>
              )}
              {cognitiveState?.current_task && (
                <motion.span
                  className="font-mono text-[9px] text-white/35 truncate max-w-[120px]"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  Task: <span className="text-white/65">{cognitiveState.current_task}</span>
                </motion.span>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
