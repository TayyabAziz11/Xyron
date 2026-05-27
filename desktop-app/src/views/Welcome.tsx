import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Zap, Minus, X } from 'lucide-react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { useAuthStore } from '@desktop/stores/authStore'
import { useSubscriptionStore } from '@desktop/stores/subscriptionStore'

const BOOT_LINES = [
  { delay: 0,    text: 'XYRON OS v2.0.0 — initializing kernel...',                 color: '#ff2020' },
  { delay: 300,  text: '>> Loading neural interface modules...',                    color: '#94a3b8' },
  { delay: 600,  text: '>> Mounting cognitive subsystems...',                       color: '#94a3b8' },
  { delay: 900,  text: '>> Voice pipeline: whisper-large-v3 [READY]',              color: '#00ff88' },
  { delay: 1100, text: '>> Wake word classifier: openwakeword [READY]',            color: '#00ff88' },
  { delay: 1300, text: '>> LLM router: gpt-4o + qwen2.5 offline [READY]',         color: '#00ff88' },
  { delay: 1500, text: '>> Emotion engine: 11-state FSM [READY]',                  color: '#00ff88' },
  { delay: 1700, text: '>> Takeover mode: ARMED',                                  color: '#ff2020' },
  { delay: 1900, text: '>> Memory subsystem: episodic + long-term [READY]',        color: '#00ff88' },
  { delay: 2100, text: '>> Desktop bridge: Tauri v2 / WebKit2GTK [READY]',        color: '#00ff88' },
  { delay: 2400, text: '>> All systems nominal. Awaiting operator authentication...', color: '#00ffff' },
]

function ParticleBg() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    let raf: number
    let last = 0
    const INTERVAL = 1000 / 20
    const resize = () => { canvas.width = window.innerWidth; canvas.height = window.innerHeight }
    resize()
    window.addEventListener('resize', resize)
    const particles = Array.from({ length: 35 }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: Math.random() * 1.5 + 0.5,
      alpha: Math.random() * 0.35 + 0.05,
    }))
    const draw = (ts: number) => {
      raf = requestAnimationFrame(draw)
      if (ts - last < INTERVAL) return
      last = ts
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      for (const p of particles) {
        p.x += p.vx; p.y += p.vy
        if (p.x < 0) p.x = canvas.width
        if (p.x > canvas.width) p.x = 0
        if (p.y < 0) p.y = canvas.height
        if (p.y > canvas.height) p.y = 0
        ctx.fillStyle = `rgba(255,32,32,${p.alpha})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2)
        ctx.fill()
      }
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
  }, [])
  return <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />
}

export default function Welcome() {
  const navigate = useNavigate()
  const { isSignedIn } = useAuthStore()
  const { tierSelectedAt } = useSubscriptionStore()
  const [bootLines, setBootLines] = useState<typeof BOOT_LINES[number][]>([])
  const [done, setDone] = useState(false)
  const win = getCurrentWindow()

  useEffect(() => {
    let cancelled = false
    BOOT_LINES.forEach(line => {
      setTimeout(() => { if (!cancelled) setBootLines(prev => [...prev, line]) }, line.delay)
    })
    const total = BOOT_LINES[BOOT_LINES.length - 1].delay + 900
    setTimeout(() => { if (!cancelled) setDone(true) }, total)
    return () => { cancelled = true }
  }, [])

  // Once boot is done, route to the right place based on auth state
  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => {
      if (isSignedIn) {
        if (!tierSelectedAt) {
          navigate('/onboarding/plan', { replace: true })
        } else {
          // Check if user has set a preferred_name — if not, route to name onboarding
          const stored = localStorage.getItem('xyron-auth')
          let hasName = false
          try {
            const parsed = JSON.parse(stored ?? '{}')
            hasName = !!parsed?.state?.user?.preferredName
          } catch { /* ok */ }
          navigate(hasName ? '/dashboard' : '/onboarding/name', { replace: true })
        }
      } else {
        navigate('/auth', { replace: true })
      }
    }, 600)
    return () => clearTimeout(t)
  }, [done, navigate, isSignedIn, tierSelectedAt])

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-[#08080f] flex flex-col items-center justify-center">
      {/* Title bar */}
      <div data-tauri-drag-region
        className="absolute top-0 left-0 right-0 h-8 z-50 flex items-center justify-between px-3 select-none"
        style={{ background: 'rgba(8,8,15,0.9)' }}>
        <span data-tauri-drag-region className="font-mono text-[9px] tracking-[0.3em] text-[#ff2020]/40 pointer-events-none">
          XYRON OS v2.0
        </span>
        <div className="flex items-center gap-0.5">
          <button onClick={() => win.minimize()}
            className="flex h-5 w-5 items-center justify-center rounded text-[#475569] hover:bg-white/10 hover:text-white transition-colors">
            <Minus className="h-2.5 w-2.5" />
          </button>
          <button onClick={() => win.close()}
            className="flex h-5 w-5 items-center justify-center rounded text-[#475569] hover:bg-[rgba(255,32,32,0.2)] hover:text-[#ff2020] transition-colors">
            <X className="h-2.5 w-2.5" />
          </button>
        </div>
      </div>

      <ParticleBg />

      {/* Grid overlay */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: 'linear-gradient(rgba(255,32,32,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(255,32,32,0.03) 1px,transparent 1px)', backgroundSize: '40px 40px' }} />

      {/* Scan line */}
      <div className="absolute top-0 left-0 w-full h-0.5 bg-gradient-to-r from-transparent via-[#ff2020]/30 to-transparent animate-scan pointer-events-none" />

      {/* Boot content */}
      <motion.div key="boot" initial={{ opacity: 0 }} animate={{ opacity: done ? 0 : 1 }}
        transition={{ duration: 0.5 }} className="relative z-10 w-full max-w-2xl px-6">
        <motion.div initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.6, ease: 'easeOut' }} className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl border border-[rgba(255,32,32,0.4)] bg-[rgba(255,32,32,0.08)] mb-4 shadow-[0_0_40px_rgba(255,32,32,0.3)]">
            <Zap className="w-10 h-10 text-[#ff2020]" />
          </div>
          <h1 className="text-5xl font-black tracking-[0.4em] text-[#ff2020] drop-shadow-[0_0_20px_rgba(255,32,32,0.8)]">XYRON</h1>
          <p className="font-mono text-xs tracking-[0.3em] text-[#475569] mt-1">AUTONOMOUS AI OPERATING SYSTEM</p>
        </motion.div>
        <div className="font-mono text-xs space-y-0.5 h-48 overflow-hidden">
          {bootLines.map((line, i) => (
            <motion.div key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2 }} style={{ color: line.color }}>
              {line.text}
            </motion.div>
          ))}
          {bootLines.length < BOOT_LINES.length && (
            <span className="inline-block w-2 h-3 bg-[#ff2020] animate-blink" />
          )}
        </div>
      </motion.div>

      {/* Corner decorations */}
      <div className="absolute top-10 left-4 font-mono text-[9px] text-[#333] tracking-widest pointer-events-none">
        SYS:BOOT / {new Date().toLocaleDateString()}
      </div>
      <div className="absolute bottom-4 left-4 font-mono text-[9px] text-[#333] tracking-widest pointer-events-none">
        TAURI v2 / WEBKIT / WSL2
      </div>
      <div className="absolute bottom-4 right-4 font-mono text-[9px] text-[#333] tracking-widest pointer-events-none">
        BUILD: OMEGA-{new Date().toISOString().slice(0,10).replace(/-/g,'')}
      </div>
    </div>
  )
}
