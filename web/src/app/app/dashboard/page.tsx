'use client'

import { useEffect, useRef, useState } from 'react'
import {
  Volume2, Battery, Wifi, HardDrive,
  Mic, Send, Music, Play, SkipForward, SkipBack,
} from 'lucide-react'
import { BrainCanvas } from '@/components/dashboard/BrainCanvas'
import { ParticleCanvas } from '@/components/dashboard/ParticleCanvas'
import { useSystemMetrics } from '@/hooks/useSystemMetrics'
import { useEnvironment } from '@/hooks/useEnvironment'
import EnvironmentPanel from '@/components/system/EnvironmentPanel'

// ── Helpers ────────────────────────────────────────────────────────────────────

function CyberPanel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`cyber-panel p-4 ${className}`}>
      {children}
    </div>
  )
}

function SectionTitle({ label, live = false }: { label: string; live?: boolean }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {live && (
        <span className="flex h-2 w-2 flex-shrink-0">
          <span className="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[#ff2020] opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[#ff2020]" />
        </span>
      )}
      <span className="font-mono text-[10px] font-semibold tracking-[0.2em] text-[#ff2020]">{label}</span>
      <div className="flex-1 h-px bg-[rgba(255,32,32,0.2)]" />
    </div>
  )
}

// ── Circular SVG gauge ─────────────────────────────────────────────────────────

function CircularGauge({
  value, max = 100, label, unit = '%', color = '#ff2020', size = 80,
}: {
  value: number; max?: number; label: string; unit?: string; color?: string; size?: number
}) {
  const r = size * 0.38
  const circ = 2 * Math.PI * r
  const pct = Math.min(value / max, 1)
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,32,32,0.12)" strokeWidth={size * 0.08} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          stroke={color} strokeWidth={size * 0.08}
          strokeDasharray={circ} strokeDashoffset={circ * (1 - pct)}
          strokeLinecap="round"
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ filter: `drop-shadow(0 0 4px ${color})`, transition: 'stroke-dashoffset 1s ease' }}
        />
        <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle"
          fill={color} fontSize={size * 0.18} fontFamily="Share Tech Mono, monospace" fontWeight="bold">
          {value}{unit}
        </text>
      </svg>
      <span className="font-mono text-[10px] tracking-widest text-text-muted">{label}</span>
    </div>
  )
}

// ── Metric bar ─────────────────────────────────────────────────────────────────

function MetricBar({ label, value, color = '#ff2020' }: { label: string; value: number; color?: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-text-muted">{label}</span>
        <span className="font-mono text-[10px] text-text-secondary">{value}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
        <div
          className="h-full rounded-full transition-all duration-1000"
          style={{ width: `${value}%`, background: color, boxShadow: `0 0 6px ${color}` }}
        />
      </div>
    </div>
  )
}

// ── Nav tabs ──────────────────────────────────────────────────────────────────

const TABS = ['DASHBOARD', 'CONVERSATION', 'MEMORY', 'TASKS', 'AUTOMATION', 'SYSTEM', 'ANALYTICS', 'SETTINGS']

// ── Terminal lines ────────────────────────────────────────────────────────────

const TERMINAL_LINES = [
  '> XYRON SYSTEM BOOT SEQUENCE INITIATED',
  '> Loading cognitive architecture... [OK]',
  '> Whisper STT engine: READY',
  '> Kokoro TTS engine: READY',
  '> OpenAI GPT-4o: CONNECTED',
  '> Memory service: 847 facts loaded',
  '> Intent classifier: 19 routers active',
  '> MCP servers: 2 subprocesses running',
  '> Episodic memory: 1,204 episodes indexed',
  '> Wake word listener: ACTIVE — "hey xyron"',
  '> Voice pipeline: ONLINE',
  '> XYRON AI OPERATOR v2.0.0 OMEGA — ALL SYSTEMS NOMINAL_',
]

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [activeTab, setActiveTab] = useState('DASHBOARD')
  const [terminalLines, setTerminalLines] = useState<string[]>([])
  const [voiceInput, setVoiceInput] = useState('')
  const [countdown, setCountdown] = useState(3600)
  const [dateStr, setDateStr] = useState('')
  const terminalRef = useRef<HTMLDivElement>(null)
  const { data: metrics } = useSystemMetrics(2000)
  const environment = useEnvironment()

  // live date string
  useEffect(() => {
    const tick = () => setDateStr(new Date().toLocaleDateString('en-US', {
      weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
    }))
    tick()
    const id = setInterval(tick, 60_000)
    return () => clearInterval(id)
  }, [])

  // terminal typewriter
  useEffect(() => {
    let i = 0
    const id = setInterval(() => {
      if (i < TERMINAL_LINES.length) {
        setTerminalLines(prev => [...prev, TERMINAL_LINES[i]])
        i++
      } else {
        clearInterval(id)
      }
    }, 280)
    return () => clearInterval(id)
  }, [])

  // auto-scroll terminal
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight
    }
  }, [terminalLines])

  // countdown
  useEffect(() => {
    const id = setInterval(() => setCountdown(c => (c > 0 ? c - 1 : 0)), 1000)
    return () => clearInterval(id)
  }, [])

  const fmtCountdown = (s: number) => {
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = s % 60
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  }

  return (
    <div className="min-h-screen bg-[#0a0a0a] pb-40 font-sans text-text-primary">

      {/* ── Nav Tabs ──────────────────────────────────────────── */}
      <div className="sticky top-14 z-20 border-b border-[rgba(255,32,32,0.15)] bg-[rgba(10,10,10,0.96)] backdrop-blur-sm">
        <div className="no-scrollbar flex overflow-x-auto px-4">
          {TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-shrink-0 border-b-2 px-4 py-3 font-mono text-[10px] tracking-[0.15em] transition-all duration-150 ${
                activeTab === tab
                  ? 'border-[#ff2020] text-[#ff2020]'
                  : 'border-transparent text-text-muted hover:border-[rgba(255,32,32,0.3)] hover:text-text-secondary'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* ── Main 3-col grid ───────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[260px_1fr_260px] lg:p-5">

        {/* ══ LEFT SIDEBAR ════════════════════════════════════════ */}
        <div className="space-y-4">

          {/* Xyron Core */}
          <CyberPanel className="flex flex-col items-center gap-3 py-6">
            <div className="orb-glow h-20 w-20" />
            <div className="text-center">
              <p className="text-sm font-black tracking-[0.25em] text-[#ff2020]">XYRON CORE</p>
              <div className="mt-1 flex items-center justify-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-[#00ff88] shadow-[0_0_6px_rgba(0,255,136,0.8)]" />
                <span className="font-mono text-[10px] text-[#00ff88]">ONLINE</span>
              </div>
            </div>
          </CyberPanel>

          {/* Quick Status */}
          <CyberPanel>
            <SectionTitle label="QUICK STATUS" />
            <div className="space-y-2.5">
              {[
                { label: 'System Uptime',  value: metrics?.uptime_str ?? '—',                           color: '#00ff88' },
                { label: 'CPU Usage',      value: metrics ? `${metrics.cpu_pct}%` : '—',                 color: '#ff2020' },
                { label: 'RAM Usage',      value: metrics ? `${metrics.ram_used_gb}/${metrics.ram_total_gb} GB` : '—', color: '#00ffff' },
                { label: 'CPU Cores',      value: metrics ? `${metrics.cpu_cores} LOGICAL` : '—',        color: '#00ff88' },
                { label: 'Threat Level',   value: 'LOW',                                                 color: '#f59e0b' },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex items-center justify-between">
                  <span className="font-mono text-[11px] text-text-muted">{label}</span>
                  <span className="font-mono text-[11px] font-semibold" style={{ color }}>{value}</span>
                </div>
              ))}
            </div>
          </CyberPanel>

          {/* Core Modules */}
          <CyberPanel>
            <SectionTitle label="CORE MODULES" />
            <div className="space-y-2">
              {[
                { name: 'Intent Router',    status: 'ACTIVE',   color: '#00ff88' },
                { name: 'Memory Service',   status: 'ACTIVE',   color: '#00ff88' },
                { name: 'TTS Engine',       status: 'ACTIVE',   color: '#00ff88' },
                { name: 'STT Engine',       status: 'ACTIVE',   color: '#00ff88' },
                { name: 'Tool Registry',    status: 'ACTIVE',   color: '#00ff88' },
                { name: 'MCP Servers',      status: 'ACTIVE',   color: '#00ff88' },
                { name: 'Screen Context',   status: 'STANDBY',  color: '#f59e0b' },
                { name: 'Proactive Engine', status: 'STANDBY',  color: '#f59e0b' },
              ].map(({ name, status, color }) => (
                <div key={name} className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] text-text-muted truncate">{name}</span>
                  <span className="flex-shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] font-bold"
                    style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
                    {status}
                  </span>
                </div>
              ))}
            </div>
            <button className="mt-3 w-full rounded border border-[rgba(255,32,32,0.3)] py-1.5 font-mono text-[10px] tracking-widest text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.08)]">
              VIEW ALL MODULES
            </button>
          </CyberPanel>

          {/* Shortcut Commands */}
          <CyberPanel>
            <SectionTitle label="SHORTCUTS" />
            <div className="space-y-2">
              {[
                { cmd: '/status',   desc: 'System health check' },
                { cmd: '/memory',   desc: 'View stored facts' },
                { cmd: '/tasks',    desc: 'List active tasks' },
                { cmd: '/workflow', desc: 'Trigger automation' },
                { cmd: '/screen',   desc: 'Capture & analyze' },
                { cmd: '/takeover', desc: 'Activate takeover mode' },
              ].map(({ cmd, desc }) => (
                <div key={cmd} className="flex items-start gap-2 rounded border border-[rgba(255,32,32,0.1)] bg-[rgba(255,32,32,0.03)] px-2 py-1.5">
                  <span className="font-mono text-[11px] font-bold text-[#ff2020]">{cmd}</span>
                  <span className="font-mono text-[10px] text-text-muted">{desc}</span>
                </div>
              ))}
            </div>
          </CyberPanel>
        </div>

        {/* ══ CENTER ══════════════════════════════════════════════ */}
        <div className="space-y-4 min-w-0">

          {/* Quote banner + live date */}
          <div className="flex items-start justify-between gap-4">
            <div className="border-l-2 border-[#ff2020] pl-4 py-1">
              <p className="font-mono text-sm italic text-text-secondary">
                "The machine does not isolate man from the great problems of nature
                but plunges him more deeply into them."
              </p>
              <p className="mt-1 font-mono text-[10px] text-text-muted">— Antoine de Saint-Exupéry</p>
            </div>
            <div className="flex-shrink-0 text-right">
              <p className="font-mono text-[10px] text-[#ff2020]">{dateStr}</p>
              <p className="font-mono text-[10px] text-text-muted">
                UPTIME: {metrics?.uptime_str ?? '—'}
              </p>
            </div>
          </div>

          {/* System Overview */}
          <CyberPanel>
            <SectionTitle label="SYSTEM OVERVIEW" live />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[auto_1fr_auto]">
              {/* Gauges — live from psutil */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-1 md:gap-2">
                <CircularGauge value={metrics?.cpu_pct  ?? 0} label="CPU"  color="#ff2020" size={76} />
                <CircularGauge value={metrics?.disk_pct ?? 0} label="DISK" color="#00ffff" size={76} />
                <CircularGauge value={metrics?.ram_pct  ?? 0} label="RAM"  color="#ff2020" size={76} />
                <CircularGauge value={metrics?.temp_c   ?? 0} label="TEMP" unit="°C" color="#f59e0b" max={100} size={76} />
              </div>

              {/* Brain canvas */}
              <div className="relative min-h-[220px] overflow-hidden rounded border border-[rgba(255,32,32,0.15)] bg-[rgba(0,0,0,0.6)]">
                <BrainCanvas className="absolute inset-0" />
                <div className="absolute bottom-2 left-2 font-mono text-[9px] text-[rgba(255,32,32,0.6)]">
                  NEURAL MESH ACTIVE
                </div>
              </div>

              {/* Overall gauge + bars */}
              <div className="flex flex-col items-center gap-3">
                <CircularGauge value={94} label="OVERALL" color="#00ff88" size={88} />
                <div className="w-full space-y-2">
                  <MetricBar label="CPU LOAD"    value={metrics?.cpu_pct   ?? 0} color="#ff2020" />
                  <MetricBar label="RAM USAGE"   value={metrics?.ram_pct   ?? 0} color="#00ffff" />
                  <MetricBar label="DISK USAGE"  value={metrics?.disk_pct  ?? 0} color="#f59e0b" />
                  <MetricBar label="SWAP USAGE"  value={metrics?.swap_pct  ?? 0} color="#00ff88" />
                </div>
              </div>
            </div>
          </CyberPanel>

          {/* Active Processes — pipeline */}
          <CyberPanel>
            <SectionTitle label="ACTIVE PROCESSES" />
            {/* Pipeline */}
            <div className="relative mb-4 overflow-x-auto">
              <div className="no-scrollbar flex min-w-max items-center gap-0 overflow-x-auto">
                {['INPUT', 'NLP', 'PLANNER', 'EXECUTOR', 'FEEDBACK'].map((stage, i, arr) => (
                  <div key={stage} className="flex items-center">
                    <div className="flex flex-col items-center gap-1 px-3 py-2 rounded border border-[rgba(255,32,32,0.3)] bg-[rgba(255,32,32,0.06)] min-w-[70px]">
                      <span className="font-mono text-[9px] tracking-widest text-[#ff2020]">{stage}</span>
                      <span className="h-1.5 w-1.5 rounded-full bg-[#00ff88] shadow-[0_0_4px_rgba(0,255,136,0.8)]" />
                    </div>
                    {i < arr.length - 1 && (
                      <div className="relative mx-0.5 h-0.5 w-8 bg-[rgba(255,32,32,0.2)] overflow-hidden">
                        <div className="pipeline-dot" />
                        <div className="pipeline-dot pipeline-dot-2" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
            {/* Progress bar */}
            <div className="space-y-1">
              <div className="flex justify-between">
                <span className="font-mono text-[10px] text-text-muted">PROCESSING FLOW</span>
                <span className="font-mono text-[10px] text-[#ff2020]">100%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-[rgba(255,32,32,0.1)]">
                <div className="h-full w-full rounded-full bg-gradient-to-r from-[#cc0000] to-[#ff2020]"
                  style={{ boxShadow: '0 0 8px rgba(255,32,32,0.6)', animation: 'glowPulse 2s ease-in-out infinite' }} />
              </div>
            </div>
          </CyberPanel>

          {/* Bottom 3 sub-panels */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">

            {/* Neural Activity */}
            <CyberPanel className="flex flex-col gap-2">
              <SectionTitle label="NEURAL ACTIVITY" />
              <div className="relative h-28 overflow-hidden rounded border border-[rgba(0,255,255,0.15)] bg-black/60">
                <ParticleCanvas />
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  { l: 'NODES',    v: '847' },
                  { l: 'EDGES',    v: '2.1K' },
                  { l: 'ACTIVE',   v: '312' },
                  { l: 'LATENCY',  v: '14ms' },
                ].map(({ l, v }) => (
                  <div key={l} className="rounded bg-[rgba(0,255,255,0.04)] border border-[rgba(0,255,255,0.1)] px-2 py-1">
                    <p className="font-mono text-[9px] text-text-muted">{l}</p>
                    <p className="font-mono text-[11px] font-bold text-[#00ffff]">{v}</p>
                  </div>
                ))}
              </div>
            </CyberPanel>

            {/* Memory & Context */}
            <CyberPanel className="flex flex-col gap-2">
              <SectionTitle label="MEMORY & CONTEXT" />
              {/* Donut */}
              <div className="flex items-center justify-center py-2">
                <svg width={90} height={90} viewBox="0 0 90 90">
                  <circle cx={45} cy={45} r={34} fill="none" stroke="rgba(255,32,32,0.1)" strokeWidth={10} />
                  <circle cx={45} cy={45} r={34} fill="none" stroke="#ff2020" strokeWidth={10}
                    strokeDasharray={`${2 * Math.PI * 34 * 0.87} ${2 * Math.PI * 34 * 0.13}`}
                    strokeLinecap="round" transform="rotate(-90 45 45)"
                    style={{ filter: 'drop-shadow(0 0 4px #ff2020)' }} />
                  <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle"
                    fill="#ff2020" fontSize="14" fontFamily="Share Tech Mono,monospace" fontWeight="bold">87%</text>
                </svg>
              </div>
              <div className="space-y-1.5">
                {[
                  { l: 'Short-term',  v: '40 turns' },
                  { l: 'Long-term',   v: '847 facts' },
                  { l: 'Episodes',    v: '1,204' },
                  { l: 'Context Len', v: '128K tok' },
                ].map(({ l, v }) => (
                  <div key={l} className="flex justify-between">
                    <span className="font-mono text-[10px] text-text-muted">{l}</span>
                    <span className="font-mono text-[10px] text-text-secondary">{v}</span>
                  </div>
                ))}
              </div>
            </CyberPanel>

            {/* Learning Analytics */}
            <CyberPanel className="flex flex-col gap-2">
              <SectionTitle label="LEARNING ANALYTICS" />
              {/* Simple CSS bar chart */}
              <div className="flex h-24 items-end gap-1 px-1">
                {[40, 55, 45, 70, 60, 80, 90].map((h, i) => (
                  <div key={i} className="flex-1 rounded-t transition-all duration-700"
                    style={{
                      height: `${h}%`,
                      background: `linear-gradient(to top, #cc0000, #ff4444)`,
                      boxShadow: '0 0 4px rgba(255,32,32,0.4)',
                    }} />
                ))}
              </div>
              <p className="text-center font-mono text-[9px] text-text-muted">7-DAY LEARNING CURVE</p>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  { l: 'GROWTH',    v: '+12.4%' },
                  { l: 'ACCURACY',  v: '91.2%' },
                  { l: 'PATTERNS',  v: '3,847' },
                  { l: 'ADAPTIONS', v: '214' },
                ].map(({ l, v }) => (
                  <div key={l} className="rounded bg-[rgba(255,32,32,0.04)] border border-[rgba(255,32,32,0.1)] px-2 py-1">
                    <p className="font-mono text-[9px] text-text-muted">{l}</p>
                    <p className="font-mono text-[11px] font-bold text-[#ff2020]">{v}</p>
                  </div>
                ))}
              </div>
            </CyberPanel>
          </div>
        </div>

        {/* ══ RIGHT SIDEBAR ════════════════════════════════════════ */}
        <div className="space-y-4">

          {/* Live Terminal */}
          <CyberPanel>
            <SectionTitle label="LIVE TERMINAL FEED" live />
            <div
              ref={terminalRef}
              className="h-48 overflow-y-auto rounded bg-black/80 p-3 font-mono text-[10px] leading-relaxed"
            >
              {terminalLines.map((line, i) => (
                <div key={i} className="terminal-text animate-fade-in">{line}</div>
              ))}
              {terminalLines.length < TERMINAL_LINES.length && (
                <span className="terminal-text animate-blink">█</span>
              )}
            </div>
          </CyberPanel>

          {/* Tasks & Automation */}
          <CyberPanel>
            <SectionTitle label="TASKS & AUTOMATION" />
            <div className="space-y-2">
              {[
                { name: 'Deep Work Session',  status: 'ACTIVE',    time: fmtCountdown(countdown), color: '#00ff88' },
                { name: 'Email Batch',         status: 'QUEUED',    time: '09:30',                 color: '#f59e0b' },
                { name: 'System Backup',       status: 'SCHEDULED', time: '23:00',                 color: '#00ffff' },
                { name: 'Weekly Report',       status: 'PENDING',   time: 'FRI',                   color: '#ff2020' },
              ].map(({ name, status, time, color }) => (
                <div key={name} className="flex items-center justify-between gap-2 rounded border border-[rgba(255,32,32,0.1)] bg-[rgba(255,32,32,0.03)] px-2.5 py-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-[10px] text-text-secondary">{name}</p>
                    <p className="font-mono text-[9px] tabular-nums" style={{ color }}>{time}</p>
                  </div>
                  <span className="flex-shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px]"
                    style={{ color, background: `${color}18`, border: `1px solid ${color}40` }}>
                    {status}
                  </span>
                </div>
              ))}
            </div>
            <button className="mt-3 w-full rounded border border-[rgba(255,32,32,0.3)] py-1.5 font-mono text-[10px] tracking-widest text-[#ff2020] transition-all hover:bg-[rgba(255,32,32,0.08)]">
              VIEW ALL TASKS
            </button>
          </CyberPanel>

          {/* Now Playing */}
          <CyberPanel>
            <SectionTitle label="NOW PLAYING" />
            <div className="flex items-center gap-3 mb-3">
              <div className="h-10 w-10 flex-shrink-0 rounded bg-gradient-to-br from-[#cc0000] to-[#ff2020] flex items-center justify-center">
                <Music className="h-5 w-5 text-white" />
              </div>
              <div className="min-w-0">
                <p className="truncate font-mono text-[11px] text-text-primary">Blade Runner Blues</p>
                <p className="font-mono text-[10px] text-text-muted">Vangelis</p>
              </div>
            </div>
            {/* Waveform */}
            <div className="flex items-end justify-center gap-0.5 h-8 mb-2">
              {[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15].map((_, i) => (
                <div key={i} className="w-1 rounded-full bg-[#ff2020] origin-bottom"
                  style={{
                    height: `${30 + Math.random() * 70}%`,
                    animation: `waveBar ${0.7 + (i % 5) * 0.15}s ease-in-out ${i * 0.06}s infinite`,
                    boxShadow: '0 0 3px rgba(255,32,32,0.5)',
                  }} />
              ))}
            </div>
            {/* Progress */}
            <div className="mb-3 space-y-1">
              <div className="h-1 w-full overflow-hidden rounded-full bg-[rgba(255,32,32,0.1)]">
                <div className="h-full w-2/5 rounded-full bg-[#ff2020]" style={{ boxShadow: '0 0 6px rgba(255,32,32,0.6)' }} />
              </div>
              <div className="flex justify-between font-mono text-[9px] text-text-muted">
                <span>2:14</span><span>5:32</span>
              </div>
            </div>
            {/* Controls */}
            <div className="flex items-center justify-center gap-4">
              <button className="text-text-muted hover:text-[#ff2020] transition-colors"><SkipBack className="h-4 w-4" /></button>
              <button className="flex h-8 w-8 items-center justify-center rounded-full bg-[#ff2020] text-white hover:bg-[#ff4444] transition-colors shadow-[0_0_8px_rgba(255,32,32,0.5)]">
                <Play className="h-4 w-4 ml-0.5" />
              </button>
              <button className="text-text-muted hover:text-[#ff2020] transition-colors"><SkipForward className="h-4 w-4" /></button>
            </div>
          </CyberPanel>

          {/* Environment Status */}
          <CyberPanel>
            <SectionTitle label="ENVIRONMENT STATUS" />
            <div className="grid grid-cols-2 gap-2">
              {[
                { icon: HardDrive, label: 'DISK FREE', value: metrics ? `${(metrics.disk_total_gb - metrics.disk_used_gb).toFixed(0)} GB` : '—', color: '#00ff88' },
                { icon: Wifi,      label: 'NET RECV',  value: metrics ? `${(metrics.net_bytes_recv / 1024 / 1024).toFixed(0)} MB` : '—',          color: '#00ffff' },
                { icon: Battery,   label: 'SWAP',      value: metrics ? `${metrics.swap_pct}%` : '—',                                              color: '#f59e0b' },
                { icon: Volume2,   label: 'TEMP',      value: metrics?.temp_c != null ? `${metrics.temp_c}°C` : 'N/A',                             color: '#00ff88' },
              ].map(({ icon: Icon, label, value, color }) => (
                <div key={label} className="flex items-center gap-2 rounded border border-[rgba(255,32,32,0.12)] bg-[rgba(255,32,32,0.03)] p-2">
                  <Icon className="h-4 w-4 flex-shrink-0" style={{ color }} />
                  <div>
                    <p className="font-mono text-[9px] text-text-muted">{label}</p>
                    <p className="font-mono text-[11px] font-bold" style={{ color }}>{value}</p>
                  </div>
                </div>
              ))}
            </div>
            <button className="mt-3 w-full rounded border border-[rgba(0,255,255,0.3)] py-1.5 font-mono text-[10px] tracking-widest text-[#00ffff] transition-all hover:bg-[rgba(0,255,255,0.06)]">
              OPTIMIZE ENVIRONMENT
            </button>
          </CyberPanel>

          {/* Live Environment Monitor */}
          <CyberPanel>
            <SectionTitle label="LIVE ENVIRONMENT" live />
            <EnvironmentPanel data={environment} />
          </CyberPanel>
        </div>
      </div>

      {/* ── Bottom Bar ───────────────────────────────────────────── */}
      <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-[rgba(255,32,32,0.25)] bg-[rgba(10,10,10,0.97)] backdrop-blur-md lg:left-60">
        <div className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:gap-4">

          {/* Left — status */}
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="orb-glow h-7 w-7 flex-shrink-0" />
            <div>
              <p className="font-mono text-[10px] font-bold text-[#ff2020]">XYRON IS ACTIVE AND MONITORING</p>
              {/* Waveform */}
              <div className="flex items-end gap-0.5 h-3 mt-0.5">
                {[1,2,3,4,5,6,7,8].map((_, i) => (
                  <div key={i} className="w-0.5 rounded-full bg-[#ff2020] origin-bottom"
                    style={{ height: `${50 + (i % 4) * 15}%`, animation: `waveBar ${0.6 + i * 0.12}s ease-in-out ${i * 0.08}s infinite` }} />
                ))}
              </div>
            </div>
          </div>

          {/* Center — voice input */}
          <div className="flex flex-1 items-center gap-2 rounded border border-[rgba(255,32,32,0.3)] bg-[rgba(255,32,32,0.04)] px-3 py-2">
            <Mic className="h-4 w-4 flex-shrink-0 text-[#ff2020]" />
            <input
              value={voiceInput}
              onChange={e => setVoiceInput(e.target.value)}
              placeholder="TALK TO XYRON — type or speak a command..."
              className="flex-1 bg-transparent font-mono text-[11px] text-text-secondary placeholder:text-text-muted focus:outline-none"
            />
            <button className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-[#ff2020] text-white hover:bg-[#ff4444] transition-colors">
              <Send className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Right — Takeover */}
          <div className="flex-shrink-0">
            <button className="btn-takeover w-full rounded px-5 py-2.5 font-mono text-[11px] font-bold tracking-[0.15em] text-white sm:w-auto">
              ⚡ ACTIVATE TAKEOVER
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
