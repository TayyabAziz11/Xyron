

import { useEffect, useRef } from 'react'
import { useParallaxCore } from '../../../hooks/useParallaxCore'

interface Node { x: number; y: number; vx: number; vy: number; r: number }

function buildNodes(w: number, h: number, n = 38): Node[] {
  return Array.from({ length: n }, () => ({
    x: Math.random() * w,
    y: Math.random() * h,
    vx: (Math.random() - 0.5) * 0.22,
    vy: (Math.random() - 0.5) * 0.22,
    r: 2.5 + Math.random() * 3.5,
  }))
}

export default function NeuralBrain() {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const parallaxRef  = useParallaxCore(6)
  const rafRef       = useRef(0)
  const nodesRef     = useRef<Node[]>([])
  const pulseRef     = useRef({ r: 0, alpha: 0, active: false })
  const frameRef     = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!

    const resize = () => {
      canvas.width  = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
      nodesRef.current = buildNodes(canvas.width, canvas.height)
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    // Periodic pulse from center
    const pulseId = setInterval(() => {
      pulseRef.current = { r: 0, alpha: 0.6, active: true }
    }, 3500)

    const draw = () => {
      frameRef.current++
      rafRef.current = requestAnimationFrame(draw)

      const { width: W, height: H } = canvas
      const nodes = nodesRef.current
      const cx = W / 2, cy = H / 2
      const EDGE_DIST = Math.min(W, H) * 0.32

      ctx.clearRect(0, 0, W, H)

      // Update node positions
      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy
        if (n.x < 0 || n.x > W) n.vx *= -1
        if (n.y < 0 || n.y > H) n.vy *= -1
      }

      // Draw edges
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx   = nodes[i].x - nodes[j].x
          const dy   = nodes[i].y - nodes[j].y
          const dist = Math.sqrt(dx * dx + dy * dy)
          if (dist > EDGE_DIST) continue

          const alpha = (1 - dist / EDGE_DIST) * 0.5
          ctx.beginPath()
          ctx.moveTo(nodes[i].x, nodes[i].y)
          ctx.lineTo(nodes[j].x, nodes[j].y)
          ctx.strokeStyle = `rgba(180,0,0,${alpha})`
          ctx.lineWidth   = 0.6
          ctx.stroke()
        }
      }

      // Draw nodes
      for (const n of nodes) {
        const distFromCenter = Math.sqrt((n.x - cx) ** 2 + (n.y - cy) ** 2)
        const proximity      = Math.max(0, 1 - distFromCenter / (Math.min(W, H) / 2))
        const alpha          = 0.4 + proximity * 0.55

        // Glow
        const grd = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 3)
        grd.addColorStop(0, `rgba(220,30,30,${alpha * 0.6})`)
        grd.addColorStop(1, 'rgba(180,0,0,0)')
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.r * 3, 0, Math.PI * 2)
        ctx.fillStyle = grd
        ctx.fill()

        // Core dot
        ctx.beginPath()
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(255,40,40,${alpha})`
        ctx.fill()
      }

      // Center node (always visible, larger)
      const t   = frameRef.current * 0.04
      const cr  = 8 + Math.sin(t) * 3
      const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, cr * 4)
      grd.addColorStop(0, 'rgba(255,60,60,0.95)')
      grd.addColorStop(0.4, 'rgba(200,0,0,0.5)')
      grd.addColorStop(1, 'rgba(180,0,0,0)')
      ctx.beginPath()
      ctx.arc(cx, cy, cr * 4, 0, Math.PI * 2)
      ctx.fillStyle = grd
      ctx.fill()
      ctx.beginPath()
      ctx.arc(cx, cy, cr, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(255,80,80,0.95)'
      ctx.fill()

      // Pulse wave
      const p = pulseRef.current
      if (p.active) {
        ctx.beginPath()
        ctx.arc(cx, cy, p.r, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(220,0,0,${p.alpha})`
        ctx.lineWidth   = 1.5
        ctx.stroke()
        p.r     += 4
        p.alpha *= 0.93
        if (p.alpha < 0.02) p.active = false
      }
    }

    rafRef.current = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafRef.current)
      clearInterval(pulseId)
      ro.disconnect()
    }
  }, [])

  return (
    <div ref={parallaxRef} className="w-full h-full" style={{ willChange: 'transform' }}>
      <canvas ref={canvasRef} className="w-full h-full" />
    </div>
  )
}
