

import { useEffect, useRef } from 'react'

interface Particle { x: number; y: number; vx: number; vy: number; life: number; maxLife: number }

const PARTICLE_COUNT = 25   // was 60 — O(n²) connections
const CONNECT_DIST   = 50   // was 70
const FPS_CAP        = 20

export function ParticleCanvas({ className = '' }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let particles: Particle[] = []
    let raf: number
    let last = 0
    const interval = 1000 / FPS_CAP

    const resize = () => {
      canvas.width  = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
    }

    const spawn = (): Particle => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.8,
      vy: (Math.random() - 0.5) * 0.8,
      life: 0,
      maxLife: 150 + Math.random() * 150,
    })

    const init = () => { particles = Array.from({ length: PARTICLE_COUNT }, spawn) }

    const draw = (ts: number) => {
      raf = requestAnimationFrame(draw)
      if (ts - last < interval) return
      last = ts

      ctx.clearRect(0, 0, canvas.width, canvas.height)

      // Update + draw particles — no shadowBlur
      ctx.beginPath()
      particles.forEach((p, i) => {
        p.x += p.vx; p.y += p.vy; p.life++
        if (p.life >= p.maxLife) particles[i] = spawn()
        const alpha = Math.sin((p.life / p.maxLife) * Math.PI) * 0.75
        ctx.fillStyle = `rgba(0,255,255,${alpha.toFixed(2)})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, 1.5, 0, Math.PI * 2)
        ctx.fill()
      })

      // Connections
      ctx.lineWidth = 0.6
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x
          const dy = particles[i].y - particles[j].y
          const d2 = dx * dx + dy * dy
          if (d2 < CONNECT_DIST * CONNECT_DIST) {
            const alpha = (1 - Math.sqrt(d2) / CONNECT_DIST) * 0.22
            ctx.beginPath()
            ctx.strokeStyle = `rgba(0,229,255,${alpha.toFixed(2)})`
            ctx.moveTo(particles[i].x, particles[i].y)
            ctx.lineTo(particles[j].x, particles[j].y)
            ctx.stroke()
          }
        }
      }
    }

    const ro = new ResizeObserver(() => { resize(); init() })
    ro.observe(canvas)
    resize(); init()
    raf = requestAnimationFrame(draw)

    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={canvasRef} className={`w-full h-full ${className}`} />
}
