'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2, FileCode2, FolderOpen, GitBranch, Terminal,
  Zap, Bug, Wrench, Layers, Search,
  Activity, Cpu, AlertTriangle, Info, Clock,
  Hash, ListTodo, ShieldAlert, Copy, Check,
  Database, TrendingUp,
} from 'lucide-react'

const API = 'http://localhost:8000'

// ── Types ─────────────────────────────────────────────────────────────────────

interface DevStatus {
  code_mode: boolean
  active_project: string | null
  active_file: string | null
  active_ui_mode: string
}

interface FileContext {
  project: string | null
  file: string | null
  language: string
  framework: string
  summary: string
  imports: string[]
  symbols: string[]
  todos: string[]
  issues: string[]
  hash: string | null
  latency_ms: number
  cache_hit: boolean
}

interface ProjectMemory {
  project_name: string
  project_path: string
  detected_stack: string[]
  package_manager: string
  git_branch: string
  common_commands: string[]
  architecture_notes: string[]
  recurring_errors: Array<{ type: string; summary: string; count: number; last_seen: number }>
  recent_files: string[]
  recent_tasks: string[]
  session_summaries: Array<{ text: string; timestamp: number }>
  updated_at: number
}

interface TerminalError {
  detected: boolean
  error_type: string | null
  severity: 'low' | 'medium' | 'high'
  summary: string
  likely_cause: string
  suggested_fix: string
  commands: string[]
  files_to_check: string[]
  confidence: string
  source: string
  timestamp?: number
}

interface Insight {
  type: 'insight' | 'heartbeat' | 'connected' | 'error'
  message?: string
  severity?: 'info' | 'warning' | 'critical'
  source?: 'file' | 'terminal' | 'memory' | 'project'
  ts?: number
}

interface CodeAssistantPanelProps {
  onInjectPrompt: (text: string) => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

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

function basename(p: string | null | undefined): string {
  if (!p) return '—'
  return p.split(/[\\/]/).pop() ?? p
}

function severityColor(s: string | undefined): string {
  if (s === 'high' || s === 'critical') return '#ff2020'
  if (s === 'warning' || s === 'medium') return '#fbbf24'
  return '#00ffff'
}

function severityIcon(s: string | undefined) {
  if (s === 'high' || s === 'critical') return <ShieldAlert className="h-3 w-3" />
  if (s === 'warning' || s === 'medium') return <AlertTriangle className="h-3 w-3" />
  return <Info className="h-3 w-3" />
}

function tsRelative(ts: number): string {
  const diff = Math.floor(Date.now() / 1000 - ts)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

// ── Sub-components ────────────────────────────────────────────────────────────

function CodeRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = canvas.offsetWidth
    canvas.height = canvas.offsetHeight
    const chars = '01{}[]()<>=!;:,./*&|^~'
    const cols = Math.floor(canvas.width / 14)
    const drops = Array(cols).fill(1)
    let raf: number
    const draw = () => {
      ctx.fillStyle = 'rgba(0,0,0,0.05)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
      ctx.fillStyle = 'rgba(255,32,32,0.4)'
      ctx.font = '12px monospace'
      drops.forEach((y, i) => {
        ctx.fillText(chars[Math.floor(Math.random() * chars.length)], i * 14, y * 14)
        if (y * 14 > canvas.height && Math.random() > 0.975) drops[i] = 0
        drops[i]++
      })
      raf = requestAnimationFrame(draw)
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [])
  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full opacity-40" />
}

function Tag({ text, color = 'rgba(255,255,255,0.08)' }: { text: string; color?: string }) {
  return (
    <span className="inline-block rounded px-1.5 py-0.5 font-mono text-[8px] text-text-muted border truncate max-w-[140px]"
      style={{ borderColor: color, background: color + '22' }} title={text}>
      {text}
    </span>
  )
}

function Section({ title, icon, children, accent = '#ff2020' }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; accent?: string
}) {
  return (
    <div className="border-b border-[rgba(255,32,32,0.08)] last:border-0">
      <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[rgba(0,0,0,0.2)]">
        <span style={{ color: accent }}>{icon}</span>
        <span className="font-mono text-[7px] tracking-[0.16em] text-text-muted">{title}</span>
      </div>
      <div className="px-3 pb-2.5 pt-1.5">{children}</div>
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }) }}
      className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[8px] text-text-muted border border-[rgba(255,255,255,0.08)] hover:border-[rgba(0,255,255,0.3)] hover:text-[#00ffff] transition-colors flex-shrink-0">
      {copied ? <Check className="h-2.5 w-2.5" /> : <Copy className="h-2.5 w-2.5" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

function StreamPanel({ response, streaming }: { response: string; streaming: boolean }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [response])
  if (!response && !streaming) {
    return <p className="font-mono text-[9px] text-text-muted italic">Awaiting query…</p>
  }
  const parts = response.split(/(```[\w]*\n[\s\S]*?```)/g)
  return (
    <div className="space-y-1.5">
      {parts.map((part, i) => {
        if (part.startsWith('```')) {
          const lines = part.split('\n')
          const lang = lines[0].replace('```', '').trim()
          const code = lines.slice(1, -1).join('\n')
          return (
            <pre key={i} className="rounded-md border border-[rgba(0,255,255,0.15)] bg-[rgba(0,255,255,0.04)] p-2.5 font-mono text-[9px] text-[#00ffff] overflow-x-auto">
              {lang && <div className="mb-1 text-[7px] text-text-muted uppercase tracking-widest">{lang}</div>}
              {code}
            </pre>
          )
        }
        return (
          <p key={i} className="font-mono text-[10px] text-text-secondary leading-relaxed whitespace-pre-wrap">{part}</p>
        )
      })}
      {streaming && <span className="inline-block h-2.5 w-0.5 animate-pulse bg-[#ff2020]" />}
      <div ref={endRef} />
    </div>
  )
}

const QUICK_ACTIONS = [
  { label: 'Explain',  icon: Search,  prompt: 'explain this' },
  { label: 'Write',    icon: Code2,   prompt: 'write code for' },
  { label: 'Debug',    icon: Bug,     prompt: 'debug this' },
  { label: 'Refactor', icon: Wrench,  prompt: 'refactor this' },
  { label: 'Architect',icon: Layers,  prompt: 'architect this' },
  { label: 'Optimize', icon: Zap,     prompt: 'optimize this' },
]

// ════════════════════════════════════════════════════════════════════════════════
//  Main Panel
// ════════════════════════════════════════════════════════════════════════════════

export function CodeAssistantPanel({ onInjectPrompt }: CodeAssistantPanelProps) {
  const [status,     setStatus]     = useState<DevStatus | null>(null)
  const [fileCtx,    setFileCtx]    = useState<FileContext | null>(null)
  const [projectMem, setProjectMem] = useState<ProjectMemory | null>(null)
  const [termError,  setTermError]  = useState<TerminalError | null>(null)
  const [insights,   setInsights]   = useState<Insight[]>([])
  const [response,   setResponse]   = useState('')
  const [streaming,  setStreaming]  = useState(false)
  const [ctxFreshAt, setCtxFreshAt] = useState<number | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const sseRef   = useRef<EventSource | null>(null)

  const isActive = status?.code_mode ?? false

  // Poll /dev/status every 2s
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${API}/api/v1/dev/status`)
        if (r.ok) setStatus(await r.json())
      } catch { /* offline */ }
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [])

  // Fetch context when code_mode or file/project changes
  useEffect(() => {
    if (!isActive) return
    let cancelled = false
    const fetchAll = async () => {
      try {
        const [fcR, pmR, teR] = await Promise.allSettled([
          fetch(`${API}/api/v1/dev/active-file-context`).then(r => r.json()),
          status?.active_project
            ? fetch(`${API}/api/v1/dev/project-memory`).then(r => r.json())
            : Promise.resolve(null),
          status?.active_project
            ? fetch(`${API}/api/v1/dev/latest-terminal-error`).then(r => r.json())
            : Promise.resolve(null),
        ])
        if (cancelled) return
        if (fcR.status === 'fulfilled' && fcR.value) setFileCtx(fcR.value)
        if (pmR.status === 'fulfilled' && pmR.value) setProjectMem(pmR.value)
        if (teR.status === 'fulfilled' && teR.value?.detected) setTermError(teR.value)
        setCtxFreshAt(Date.now())
      } catch { /* continue */ }
    }
    fetchAll()
    const id = setInterval(fetchAll, 8000)
    return () => { cancelled = true; clearInterval(id) }
  }, [isActive, status?.active_file, status?.active_project])

  // SSE observer insights
  useEffect(() => {
    if (!isActive) { sseRef.current?.close(); sseRef.current = null; return }
    if (sseRef.current) return
    const es = new EventSource(`${API}/api/v1/dev/observer-stream`)
    sseRef.current = es
    es.onmessage = (e) => {
      try {
        const msg: Insight = JSON.parse(e.data)
        if (msg.type === 'heartbeat' || msg.type === 'connected') return
        setInsights(prev => [{ ...msg, ts: Date.now() / 1000 }, ...prev].slice(0, 20))
      } catch { /* skip */ }
    }
    es.onerror = () => { es.close(); sseRef.current = null }
    return () => { es.close(); sseRef.current = null }
  }, [isActive])

  const handleQuickAction = useCallback((prompt: string) => {
    onInjectPrompt(prompt)
    setResponse('')
    setStreaming(true)
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    ;(async () => {
      try {
        const resp = await fetch(`${API}/api/v1/dev/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: prompt, active_project: status?.active_project, active_file: status?.active_file }),
          signal: abortRef.current!.signal,
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
        if ((e as Error).name !== 'AbortError') setResponse(p => p + '\n[stream error]')
      } finally {
        setStreaming(false)
      }
    })()
  }, [status, onInjectPrompt])

  const lang = detectLang(status?.active_file ?? null)

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
      }}>

      {/* Code rain */}
      <AnimatePresence>
        {isActive && (
          <motion.div key="rain" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 pointer-events-none">
            <CodeRain />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="relative z-10">

        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[rgba(255,32,32,0.12)]">
          <div className="flex items-center gap-2">
            <Terminal className="h-3.5 w-3.5 text-[#ff2020]" />
            <span className="font-mono text-[9px] tracking-[0.18em] text-text-muted">CODE ASSISTANT</span>
          </div>
          <div className="flex items-center gap-2">
            <AnimatePresence>
              {isActive && (
                <motion.div key="badge"
                  initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                  className="flex items-center gap-1.5 rounded-full border border-[rgba(255,32,32,0.4)] bg-[rgba(255,32,32,0.12)] px-2.5 py-0.5"
                  style={{ boxShadow: '0 0 8px rgba(255,32,32,0.2)' }}>
                  <div className="h-1.5 w-1.5 rounded-full bg-[#ff2020] animate-pulse" />
                  <span className="font-mono text-[8px] font-bold text-[#ff2020] tracking-widest">DEV MODE ACTIVE</span>
                </motion.div>
              )}
            </AnimatePresence>
            {!isActive && <span className="font-mono text-[9px] text-text-muted">Standby</span>}
          </div>
        </div>

        {/* ── Context Status Grid ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-px border-b border-[rgba(255,32,32,0.08)]">
          {[
            { icon: <FolderOpen className="h-3 w-3 text-[#ff2020]" />, label: 'PROJECT',  value: basename(status?.active_project) },
            { icon: <FileCode2  className="h-3 w-3 text-[#00ffff]" />, label: 'FILE',     value: basename(status?.active_file) },
            { icon: <Code2      className="h-3 w-3 text-[#00ff88]" />, label: 'LANGUAGE', value: fileCtx?.language ?? lang },
            { icon: <Activity   className="h-3 w-3 text-[#a78bfa]" />, label: 'CONTEXT',  value: ctxFreshAt ? tsRelative(ctxFreshAt / 1000) : '—' },
          ].map(({ icon, label, value }) => (
            <div key={label} className="flex items-center gap-2 px-3 py-2 bg-[rgba(0,0,0,0.3)]">
              {icon}
              <div className="min-w-0">
                <p className="font-mono text-[7px] tracking-widest text-text-muted">{label}</p>
                <p className="font-mono text-[9px] text-text-secondary truncate">{value ?? '—'}</p>
              </div>
            </div>
          ))}
        </div>

        {/* ── Section 1: Active File Intelligence ────────────────────────────── */}
        {isActive && (
          <Section title="ACTIVE FILE INTELLIGENCE" icon={<FileCode2 className="h-3 w-3" />} accent="#00ffff">
            {!fileCtx || fileCtx.language === 'unknown' ? (
              <p className="font-mono text-[9px] text-text-muted italic">No active file detected.</p>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <Tag text={fileCtx.language} color="rgba(0,255,255,0.3)" />
                  {fileCtx.framework !== 'unknown' && <Tag text={fileCtx.framework} color="rgba(0,255,136,0.3)" />}
                  {fileCtx.cache_hit && <Tag text="cached" color="rgba(255,255,255,0.1)" />}
                  <span className="font-mono text-[7px] text-text-muted">{fileCtx.latency_ms}ms</span>
                </div>
                {fileCtx.summary && (
                  <p className="font-mono text-[9px] text-text-secondary leading-relaxed">{fileCtx.summary}</p>
                )}
                {fileCtx.symbols.length > 0 && (
                  <div>
                    <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">
                      <Hash className="inline h-2.5 w-2.5 mr-0.5" />SYMBOLS
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {fileCtx.symbols.slice(0, 10).map(s => <Tag key={s} text={s} color="rgba(0,255,255,0.15)" />)}
                    </div>
                  </div>
                )}
                {fileCtx.todos.length > 0 && (
                  <div>
                    <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">
                      <ListTodo className="inline h-2.5 w-2.5 mr-0.5" />TODOS ({fileCtx.todos.length})
                    </p>
                    {fileCtx.todos.slice(0, 4).map((t, i) => (
                      <p key={i} className="font-mono text-[8px] text-[#fbbf24]/80 truncate">· {t}</p>
                    ))}
                  </div>
                )}
                {fileCtx.issues.length > 0 && (
                  <div>
                    <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">
                      <AlertTriangle className="inline h-2.5 w-2.5 mr-0.5" />ISSUES
                    </p>
                    {fileCtx.issues.map((iss, i) => (
                      <p key={i} className="font-mono text-[8px] text-[#ff2020]/80">⚠ {iss}</p>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Section>
        )}

        {/* ── Section 2: Project Memory ───────────────────────────────────────── */}
        {isActive && projectMem && (
          <Section title="PROJECT MEMORY" icon={<Database className="h-3 w-3" />} accent="#00ff88">
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 flex-wrap">
                {projectMem.detected_stack.slice(0, 5).map(s => (
                  <Tag key={s} text={s} color="rgba(0,255,136,0.25)" />
                ))}
                {projectMem.git_branch !== 'unknown' && (
                  <span className="flex items-center gap-0.5 font-mono text-[8px] text-text-muted">
                    <GitBranch className="h-2.5 w-2.5" />{projectMem.git_branch}
                  </span>
                )}
              </div>
              {projectMem.recurring_errors.length > 0 && (
                <div>
                  <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">
                    <TrendingUp className="inline h-2.5 w-2.5 mr-0.5" />RECURRING ERRORS
                  </p>
                  {projectMem.recurring_errors.slice(0, 3).map((e, i) => (
                    <div key={i} className="flex items-center justify-between">
                      <p className="font-mono text-[8px] text-[#fbbf24]/80 truncate">{e.type.replace(/_/g, ' ')}</p>
                      <span className="font-mono text-[7px] text-text-muted ml-2 flex-shrink-0">×{e.count}</span>
                    </div>
                  ))}
                </div>
              )}
              {projectMem.recent_files.length > 0 && (
                <div>
                  <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">
                    <Clock className="inline h-2.5 w-2.5 mr-0.5" />RECENT FILES
                  </p>
                  {projectMem.recent_files.slice(0, 4).map((f, i) => (
                    <p key={i} className="font-mono text-[8px] text-text-muted truncate">· {basename(f)}</p>
                  ))}
                </div>
              )}
              {projectMem.session_summaries.length > 0 && (
                <div>
                  <p className="font-mono text-[7px] tracking-widest text-text-muted mb-1">LAST SESSION</p>
                  <p className="font-mono text-[8px] text-text-secondary leading-relaxed">
                    {projectMem.session_summaries[0].text}
                  </p>
                </div>
              )}
            </div>
          </Section>
        )}

        {/* ── Section 3: Terminal Intelligence ───────────────────────────────── */}
        {isActive && termError?.detected && (
          <Section title="TERMINAL INTELLIGENCE" icon={<Bug className="h-3 w-3" />} accent="#ff2020">
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span style={{ color: severityColor(termError.severity) }}>
                  {severityIcon(termError.severity)}
                </span>
                <span className="font-mono text-[9px]" style={{ color: severityColor(termError.severity) }}>
                  {termError.error_type?.replace(/_/g, ' ').toUpperCase()}
                </span>
                <Tag text={termError.severity} color={severityColor(termError.severity)} />
              </div>
              <p className="font-mono text-[9px] text-text-secondary">{termError.summary}</p>
              <div className="rounded-lg border border-[rgba(255,32,32,0.15)] bg-[rgba(255,32,32,0.04)] p-2 space-y-1">
                <p className="font-mono text-[7px] tracking-widest text-text-muted">CAUSE</p>
                <p className="font-mono text-[8px] text-text-secondary">{termError.likely_cause}</p>
                <p className="font-mono text-[7px] tracking-widest text-text-muted mt-1">FIX</p>
                <p className="font-mono text-[8px] text-[#00ff88]">{termError.suggested_fix}</p>
              </div>
              {termError.commands.length > 0 && (
                <div className="space-y-1">
                  <p className="font-mono text-[7px] tracking-widest text-text-muted">RUN</p>
                  {termError.commands.slice(0, 3).map((cmd, i) => (
                    <div key={i} className="flex items-center gap-2 rounded border border-[rgba(0,255,255,0.15)] bg-[rgba(0,255,255,0.04)] px-2 py-1">
                      <code className="font-mono text-[8px] text-[#00ffff] truncate flex-1">{cmd}</code>
                      <CopyButton text={cmd} />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Section>
        )}

        {/* ── Section 4: Passive Insights Feed ───────────────────────────────── */}
        {isActive && (
          <Section title="PASSIVE INSIGHTS" icon={<Cpu className="h-3 w-3" />} accent="#a78bfa">
            {insights.length === 0 ? (
              <p className="font-mono text-[9px] text-text-muted italic">Observer watching…</p>
            ) : (
              <div className="space-y-1.5 max-h-[120px] overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
                <AnimatePresence initial={false}>
                  {insights.map((ins, i) => (
                    <motion.div key={i}
                      initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                      className="flex items-start gap-2 rounded-lg border border-[rgba(255,255,255,0.05)] bg-[rgba(0,0,0,0.3)] px-2.5 py-1.5">
                      <span style={{ color: severityColor(ins.severity), marginTop: 1, flexShrink: 0 }}>
                        {severityIcon(ins.severity)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="font-mono text-[8px] text-text-secondary leading-relaxed">{ins.message}</p>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          {ins.source && <Tag text={ins.source} color="rgba(167,139,250,0.2)" />}
                          {ins.ts && <span className="font-mono text-[7px] text-text-muted">{tsRelative(ins.ts)}</span>}
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            )}
          </Section>
        )}

        {/* ── Quick Actions ───────────────────────────────────────────────────── */}
        <div className="px-3 py-2.5 border-b border-[rgba(255,32,32,0.08)]">
          <p className="font-mono text-[7px] tracking-widest text-text-muted mb-2">QUICK ACTIONS</p>
          <div className="flex flex-wrap gap-1.5">
            {QUICK_ACTIONS.map(({ label, icon: Icon, prompt }) => (
              <button key={label} onClick={() => handleQuickAction(prompt)}
                className="flex items-center gap-1 rounded-lg border border-[rgba(255,32,32,0.2)] bg-[rgba(255,32,32,0.05)] px-2.5 py-1.5 font-mono text-[9px] text-text-muted transition-all hover:border-[rgba(255,32,32,0.5)] hover:bg-[rgba(255,32,32,0.12)] hover:text-[#ff2020]">
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Scan line */}
        <AnimatePresence>
          {isActive && (
            <motion.div key="scan"
              initial={{ scaleX: 0, opacity: 0.8 }} animate={{ scaleX: 1, opacity: 0 }}
              transition={{ duration: 0.6, ease: 'easeOut' }}
              className="absolute top-0 left-0 right-0 h-px origin-left"
              style={{ background: 'linear-gradient(90deg, transparent, #ff2020, #00ffff, transparent)' }} />
          )}
        </AnimatePresence>

        {/* ── Response Panel ──────────────────────────────────────────────────── */}
        <div className="px-3 pb-3 pt-2.5">
          <div className="flex items-center justify-between mb-2">
            <p className="font-mono text-[7px] tracking-widest text-text-muted">LAST RESPONSE</p>
            {(response || streaming) && (
              <button onClick={() => { abortRef.current?.abort(); setResponse(''); setStreaming(false) }}
                className="font-mono text-[8px] text-text-muted hover:text-[#ff2020] transition-colors">
                Clear
              </button>
            )}
          </div>
          <div className="min-h-[80px] max-h-[260px] overflow-y-auto rounded-lg border border-[rgba(255,32,32,0.1)] bg-[rgba(0,0,0,0.5)] p-3"
            style={{ scrollbarWidth: 'thin' }}>
            <StreamPanel response={response} streaming={streaming} />
          </div>
        </div>

      </div>
    </motion.div>
  )
}
export default CodeAssistantPanel
