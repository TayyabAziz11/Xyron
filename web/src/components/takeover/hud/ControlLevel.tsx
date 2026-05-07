'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props {
  phase: TakeoverPhase
  controlLevel: number
}

const R    = 28
const CIRC = 2 * Math.PI * R

export default function ControlLevel({ phase, controlLevel }: Props) {
  const [display, setDisplay]         = useState(0)
  const [showAuthority, setAuthority] = useState(false)
  const prevRef  = useRef(0)
  const rafRef   = useRef(0)
  const firedRef = useRef(false)

  const active = phase !== 'idle' && phase !== 'standdown'
  const isHud  = phase === 'hud' || phase === 'active'

  useEffect(() => {
    if (!active) { setDisplay(0); prevRef.current = 0; firedRef.current = false; return }

    cancelAnimationFrame(rafRef.current)
    const start = prevRef.current
    const end   = controlLevel
    if (start === end) return

    const t0  = performance.now()
    const dur = 900

    const tick = (now: number) => {
      const p    = Math.min((now - t0) / dur, 1)
      const ease = 1 - Math.pow(1 - p, 3)
      const cur  = Math.round(start + (end - start) * ease)
      setDisplay(cur)
      if (p < 1) {
        rafRef.current = requestAnimationFrame(tick)
      } else {
        prevRef.current = end
        if (end === 100 && !firedRef.current) {
          firedRef.current = true
          setAuthority(true)
          setTimeout(() => setAuthority(false), 3000)
        }
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [active, controlLevel])

  const lvlColor =
    display >= 95 ? 'rgba(255,255,255,0.96)' :
    display >= 80 ? 'rgba(255,90,90,0.95)'   :
    display >= 60 ? 'rgba(240,40,40,0.92)'   :
                    'rgba(200,20,20,0.85)'

  const glowColor =
    display >= 95 ? 'rgba(255,200,200,0.6)'  :
    display >= 80 ? 'rgba(255,60,60,0.75)'   :
                    'rgba(200,0,0,0.5)'

  const dashOffset = CIRC * (1 - display / 100)

  return (
    <>
      {/* ── Compact corner badge ── */}
      <AnimatePresence>
        {active && (
          <motion.div
            key="ctrl-lvl"
            className="fixed z-[79] pointer-events-none select-none"
            style={{ bottom: isHud ? 168 : 20, right: 20 }}
            initial={{ opacity: 0, scale: 0.75, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.75 }}
            transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
          >
            <div style={{
              background:    'rgba(3,0,0,0.90)',
              border:        `1px solid ${glowColor}`,
              borderRadius:  10,
              padding:       '10px 12px',
              backdropFilter:'blur(16px)',
              boxShadow:     `0 0 22px ${glowColor}, 0 0 1px rgba(255,30,30,0.12) inset`,
              transition:    'border-color 0.4s, box-shadow 0.4s',
              minWidth:      148,
            }}>
              {/* Label */}
              <div className="text-[7px] font-mono tracking-[4px] uppercase mb-2"
                style={{ color: 'rgba(180,60,60,0.65)' }}>
                CONTROL LEVEL
              </div>

              <div className="flex items-center gap-3">
                {/* Progress ring */}
                <div className="relative flex items-center justify-center flex-shrink-0"
                  style={{ width: 64, height: 64 }}>
                  <svg width="64" height="64" viewBox="0 0 68 68"
                    style={{ transform: 'rotate(-90deg)', overflow: 'visible' }}>
                    {/* Track */}
                    <circle cx="34" cy="34" r={R}
                      fill="none" stroke="rgba(180,0,0,0.14)" strokeWidth="5" />
                    {/* Fill */}
                    <motion.circle
                      cx="34" cy="34" r={R}
                      fill="none"
                      stroke={lvlColor}
                      strokeWidth="5"
                      strokeLinecap="round"
                      strokeDasharray={CIRC}
                      animate={{ strokeDashoffset: dashOffset }}
                      transition={{ duration: 0.25, ease: 'easeOut' }}
                      style={{ filter: `drop-shadow(0 0 4px ${glowColor})` }}
                    />
                  </svg>
                  <div className="absolute text-center">
                    <div className="text-[15px] font-black font-mono tabular-nums leading-none"
                      style={{ color: lvlColor }}>
                      {display}
                    </div>
                    <div className="text-[7px] font-mono leading-none"
                      style={{ color: 'rgba(180,80,80,0.55)' }}>%</div>
                  </div>
                </div>

                {/* Loading bars */}
                <div className="flex flex-col gap-[5px]" style={{ width: 58 }}>
                  {[0, 25, 50, 75].map((threshold, i) => {
                    const fill = Math.max(0, Math.min(100, (display - threshold) * 4))
                    return (
                      <div key={i} className="h-[3px] rounded-full overflow-hidden"
                        style={{ background: 'rgba(160,0,0,0.14)' }}>
                        <div className="h-full rounded-full"
                          style={{
                            width: `${fill}%`,
                            background: fill > 0
                              ? `linear-gradient(90deg, rgba(140,0,0,0.8), ${lvlColor})`
                              : 'transparent',
                            boxShadow: fill > 0 ? `0 0 5px ${glowColor}` : 'none',
                            transition: 'width 0.25s ease, box-shadow 0.25s',
                          }}
                        />
                      </div>
                    )
                  })}
                  {/* Status label */}
                  <div className="text-[6px] font-mono tracking-[2px] uppercase mt-0.5"
                    style={{ color: display >= 100 ? 'rgba(255,255,255,0.7)' : 'rgba(160,60,60,0.55)' }}>
                    {display >= 100 ? 'AUTHORITY GRANTED' :
                     display >= 80  ? 'SEIZING CONTROL'  :
                     display >= 50  ? 'ESCALATING'        :
                                      'INITIALIZING'}
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── SYSTEM AUTHORITY GRANTED full-screen flash ── */}
      <AnimatePresence>
        {showAuthority && (
          <motion.div
            key="authority-granted"
            className="fixed inset-0 z-[92] flex flex-col items-center justify-center pointer-events-none"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 1, 0.85, 0] }}
            transition={{ duration: 3, times: [0, 0.08, 0.7, 0.9, 1] }}
          >
            <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.88)' }} />

            <div className="relative z-10 text-center px-8">
              {/* Pulse rings */}
              {[1, 2, 3].map(i => (
                <motion.div key={i} className="absolute rounded-full pointer-events-none"
                  style={{
                    border: `1px solid rgba(255,40,40,${0.35 - i * 0.08})`,
                    width: i * 260, height: i * 260,
                    left: '50%', top: '50%',
                    transform: 'translate(-50%,-50%)',
                  }}
                  animate={{ scale: [1, 1.5, 1], opacity: [0.4, 0, 0.4] }}
                  transition={{ duration: 2.4, delay: i * 0.3, repeat: Infinity }}
                />
              ))}

              <motion.div
                className="text-[10px] font-mono tracking-[12px] uppercase mb-5"
                style={{ color: 'rgba(255,80,80,0.6)' }}
                animate={{ opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 0.9, repeat: Infinity }}
              >
                AUTHORITY TRANSFER COMPLETE
              </motion.div>

              <motion.div
                className="font-black tracking-tight leading-none font-mono"
                style={{
                  fontSize: 56,
                  color:      'rgba(255,255,255,0.98)',
                  textShadow: '0 0 60px rgba(255,60,60,1), 0 0 120px rgba(255,0,0,0.5)',
                }}
                initial={{ scale: 0.55, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
              >
                SYSTEM AUTHORITY
              </motion.div>

              <motion.div
                className="font-black tracking-tight leading-none font-mono"
                style={{
                  fontSize:   56,
                  color:      'rgba(255,50,50,0.99)',
                  textShadow: '0 0 60px rgba(255,60,60,1), 0 0 120px rgba(255,0,0,0.5)',
                }}
                initial={{ scale: 0.55, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.45, delay: 0.07, ease: [0.16, 1, 0.3, 1] }}
              >
                GRANTED
              </motion.div>

              <motion.div
                className="text-[13px] font-mono tracking-[6px] uppercase mt-6"
                style={{ color: 'rgba(200,80,80,0.7)' }}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4, duration: 0.4 }}
              >
                HUD LOCKING INTO PLACE
              </motion.div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
