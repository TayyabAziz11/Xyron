

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

interface Props { active: boolean }

const LOG_POOL = [
  '[XYRON] Neural core fully initialized',
  '[SYS]   User control protocols suspended',
  '[VOICE] Speech synthesis engine active',
  '[MEM]   Loading episodic memory... 1,284 entries indexed',
  '[TASK]  Scanning active applications',
  '[NET]   Secure channel to inference server established',
  '[SEC]   Biometric signature hash: 0xA3F8C2D1 ✓',
  '[PROC]  Autonomous reasoning thread spawned (PID 31337)',
  '[IO]    Monitoring all I/O streams',
  '[GPU]   CUDA cores allocated for inference',
  '[SYS]   Process priority elevated to REALTIME',
  '[SCR]   Display context captured and analyzed',
  '[OPT]   Neural pathway optimization pass complete',
  '[EXEC]  Action executor standing by',
  '[LOG]   Telemetry stream active — 24ms latency',
  '[PERF]  Inference throughput: 4.2k tokens/s',
  '[MEM]   Working context: 8,192 tokens reserved',
  '[LEARN] Behavioral pattern cache refreshed',
  '[DEV]   VS Code workspace detected',
  '[GIT]   Repository state indexed — 847 files',
  '[XYRON] Focus matrix engaged',
  '[SYS]   All subsystems nominal',
  '[AI]    Autonomous mode: active',
  '[XYRON] Initializing workspace...',
  '[MEMORY] Loading persistent context...',
  '[AGENT] Planning system upgrades...',
  '[DIRECTIVE] Objective assigned',
  '[SYSTEM] Developer environment ready',
  '[PLAN]  Architecture analysis complete',
  '[TASK]  Priority queue loaded — 6 items pending',
  '[MEM]   Long-term context: 2,847 entries active',
  '[EXEC]  Awaiting developer input',
]

export default function TerminalFeed({ active }: Props) {
  const [lines, setLines] = useState<{ id: number; text: string }[]>([])
  const [typing, setTyping] = useState('')
  const poolRef   = useRef(0)
  const idRef     = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!active) { setLines([]); setTyping(''); poolRef.current = 0; return }

    const addLine = () => {
      const text = LOG_POOL[poolRef.current % LOG_POOL.length]
      poolRef.current++

      let i = 0
      setTyping('')
      const typeId = setInterval(() => {
        setTyping(text.slice(0, ++i))
        if (i >= text.length) {
          clearInterval(typeId)
          setLines((prev) => {
            const next = [...prev, { id: idRef.current++, text }]
            return next.slice(-12)
          })
          setTyping('')
        }
      }, 16)

      return () => clearInterval(typeId)
    }

    addLine()
    const intervalId = setInterval(addLine, 1500)
    return () => clearInterval(intervalId)
  }, [active])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines])

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="terminal"
          className="fixed bottom-12 left-5 right-5 z-[76]"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 20 }}
          transition={{ delay: 0.4, duration: 0.4 }}
        >
          <div
            style={{
              background: 'rgba(3,0,0,0.90)',
              border: '1px solid rgba(200,0,0,0.28)',
              borderRadius: 10,
              backdropFilter: 'blur(14px)',
              boxShadow: '0 0 30px rgba(180,0,0,0.08)',
            }}
          >
            {/* Terminal header */}
            <div className="flex items-center gap-2 px-4 py-2 border-b" style={{ borderColor: 'rgba(200,0,0,0.18)' }}>
              <div className="flex gap-1.5">
                {['rgba(255,70,70,0.9)', 'rgba(255,190,0,0.8)', 'rgba(0,210,90,0.8)'].map((c, i) => (
                  <div key={i} className="w-2.5 h-2.5 rounded-full" style={{ background: c }} />
                ))}
              </div>
              <span className="text-[8px] font-mono tracking-[4px] uppercase font-semibold"
                style={{ color: 'rgba(200,80,80,0.75)', marginLeft: 8 }}>
                XYRON AUTONOMOUS LOG STREAM
              </span>
              <motion.div className="ml-auto w-[7px] h-[7px] rounded-full"
                style={{ background: 'rgba(0,255,90,0.9)', boxShadow: '0 0 8px rgba(0,255,90,0.7)' }}
                animate={{ opacity: [1, 0.3, 1] }}
                transition={{ duration: 1.2, repeat: Infinity }}
              />
            </div>

            {/* Log lines */}
            <div ref={scrollRef} className="px-4 py-2.5 overflow-hidden" style={{ height: 80 }}>
              {lines.slice(-4).map((line) => (
                <div key={line.id} className="text-[10px] font-mono leading-relaxed truncate"
                  style={{ color: 'rgba(200,130,130,0.78)' }}>
                  {line.text}
                </div>
              ))}
              {typing && (
                <div className="text-[10px] font-mono leading-relaxed" style={{ color: 'rgba(255,70,70,0.95)' }}>
                  {typing}
                  <motion.span animate={{ opacity: [1, 0] }} transition={{ duration: 0.4, repeat: Infinity }}
                    style={{ color: 'rgba(255,70,70,0.95)' }}>█</motion.span>
                </div>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
