

import { useEffect, useRef } from 'react'

interface Node { x: number; y: number; vx: number; vy: number }

const NODE_COUNT = 28   // was 55 — O(n²) edge loop, halving = 4× fewer checks
const MAX_DIST   = 80   // was 110 — fewer edges per frame
const FPS_CAP    = 20   // software rendering (WSL2 WEBKIT_DISABLE_DMABUF_RENDERER) can't sustain 60fps

export function BrainCanvas({ className = '' }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let nodes: Node[] = []
    let raf: number
    let last = 0
    const interval = 1000 / FPS_CAP

    const resize = () => {
      canvas.width  = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
    }

    const init = () => {
      nodes = Array.from({ length: NODE_COUNT }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
      }))
    }

    const draw = (ts: number) => {
      raf = requestAnimationFrame(draw)
      if (ts - last < interval) return
      last = ts

      ctx.clearRect(0, 0, canvas.width, canvas.height)

      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy
        if (n.x < 0 || n.x > canvas.width)  n.vx *= -1
        if (n.y < 0 || n.y > canvas.height) n.vy *= -1
      }

      // Batch all edges into one path per alpha level to minimise ctx.stroke() calls
      ctx.lineWidth = 0.8
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x
          const dy = nodes[i].y - nodes[j].y
          const d2 = dx * dx + dy * dy
          if (d2 < MAX_DIST * MAX_DIST) {
            const alpha = (1 - Math.sqrt(d2) / MAX_DIST) * 0.45
            ctx.beginPath()
            ctx.strokeStyle = `rgba(255,32,32,${alpha.toFixed(2)})`
            ctx.moveTo(nodes[i].x, nodes[i].y)
            ctx.lineTo(nodes[j].x, nodes[j].y)
            ctx.stroke()
          }
        }
      }

      // Draw nodes — no shadowBlur (extremely expensive in software rendering)
      ctx.fillStyle = 'rgba(255,80,80,0.85)'
      ctx.beginPath()
      for (const n of nodes) {
        ctx.moveTo(n.x + 2.5, n.y)
        ctx.arc(n.x, n.y, 2.5, 0, Math.PI * 2)
      }
      ctx.fill()
    }

    const ro = new ResizeObserver(() => { resize(); init() })
    ro.observe(canvas)
    resize(); init()
    raf = requestAnimationFrame(draw)

    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [])

  return <canvas ref={canvasRef} className={`w-full h-full ${className}`} />
}
