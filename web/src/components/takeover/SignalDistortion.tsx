'use client'

import { useEffect, useRef } from 'react'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

// Phase 1 — RGB split, screen shake, brightness flicker, persistent scanlines.
// All animation is done via direct DOM writes inside rAF — zero React re-renders.
export default function SignalDistortion({ phase }: Props) {
  const rRef  = useRef<HTMLDivElement>(null)
  const gRef  = useRef<HTMLDivElement>(null)
  const flRef = useRef<HTMLDivElement>(null)
  const rafId = useRef<number>(0)

  const disrupting = phase === 'disruption'

  useEffect(() => {
    cancelAnimationFrame(rafId.current)

    if (!disrupting) {
      if (rRef.current)  rRef.current.style.opacity  = '0'
      if (gRef.current)  gRef.current.style.opacity  = '0'
      if (flRef.current) flRef.current.style.opacity = '0'
      return
    }

    let frame = 0
    const tick = () => {
      frame++
      const rx = frame % 2 === 0 ? -5 : 4
      const gx = frame % 2 === 0 ? 4  : -5
      const fl = frame % 6 === 0

      if (rRef.current) {
        rRef.current.style.opacity   = '1'
        rRef.current.style.transform = `translateX(${rx}px)`
      }
      if (gRef.current) {
        gRef.current.style.opacity   = '1'
        gRef.current.style.transform = `translateX(${gx}px)`
      }
      if (flRef.current) {
        flRef.current.style.opacity = fl ? '0.07' : '0'
      }

      rafId.current = requestAnimationFrame(tick)
    }
    rafId.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId.current)
  }, [disrupting])

  if (phase === 'idle') return null

  return (
    <>
      {/* Persistent scanlines — visible across all non-idle phases */}
      <div
        className="fixed inset-0 pointer-events-none z-[68]"
        style={{
          backgroundImage:
            'repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.18) 3px, rgba(0,0,0,0.18) 4px)',
          opacity: disrupting ? 0.9 : 0.35,
          transition: 'opacity 0.6s',
        }}
      />

      {/* RGB channel — red */}
      <div
        ref={rRef}
        className="fixed inset-0 pointer-events-none z-[70]"
        style={{
          background: 'rgba(255,20,60,0.09)',
          mixBlendMode: 'screen',
          opacity: 0,
          willChange: 'transform, opacity',
        }}
      />

      {/* RGB channel — cyan */}
      <div
        ref={gRef}
        className="fixed inset-0 pointer-events-none z-[70]"
        style={{
          background: 'rgba(0,220,255,0.07)',
          mixBlendMode: 'screen',
          opacity: 0,
          willChange: 'transform, opacity',
        }}
      />

      {/* Brightness flicker */}
      <div
        ref={flRef}
        className="fixed inset-0 pointer-events-none z-[71]"
        style={{
          background: 'rgba(255,255,255,1)',
          opacity: 0,
          willChange: 'opacity',
        }}
      />
    </>
  )
}
