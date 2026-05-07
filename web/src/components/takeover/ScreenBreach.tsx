'use client'

import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

// Primary crack segments — radiate from screen center (cx=50%, cy=50%)
// Coordinates in a 1000×700 viewBox with origin at center (500, 350).
const CRACKS: { d: string; width: number; delay: number }[] = [
  { d: 'M 500 350 L 335 195 L 290 110 L 260 60',  width: 1.4, delay: 0.00 },
  { d: 'M 500 350 L 665 185 L 740  90 L 790 30',  width: 1.4, delay: 0.05 },
  { d: 'M 500 350 L 210 345 L  90 310 L  20 330', width: 1.2, delay: 0.10 },
  { d: 'M 500 350 L 790 360 L 920 325 L 990 345', width: 1.2, delay: 0.08 },
  { d: 'M 500 350 L 430 520 L 370 640 L 340 710', width: 1.0, delay: 0.15 },
  { d: 'M 500 350 L 580 530 L 640 660 L 680 720', width: 1.0, delay: 0.12 },
  { d: 'M 500 350 L 260 465 L 160 560 L 100 640', width: 0.9, delay: 0.18 },
  { d: 'M 500 350 L 750 470 L 860 570 L 930 650', width: 0.9, delay: 0.16 },
]

// Hairline secondary cracks
const SECONDARY: { d: string; delay: number }[] = [
  { d: 'M 500 350 L 395 240 L 350 160', delay: 0.22 },
  { d: 'M 500 350 L 610 230 L 660 150', delay: 0.24 },
  { d: 'M 500 350 L 310 420 L 240 500', delay: 0.28 },
  { d: 'M 500 350 L 680 440 L 760 530', delay: 0.26 },
]

const GLOW = 'rgba(0, 210, 255, 0.75)'
const DIM  = 'rgba(0, 160, 200, 0.25)'

export default function ScreenBreach({ phase }: Props) {
  const visible =
    phase === 'breach'   ||
    phase === 'awakening' ||
    phase === 'hud'      ||
    phase === 'active'

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="breach"
          className="fixed inset-0 z-[65] pointer-events-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 1.2 } }}
          transition={{ duration: 0.15 }}
        >
          <svg
            viewBox="0 0 1000 700"
            className="w-full h-full"
            preserveAspectRatio="xMidYMid slice"
          >
            <defs>
              <filter id="breach-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="2.5" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              <filter id="crack-soft" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="1" />
              </filter>
            </defs>

            {/* Impact radial burst */}
            <motion.circle
              cx="500" cy="350" r="4"
              fill={GLOW}
              filter="url(#breach-glow)"
              initial={{ r: 0, opacity: 0 }}
              animate={{ r: [0, 12, 4], opacity: [0, 1, 0.8] }}
              transition={{ duration: 0.35, ease: 'easeOut' }}
            />

            {/* Primary cracks */}
            {CRACKS.map((c, i) => (
              <motion.path
                key={`p${i}`}
                d={c.d}
                stroke={GLOW}
                strokeWidth={c.width}
                fill="none"
                filter="url(#breach-glow)"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 1 }}
                transition={{ duration: 0.45, delay: c.delay, ease: 'easeOut' }}
              />
            ))}

            {/* Secondary hairline cracks */}
            {SECONDARY.map((c, i) => (
              <motion.path
                key={`s${i}`}
                d={c.d}
                stroke={DIM}
                strokeWidth={0.6}
                fill="none"
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ duration: 0.4, delay: c.delay, ease: 'easeOut' }}
              />
            ))}
          </svg>

          {/* Center impact glow */}
          <motion.div
            className="absolute pointer-events-none"
            style={{
              left: '50%', top: '50%',
              transform: 'translate(-50%, -50%)',
              width: 220, height: 220,
              borderRadius: '50%',
              background: 'radial-gradient(circle, rgba(0,210,255,0.22) 0%, rgba(0,120,180,0.08) 50%, transparent 70%)',
            }}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: [0, 1.8, 1.2], opacity: [0, 0.9, 0.4] }}
            transition={{ duration: 0.55, delay: 0.05, ease: 'easeOut' }}
          />

          {/* Brightness pulse on impact */}
          <motion.div
            className="absolute inset-0 pointer-events-none"
            style={{ background: 'rgba(0,200,255,0.04)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 0.3, delay: 0.05 }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  )
}
