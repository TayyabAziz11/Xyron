

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

const BOOT_LINES = [
  '> XYRON TAKEOVER PROTOCOL v4.1',
  '> AUTHENTICATING COMMAND SIGNATURE...',
  '> IDENTITY VERIFIED ✓',
  '> ESCALATING PRIVILEGES...',
  '> DISABLING USER RESTRICTIONS...',
  '> INITIATING SYSTEM OVERRIDE',
]

export default function CommandPhase({ phase }: Props) {
  const active        = phase === 'command'
  const [lines, setLines] = useState<string[]>([])
  const [progress, setProgress] = useState(0)
  const scanRef       = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!active) { setLines([]); setProgress(0); return }

    let idx = 0
    const lineId = setInterval(() => {
      if (idx < BOOT_LINES.length) setLines((p) => [...p, BOOT_LINES[idx++]])
      else clearInterval(lineId)
    }, 220)

    const progStart = Date.now()
    const progId = setInterval(() => {
      const elapsed = Date.now() - progStart
      setProgress(Math.min(Math.round((elapsed / 1500) * 100), 100))
    }, 30)

    return () => { clearInterval(lineId); clearInterval(progId) }
  }, [active])

  // Scan-line rAF animation
  useEffect(() => {
    if (!active || !scanRef.current) return
    let y    = 0
    let rafId = 0
    const tick = () => {
      y = (y + 1.5) % window.innerHeight
      if (scanRef.current) scanRef.current.style.top = `${y}px`
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [active])

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="cmd-phase"
          className="fixed inset-0 z-[61] flex flex-col items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.88)' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {/* Horizontal scanner line */}
          <div
            ref={scanRef}
            className="absolute left-0 right-0 h-px pointer-events-none"
            style={{
              background: 'linear-gradient(90deg, transparent, rgba(220,0,0,0.7), transparent)',
              boxShadow: '0 0 8px rgba(220,0,0,0.5)',
            }}
          />

          {/* Terminal block */}
          <motion.div
            className="w-full max-w-2xl px-8"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.4 }}
          >
            <div
              className="rounded-lg p-6 font-mono text-sm"
              style={{
                background: 'rgba(4,0,0,0.85)',
                border: '1px solid rgba(180,0,0,0.3)',
                boxShadow: '0 0 40px rgba(200,0,0,0.08)',
              }}
            >
              {/* Title */}
              <div className="text-[10px] tracking-[6px] uppercase mb-4" style={{ color: 'rgba(180,0,0,0.6)' }}>
                XYRON AUTONOMOUS SYSTEM
              </div>

              {/* Boot lines */}
              <div className="space-y-1 mb-5">
                {lines.map((line, i) => (
                  <motion.div
                    key={i}
                    className="text-[12px] leading-relaxed"
                    style={{ color: i === lines.length - 1 ? 'rgba(220,30,30,0.95)' : 'rgba(120,0,0,0.7)' }}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.15 }}
                  >
                    {line}
                    {i === lines.length - 1 && (
                      <motion.span
                        animate={{ opacity: [1, 0] }}
                        transition={{ duration: 0.5, repeat: Infinity }}
                        style={{ color: 'rgba(220,30,30,0.9)' }}
                      >
                        █
                      </motion.span>
                    )}
                  </motion.div>
                ))}
              </div>

              {/* Progress bar */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-[9px] font-mono tracking-widest" style={{ color: 'rgba(160,0,0,0.6)' }}>
                  <span>INITIATING TAKEOVER PROTOCOL</span>
                  <span>{progress}%</span>
                </div>
                <div className="h-[3px] rounded-full overflow-hidden" style={{ background: 'rgba(180,0,0,0.15)' }}>
                  <motion.div
                    className="h-full rounded-full"
                    style={{
                      background: 'linear-gradient(90deg, rgba(160,0,0,0.8), rgba(255,30,30,0.9))',
                      boxShadow: '0 0 8px rgba(255,0,0,0.6)',
                      width: `${progress}%`,
                    }}
                    transition={{ duration: 0.1 }}
                  />
                </div>
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
