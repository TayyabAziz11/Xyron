import { motion, AnimatePresence } from 'framer-motion'

export type SessionState = 'idle' | 'greeting' | 'listening' | 'transcribing' | 'processing' | 'speaking' | 'stopped'

const CONFIG: Record<SessionState, { label: string; bg: string; dot: string; pulse: boolean }> = {
  idle:         { label: 'Ready',        bg: 'bg-slate-800/60 text-slate-400 border-slate-700/50',   dot: 'bg-slate-500',         pulse: false },
  greeting:     { label: 'Starting…',   bg: 'bg-brand/10 text-brand-light border-brand/30',          dot: 'bg-brand-light',       pulse: true },
  listening:    { label: 'Listening…',  bg: 'bg-ok/10 text-ok border-ok/30',                         dot: 'bg-ok',                pulse: true },
  transcribing: { label: 'Processing…', bg: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30', dot: 'bg-yellow-400',        pulse: true },
  processing:   { label: 'Thinking…',   bg: 'bg-blue-500/10 text-blue-400 border-blue-500/30',       dot: 'bg-blue-400',          pulse: true },
  speaking:     { label: 'Speaking…',   bg: 'bg-brand/10 text-brand-light border-brand/30',          dot: 'bg-brand-light',       pulse: true },
  stopped:      { label: 'Done',         bg: 'bg-slate-800/60 text-slate-500 border-slate-700/50',   dot: 'bg-slate-600',         pulse: false },
}

export default function StatusBadge({ status }: { status: SessionState }) {
  const cfg = CONFIG[status] ?? CONFIG.idle
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={status}
        initial={{ opacity: 0, y: -8, scale: 0.92 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 8, scale: 0.92 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${cfg.bg}`}
      >
        <div className="relative flex-shrink-0">
          <div className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
          {cfg.pulse && (
            <div className={`absolute inset-0 rounded-full ${cfg.dot} animate-ping opacity-75`} />
          )}
        </div>
        {cfg.label}
      </motion.div>
    </AnimatePresence>
  )
}
