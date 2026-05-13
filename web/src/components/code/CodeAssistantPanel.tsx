'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2, FileCode2, FolderOpen, GitBranch, Terminal,
  Zap, Bug, Wrench, Layers, Search, RefreshCw, ChevronRight,
  Activity, Cpu, CheckCircle2,
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DevStatus {
  code_mode: boolean
  active_project: string | null
  active_file: string | null
  active_ui_mode: string
}

interface CodeAssistantPanelProps {
  onInjectPrompt: (text: string) => void
}

// ── Language detection ────────────────────────────────────────────────────────

function detectLang(filename: string | null): string {
  if (!filename) return 'unknown'
  const ext = filename.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    ts: 'TypeScript', tsx: 'TypeScript/React', js: 'JavaScript', jsx: 'JavaScript/React',
    py: 'Python', rs: 'Rust', go: 'Go', java: 'Java', cpp: 'C++', c: 'C',
    cs: 'C#', rb: 'Ruby', php: 'PHP', swift: 'Swift', kt: 'Kotlin',
    md: 'Markdown', json: 'JSON', yaml: 'YAML', yml: 'YAML', toml: 'TOML',
    sh: 'Shell', bash: 'Bash', sql: 'SQL', html: 'HTML', css: 'CSS', scss: 'SCSS',
  }
  return map[ext] ?? (ext.toUpperCase() || 'unknown')
}

// ── Passive intelligence suggestions ─────────────────────────────────────────

const PASSIVE_HINTS = [
  'Detected repeated error pattern.',
  'Potential optimization opportunity found.',
  'Large render cycle detected.',
  'Unused imports may increase bundle size.',
  'Consider memoizing this computation.',
  'Deep nesting detected — refactor candidate.',
  'Missing error boundary in component tree.',
  'N+1 query pattern suspected.',
  'Unhandled promise rejection path found.',
  'Type assertion could be narrowed safely.',
]

function usePassiveHint(active: boolean) {
  const [hint, setHint] = useState<string | null>(null)
  useEffect(() => {
    if (!active) { setHint(null); return }
    const show = () => {
      setHint(PASSIVE_HINTS[Math.floor(Math.random() * PASSIVE_HINTS.length)])
      setTimeout(() => setHint(null), 6000)
    }
    // Fire once after 8s, then every 45–90s
    const first = setTimeout(show, 8000)
    const interval = setInterval(show, 45000 + Math.random() * 45000)
    return () => { clearTimeout(first); clearInterval(interval) }
  }, [active])
  return hint
}

// ── Code rain background ──────────────────────────────────────────────────────

function CodeRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const resize = () => {
      canvas.width = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
    }
    resize()
    window.addEventListener('resize', resize)

    const chars = '01{}[]()<>=!;:,./*&|^~'
    const fontSize = 11
    const cols = Math.floor(canvas.width / fontSize)
    const drops: number[] = Array.from({ length: cols }, () => Math.random() * -50)

    let raf: number
    const draw = () => {
      ctx.fillStyle = 'rgba(0,0,0,0.05)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.font = `${fontSize}px monospace`

      for (let i = 0; i < drops.length; i++) {
        const ch = chars[Math.floor(Math.random() * chars.length)]
        const alpha = 0.06 + Math.random() * 0.08
        ctx.fillStyle = `rgba(255,32,32,${alpha})`
        ctx.fillText(ch, i * fontSize, drops[i] * fontSize)
        if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) drops[i] = 0
        drops[i] += 0.4
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={{ opacity: 0.4 }}
    />
  )
}

// ── Streaming response panel ──────────────────────────────────────────────────

interface StreamPanelProps {
  response: string
  streaming: boolean
}

function StreamPanel({ response, streaming }: StreamPanelProps) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [response])

  if (!response && !streaming) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <Terminal className="h-6 w-6 text-[#ff2020]/20 mb-2" />
        <p className="font-mono text-[9px] text-text-muted">Ask anything about your code</p>
      </div>
    )
  }

  // Simple code block rendering — split on ```
  const parts = response.split(/(```[\s\S]*?```)/g)

  return (
    <div className="space-y-2 text-[11px] font-mono leading-relaxed text-text-secondary">
      {parts.map((part, i) => {
        if (part.startsWith('```')) {
          const lines = part.split('\n')
          const lang = lines[0].replace('```', '').trim()
          const code = lines.slice(1, -1).join('\n')
          return (
            <pre key={i}
              className="overflow-x-auto rounded-lg border border-[rgba(255,32,32,0.2)] bg-[rgba(0,0,0,0.7)] p-3 text-[10px] text-[#00ff88]"
              style={{ boxShadow: '0 0 12px rgba(0,255,136,0.06)' }}>
              {lang && <span className="text-[#ff2020]/60 text-[8px] block mb-1">{lang}</span>}
              <code>{code}</code>
            </pre>
          )
        }
        return <span key={i} style={{ whiteSpace: 'pre-wrap' }}>{part}</span>
      })}
      {streaming && (
        <span className="inline-block w-1.5 h-3 bg-[#ff2020] animate-pulse ml-0.5" />
      )}
      <div ref={endRef} />
    </div>
  )
}

// ── Quick action buttons ──────────────────────────────────────────────────────

const QUICK_ACTIONS = [
  { label: 'Explain',    icon: Search,   prompt: 'Explain this code.' },
  { label: 'Write',      icon: Code2,    prompt: 'Write code for: ' },
  { label: 'Debug',      icon: Bug,      prompt: 'Debug this: ' },
  { label: 'Refactor',   icon: RefreshCw,prompt: 'Refactor this code.' },
  { label: 'Architect',  icon: Layers,   prompt: 'Design the architecture for: ' },
  { label: 'Optimize',   icon: Zap,      prompt: 'Optimize this for performance.' },
]

// ── Main component ────────────────────────────────────────────────────────────

export function CodeAssistantPanel({ onInjectPrompt }: CodeAssistantPanelProps) {
  const [status, setStatus] = useState<DevStatus | null>(null)
  const [response, setResponse] = useState('')
  const [streaming, setStreaming] = useState(false)
  const passiveHint = usePassiveHint(status?.code_mode ?? false)
  const abortRef = useRef<AbortController | null>(null)

  // Poll /api/v1/dev/status every 2s
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const r = await fetch('http://localhost:8000/api/v1/dev/status')
        if (!r.ok || cancelled) return
        setStatus(await r.json())
      } catch { /* silent */ }
    }
    poll()
    const t = setInterval(poll, 2000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const handleQuickAction = useCallback((prompt: string) => {
    onInjectPrompt(prompt)
  }, [onInjectPrompt])

  const handleStreamQuery = useCallback(async (text: string) => {
    if (abortRef.current) abortRef.current.abort()
    abortRef.current = new AbortController()
    setResponse('')
    setStreaming(true)

    try {
      const resp = await fetch('http://localhost:8000/api/v1/dev/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text,
          active_project: status?.active_project,
          active_file: status?.active_file,
        }),
        signal: abortRef.current.signal,
      })
      const reader = resp.body?.getReader()
      if (!reader) return
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const msg = JSON.parse(line.slice(6))
            if (msg.type === 'token') setResponse(p => p + msg.text)
          } catch { /* partial */ }
        }
      }
    } catch (e: unknown) {
      if ((e as Error).name !== 'AbortError') {
        setResponse(p => p + '\n[stream error]')
      }
    } finally {
      setStreaming(false)
    }
  }, [status])

  const lang = detectLang(status?.active_file ?? null)
  const isActive = status?.code_mode ?? false

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: 'easeOut' }}
      className="relative rounded-xl border overflow-hidden"
      style={{
        borderColor: isActive ? 'rgba(255,32,32,0.35)' : 'rgba(255,32,32,0.12)',
        background: 'rgba(0,0,0,0.7)',
        boxShadow: isActive ? '0 0 30px rgba(255,32,32,0.08), inset 0 0 40px rgba(255,32,32,0.03)' : 'none',
      }}
    >
      {/* Code rain background — only when active */}
      <AnimatePresence>
        {isActive && (
          <motion.div
            key="rain"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 pointer-events-none"
          >
            <CodeRain />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="relative z-10">

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(255,32,32,0.12)]">
          <div className="flex items-center gap-2">
            <Terminal className="h-3.5 w-3.5 text-[#ff2020]" />
            <span className="font-mono text-[9px] tracking-[0.18em] text-text-muted">CODE ASSISTANT</span>
          </div>
          <div className="flex items-center gap-2">
            <AnimatePresence>
              {isActive && (
                <motion.div
                  key="badge"
                  initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                  className="flex items-center gap-1.5 rounded-full border border-[rgba(255,32,32,0.4)] bg-[rgba(255,32,32,0.12)] px-2.5 py-0.5"
                  style={{ boxShadow: '0 0 8px rgba(255,32,32,0.2)' }}
                >
                  <div className="h-1.5 w-1.5 rounded-full bg-[#ff2020] animate-pulse" />
                  <span className="font-mono text-[8px] font-bold text-[#ff2020] tracking-widest">DEV MODE ACTIVE</span>
                </motion.div>
              )}
            </AnimatePresence>
            {!isActive && (
              <span className="font-mono text-[9px] text-text-muted">Standby</span>
            )}
          </div>
        </div>

        {/* ── Context info ── */}
        <div className="grid grid-cols-2 gap-px border-b border-[rgba(255,32,32,0.08)]">
          {[
            {
              icon: <FolderOpen className="h-3 w-3 text-[#ff2020]" />,
              label: 'PROJECT',
              value: status?.active_project ?? '—',
            },
            {
              icon: <FileCode2 className="h-3 w-3 text-[#00ffff]" />,
              label: 'FILE',
              value: status?.active_file ?? '—',
            },
            {
              icon: <Code2 className="h-3 w-3 text-[#00ff88]" />,
              label: 'LANGUAGE',
              value: lang,
            },
            {
              icon: <Activity className="h-3 w-3 text-[#a78bfa]" />,
              label: 'UI MODE',
              value: (status?.active_ui_mode ?? 'default').toUpperCase(),
            },
          ].map(({ icon, label, value }) => (
            <div key={label} className="flex items-center gap-2 px-3 py-2 bg-[rgba(0,0,0,0.3)]">
              {icon}
              <div className="min-w-0">
                <p className="font-mono text-[7px] tracking-widest text-text-muted">{label}</p>
                <p className="font-mono text-[9px] text-text-secondary truncate">{value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* ── Quick actions ── */}
        <div className="px-3 py-2.5 border-b border-[rgba(255,32,32,0.08)]">
          <p className="font-mono text-[7px] tracking-widest text-text-muted mb-2">QUICK ACTIONS</p>
          <div className="flex flex-wrap gap-1.5">
            {QUICK_ACTIONS.map(({ label, icon: Icon, prompt }) => (
              <button
                key={label}
                onClick={() => handleQuickAction(prompt)}
                className="flex items-center gap-1 rounded-lg border border-[rgba(255,32,32,0.2)] bg-[rgba(255,32,32,0.05)] px-2.5 py-1.5 font-mono text-[9px] text-text-muted transition-all hover:border-[rgba(255,32,32,0.5)] hover:bg-[rgba(255,32,32,0.12)] hover:text-[#ff2020]"
                style={{ boxShadow: 'none' }}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* ── AI suggestions (passive hints) ── */}
        <AnimatePresence>
          {passiveHint && (
            <motion.div
              key={passiveHint}
              initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
              className="border-b border-[rgba(255,32,32,0.08)]"
            >
              <div className="flex items-center gap-2 px-3 py-2 bg-[rgba(255,32,32,0.04)]">
                <Cpu className="h-3 w-3 text-[#ff2020] flex-shrink-0 animate-pulse" />
                <p className="font-mono text-[9px] text-[#ff2020]/70 italic">{passiveHint}</p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Holographic scan line (entrance effect) ── */}
        <AnimatePresence>
          {isActive && (
            <motion.div
              key="scan"
              initial={{ scaleX: 0, opacity: 0.8 }}
              animate={{ scaleX: 1, opacity: 0 }}
              exit={{}}
              transition={{ duration: 0.6, ease: 'easeOut' }}
              className="absolute top-0 left-0 right-0 h-px origin-left"
              style={{ background: 'linear-gradient(90deg, transparent, #ff2020, #00ffff, transparent)' }}
            />
          )}
        </AnimatePresence>

        {/* ── Response panel ── */}
        <div className="px-3 pb-3 pt-2.5">
          <div className="flex items-center justify-between mb-2">
            <p className="font-mono text-[7px] tracking-widest text-text-muted">LAST RESPONSE</p>
            {(response || streaming) && (
              <button
                onClick={() => { abortRef.current?.abort(); setResponse(''); setStreaming(false) }}
                className="font-mono text-[8px] text-text-muted hover:text-[#ff2020] transition-colors"
              >
                Clear
              </button>
            )}
          </div>
          <div
            className="min-h-[80px] max-h-[260px] overflow-y-auto rounded-lg border border-[rgba(255,32,32,0.1)] bg-[rgba(0,0,0,0.5)] p-3"
            style={{ scrollbarWidth: 'thin' }}
          >
            <StreamPanel response={response} streaming={streaming} />
          </div>
        </div>

      </div>
    </motion.div>
  )
}
