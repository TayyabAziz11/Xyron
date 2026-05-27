

import { useEffect, useRef } from 'react'
import type { TakeoverPhase } from '../../../hooks/useTakeoverMode'

interface Props { phase: TakeoverPhase }

const CHARS = 'ｦｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEF><[]{}⠿⠯⣿'
const FONT  = 13

// Opacity per phase — rain is atmospheric, not dominant
const PHASE_OPACITY: Partial<Record<TakeoverPhase, number>> = {
  command:    0.14,
  breach:     0.22,
  root:       0.28,
  sync:       0.16,
  activation: 0.10,
  hud:        0.07,
  active:     0.06,
  standdown:  0.20,
}

export default function MatrixRain({ phase }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef    = useRef(0)
  const frameRef  = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!

    const resize = () => {
      canvas.width  = window.innerWidth
      canvas.height = window.innerHeight
    }
    resize()
    window.addEventListener('resize', resize, { passive: true })

    let cols  = Math.floor(canvas.width / FONT)
    let drops = Array.from({ length: cols }, () => Math.random() * -80)

    const draw = () => {
      frameRef.current++
      // Throttle to ~24fps for performance
      if (frameRef.current % 2 !== 0) { rafRef.current = requestAnimationFrame(draw); return }

      // Semi-transparent black — creates the fade trail
      ctx.fillStyle = 'rgba(0,0,0,0.055)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      ctx.font = `${FONT}px "Courier New", monospace`

      // Refresh column count if canvas resized
      const newCols = Math.floor(canvas.width / FONT)
      if (newCols !== cols) {
        cols  = newCols
        drops = Array.from({ length: cols }, () => Math.random() * -80)
      }

      for (let i = 0; i < drops.length; i++) {
        const char = CHARS[Math.floor(Math.random() * CHARS.length)]
        const x    = i * FONT
        const y    = drops[i] * FONT

        // Lead character — bright
        ctx.fillStyle = 'rgba(255, 20, 20, 0.95)'
        ctx.fillText(char, x, y)

        // Previous character — dim red
        if (drops[i] > 1) {
          ctx.fillStyle = 'rgba(160, 0, 0, 0.6)'
          ctx.fillText(CHARS[Math.floor(Math.random() * CHARS.length)], x, y - FONT)
        }

        if (y > canvas.height && Math.random() > 0.975) drops[i] = 0
        drops[i] += 0.6
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [])

  const opacity = PHASE_OPACITY[phase] ?? 0

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 z-[55] pointer-events-none"
      style={{
        opacity,
        transition: 'opacity 0.8s ease',
        willChange: 'opacity',
      }}
    />
  )
}
