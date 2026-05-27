import { useEffect, useRef } from 'react'

interface Node { x: number; y: number; vx: number; vy: number }

const NODE_COUNT = 22
const MAX_DIST   = 70
const FPS_CAP    = 20

// Red-tinted neural mesh for dashboard panels
export function NeuralMesh({ className = '' }: { className?: string }) {
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
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
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

      ctx.lineWidth = 0.6
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x
          const dy = nodes[i].y - nodes[j].y
          const d2 = dx * dx + dy * dy
          if (d2 < MAX_DIST * MAX_DIST) {
            const alpha = (1 - Math.sqrt(d2) / MAX_DIST) * 0.35
            ctx.beginPath()
            ctx.strokeStyle = `rgba(255,31,45,${alpha.toFixed(2)})`
            ctx.moveTo(nodes[i].x, nodes[i].y)
            ctx.lineTo(nodes[j].x, nodes[j].y)
            ctx.stroke()
          }
        }
      }

      ctx.fillStyle = 'rgba(255,60,70,0.8)'
      ctx.beginPath()
      for (const n of nodes) {
        ctx.moveTo(n.x + 2, n.y)
        ctx.arc(n.x, n.y, 2, 0, Math.PI * 2)
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
