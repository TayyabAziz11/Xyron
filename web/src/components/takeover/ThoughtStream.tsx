'use client'

import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

const LINES = [
  'initializing neural bridge',
  'syncing neural systems',
  'voice core connected',
  'scanning environment',
  'memory banks loaded',
  'workspace protocol active',
  'threat level: none',
  'focus matrix engaged',
  'neural link stable',
  'bio-rhythm synchronized',
  'system resources allocated',
  'takeover complete',
]

export default function ThoughtStream({ phase }: Props) {
  const active = phase === 'hud' || phase === 'active'
  const [visible, setVisible] = useState<string[]>([])

  useEffect(() => {
    if (!active) { setVisible([]); return }

    let i = 0
    setVisible([LINES[0]])

    const id = setInterval(() => {
      i = (i + 1) % LINES.length
      setVisible((prev) => [...prev.slice(-2), LINES[i]])
    }, 1900)

    return () => clearInterval(id)
  }, [active])

  return (
    <AnimatePresence>
      {active && (
        <motion.div
          key="thought-stream"
          className="fixed bottom-20 left-7 z-[75] select-none pointer-events-none"
          initial={{ opacity: 0, x: -16 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -16 }}
          transition={{ duration: 0.4, delay: 0.3 }}
          style={{ width: 270 }}
        >
          <div
            className="text-[7px] font-mono tracking-[4px] uppercase mb-2.5"
            style={{ color: 'rgba(0,200,255,0.28)' }}
          >
            AI THOUGHT STREAM
          </div>

          <div className="flex flex-col gap-[3px]">
            <AnimatePresence>
              {visible.map((line, i) => {
                const isCurrent = i === visible.length - 1
                return (
                  <motion.div
                    key={line}
                    className="text-[10px] font-mono tracking-widest leading-relaxed"
                    style={{
                      color: isCurrent
                        ? 'rgba(0,230,255,0.92)'
                        : 'rgba(0,160,190,0.28)',
                    }}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.3 }}
                  >
                    <span style={{ color: isCurrent ? 'rgba(0,200,255,0.55)' : 'rgba(0,130,160,0.2)' }}>
                      {'> '}
                    </span>
                    {line}
                    {isCurrent && (
                      <motion.span
                        animate={{ opacity: [1, 0] }}
                        transition={{ duration: 0.7, repeat: Infinity }}
                        style={{ color: 'rgba(0,220,255,0.8)', marginLeft: 2 }}
                      >
                        _
                      </motion.span>
                    )}
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
