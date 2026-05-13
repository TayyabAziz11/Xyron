'use client'

import { useEffect, useRef } from 'react'
import type { ImHomePhase } from '@/hooks/useImHomeProtocol'

interface Particle {
  x: number; y: number
  vx: number; vy: number
  life: number; maxLife: number
  size: number; type: 'node' | 'pulse'
  pulseR?: number
}

interface Props {
  phase: ImHomePhase
}

export function NeuralCanvas({ phase }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef    = useRef<number>(0)
  const phaseRef  = useRef(phase)

  useEffect(() => { phaseRef.current = phase }, [phase])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const resize = () => {
      canvas.width  = window.innerWidth
      canvas.height = window.innerHeight
    }
    resize()
    window.addEventListener('resize', resize)

    // ── Nodes (persistent floating points) ────────────────────────────────────
    const NODES = 60
    type Node = { x: number; y: number; vx: number; vy: number; glow: number; phase: number }
    const nodes: Node[] = Array.from({ length: NODES }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.4,
      vy: (Math.random() - 0.5) * 0.4,
      glow: Math.random(),
      phase: Math.random() * Math.PI * 2,
    }))

    // ── Pulse rings ────────────────────────────────────────────────────────────
    const pulses: { x: number; y: number; r: number; maxR: number; alpha: number }[] = []
    let pulseTimer = 0

    let t = 0
    const draw = () => {
      t++
      const W = canvas.width
      const H = canvas.height
      const cx = W / 2
      const cy = H / 2
      const p  = phaseRef.current

      ctx.clearRect(0, 0, W, H)

      const intensity = p === 'idle' ? 0 : p === 'phase1' ? 0.4 : p === 'phase5' ? 1.0 : 0.65

      // Background vignette
      const vign = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * 0.7)
      vign.addColorStop(0, 'transparent')
      vign.addColorStop(1, `rgba(0,0,0,${0.5 * intensity})`)
      ctx.fillStyle = vign
      ctx.fillRect(0, 0, W, H)

      if (intensity < 0.05) { rafRef.current = requestAnimationFrame(draw); return }

      // ── Move + draw nodes ────────────────────────────────────────────────────
      nodes.forEach(n => {
        n.x += n.vx
        n.y += n.vy
        if (n.x < 0 || n.x > W) n.vx *= -1
        if (n.y < 0 || n.y > H) n.vy *= -1
        n.glow = 0.5 + 0.5 * Math.sin(t * 0.02 + n.phase)

        const alpha = n.glow * intensity * 0.6
        ctx.beginPath()
        ctx.arc(n.x, n.y, 1.5, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(255,32,32,${alpha})`
        ctx.fill()
      })

      // ── Draw connections ──────────────────────────────────────────────────────
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x
          const dy = nodes[i].y - nodes[j].y
          const d  = Math.sqrt(dx * dx + dy * dy)
          if (d < 140) {
            const alpha = (1 - d / 140) * intensity * 0.18
            ctx.beginPath()
            ctx.moveTo(nodes[i].x, nodes[i].y)
            ctx.lineTo(nodes[j].x, nodes[j].y)
            ctx.strokeStyle = `rgba(255,32,32,${alpha})`
            ctx.lineWidth = 0.5
            ctx.stroke()
          }
        }
      }

      // ── Cyan accent nodes (small subset) ─────────────────────────────────────
      nodes.slice(0, 8).forEach((n, i) => {
        const a = (0.3 + 0.3 * Math.sin(t * 0.015 + i)) * intensity
        ctx.beginPath()
        ctx.arc(n.x, n.y, 2, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(0,255,255,${a})`
        ctx.fill()
      })

      // ── Pulse rings from center ────────────────────────────────────────────
      pulseTimer++
      const pulseInterval = p === 'phase5' ? 60 : 110
      if (pulseTimer >= pulseInterval) {
        pulseTimer = 0
        pulses.push({ x: cx, y: cy, r: 0, maxR: Math.max(W, H) * 0.55, alpha: 0.5 * intensity })
      }

      for (let i = pulses.length - 1; i >= 0; i--) {
        const pulse = pulses[i]
        pulse.r     += 3.2
        pulse.alpha *= 0.97
        if (pulse.alpha < 0.005 || pulse.r > pulse.maxR) { pulses.splice(i, 1); continue }
        ctx.beginPath()
        ctx.arc(pulse.x, pulse.y, pulse.r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(255,32,32,${pulse.alpha})`
        ctx.lineWidth = 1.2
        ctx.stroke()
      }

      // ── Phase5 second pulse (cyan) ────────────────────────────────────────
      if (p === 'phase5' && t % 80 === 0) {
        pulses.push({ x: cx, y: cy, r: 20, maxR: Math.max(W, H) * 0.4, alpha: 0.4 })
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    rafRef.current = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
      style={{ zIndex: 51, mixBlendMode: 'screen' }}
    />
  )
}
