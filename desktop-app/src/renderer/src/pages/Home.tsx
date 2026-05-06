import { useCallback, useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, Radio, Zap, Lightbulb, ChevronRight } from 'lucide-react'
import { useVoiceSession } from '../hooks/useVoiceSession'
import { useWakeWord } from '../hooks/useWakeWord'
import VoiceOrb from '../components/VoiceOrb'
import StatusBadge from '../components/StatusBadge'

const SUGGESTIONS = [
  { icon: '₿',  text: 'Bitcoin price?' },
  { icon: '📰', text: "Today's news" },
  { icon: '🎵', text: 'Play lofi music' },
  { icon: '📂', text: 'Create a folder' },
  { icon: '🌐', text: 'Open YouTube' },
  { icon: '🖥️', text: "What's on my screen?" },
]

function MsgBubble({
  role, text, status, timestamp,
}: { role: string; text: string; status: string; timestamp: Date }) {
  const isUser = role === 'user'
  const isSys  = role === 'system'
  const [showTime, setShowTime] = useState(false)

  if (isSys) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="flex justify-center my-1"
      >
        <span className="text-[10px] text-slate-600 italic px-3 py-1 rounded-full bg-surface/50 border border-border/30">
          {text}
        </span>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
      className={`flex items-end gap-2.5 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
      onMouseEnter={() => setShowTime(true)}
      onMouseLeave={() => setShowTime(false)}
    >
      {/* Avatar */}
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-gradient-to-br from-brand to-violet-500 flex items-center justify-center flex-shrink-0 mb-0.5 shadow-lg shadow-brand/25 ring-1 ring-brand/30">
          <Sparkles size={11} className="text-white" />
        </div>
      )}

      <div className={`flex flex-col gap-0.5 max-w-[80%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div className={`px-4 py-2.5 text-[13px] leading-relaxed ${
          isUser
            ? 'bg-gradient-to-br from-brand to-violet-500 text-white rounded-2xl rounded-br-sm shadow-lg shadow-brand/25'
            : 'bg-card/90 backdrop-blur-sm border border-border/50 text-slate-200 rounded-2xl rounded-bl-sm shadow-sm'
        }`}>
          {status === 'processing' ? (
            <span className="flex items-center gap-1.5 px-1">
              {[0, 1, 2].map((i) => (
                <motion.span key={i}
                  className="w-1.5 h-1.5 rounded-full bg-brand-light block"
                  animate={{ scale: [1, 1.6, 1], opacity: [0.4, 1, 0.4] }}
                  transition={{ duration: 0.7, repeat: Infinity, delay: i * 0.15 }}
                />
              ))}
            </span>
          ) : text}
        </div>

        {/* Timestamp on hover */}
        <AnimatePresence>
          {showTime && (
            <motion.span
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.12 }}
              className="text-[10px] text-slate-600 px-1"
            >
              {timestamp.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}

export default function Home() {
  const {
    sessionState, messages, error, startSession, startWorkSession, stopSession, followUp, dismissFollowUp,
  } = useVoiceSession()
  const scrollRef = useRef<HTMLDivElement>(null)
  const isActive  = sessionState !== 'idle' && sessionState !== 'stopped'
  const hasMessages = messages.length > 0

  const handleWakeActivate = useCallback(() => {
    console.log('[SESSION_FLOW] wake_detected — sessionState:', sessionState, 'isActive:', isActive)
    if (!isActive) {
      startSession()
    } else if (sessionState === 'listening') {
      // Already listening — wake word fired redundantly, ignore
      console.log('[SESSION_FLOW] wake ignored — already listening')
    } else {
      // Session alive but busy (speaking/processing/greeting) — ignore
      console.log('[SESSION_FLOW] wake ignored — session busy, state:', sessionState)
    }
  }, [startSession, sessionState, isActive])
  const handleWorkActivate = useCallback(() => {
    startWorkSession()
  }, [startWorkSession])
  // Disable wake WS during active session — prevents re-triggering while assistant speaks
  const { supported: wakeSupported, listening: wakeListening } = useWakeWord(handleWakeActivate, !isActive, handleWorkActivate)

  // Auto-scroll to latest message
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // Live clock
  const [time, setTime] = useState(() =>
    new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })
  )
  useEffect(() => {
    const id = setInterval(() => setTime(
      new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true })
    ), 10_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="h-full flex flex-col bg-bg">
      {/* ── Header ── */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border/50 bg-surface/20 backdrop-blur-md flex-shrink-0">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-[13px] font-semibold text-slate-200 leading-tight">Voice Interface</h1>
            <p className="text-[10px] text-slate-600 mt-0.5">
              {isActive
                ? `${messages.filter(m => m.role === 'user').length} commands this session`
                : `Last active ${time}`}
            </p>
          </div>
          {/* Live indicator */}
          <AnimatePresence>
            {isActive && (
              <motion.div
                initial={{ opacity: 0, scale: 0.7 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.7 }}
                className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-ok/10 border border-ok/25"
              >
                <div className="relative">
                  <div className="w-1.5 h-1.5 rounded-full bg-ok" />
                  <div className="absolute inset-0 rounded-full bg-ok animate-ping opacity-60" />
                </div>
                <span className="text-[9px] font-bold text-ok uppercase tracking-wide">Live</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        <StatusBadge status={sessionState} />
      </div>

      {/* ── Content area ── */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Messages — scrollable, grows above the orb */}
        <div
          ref={scrollRef}
          className={`overflow-y-auto px-5 py-4 space-y-3 scroll-smooth transition-all duration-300 ${hasMessages ? 'flex-1 min-h-0' : 'h-0'}`}
        >
          <AnimatePresence initial={false}>
            {messages.map((msg) => (
              <MsgBubble
                key={msg.id}
                role={msg.role}
                text={msg.text}
                status={msg.status}
                timestamp={msg.timestamp}
              />
            ))}
          </AnimatePresence>
        </div>

        {/* Follow-up suggestion */}
        <AnimatePresence>
          {followUp && (
            <motion.div
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.2 }}
              className="mx-5 mb-1 flex-shrink-0"
            >
              <button
                onClick={() => { dismissFollowUp(); startSession() }}
                className="flex items-center gap-2 px-4 py-2 rounded-2xl bg-brand/8 border border-brand/25 text-[11px] text-brand-light hover:bg-brand/15 transition-all duration-200 max-w-full group"
              >
                <Zap size={10} className="flex-shrink-0 group-hover:text-brand" />
                <span className="truncate">{followUp}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); dismissFollowUp() }}
                  className="ml-auto text-slate-600 hover:text-slate-300 flex-shrink-0 text-base leading-none"
                >×</button>
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Error */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="mx-5 mb-1 px-4 py-2.5 rounded-xl bg-err/8 border border-err/20 text-err text-[11px] font-medium flex-shrink-0"
            >
              {error}
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Orb section — always centered, never moves ── */}
        <div className={`flex-shrink-0 flex flex-col items-center justify-center gap-3 px-6 ${hasMessages ? 'py-5' : 'flex-1'}`}>
          {/* Ambient glow */}
          <div className="relative flex flex-col items-center gap-3">
            <div className="absolute w-56 h-56 rounded-full bg-brand/6 blur-3xl pointer-events-none" />
            <VoiceOrb state={sessionState} onToggle={isActive ? stopSession : startSession} />
          </div>

          {/* Status text */}
          <AnimatePresence mode="wait">
            <motion.p
              key={sessionState}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              className="text-[12px] text-slate-500 text-center"
            >
              {sessionState === 'listening'
                ? "I'm listening — go ahead…"
                : sessionState === 'speaking'
                ? 'Speaking…'
                : sessionState === 'greeting'
                ? 'Starting up…'
                : sessionState === 'transcribing' || sessionState === 'processing'
                ? 'Thinking…'
                : isActive
                ? 'Active'
                : 'Tap to talk'}
            </motion.p>
          </AnimatePresence>

          {/* Suggestion chips — only when idle */}
          <AnimatePresence>
            {!hasMessages && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 4 }}
                transition={{ duration: 0.3 }}
                className="w-full"
              >
                <div className="flex items-center gap-1.5 mb-2 justify-center">
                  <Lightbulb size={10} className="text-warn/70" />
                  <span className="text-[10px] text-slate-600 uppercase tracking-widest font-medium">Try saying</span>
                </div>
                <div className="flex flex-wrap gap-2 justify-center">
                  {SUGGESTIONS.map((s, i) => (
                    <motion.button
                      key={s.text}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: i * 0.04 }}
                      whileHover={{ scale: 1.04, y: -2 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={startSession}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-card/60 border border-border/50 text-[11px] text-slate-400 hover:text-slate-200 hover:border-brand/30 hover:bg-brand/5 transition-all duration-200"
                    >
                      <span className="text-xs">{s.icon}</span>
                      <span>"{s.text}"</span>
                      <ChevronRight size={9} className="text-slate-600" />
                    </motion.button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Wake word indicator */}
          <AnimatePresence>
            {!isActive && wakeSupported && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-card/50 border border-border/40"
              >
                <div className="relative">
                  <Radio size={9} className={wakeListening ? 'text-ok' : 'text-slate-600'} />
                  {wakeListening && <div className="absolute inset-0 rounded-full bg-ok/30 animate-ping" />}
                </div>
                <span className="text-[9px] text-slate-600">
                  {wakeListening ? <span className="text-ok/80">Listening for wake word</span> : 'Wake word off'}
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

      </div>
    </div>
  )
}
