'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { useParallaxCore } from '../../hooks/useParallaxCore'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

const NETWORK_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
const ORB_ANGLES     = [0, 60, 120, 180, 240, 300]

export default function NeuralCore({ phase }: Props) {
  const visible = phase === 'awakening' || phase === 'hud' || phase === 'active'
  const parallaxRef = useParallaxCore(9)

  const coreOpacity = phase === 'hud' || phase === 'active' ? 0.55 : 1

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="neural-core"
          className="fixed inset-0 z-[67] pointer-events-none flex items-center justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: coreOpacity }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.7 }}
        >
          {/* Deep ambient darkening behind the core */}
          <div
            className="absolute inset-0"
            style={{
              background: 'radial-gradient(ellipse 55% 45% at 50% 50%, rgba(0,6,18,0.5) 0%, transparent 100%)',
            }}
          />

          {/* Parallax container — mouse movement shifts this element */}
          <div
            ref={parallaxRef}
            className="relative flex items-center justify-center"
            style={{ width: 300, height: 300, willChange: 'transform' }}
          >
            {/* ── Outer energy field ── */}
            <motion.div
              className="absolute rounded-full"
              style={{
                width: 300, height: 300,
                background: 'radial-gradient(circle, transparent 38%, rgba(0,180,255,0.04) 65%, transparent 100%)',
              }}
              animate={{ scale: [1, 1.12, 1], opacity: [0.5, 1, 0.5] }}
              transition={{ duration: 3.5, repeat: Infinity, ease: 'easeInOut' }}
            />

            {/* ── Ring 1 — equatorial, full circle ── */}
            <motion.div
              className="absolute rounded-full"
              style={{
                width: 240, height: 240,
                border: '1px solid rgba(0,200,255,0.12)',
                borderTopColor:    'rgba(0,220,255,0.85)',
                borderBottomColor: 'rgba(0,180,255,0.15)',
              }}
              animate={{ rotate: 360 }}
              transition={{ duration: 9, repeat: Infinity, ease: 'linear' }}
            />

            {/* ── Ring 2 — polar ring (flattened = 60% height) ── */}
            <motion.div
              className="absolute rounded-full"
              style={{
                width: 200, height: 120,
                border: '1px solid rgba(0,200,255,0.10)',
                borderLeftColor:  'rgba(0,220,255,0.7)',
                borderRightColor: 'rgba(0,180,255,0.12)',
              }}
              animate={{ rotate: -360 }}
              transition={{ duration: 6.5, repeat: Infinity, ease: 'linear' }}
            />

            {/* ── Ring 3 — diagonal axis (flattened, rotated 45°) ── */}
            <div className="absolute" style={{ transform: 'rotate(42deg)' }}>
              <motion.div
                className="rounded-full"
                style={{
                  width: 170, height: 100,
                  border: '1px solid rgba(0,200,255,0.08)',
                  borderTopColor:    'rgba(0,200,255,0.55)',
                  borderBottomColor: 'rgba(0,140,200,0.08)',
                }}
                animate={{ rotate: 360 }}
                transition={{ duration: 5, repeat: Infinity, ease: 'linear' }}
              />
            </div>

            {/* ── Orbital dots ── */}
            <motion.div
              className="absolute"
              style={{ width: '100%', height: '100%' }}
              animate={{ rotate: 360 }}
              transition={{ duration: 9, repeat: Infinity, ease: 'linear' }}
            >
              {ORB_ANGLES.map((deg, i) => {
                const rad = (deg * Math.PI) / 180
                return (
                  <div
                    key={i}
                    className="absolute"
                    style={{
                      width: i % 2 === 0 ? 5 : 3.5,
                      height: i % 2 === 0 ? 5 : 3.5,
                      borderRadius: '50%',
                      background: i % 2 === 0 ? 'rgba(0,230,255,0.95)' : 'rgba(0,190,240,0.6)',
                      boxShadow: i % 2 === 0 ? '0 0 8px rgba(0,220,255,1), 0 0 16px rgba(0,200,255,0.5)' : 'none',
                      left: 150 + Math.cos(rad) * 119 - (i % 2 === 0 ? 2.5 : 1.75),
                      top:  150 + Math.sin(rad) * 119 - (i % 2 === 0 ? 2.5 : 1.75),
                    }}
                  />
                )
              })}
            </motion.div>

            {/* ── Central sphere ── */}
            <motion.div
              className="relative z-10 rounded-full"
              style={{
                width: 96, height: 96,
                background:
                  'radial-gradient(circle at 33% 30%, rgba(130,230,255,0.95) 0%, rgba(0,160,230,0.85) 38%, rgba(0,50,100,0.95) 100%)',
                boxShadow:
                  '0 0 40px rgba(0,200,255,0.55), 0 0 80px rgba(0,180,255,0.22), 0 0 140px rgba(0,160,255,0.10), inset 0 0 22px rgba(255,255,255,0.12)',
              }}
              initial={{ scale: 0 }}
              animate={{
                scale: 1,
                boxShadow: [
                  '0 0 40px rgba(0,200,255,0.55), 0 0 80px rgba(0,180,255,0.22), 0 0 140px rgba(0,160,255,0.10), inset 0 0 22px rgba(255,255,255,0.12)',
                  '0 0 60px rgba(0,220,255,0.80), 0 0 110px rgba(0,200,255,0.38), 0 0 180px rgba(0,180,255,0.18), inset 0 0 30px rgba(255,255,255,0.18)',
                  '0 0 40px rgba(0,200,255,0.55), 0 0 80px rgba(0,180,255,0.22), 0 0 140px rgba(0,160,255,0.10), inset 0 0 22px rgba(255,255,255,0.12)',
                ],
              }}
              transition={{
                scale: { duration: 0.8, ease: [0.16, 1, 0.3, 1] },
                boxShadow: { duration: 2.8, repeat: Infinity, ease: 'easeInOut' },
              }}
            >
              {/* Specular highlight */}
              <div
                className="absolute rounded-full"
                style={{
                  width: 28, height: 18,
                  background: 'radial-gradient(circle, rgba(255,255,255,0.65) 0%, transparent 100%)',
                  top: 14, left: 18,
                }}
              />
            </motion.div>

            {/* ── Network lines radiating from center ── */}
            <svg
              className="absolute inset-0 w-full h-full pointer-events-none"
              viewBox="0 0 300 300"
            >
              {NETWORK_ANGLES.map((deg, i) => {
                const rad = (deg * Math.PI) / 180
                const x2  = 150 + Math.cos(rad) * 145
                const y2  = 150 + Math.sin(rad) * 145
                return (
                  <motion.line
                    key={i}
                    x1="150" y1="150"
                    x2={x2}   y2={y2}
                    stroke="rgba(0,200,255,0.5)"
                    strokeWidth="0.6"
                    initial={{ pathLength: 0, opacity: 0 }}
                    animate={{ pathLength: [0, 1, 0], opacity: [0, 0.5, 0] }}
                    transition={{
                      duration: 3.5,
                      repeat: Infinity,
                      delay: i * 0.22,
                      ease: 'easeInOut',
                    }}
                  />
                )
              })}
            </svg>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
