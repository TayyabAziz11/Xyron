'use client'

import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

// Crack paths in 1000×700 viewBox — crimson/red theme
const CRACKS = [
  { d: 'M 500 350 L 318 178 L 270 95  L 235 40',  w: 1.6, delay: 0.00 },
  { d: 'M 500 350 L 688 172 L 760 80  L 810 20',  w: 1.6, delay: 0.04 },
  { d: 'M 500 350 L 190 342 L  60 305 L  10 330', w: 1.3, delay: 0.09 },
  { d: 'M 500 350 L 810 358 L 940 320 L 998 342', w: 1.3, delay: 0.07 },
  { d: 'M 500 350 L 410 528 L 345 658 L 310 720', w: 1.1, delay: 0.14 },
  { d: 'M 500 350 L 595 532 L 655 668 L 700 724', w: 1.1, delay: 0.11 },
  { d: 'M 500 350 L 242 468 L 135 572 L  70 658', w: 0.9, delay: 0.18 },
  { d: 'M 500 350 L 762 476 L 875 580 L 950 662', w: 0.9, delay: 0.16 },
  { d: 'M 500 350 L 385 235 L 330 145', w: 0.7, delay: 0.21 },
  { d: 'M 500 350 L 618 228 L 672 140', w: 0.7, delay: 0.23 },
  { d: 'M 500 350 L 295 425 L 218 510', w: 0.6, delay: 0.26 },
  { d: 'M 500 350 L 698 448 L 782 542', w: 0.6, delay: 0.25 },
]

export default function BreachPhase({ phase }: Props) {
  const flickerRef = useRef<HTMLDivElement>(null)
  const shakeRef   = useRef<HTMLDivElement>(null)
  const rafRef     = useRef(0)

  const showCracks = phase === 'breach' || phase === 'root'
  const showOverlay = phase === 'breach'

  // CRT flicker + camera shake rAF
  useEffect(() => {
    if (!showOverlay) { cancelAnimationFrame(rafRef.current); return }
    let frame = 0
    const tick = () => {
      frame++
      if (flickerRef.current) {
        // Random brightness flicker
        flickerRef.current.style.opacity = frame % 7 === 0 ? '0.12' : '0'
      }
      if (shakeRef.current) {
        const sx = frame % 3 === 0 ? (Math.random() - 0.5) * 5 : 0
        const sy = frame % 5 === 0 ? (Math.random() - 0.5) * 3 : 0
        shakeRef.current.style.transform = `translate(${sx}px, ${sy}px)`
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [showOverlay])

  return (
    <>
      {/* Camera shake wrapper */}
      <div ref={shakeRef} className="fixed inset-0 z-[62] pointer-events-none" style={{ willChange: 'transform' }}>
        {/* SVG Cracks */}
        <AnimatePresence>
          {showCracks && (
            <motion.div
              key="cracks"
              className="absolute inset-0"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, transition: { duration: 1.5 } }}
              transition={{ duration: 0.1 }}
            >
              <svg viewBox="0 0 1000 700" className="w-full h-full" preserveAspectRatio="xMidYMid slice">
                <defs>
                  <filter id="red-crack-glow" x="-30%" y="-30%" width="160%" height="160%">
                    <feGaussianBlur stdDeviation="2" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>

                {/* Impact point */}
                <motion.circle cx="500" cy="350" r="5" fill="rgba(255,40,40,0.9)"
                  filter="url(#red-crack-glow)"
                  initial={{ r: 0, opacity: 0 }}
                  animate={{ r: [0, 16, 5], opacity: [0, 1, 0.8] }}
                  transition={{ duration: 0.3 }}
                />

                {CRACKS.map((c, i) => (
                  <motion.path key={i} d={c.d}
                    stroke="rgba(220,30,30,0.85)" strokeWidth={c.w} fill="none"
                    filter="url(#red-crack-glow)"
                    initial={{ pathLength: 0, opacity: 0 }}
                    animate={{ pathLength: 1, opacity: 1 }}
                    transition={{ duration: 0.45, delay: c.delay, ease: 'easeOut' }}
                  />
                ))}
              </svg>

              {/* Impact glow */}
              <motion.div className="absolute pointer-events-none"
                style={{
                  left: '50%', top: '50%', transform: 'translate(-50%,-50%)',
                  width: 240, height: 240, borderRadius: '50%',
                  background: 'radial-gradient(circle, rgba(255,40,40,0.25) 0%, transparent 70%)',
                }}
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: [0, 2, 1.3], opacity: [0, 0.9, 0.35] }}
                transition={{ duration: 0.5, delay: 0.05 }}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Breach warning overlay */}
      <AnimatePresence>
        {showOverlay && (
          <motion.div
            key="breach-overlay"
            className="fixed inset-0 z-[63] flex flex-col items-center justify-center pointer-events-none"
            style={{ background: 'rgba(8,0,0,0.72)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            {/* RGB chromatic aberration layers */}
            <div className="absolute inset-0 overflow-hidden pointer-events-none">
              <div className="absolute inset-0" style={{ background: 'rgba(255,0,0,0.04)', transform: 'translateX(-3px)', mixBlendMode: 'screen' }} />
              <div className="absolute inset-0" style={{ background: 'rgba(0,200,255,0.03)', transform: 'translateX(3px)', mixBlendMode: 'screen' }} />
            </div>

            {/* Warning text */}
            <motion.div className="text-center" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.3, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}>
              <motion.div
                className="text-[11px] font-mono tracking-[8px] uppercase mb-4"
                style={{ color: 'rgba(255,60,60,0.6)' }}
                animate={{ opacity: [0.6, 1, 0.6] }}
                transition={{ duration: 0.8, repeat: Infinity }}
              >
                ⚠ SECURITY BREACH DETECTED ⚠
              </motion.div>
              <div
                className="text-[42px] font-black tracking-tighter leading-none"
                style={{
                  color: 'rgba(255,25,25,0.95)',
                  textShadow: '0 0 40px rgba(255,0,0,0.8), 0 0 80px rgba(255,0,0,0.4)',
                  fontFamily: 'monospace',
                }}
              >
                BREACH
              </div>
              <div className="text-[13px] font-mono tracking-[4px] mt-3" style={{ color: 'rgba(180,0,0,0.7)' }}>
                ACCESSING CORE SYSTEM...
              </div>
            </motion.div>

            {/* Scanline interference */}
            <div
              className="absolute inset-0 pointer-events-none"
              style={{
                backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(255,0,0,0.04) 2px, rgba(255,0,0,0.04) 4px)',
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Flash flicker */}
      <div ref={flickerRef} className="fixed inset-0 z-[64] pointer-events-none"
        style={{ background: 'rgba(255,30,30,1)', opacity: 0, willChange: 'opacity' }}
      />
    </>
  )
}
