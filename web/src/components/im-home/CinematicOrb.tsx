'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import type { ImHomePhase } from '@/hooks/useImHomeProtocol'
import { useEmotionState } from '@/hooks/useEmotionState'
import { ORB_VARIANTS } from '@/state/emotionState'

interface Props {
  phase: ImHomePhase
}

// ── Inner canvas: neural energy swirl ─────────────────────────────────────────

function EnergyCanvas({
  phase,
  primaryColor = '#ff2020',
  accentColor  = '#00ffff',
}: {
  phase:        ImHomePhase
  primaryColor?: string
  accentColor?:  string
}) {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const rafRef       = useRef<number>(0)
  const phaseRef     = useRef(phase)
  const primaryRef   = useRef(primaryColor)
  const accentRef    = useRef(accentColor)
  useEffect(() => { phaseRef.current = phase }, [phase])
  useEffect(() => { primaryRef.current = primaryColor }, [primaryColor])
  useEffect(() => { accentRef.current  = accentColor  }, [accentColor])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = canvas.height = 280

    type Spark = { angle: number; speed: number; r: number; life: number; maxLife: number; cyan: boolean }
    const sparks: Spark[] = Array.from({ length: 40 }, (_, i) => ({
      angle:   (i / 40) * Math.PI * 2,
      speed:   0.012 + Math.random() * 0.018,
      r:       40 + Math.random() * 55,
      life:    Math.random() * 80,
      maxLife: 80 + Math.random() * 40,
      cyan:    i % 7 === 0,
    }))

    let t = 0
    const draw = () => {
      t++
      const p = phaseRef.current
      const intensity = p === 'idle' ? 0 : p === 'phase5' ? 1.2 : 0.85
      const cx = 140, cy = 140

      ctx.clearRect(0, 0, 280, 280)

      sparks.forEach(sp => {
        sp.angle += sp.speed
        sp.life++
        if (sp.life >= sp.maxLife) {
          sp.life    = 0
          sp.r       = 30 + Math.random() * 65
          sp.speed   = 0.01 + Math.random() * 0.02
          sp.cyan    = Math.random() < 0.12
        }
        const fade = Math.sin((sp.life / sp.maxLife) * Math.PI)
        const x    = cx + Math.cos(sp.angle) * sp.r
        const y    = cy + Math.sin(sp.angle) * sp.r
        const a    = fade * intensity * 0.7

        ctx.beginPath()
        ctx.arc(x, y, sp.cyan ? 1.8 : 1.2, 0, Math.PI * 2)
        ctx.fillStyle = sp.cyan
          ? `rgba(${hexToRgb(accentRef.current)},${a})`
          : `rgba(${hexToRgb(primaryRef.current)},${a})`
        ctx.fill()
      })

      // Core glow rings
      ;[0, 1, 2].forEach(i => {
        const phase2 = t * 0.04 + (i * Math.PI * 2) / 3
        const r      = 18 + i * 10 + Math.sin(phase2) * 4
        const a      = (0.3 - i * 0.07) * intensity
        const grd    = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
        grd.addColorStop(0, `rgba(${hexToRgb(primaryRef.current)},${a})`)
        grd.addColorStop(1, 'transparent')
        ctx.beginPath()
        ctx.arc(cx, cy, r, 0, Math.PI * 2)
        ctx.fillStyle = grd
        ctx.fill()
      })

      rafRef.current = requestAnimationFrame(draw)
    }
    rafRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return <canvas ref={canvasRef} width={280} height={280} className="absolute inset-0 rounded-full" style={{ mixBlendMode: 'screen' }} />
}

// ── Hex color utility ──────────────────────────────────────────────────────────

function hexToRgb(hex: string): string {
  const clean = hex.replace('#', '')
  if (clean.length !== 6) return '255,32,32'
  const r = parseInt(clean.slice(0, 2), 16)
  const g = parseInt(clean.slice(2, 4), 16)
  const b = parseInt(clean.slice(4, 6), 16)
  return `${r},${g},${b}`
}

// ── Waveform bars ──────────────────────────────────────────────────────────────

function WaveBars({ active }: { active: boolean }) {
  const barsRef = useRef<HTMLDivElement[]>([])
  const rafRef  = useRef<number>(0)

  useEffect(() => {
    const animate = () => {
      barsRef.current.forEach(bar => {
        if (!bar) return
        const h = active ? (Math.random() * 32 + 6) : (Math.random() * 6 + 2)
        bar.style.height = `${h}px`
      })
      rafRef.current = requestAnimationFrame(animate)
    }
    rafRef.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(rafRef.current)
  }, [active])

  return (
    <div className="flex items-end gap-[2px] h-10">
      {Array.from({ length: 12 }).map((_, i) => (
        <div
          key={i}
          ref={el => { if (el) barsRef.current[i] = el }}
          className="w-[3px] rounded-full"
          style={{
            height: '4px',
            background: i % 4 === 0 ? '#00ffff' : '#ff2020',
            boxShadow: i % 4 === 0 ? '0 0 4px #00ffff' : '0 0 4px #ff2020',
            transition: 'height 60ms ease',
          }}
        />
      ))}
    </div>
  )
}

// ── Rotating segmented ring ────────────────────────────────────────────────────

function SegmentRing({ radius, segments, speed, color, width = 1, gap = 6 }: {
  radius: number; segments: number; speed: number
  color: string; width?: number; gap?: number
}) {
  const [angle, setAngle] = useState(0)
  const rafRef = useRef<number>(0)
  const tRef   = useRef(0)

  useEffect(() => {
    const tick = () => {
      tRef.current += speed
      setAngle(tRef.current)
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafRef.current)
  }, [speed])

  const cx = radius + 4
  const cy = radius + 4
  const totalAngle = (Math.PI * 2) / segments
  const gapAngle   = gap * (Math.PI / 180)
  const arcAngle   = totalAngle - gapAngle

  return (
    <svg
      width={cx * 2} height={cy * 2}
      className="absolute"
      style={{
        transform:  `rotate(${angle}rad)`,
        willChange: 'transform',
        top: '50%', left: '50%',
        marginTop:  -cy,
        marginLeft: -cx,
        position:   'absolute',
      }}
    >
      {Array.from({ length: segments }).map((_, i) => {
        const start = (i * totalAngle)
        const x1 = cx + radius * Math.cos(start)
        const y1 = cy + radius * Math.sin(start)
        const x2 = cx + radius * Math.cos(start + arcAngle)
        const y2 = cy + radius * Math.sin(start + arcAngle)
        return (
          <path
            key={i}
            d={`M ${x1} ${y1} A ${radius} ${radius} 0 0 1 ${x2} ${y2}`}
            fill="none"
            stroke={color}
            strokeWidth={width}
            style={{ filter: `drop-shadow(0 0 3px ${color})` }}
          />
        )
      })}
    </svg>
  )
}

// ── Scan sweep ────────────────────────────────────────────────────────────────

function ScanSweep({ radius }: { radius: number }) {
  const rafRef  = useRef<number>(0)
  const angleRef = useRef(0)
  const canvRef  = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const size = (radius + 4) * 2
    canvas.width = canvas.height = size
    const cx = size / 2, cy = size / 2

    const draw = () => {
      angleRef.current += 0.025
      ctx.clearRect(0, 0, size, size)

      const a = angleRef.current
      // sweep drawn manually below

      // Draw sweep arc manually
      ctx.save()
      ctx.translate(cx, cy)
      ctx.rotate(a)
      const sweep = ctx.createLinearGradient(0, 0, radius, 0)
      sweep.addColorStop(0,    'rgba(0,255,255,0.0)')
      sweep.addColorStop(0.5,  'rgba(0,255,255,0.12)')
      sweep.addColorStop(1.0,  'rgba(0,255,255,0.0)')
      ctx.beginPath()
      ctx.moveTo(0, 0)
      ctx.arc(0, 0, radius, -0.35, 0.35)
      ctx.closePath()
      ctx.fillStyle = sweep
      ctx.fill()
      ctx.restore()

      rafRef.current = requestAnimationFrame(draw)
    }
    rafRef.current = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(rafRef.current)
  }, [radius])

  const size = (radius + 4) * 2
  return (
    <canvas
      ref={canvRef}
      width={size} height={size}
      className="absolute"
      style={{ top: '50%', left: '50%', transform: 'translate(-50%,-50%)', mixBlendMode: 'screen', pointerEvents: 'none' }}
    />
  )
}

// ── Audience HUD ──────────────────────────────────────────────────────────────
// Shown when mood_state === 'AUDIENCE_MODE'. Lingers 5s after mode ends.

function AudienceHUD({ moduleCount }: { moduleCount: number }) {
  return (
    <motion.div
      className="absolute inset-0 flex flex-col items-center justify-end pb-4 pointer-events-none z-20"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.5 }}
    >
      <div
        className="font-mono text-center"
        style={{ color: '#7c4dff', textShadow: '0 0 12px rgba(124,77,255,0.8)' }}
      >
        <p className="text-[8px] tracking-[0.4em] font-bold uppercase mb-0.5">
          XYRON SELF-INTRODUCTION
        </p>
        <p className="text-[7px] tracking-[0.25em] opacity-70">
          {moduleCount} MODULES ACTIVE · LOCAL-FIRST SYSTEM
        </p>
      </div>
    </motion.div>
  )
}

// ── Cinematic Orb ─────────────────────────────────────────────────────────────

export function CinematicOrb({ phase }: Props) {
  const isActive = phase !== 'idle'
  const isPhase5 = phase === 'phase5'
  const emotion  = useEmotionState()
  const variant  = ORB_VARIANTS[emotion.mood_state] ?? ORB_VARIANTS.CALM

  // Track audience mode with 5s linger after it ends
  const [showHUD, setShowHUD] = useState(false)
  const hudTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (emotion.mood_state === 'AUDIENCE_MODE') {
      if (hudTimerRef.current) clearTimeout(hudTimerRef.current)
      setShowHUD(true)
    } else if (showHUD) {
      hudTimerRef.current = setTimeout(() => setShowHUD(false), 5000)
    }
    return () => { if (hudTimerRef.current) clearTimeout(hudTimerRef.current) }
  }, [emotion.mood_state, showHUD])

  const outerScale = isPhase5 ? 1.15 : isActive ? 1.0 : 0.0

  // Derive glow values from current emotion theme
  const rgb          = hexToRgb(variant.primaryColor)
  const glowMed      = `rgba(${rgb},${0.4 * variant.glowIntensity})`
  const glowStrong   = `rgba(${rgb},${0.6 * variant.glowIntensity})`
  const glowFaint    = `rgba(${rgb},${0.15 * variant.glowIntensity})`
  const borderColor  = `rgba(${rgb},0.45)`
  const breathDur    = `${variant.duration}s`

  return (
    <motion.div
      className="relative flex items-center justify-center"
      style={{ width: 320, height: 320 }}
      animate={{ scale: outerScale, opacity: isActive ? 1 : 0 }}
      transition={{ duration: 0.8, ease: 'easeOut' }}
    >
      {/* Ambient outer glow — emotion color */}
      <motion.div
        className="absolute rounded-full"
        style={{ width: 380, height: 380 }}
        animate={{
          background: [
            `radial-gradient(circle, ${glowFaint} 0%, transparent 70%)`,
            `radial-gradient(circle, ${glowMed} 0%, transparent 70%)`,
            `radial-gradient(circle, ${glowFaint} 0%, transparent 70%)`,
          ],
        }}
        transition={{ duration: variant.duration, repeat: Infinity, ease: 'easeInOut' }}
      />

      {/* Volumetric outer ring — emotion color */}
      <motion.div
        className="absolute rounded-full"
        style={{ width: 320, height: 320, border: `1px solid rgba(${rgb},0.15)` }}
        animate={{
          boxShadow: [
            `0 0 40px ${glowMed}, 0 0 80px ${glowFaint}`,
            `0 0 60px ${glowStrong}, 0 0 120px ${glowMed}`,
            `0 0 40px ${glowMed}, 0 0 80px ${glowFaint}`,
          ],
        }}
        transition={{ duration: variant.duration, repeat: Infinity, ease: 'easeInOut' }}
      />

      {/* Segmented rotating rings — emotion primary + accent */}
      <SegmentRing radius={148} segments={12} speed={0.003}  color={`rgba(${rgb},0.35)`} width={1.5} gap={8}  />
      <SegmentRing radius={128} segments={8}  speed={-0.005} color="rgba(0,255,255,0.25)" width={1}   gap={12} />
      <SegmentRing radius={108} segments={16} speed={0.008}  color={`rgba(${rgb},0.20)`} width={1}   gap={4}  />

      {/* Scan sweep */}
      <ScanSweep radius={120} />

      {/* Main orb body — emotion reactive glow */}
      <motion.div
        className="relative rounded-full flex items-center justify-center overflow-hidden"
        style={{
          width: 280, height: 280,
          border: `1.5px solid ${borderColor}`,
        }}
        animate={{
          background: [
            `radial-gradient(circle at 35% 30%, rgba(${rgb},0.25), rgba(${rgb},0.06) 55%, transparent)`,
            `radial-gradient(circle at 35% 30%, rgba(${rgb},0.35), rgba(${rgb},0.10) 55%, transparent)`,
            `radial-gradient(circle at 35% 30%, rgba(${rgb},0.25), rgba(${rgb},0.06) 55%, transparent)`,
          ],
          boxShadow: [
            `0 0 40px ${glowMed}, 0 0 80px ${glowFaint}, inset 0 0 20px rgba(${rgb},0.08)`,
            `0 0 60px ${glowStrong}, 0 0 120px ${glowMed}, inset 0 0 40px rgba(${rgb},0.12)`,
            `0 0 40px ${glowMed}, 0 0 80px ${glowFaint}, inset 0 0 20px rgba(${rgb},0.08)`,
          ],
          scale: variant.scale,
        }}
        transition={{ duration: variant.duration, repeat: Infinity, ease: 'easeInOut' }}
      >
        {/* Inner canvas neural energy — emotion colors */}
        <EnergyCanvas phase={phase} primaryColor={variant.primaryColor} accentColor="#00ffff" />

        {/* Specular highlight */}
        <div
          className="absolute rounded-full"
          style={{
            width: 80, height: 80,
            top: 28, left: 32,
            background: 'radial-gradient(circle at 30% 30%, rgba(255,255,255,0.12), transparent 70%)',
            pointerEvents: 'none',
          }}
        />

        {/* Waveform bars (center) */}
        <div className="relative z-10 flex flex-col items-center gap-2">
          <WaveBars active={isActive} />
          <p
            className="font-mono text-[9px] tracking-[0.3em] font-bold"
            style={{
              color:      variant.primaryColor,
              textShadow: `0 0 8px rgba(${rgb},0.8)`,
              letterSpacing: '0.3em',
              transition: 'color 1s ease',
            }}
          >
            XYRON
          </p>
        </div>
      </motion.div>

      {/* Outer ripple rings (phase1 only) — emotion color */}
      {phase === 'phase1' && [0, 1, 2].map(i => (
        <motion.div
          key={i}
          className="absolute rounded-full"
          style={{ width: 280, height: 280, border: `1px solid rgba(${rgb},0.4)` }}
          animate={{ scale: [1, 2.4], opacity: [0.5, 0] }}
          transition={{ duration: 2.5, repeat: Infinity, delay: i * 0.8, ease: 'easeOut' }}
        />
      ))}

      {/* Phase 5 cyan halo */}
      {phase === 'phase5' && (
        <motion.div
          className="absolute rounded-full"
          style={{
            width: 340, height: 340,
            border: '1px solid rgba(0,255,255,0.3)',
            boxShadow: '0 0 30px rgba(0,255,255,0.15)',
          }}
          animate={{ opacity: [0.3, 0.8, 0.3] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}

      {/* Audience mode HUD — shows during self-introduction, lingers 5s after */}
      <AnimatePresence>
        {showHUD && <AudienceHUD moduleCount={6} />}
      </AnimatePresence>
    </motion.div>
  )
}
