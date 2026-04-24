'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { Mic, Square, Radio } from 'lucide-react'
import type { SessionState } from '@/hooks/useVoiceSession'

// ── State label map ───────────────────────────────────────────────────────────

function getStateLabel(state: SessionState, sessionActive: boolean): string {
  if (state === 'idle') {
    // Brief transient state — auto-listen kicks in almost immediately
    return sessionActive ? 'Starting to listen…' : 'Tap to start a voice session'
  }
  const LABELS: Partial<Record<SessionState, string>> = {
    greeting:     'Xyron is greeting you…',
    listening:    "I'm listening…",
    transcribing: 'Transcribing…',
    processing:   'Thinking…',
    speaking:     'Xyron is speaking…',
    stopped:      'Session ended',
  }
  return LABELS[state] ?? state
}

interface Props {
  state:         SessionState
  sessionActive: boolean     // true = continuous session running
  onStart:       () => void  // start session
  onStop:        () => void  // end session (any state)
  error?:        string | null
  wakeWordListening?: boolean
  wakeWordSupported?: boolean
}

// ── ChatGPT-style Voice Orb ───────────────────────────────────────────────────

function VoiceOrb({
  state, sessionActive, onStart, onStop,
}: {
  state: SessionState
  sessionActive: boolean
  onStart: () => void
  onStop: () => void
}) {
  const isListening  = state === 'listening'
  const isSpeaking   = state === 'speaking' || state === 'greeting'
  const isProcessing = state === 'transcribing' || state === 'processing'
  const isBusy       = state !== 'idle' && state !== 'stopped'

  const orbColor = isListening
    ? { from: '#10b981', to: '#34d399', glow: 'rgba(16,185,129,0.55)' }
    : isSpeaking
    ? { from: '#8b5cf6', to: '#a78bfa', glow: 'rgba(139,92,246,0.55)' }
    : isProcessing
    ? { from: '#3b82f6', to: '#60a5fa', glow: 'rgba(59,130,246,0.45)' }
    : sessionActive
    ? { from: '#7c3aed', to: '#a78bfa', glow: 'rgba(124,58,237,0.40)' }   // session active, idle
    : { from: '#7c3aed', to: '#a78bfa', glow: 'rgba(124,58,237,0.20)' }   // no session

  const rippleRings = isListening ? [0, 1, 2] : isSpeaking ? [0, 1] : []

  // Click logic:
  //   session active (any state) → end session
  //   no session                 → start session
  const handleClick = () => {
    if (sessionActive) return onStop()
    return onStart()
  }

  const ariaLabel = sessionActive ? 'End voice session' : 'Start voice session'

  return (
    <div
      className="relative flex items-center justify-center w-44 h-44 cursor-pointer select-none"
      onClick={handleClick}
      role="button"
      aria-label={ariaLabel}
    >
      {/* Ripple rings */}
      <AnimatePresence>
        {rippleRings.map((i) => (
          <motion.div
            key={`${state}-ring-${i}`}
            className="absolute rounded-full"
            initial={{ opacity: 0.7, scale: 0.6 }}
            animate={{ opacity: 0, scale: 1.8 + i * 0.25 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.8, repeat: Infinity, delay: i * 0.55, ease: 'easeOut' }}
            style={{ width: 100, height: 100, border: `1.5px solid ${orbColor.from}` }}
          />
        ))}
      </AnimatePresence>

      {/* Ambient glow */}
      <motion.div
        className="absolute w-28 h-28 rounded-full blur-3xl"
        animate={{
          opacity: isListening ? [0.35, 0.6, 0.35] : isSpeaking ? [0.4, 0.7, 0.4] : sessionActive ? [0.2, 0.35, 0.2] : [0.1, 0.2, 0.1],
          scale:   isListening ? [1, 1.15, 1]       : isSpeaking ? [1.05, 1.2, 1.05] : [1, 1.05, 1],
        }}
        transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
        style={{ background: orbColor.glow }}
      />

      {/* Main orb */}
      <motion.div
        className="relative flex items-center justify-center rounded-full z-10"
        style={{
          width: 96, height: 96,
          background: `radial-gradient(circle at 35% 35%, ${orbColor.from}, ${orbColor.to})`,
          boxShadow: `0 0 32px ${orbColor.glow}, 0 0 64px ${orbColor.glow.replace(/[\d.]+\)$/, '0.12)')}`,
        }}
        animate={
          isListening
            ? { scale: [1, 1.06, 0.97, 1.04, 1], borderRadius: ['50%', '48% 52% 50% 52%', '52% 48% 52% 48%', '50%'] }
            : isSpeaking
            ? { scale: [1, 1.10, 0.95, 1.08, 0.98, 1], borderRadius: ['50%', '52% 48% 48% 52%', '48% 52% 52% 48%', '50%'] }
            : isProcessing
            ? { scale: [1, 1.04, 1], rotate: [0, 10, 0, -10, 0] }
            : { scale: 1 }
        }
        transition={{ duration: isProcessing ? 1.2 : 2.4, repeat: Infinity, ease: 'easeInOut' }}
        whileHover={{ scale: 1.06 }}
        whileTap={{ scale: 0.93 }}
      >
        {/* Specular highlight */}
        <div
          className="absolute rounded-full"
          style={{
            width: 38, height: 38, top: 12, left: 12,
            background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.35), transparent 70%)',
          }}
        />

        {/* Processing shimmer */}
        <AnimatePresence>
          {isProcessing && (
            <motion.div
              key="shimmer"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, rotate: 360 }}
              exit={{ opacity: 0 }}
              transition={{ rotate: { duration: 1.5, repeat: Infinity, ease: 'linear' }, opacity: { duration: 0.3 } }}
              className="absolute inset-0 rounded-full"
              style={{ background: 'conic-gradient(from 0deg, transparent 60%, rgba(255,255,255,0.25) 100%)' }}
            />
          )}
        </AnimatePresence>

        {/* Icon */}
        <AnimatePresence mode="wait">
          {isBusy ? (
            <motion.div key="stop" initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="z-10">
              <Square size={20} className="text-white fill-white drop-shadow" />
            </motion.div>
          ) : (
            <motion.div key="mic" initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={{ duration: 0.18 }} className="z-10">
              <Mic size={24} className="text-white drop-shadow" />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* Speaking equaliser bars */}
      <AnimatePresence>
        {isSpeaking && (
          <motion.div
            key="bars"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="absolute bottom-4 flex items-end gap-[3px]"
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

// ── Main component ────────────────────────────────────────────────────────────

export function VoiceSessionPanel({
  state, sessionActive, onStart, onStop,
  error, wakeWordListening, wakeWordSupported,
}: Props) {
  const isBusy = state !== 'idle' && state !== 'stopped'

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Orb */}
      <VoiceOrb
        state={state}
        sessionActive={sessionActive}
        onStart={onStart}
        onStop={onStop}
      />

      {/* State label */}
      <AnimatePresence mode="wait">
        <motion.p
          key={`${state}-${sessionActive}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2 }}
          className={[
            'text-sm font-medium text-center',
            state === 'listening'  ? 'text-emerald-400'
            : state === 'speaking' || state === 'greeting' ? 'text-violet-400'
            : state === 'transcribing' || state === 'processing' ? 'text-blue-400'
            : sessionActive ? 'text-brand-light/70'
            : 'text-text-secondary',
          ].join(' ')}
        >
          {getStateLabel(state, sessionActive)}
        </motion.p>
      </AnimatePresence>

      {/* Wake word indicator — only when session active + idle */}
      <AnimatePresence>
        {state === 'idle' && sessionActive && wakeWordSupported && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-surface-border bg-surface-overlay"
          >
            <div className="relative flex-shrink-0">
              <Radio size={10} className={wakeWordListening ? 'text-emerald-400' : 'text-text-muted'} />
              {wakeWordListening && (
                <div className="absolute inset-0 rounded-full bg-emerald-400/30 animate-ping" />
              )}
            </div>
            <span className="text-[11px] text-text-muted">
              {wakeWordListening
                ? <span className="text-emerald-400/80">Say <span className="font-semibold">"Hey Xyron"</span> to speak</span>
                : 'Initialising wake word…'}
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Session hint */}
      {sessionActive && (
        <p className="text-xs text-text-muted">Tap orb to end session</p>
      )}

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="flex items-start gap-2 rounded-lg border border-status-error/30 bg-status-error/5 px-3 py-2 text-xs text-status-error max-w-sm"
          >
            <span>⚠</span>
            <span>{error}</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
