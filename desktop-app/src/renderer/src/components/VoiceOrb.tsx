import { motion, AnimatePresence } from 'framer-motion'
import { Mic, Square } from 'lucide-react'

interface VoiceOrbProps {
  state: string
  onToggle: () => void
}

export default function VoiceOrb({ state, onToggle }: VoiceOrbProps) {
  const isListening  = state === 'listening'
  const isSpeaking   = state === 'speaking' || state === 'greeting'
  const isProcessing = state === 'transcribing' || state === 'processing'
  const isActive     = state !== 'idle' && state !== 'stopped'

  // ── Orb color config ──────────────────────────────────────────────────────
  const orbColor = isListening
    ? { from: '#10b981', to: '#34d399', glow: 'rgba(16,185,129,0.55)' }
    : isSpeaking
    ? { from: '#8b5cf6', to: '#a78bfa', glow: 'rgba(139,92,246,0.55)' }
    : isProcessing
    ? { from: '#3b82f6', to: '#60a5fa', glow: 'rgba(59,130,246,0.45)' }
    : { from: '#7c3aed', to: '#a78bfa', glow: 'rgba(124,58,237,0.30)' }

  // ── Outer ripple rings (listening) ────────────────────────────────────────
  const rippleRings = isListening ? [0, 1, 2] : isSpeaking ? [0, 1] : []

  return (
    <div
      className="relative flex items-center justify-center w-52 h-52 cursor-pointer select-none"
      onClick={onToggle}
      role="button"
      aria-label={isActive ? 'Stop session' : 'Start session'}
    >
      {/* ── Ripple rings ─────────────────────────────────────────────────── */}
      <AnimatePresence>
        {rippleRings.map((i) => (
          <motion.div
            key={`${state}-ring-${i}`}
            className="absolute rounded-full"
            initial={{ opacity: 0.7, scale: 0.6 }}
            animate={{ opacity: 0, scale: 1.8 + i * 0.25 }}
            exit={{ opacity: 0 }}
            transition={{
              duration: 1.8,
              repeat: Infinity,
              delay: i * 0.55,
              ease: 'easeOut',
            }}
            style={{
              width: 120,
              height: 120,
              border: `1.5px solid ${orbColor.from}`,
            }}
          />
        ))}
      </AnimatePresence>

      {/* ── Background ambient glow ───────────────────────────────────────── */}
      <motion.div
        className="absolute w-36 h-36 rounded-full blur-3xl"
        animate={{
          opacity: isListening ? [0.35, 0.6, 0.35] : isSpeaking ? [0.4, 0.7, 0.4] : [0.15, 0.25, 0.15],
          scale:   isListening ? [1, 1.15, 1]       : isSpeaking ? [1.05, 1.2, 1.05] : [1, 1.05, 1],
        }}
        transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
        style={{ background: orbColor.glow }}
      />

      {/* ── Main orb ─────────────────────────────────────────────────────── */}
      <motion.div
        className="relative flex items-center justify-center rounded-full z-10"
        style={{
          width: 112,
          height: 112,
          background: `radial-gradient(circle at 35% 35%, ${orbColor.from}, ${orbColor.to})`,
          boxShadow: `0 0 40px ${orbColor.glow}, 0 0 80px ${orbColor.glow.replace('0.55', '0.20').replace('0.45', '0.15').replace('0.30', '0.10')}`,
        }}
        animate={
          isListening
            ? {
                scale: [1, 1.06, 0.97, 1.04, 1],
                borderRadius: ['50%', '48% 52% 50% 52%', '52% 48% 52% 48%', '50%'],
              }
            : isSpeaking
            ? {
                scale: [1, 1.10, 0.95, 1.08, 0.98, 1],
                borderRadius: ['50%', '52% 48% 48% 52%', '48% 52% 52% 48%', '50%'],
              }
            : isProcessing
            ? { scale: [1, 1.04, 1], rotate: [0, 10, 0, -10, 0] }
            : { scale: 1 }
        }
        transition={{
          duration: isProcessing ? 1.2 : 2.4,
          repeat: Infinity,
          ease: 'easeInOut',
        }}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.93 }}
      >
        {/* Inner specular highlight */}
        <div
          className="absolute rounded-full"
          style={{
            width: 44,
            height: 44,
            top: 14,
            left: 14,
            background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.35), transparent 70%)',
          }}
        />

        {/* Rotating shimmer ring (processing only) */}
        <AnimatePresence>
          {isProcessing && (
            <motion.div
              key="shimmer"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, rotate: 360 }}
              exit={{ opacity: 0 }}
              transition={{ rotate: { duration: 1.5, repeat: Infinity, ease: 'linear' }, opacity: { duration: 0.3 } }}
              className="absolute inset-0 rounded-full"
              style={{
                background: 'conic-gradient(from 0deg, transparent 60%, rgba(255,255,255,0.25) 100%)',
              }}
            />
          )}
        </AnimatePresence>

        {/* Icon */}
        <AnimatePresence mode="wait">
          {isActive ? (
            <motion.div
              key="stop"
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="z-10"
            >
              <Square size={22} className="text-white fill-white drop-shadow" />
            </motion.div>
          ) : (
            <motion.div
              key="mic"
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="z-10"
            >
              <Mic size={26} className="text-white drop-shadow" />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* ── Speaking bars (tiny equaliser dots below orb) ─────────────────── */}
      <AnimatePresence>
        {isSpeaking && (
          <motion.div
            key="bars"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="absolute bottom-5 flex items-end gap-[3px]"
          >
            {[5, 10, 16, 10, 14, 8, 12, 10, 16, 6].map((h, i) => (
              <motion.div
                key={i}
                className="rounded-full"
                style={{ width: 3, background: orbColor.from }}
                animate={{ height: [3, h, 3], opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 0.45 + (i % 3) * 0.12, repeat: Infinity, delay: i * 0.05, ease: 'easeInOut' }}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
