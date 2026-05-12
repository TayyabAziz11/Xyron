'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CinematicOrb } from './CinematicOrb'
import { NeuralCanvas } from './NeuralCanvas'
import { useEnvironment } from '@/hooks/useEnvironment'
import type { ImHomePhase, ImHomeData } from '@/hooks/useImHomeProtocol'

// ── CSS animations ─────────────────────────────────────────────────────────────

const ANIMS = `
@keyframes scanline {
  0%   { transform: translateY(-100vh); }
  100% { transform: translateY(100vh); }
}
@keyframes edgeGlowPulse {
  0%,100% { opacity: 0.5; }
  50%     { opacity: 1.0; }
}
@keyframes crtFlicker {
  0%,97%  { opacity: 1; }
  98%     { opacity: 0.88; }
  99%     { opacity: 1; }
  99.5%   { opacity: 0.93; }
  100%    { opacity: 1; }
}
@keyframes orbBreath {
  0%,100% { transform: scale(1); }
  50%     { transform: scale(1.04); }
}
@keyframes logAppear {
  from { opacity: 0; transform: translateX(-6px); }
  to   { opacity: 1; transform: translateX(0); }
}
@keyframes newsScroll {
  0%   { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
@keyframes glowText {
  0%,100% { text-shadow: 0 0 8px rgba(255,32,32,0.6); }
  50%     { text-shadow: 0 0 20px rgba(255,32,32,1), 0 0 40px rgba(255,32,32,0.4); }
}
@keyframes directivePulse {
  0%,100% { opacity: 1; letter-spacing: 0.3em; }
  50%     { opacity: 0.8; letter-spacing: 0.35em; }
}
@keyframes waveBar {
  0%,100% { transform: scaleY(0.3); }
  50%     { transform: scaleY(1.0); }
}
@keyframes neuralPulse {
  0%   { left: -25%; opacity: 0; }
  8%   { opacity: 1; }
  92%  { opacity: 1; }
  100% { left: 120%; opacity: 0; }
}
@keyframes blinkCursor {
  0%,49%  { opacity: 1; }
  50%,99% { opacity: 0; }
}
@keyframes headerScan {
  0%   { left: -15%; }
  100% { left: 115%; }
}
@keyframes pingRipple {
  0%   { transform: scale(1); opacity: 0.6; }
  100% { transform: scale(3.5); opacity: 0; }
}
@keyframes floatUpDown {
  0%,100% { transform: translateY(0); }
  50%     { transform: translateY(-5px); }
}
@keyframes progressReveal {
  from { width: 0; }
}
.im-no-scroll { scrollbar-width: none; -ms-overflow-style: none; }
.im-no-scroll::-webkit-scrollbar { display: none; }
`

function injectAnims() {
  if (typeof document === 'undefined' || document.getElementById('im-home-anims')) return
  const s = document.createElement('style')
  s.id = 'im-home-anims'
  s.textContent = ANIMS
  document.head.appendChild(s)
}

// ── Mouse parallax ─────────────────────────────────────────────────────────────

function useMouseParallax(strength = 5) {
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  useEffect(() => {
    const h = (e: MouseEvent) => {
      const cx = window.innerWidth / 2
      const cy = window.innerHeight / 2
      setOffset({ x: ((e.clientX - cx) / cx) * strength, y: ((e.clientY - cy) / cy) * strength })
    }
    window.addEventListener('mousemove', h, { passive: true })
    return () => window.removeEventListener('mousemove', h)
  }, [strength])
  return offset
}

// ── Primitives ─────────────────────────────────────────────────────────────────

function MeterBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between font-mono text-[8px]">
        <span style={{ color: 'rgba(255,255,255,0.58)' }}>{label}</span>
        <span style={{ color }}>{value}%</span>
      </div>
      <div className="h-[3px] rounded-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
        <div className="h-full rounded-full transition-all duration-1000" style={{ width: `${value}%`, background: color, boxShadow: `0 0 6px ${color}` }} />
      </div>
    </div>
  )
}

function CircGauge({ value, max = 100, label, color, size = 68 }: { value: number; max?: number; label: string; color: string; size?: number }) {
  const r      = size * 0.40
  const circ   = 2 * Math.PI * r
  const offset = circ * (1 - Math.min(value / max, 1))
  const cx     = size / 2
  return (
    <div className="flex flex-col items-center gap-0.5">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth={size * 0.1} />
        <circle
          cx={cx} cy={cx} r={r} fill="none"
          stroke={color} strokeWidth={size * 0.1}
          strokeDasharray={circ} strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(-90 ${cx} ${cx})`}
          style={{ transition: 'stroke-dashoffset 1.2s ease', filter: `drop-shadow(0 0 4px ${color})` }}
        />
        <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle"
          fill={color} fontSize={size * 0.21} fontFamily="'Share Tech Mono',monospace" fontWeight="bold">
          {value}
        </text>
        <text x="50%" y="70%" dominantBaseline="middle" textAnchor="middle"
          fill={color} fontSize={size * 0.13} fontFamily="monospace" opacity={0.6}>
          {max === 100 ? '%' : '°C'}
        </text>
      </svg>
      <span className="font-mono text-[7px] tracking-widest" style={{ color: 'rgba(255,255,255,0.28)' }}>{label}</span>
    </div>
  )
}

function WaveformBars({ active, color, count = 8 }: { active: boolean; color: string; count?: number }) {
  return (
    <div className="flex items-end gap-[2px]" style={{ height: 14 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          style={{
            width: 2, borderRadius: 1,
            height: active ? '68%' : '22%',
            background: color,
            boxShadow: `0 0 3px ${color}`,
            transformOrigin: 'bottom',
            animation: active ? `waveBar ${0.42 + (i % 5) * 0.09}s ease-in-out ${i * 0.05}s infinite` : undefined,
            transition: 'height 350ms ease',
          }}
        />
      ))}
    </div>
  )
}

// ── Structural wrappers ────────────────────────────────────────────────────────

function Panel({ children, className = '', style = {} }: { children: React.ReactNode; className?: string; style?: React.CSSProperties }) {
  return (
    <div className={`rounded-xl border backdrop-blur-md ${className}`}
      style={{ borderColor: 'rgba(255,32,32,0.32)', background: 'rgba(6,2,6,0.93)', ...style }}>
      {children}
    </div>
  )
}

function PanelHead({ label, live = true }: { label: string; live?: boolean }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'rgba(255,32,32,0.12)' }}>
      {live && <div className="h-1.5 w-1.5 rounded-full bg-[#ff2020]" style={{ boxShadow: '0 0 6px #ff2020', animation: 'edgeGlowPulse 1.5s infinite' }} />}
      <span className="font-mono text-[8px] tracking-[0.2em] text-white/62">{label}</span>
    </div>
  )
}

// ── Scanlines + EdgeGlow (unchanged) ──────────────────────────────────────────

function Scanlines() {
  return (
    <div className="pointer-events-none fixed inset-0" style={{ zIndex: 52 }}>
      <div style={{ position: 'absolute', left: 0, right: 0, height: 2, background: 'linear-gradient(transparent, rgba(255,32,32,0.05), transparent)', animation: 'scanline 5s linear infinite' }} />
      <div style={{ position: 'absolute', inset: 0, backgroundImage: 'repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.022) 3px, rgba(0,0,0,0.022) 4px)', pointerEvents: 'none' }} />
    </div>
  )
}

function EdgeGlow({ active }: { active: boolean }) {
  const s = active ? { animation: 'edgeGlowPulse 2.5s ease-in-out infinite' } : {}
  const b = 'pointer-events-none fixed'
  return (
    <>
      <div className={b} style={{ ...s, top: 0,    left: 0,  right: 0,   height: 2, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.8), transparent)', zIndex: 60 }} />
      <div className={b} style={{ ...s, bottom: 0, left: 0,  right: 0,   height: 2, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.8), transparent)', zIndex: 60 }} />
      <div className={b} style={{ ...s, left: 0,   top: 0,   bottom: 0,  width: 2,  background: 'linear-gradient(180deg, transparent, rgba(255,32,32,0.8), transparent)', zIndex: 60 }} />
      <div className={b} style={{ ...s, right: 0,  top: 0,   bottom: 0,  width: 2,  background: 'linear-gradient(180deg, transparent, rgba(255,32,32,0.8), transparent)', zIndex: 60 }} />
    </>
  )
}

// ── Enhanced Header ────────────────────────────────────────────────────────────

const PHASE_TITLES: Record<ImHomePhase, string> = {
  idle:     '',
  phase1:   'WELCOME BACK, TAYYAB',
  phase2:   'SYSTEM INITIALIZATION IN PROGRESS',
  phase3:   'AGENT NETWORK ACTIVATION SEQUENCE',
  phase4:   'ENVIRONMENTAL AWARENESS ONLINE',
  phase5:   'PRIMARY DIRECTIVE CONFIRMED',
  complete: 'COMMAND LINK ESTABLISHED',
}

function EnhancedHeader({ phase, onDismiss }: { phase: ImHomePhase; onDismiss: () => void }) {
  const [time, setTime] = useState('')
  const [date, setDate] = useState('')
  const [ping, setPing] = useState(12)

  useEffect(() => {
    const tick = () => {
      setTime(new Date().toLocaleTimeString('en-GB', { hour12: false }))
      setDate(new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase())
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const id = setInterval(() => setPing(7 + Math.floor(Math.random() * 14)), 3500)
    return () => clearInterval(id)
  }, [])

  return (
    <div
      className="absolute top-0 left-0 right-0 z-20 flex items-center px-4"
      style={{ height: 60, background: 'rgba(3,1,3,0.94)', borderBottom: '1px solid rgba(255,32,32,0.16)', backdropFilter: 'blur(16px)' }}
    >
      {/* Moving scan bar */}
      <div className="pointer-events-none absolute inset-y-0" style={{ width: 120, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.04), transparent)', animation: 'headerScan 4s linear infinite' }} />

      {/* Left — System identity */}
      <div className="flex flex-col justify-center" style={{ minWidth: 230 }}>
        <div className="flex items-center gap-2">
          <div className="relative">
            <div className="h-2 w-2 rounded-full bg-[#00ff88]" style={{ boxShadow: '0 0 8px #00ff88' }} />
            <div className="absolute inset-0 h-2 w-2 rounded-full bg-[#00ff88]" style={{ animation: 'pingRipple 2s ease-out infinite' }} />
          </div>
          <span className="font-mono text-[11px] font-black tracking-[0.2em] text-[#ff2020]" style={{ textShadow: '0 0 10px rgba(255,32,32,0.45)' }}>
            XYRON AI SYSTEM
          </span>
        </div>
        <span className="font-mono text-[7.5px] text-white/22 tracking-widest mt-0.5 ml-4">
          v2.0.0 OMEGA · ENV: KARACHI · OPERATOR: TAYYAB
        </span>
      </div>

      {/* Center — Dynamic title + neural pulse line */}
      <div className="flex flex-1 flex-col items-center gap-1.5 overflow-hidden px-4">
        <AnimatePresence mode="wait">
          <motion.span
            key={phase}
            className="font-mono text-[11px] font-black tracking-[0.22em] text-white/70 whitespace-nowrap"
            initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 5 }}
            transition={{ duration: 0.22 }}
          >
            {PHASE_TITLES[phase]}
          </motion.span>
        </AnimatePresence>
        <div className="relative w-full max-w-[420px] h-[2px] overflow-hidden rounded-full" style={{ background: 'rgba(255,32,32,0.1)' }}>
          <div className="absolute inset-y-0" style={{ width: 80, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.7), rgba(0,255,255,0.5), transparent)', animation: 'neuralPulse 2.2s linear infinite' }} />
        </div>
      </div>

      {/* Right — indicators + clock + dismiss */}
      <div className="flex items-center gap-4 justify-end" style={{ minWidth: 270 }}>
        <div className="flex items-center gap-1.5">
          <div className="h-1.5 w-1.5 rounded-full bg-[#00ff88]" style={{ boxShadow: '0 0 5px #00ff88' }} />
          <span className="font-mono text-[8px] text-[#00ff88]">{ping}ms</span>
        </div>
        <div className="relative">
          <svg width="15" height="16" viewBox="0 0 15 16" fill="none">
            <path d="M7.5 1a4.5 4.5 0 0 1 4.5 4.5V9l1 2H2l1-2V5.5A4.5 4.5 0 0 1 7.5 1z" stroke="rgba(255,255,255,0.3)" strokeWidth="1.1" />
            <circle cx="7.5" cy="13.5" r="1.2" fill="rgba(255,255,255,0.3)" />
          </svg>
          <div className="absolute -top-1 -right-1 h-3.5 w-3.5 rounded-full bg-[#ff2020] flex items-center justify-center font-mono text-[6px] font-black text-white" style={{ boxShadow: '0 0 6px rgba(255,32,32,0.6)' }}>3</div>
        </div>
        <div className="text-right">
          <p className="font-mono text-[18px] font-black tabular-nums leading-none" style={{ color: '#ff2020', textShadow: '0 0 12px rgba(255,32,32,0.55)' }}>{time}</p>
          <p className="font-mono text-[7px] text-white/22 tracking-widest">{date}</p>
        </div>
        <button onClick={onDismiss} className="font-mono text-[7px] tracking-[0.15em] text-white/20 hover:text-white/55 transition-colors px-2.5 py-1.5 rounded border border-white/8 hover:border-white/18">
          ESC
        </button>
      </div>
    </div>
  )
}

// ── System Init Terminal ───────────────────────────────────────────────────────

const LOG_LINES_COUNT = 13

function SystemInitTerminal({ logs }: { logs: string[] }) {
  const endRef  = useRef<HTMLDivElement>(null)
  const allDone = logs.length >= LOG_LINES_COUNT
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [logs.length])

  return (
    <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.4 }}>
      <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'rgba(255,32,32,0.32)', background: 'rgba(1,4,1,0.96)', boxShadow: '0 0 20px rgba(255,32,32,0.07), inset 0 0 24px rgba(0,0,0,0.6)' }}>
        <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'rgba(255,32,32,0.18)', background: 'rgba(255,32,32,0.03)' }}>
          <div className="flex gap-1.5">
            {['rgba(255,80,80,0.65)', 'rgba(255,200,0,0.45)', 'rgba(0,200,80,0.45)'].map((c, i) => (
              <div key={i} className="h-2 w-2 rounded-full" style={{ background: c }} />
            ))}
          </div>
          <span className="font-mono text-[7.5px] tracking-[0.18em] text-white/28 ml-1">XYRON TERMINAL v2.0</span>
          <div className="ml-auto h-1.5 w-1.5 rounded-full bg-[#ff2020]" style={{ animation: 'edgeGlowPulse 1.5s infinite', boxShadow: '0 0 6px #ff2020' }} />
        </div>
        <div className="p-3 font-mono text-[8.5px] leading-relaxed overflow-hidden" style={{ height: 196, background: 'rgba(0,3,0,0.92)' }}>
          {logs.map((line, i) => (
            <div key={i} style={{
              color:      line.startsWith('[OK]') ? '#00ff88' : line.startsWith('INIT') ? '#ff2020' : 'rgba(200,240,200,0.72)',
              textShadow: line.startsWith('[OK]') ? '0 0 8px rgba(0,255,136,0.45)' : line.startsWith('INIT') ? '0 0 8px rgba(255,32,32,0.45)' : undefined,
              animation:  'logAppear 0.18s ease both',
            }}>
              {line}
            </div>
          ))}
          {allDone && (
            <div style={{ color: '#00ff88', textShadow: '0 0 12px rgba(0,255,136,0.7)', animation: 'logAppear 0.3s ease 0.05s both', marginTop: 4 }}>
              ──────────────────────<br />ALL SYSTEMS ONLINE ✓
            </div>
          )}
          <span style={{ color: '#00ff88', animation: 'blinkCursor 1s step-end infinite', textShadow: '0 0 8px rgba(0,255,136,0.55)' }}>▊</span>
          <div ref={endRef} />
        </div>
      </div>
    </motion.div>
  )
}

// ── System Status Gauges ───────────────────────────────────────────────────────

function SystemStatusGauges({ data, batteryPct }: { data: ImHomeData; batteryPct: number | null }) {
  const s = data.system
  return (
    <motion.div className="mt-3" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.45 }}>
      <Panel>
        <PanelHead label="SYSTEM STATUS — LIVE" />
        <div className="p-3 space-y-3">
          <div className="flex items-center justify-between px-1">
            <CircGauge value={s.cpu_pct}          label="CPU"  color="#ff2020" size={66} />
            <CircGauge value={s.ram_pct}          label="RAM"  color="#00ffff" size={66} />
            <CircGauge value={batteryPct ?? 88}   label="BAT"  color="#00ff88" size={66} />
          </div>
          <div className="space-y-1.5">
            <MeterBar label="CPU LOAD" value={s.cpu_pct} color="#ff2020" />
            <MeterBar label="MEMORY"   value={s.ram_pct} color="#00ffff" />
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {[
              { l: 'UPTIME',    v: s.uptime_str,                          c: '#00ff88' },
              { l: 'PROCS',     v: `${s.process_count}`,                  c: '#f59e0b' },
              { l: 'RAM',       v: `${s.ram_used_gb}/${s.ram_total_gb}G`, c: '#00ffff' },
              { l: 'STABILITY', v: '99.8%',                               c: '#00ff88' },
            ].map(({ l, v, c }) => (
              <div key={l} className="rounded p-1.5" style={{ background: 'rgba(255,32,32,0.04)', border: '1px solid rgba(255,32,32,0.1)' }}>
                <p className="font-mono text-[7px] text-white/22">{l}</p>
                <p className="font-mono text-[9px] font-bold" style={{ color: c }}>{v}</p>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </motion.div>
  )
}

// ── Weather Panel ──────────────────────────────────────────────────────────────

function WeatherPanel({ data }: { data: ImHomeData }) {
  const w = data.weather
  const ICONS: [string, string][] = [
    ['clear','☀️'],['sunny','☀️'],['mainly clear','🌤️'],['partly cloudy','⛅'],
    ['overcast','☁️'],['fog','🌫️'],['haze','🌫️'],['mist','🌫️'],
    ['rain','🌧️'],['drizzle','🌦️'],['snow','❄️'],['thunder','⛈️'],['shower','🌦️'],
  ]
  const icon = ICONS.find(([k]) => w.description?.toLowerCase().includes(k))?.[1] ?? '🌡️'
  return (
    <motion.div className="mt-3" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.45, delay: 0.1 }}>
      <Panel>
        <PanelHead label="KARACHI — LIVE ENVIRONMENT" />
        <div className="p-3">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-3xl" style={{ animation: 'floatUpDown 3s ease-in-out infinite' }}>{icon}</span>
            <div className="flex-1">
              <p className="font-mono text-xl font-black leading-none" style={{ color: '#ff2020', textShadow: '0 0 10px rgba(255,32,32,0.45)' }}>
                {w.temp_c != null ? `${Math.round(w.temp_c)}°C` : '--°C'}
              </p>
              <p className="font-mono text-[8px] text-white/32 mt-0.5 capitalize">{w.description ?? 'unavailable'}</p>
            </div>
            <div className="text-right">
              <p className="font-mono text-[7px] text-white/22">KARACHI</p>
              <p className="font-mono text-[8px] font-bold text-[#00ff88]">ONLINE</p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            {[
              { l: 'HUMIDITY', v: w.humidity != null ? `${w.humidity}%`     : '--' },
              { l: 'WIND',     v: w.wind_kmh != null ? `${w.wind_kmh} km/h` : '--' },
              { l: 'RAIN %',   v: w.rain_pct != null ? `${w.rain_pct}%`     : '--' },
              { l: 'AQI',      v: 'MODERATE' },
            ].map(({ l, v }) => (
              <div key={l} className="rounded p-1.5" style={{ background: 'rgba(0,255,136,0.03)', border: '1px solid rgba(0,255,136,0.1)' }}>
                <p className="font-mono text-[7px] text-white/22">{l}</p>
                <p className="font-mono text-[9px] font-bold text-white/60">{v}</p>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </motion.div>
  )
}

// ── Agent Network Panel ────────────────────────────────────────────────────────

const AGENTS: { name: string; task: string; cpu: number; state: 'ACTIVE' | 'IDLE' | 'STANDBY'; color: string }[] = [
  { name: 'MEMORY AGENT',     task: 'indexing conversation history',   cpu: 4,  state: 'ACTIVE',  color: '#ff2020' },
  { name: 'DEV AGENT',        task: 'monitoring repository changes',   cpu: 2,  state: 'ACTIVE',  color: '#00ffff' },
  { name: 'VISION AGENT',     task: 'screen analysis standby',         cpu: 0,  state: 'STANDBY', color: '#a78bfa' },
  { name: 'AUTOMATION AGENT', task: 'awaiting task queue',             cpu: 1,  state: 'IDLE',    color: '#00ff88' },
  { name: 'SCHEDULER AGENT',  task: 'tracking active deadlines',       cpu: 3,  state: 'ACTIVE',  color: '#f59e0b' },
  { name: 'NEWS AGENT',       task: 'AI feeds synchronized',           cpu: 1,  state: 'ACTIVE',  color: '#ec4899' },
]

function AgentNetworkPanel() {
  return (
    <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: 0.4 }}>
      <Panel>
        <PanelHead label="ACTIVE AGENT NETWORK" />
        <div className="p-2 space-y-1.5">
          {AGENTS.map((a, i) => (
            <motion.div
              key={a.name}
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.1, duration: 0.3 }}
              className="rounded-lg border p-2"
              style={{ borderColor: `${a.color}25`, background: `${a.color}06` }}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-1.5">
                  <div className="h-1.5 w-1.5 rounded-full flex-shrink-0"
                    style={{ background: a.color, boxShadow: `0 0 5px ${a.color}`, animation: a.state === 'ACTIVE' ? 'edgeGlowPulse 1.4s infinite' : undefined }} />
                  <span className="font-mono text-[8.5px] font-bold" style={{ color: a.color }}>{a.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <WaveformBars active={a.state === 'ACTIVE'} color={a.color} count={6} />
                  <span className="font-mono text-[6.5px] px-1 py-0.5 rounded flex-shrink-0"
                    style={{ color: a.color, background: `${a.color}14`, border: `1px solid ${a.color}26` }}>
                    {a.state}
                  </span>
                </div>
              </div>
              <p className="font-mono text-[7.5px] text-white/52 mb-1">{a.task}</p>
              <div className="flex items-center gap-1.5">
                <div className="flex-1 h-[2px] rounded-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
                  <div className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${Math.max(a.cpu * 10, 4)}%`, background: a.color, boxShadow: `0 0 4px ${a.color}` }} />
                </div>
                <span className="font-mono text-[7px] text-white/22">CPU {a.cpu}%</span>
              </div>
            </motion.div>
          ))}
        </div>
      </Panel>
    </motion.div>
  )
}

// ── AI News Feed ──────────────────────────────────────────────────────────────

const NEWS_SOURCES = [
  { label: 'OPENAI',    color: '#00ff88' },
  { label: 'ANTHROPIC', color: '#a78bfa' },
  { label: 'GOOGLE',    color: '#00ffff' },
  { label: 'MISTRAL',   color: '#f59e0b' },
  { label: 'META AI',   color: '#ec4899' },
  { label: 'GITHUB',    color: '#ff2020' },
]

const TICKER_FALLBACK: ImHomeData['news'] = [
  { title: 'GPT-4o receives new real-time voice capabilities update',          url: '', points: 842 },
  { title: 'Anthropic releases Claude with extended context window',            url: '', points: 631 },
  { title: 'Google DeepMind Gemini 2.0 benchmark results exceed expectations', url: '', points: 589 },
  { title: 'Open-source LLM Mistral Large achieves GPT-4 parity on coding',   url: '', points: 473 },
  { title: 'Meta releases Llama 3.1 — outperforms closed models on reasoning', url: '', points: 391 },
  { title: 'GitHub Copilot gains autonomous agent coding capabilities',         url: '', points: 358 },
]

function AINewsFeed({ data }: { data: ImHomeData }) {
  const feed = data.news.length > 0 ? data.news : TICKER_FALLBACK
  return (
    <motion.div className="mt-3" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.4, delay: 0.12 }}>
      <Panel>
        <PanelHead label="AI INTELLIGENCE FEED" />
        <div className="p-2 space-y-1.5">
          {feed.slice(0, 5).map((item, i) => {
            const src = NEWS_SOURCES[i % NEWS_SOURCES.length]
            return (
              <motion.div key={i} className="flex gap-2 items-start" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.1 }}>
                <span className="flex-shrink-0 font-mono text-[6.5px] px-1.5 py-0.5 rounded mt-0.5"
                  style={{ color: src.color, background: `${src.color}10`, border: `1px solid ${src.color}22` }}>
                  {src.label}
                </span>
                <div className="min-w-0">
                  <p className="font-mono text-[7.5px] text-white/70 leading-tight">{item.title.slice(0, 68)}</p>
                  {item.points > 0 && <p className="font-mono text-[6.5px] text-white/18 mt-0.5">▲{item.points}</p>}
                </div>
              </motion.div>
            )
          })}
        </div>
      </Panel>
    </motion.div>
  )
}

// ── Personal Context Panel ─────────────────────────────────────────────────────

function PersonalContextPanel() {
  return (
    <motion.div className="mt-3" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.4, delay: 0.22 }}>
      <Panel>
        <PanelHead label="OPERATOR INTELLIGENCE" />
        <div className="p-2.5 space-y-1">
          {[
            { l: 'PRODUCTIVITY THIS WEEK', v: '+18%',    c: '#00ff88' },
            { l: 'CODING STREAK',          v: '6 DAYS',  c: '#00ffff' },
            { l: 'ACTIVE OBJECTIVES',      v: '3 PEND',  c: '#f59e0b' },
            { l: 'VOICE LATENCY',          v: '240ms',   c: '#00ff88' },
            { l: 'MEMORY EFFICIENCY',      v: '91.2%',   c: '#a78bfa' },
            { l: 'LAST SESSION',           v: '2h 14m',  c: '#ff2020' },
          ].map(({ l, v, c }) => (
            <div key={l} className="flex justify-between items-center py-0.5" style={{ borderBottom: '1px solid rgba(255,32,32,0.06)' }}>
              <span className="font-mono text-[7.5px] text-white/45">{l}</span>
              <span className="font-mono text-[8px] font-bold" style={{ color: c }}>{v}</span>
            </div>
          ))}
        </div>
      </Panel>
    </motion.div>
  )
}

// ── Objectives Panel ───────────────────────────────────────────────────────────

const OBJECTIVES = [
  { label: 'OPTIMIZE VOICE LATENCY',      pct: 75, color: '#00ff88', priority: 'HIGH' },
  { label: 'EXPAND MEMORY ORCHESTRATION', pct: 45, color: '#00ffff', priority: 'MED'  },
  { label: 'REFACTOR AGENT PLANNER',      pct: 30, color: '#f59e0b', priority: 'MED'  },
  { label: 'VISION PIPELINE INTEGRATION', pct: 18, color: '#a78bfa', priority: 'LOW'  },
]

function ObjectivesPanel() {
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.5, delay: 0.2 }}>
      <Panel>
        <PanelHead label="ACTIVE OBJECTIVES" />
        <div className="p-2.5 space-y-2.5">
          {OBJECTIVES.map(({ label, pct, color, priority }) => (
            <div key={label}>
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-[7.5px] text-white/68">{label}</span>
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[6px] px-1 py-0.5 rounded" style={{ color, background: `${color}14`, border: `1px solid ${color}24` }}>{priority}</span>
                  <span className="font-mono text-[8px] font-bold" style={{ color }}>{pct}%</span>
                </div>
              </div>
              <div className="h-[3px] rounded-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
                <motion.div
                  className="h-full rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 1.2, delay: 0.3, ease: 'easeOut' }}
                  style={{ background: color, boxShadow: `0 0 6px ${color}` }}
                />
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </motion.div>
  )
}

// ── Enhanced News Ticker ───────────────────────────────────────────────────────

function EnhancedTicker({ data }: { data: ImHomeData }) {
  const feed  = data.news.length > 0 ? data.news : TICKER_FALLBACK
  const items = [...feed, ...feed] // 2 copies → seamless -50% loop
  return (
    <motion.div
      className="absolute bottom-0 left-0 right-0 z-10 overflow-hidden"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="border-t" style={{ borderColor: 'rgba(255,32,32,0.18)', background: 'rgba(3,1,3,0.94)', backdropFilter: 'blur(8px)', height: 50 }}>
        <div className="flex items-center h-full overflow-hidden">
          <div className="flex gap-10 whitespace-nowrap" style={{ animation: 'newsScroll 55s linear infinite' }}>
            {items.map((n, i) => {
              const src = NEWS_SOURCES[i % NEWS_SOURCES.length]
              return (
                <span key={i} className="font-mono text-[8.5px] text-white/42 flex-shrink-0 flex items-center gap-2">
                  <span className="px-1.5 py-0.5 rounded font-bold text-[7px]" style={{ color: src.color, background: `${src.color}10`, border: `1px solid ${src.color}20` }}>
                    {src.label}
                  </span>
                  {n.title.slice(0, 82)}
                  {n.points > 0 && <span className="text-white/20 ml-1">▲{n.points}</span>}
                  <span className="text-white/12 mx-2">◆</span>
                </span>
              )
            })}
          </div>
        </div>
      </div>
    </motion.div>
  )
}

// ── Voice Command Strip ────────────────────────────────────────────────────────

const THOUGHT_STATES = ['LISTENING', 'PROCESSING', 'READY', 'ANALYZING', 'SYNTHESIZING']

function VoiceCommandStrip({ phase }: { phase: ImHomePhase }) {
  const [thoughtIdx, setThoughtIdx] = useState(2)
  const [confidence, setConfidence] = useState(94)
  const isActive = phase !== 'idle' && phase !== 'phase1'

  useEffect(() => {
    const id = setInterval(() => {
      setThoughtIdx(i => (i + 1) % THOUGHT_STATES.length)
      setConfidence(88 + Math.floor(Math.random() * 10))
    }, 3200)
    return () => clearInterval(id)
  }, [])

  return (
    <motion.div
      className="absolute left-0 right-0 z-10 flex items-center px-5 gap-6"
      style={{ bottom: 50, height: 52, background: 'rgba(3,1,3,0.92)', borderTop: '1px solid rgba(255,32,32,0.16)', backdropFilter: 'blur(12px)' }}
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.35, delay: 0.15 }}
    >
      {/* Mic orb + waveform */}
      <div className="flex items-center gap-3 flex-shrink-0">
        <div className="relative">
          <div className="h-8 w-8 rounded-full flex items-center justify-center" style={{ background: 'radial-gradient(circle, rgba(255,32,32,0.28), rgba(255,32,32,0.04))', border: '1px solid rgba(255,32,32,0.45)', boxShadow: '0 0 14px rgba(255,32,32,0.25)', animation: 'orbBreath 2.2s ease-in-out infinite' }}>
            <svg width="11" height="12" viewBox="0 0 11 12" fill="none">
              <rect x="3" y="0.5" width="5" height="6.5" rx="2.5" stroke="#ff2020" strokeWidth="1.1" />
              <path d="M1 6.5c0 2.5 9 2.5 9 0" stroke="#ff2020" strokeWidth="1.1" strokeLinecap="round" />
              <line x1="5.5" y1="9" x2="5.5" y2="11" stroke="#ff2020" strokeWidth="1.1" strokeLinecap="round" />
            </svg>
          </div>
          <div className="absolute inset-0 h-8 w-8 rounded-full" style={{ border: '1px solid rgba(255,32,32,0.3)', animation: 'pingRipple 2.2s ease-out infinite' }} />
        </div>
        <WaveformBars active={isActive} color="#ff2020" count={14} />
      </div>

      {/* Thought state */}
      <div className="flex-1 flex flex-col items-center gap-1">
        <div className="flex items-center gap-4">
          <AnimatePresence mode="wait">
            <motion.span
              key={thoughtIdx}
              className="font-mono text-[10px] font-black tracking-[0.25em]"
              style={{ color: '#ff2020', textShadow: '0 0 8px rgba(255,32,32,0.55)' }}
              initial={{ opacity: 0, y: -3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 3 }}
              transition={{ duration: 0.18 }}
            >
              {THOUGHT_STATES[thoughtIdx]}
            </motion.span>
          </AnimatePresence>
          <span className="font-mono text-[8px] text-white/28">
            CONFIDENCE <span style={{ color: '#00ff88' }}>{confidence}%</span>
          </span>
          <span className="font-mono text-[8px] text-white/28">
            LATENCY <span style={{ color: '#00ffff' }}>240ms</span>
          </span>
          <span className="font-mono text-[8px] text-white/28">
            LOAD <span style={{ color: '#f59e0b' }}>NOMINAL</span>
          </span>
        </div>
        <div className="relative h-[2px] w-full max-w-[220px] overflow-hidden rounded-full" style={{ background: 'rgba(255,32,32,0.12)' }}>
          <div className="absolute inset-y-0" style={{ width: 55, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.65), transparent)', animation: 'neuralPulse 1.8s linear infinite' }} />
        </div>
      </div>

      {/* Takeover */}
      <div className="flex-shrink-0">
        <button
          className="font-mono text-[9.5px] font-black tracking-[0.12em] px-5 py-2 rounded-lg transition-all hover:brightness-125"
          style={{ background: 'linear-gradient(135deg, rgba(255,32,32,0.22), rgba(255,32,32,0.08))', border: '1px solid rgba(255,32,32,0.45)', color: '#ff2020', boxShadow: '0 0 16px rgba(255,32,32,0.18)', animation: 'edgeGlowPulse 2.5s ease-in-out infinite' }}
        >
          ⚡ ACTIVATE TAKEOVER
        </button>
      </div>
    </motion.div>
  )
}

// ── Orb Command Frame — fills the center space around the orb ────────────────

const PHASE_ORDER: ImHomePhase[] = ['phase1', 'phase2', 'phase3', 'phase4', 'phase5', 'complete']

function OrbCommandFrame({ phase, data }: { phase: ImHomePhase; data: ImHomeData }) {
  const isActive = phase !== 'idle'
  const s        = data.system
  const cur      = PHASE_ORDER.indexOf(phase)

  const LEFT_STATS  = [
    { l: 'NEURAL SYNC', v: '99.2%',         c: '#00ff88' },
    { l: 'RESPONSE',    v: '240ms',          c: '#00ffff' },
    { l: 'CPU LOAD',    v: `${s.cpu_pct}%`,  c: '#ff2020' },
    { l: 'OPERATOR',    v: 'TAYYAB',         c: '#a78bfa' },
  ]
  const RIGHT_STATS = [
    { l: 'MEMORY',    v: `${s.ram_pct}%`, c: '#00ffff' },
    { l: 'EMOTION',   v: 'FOCUSED',       c: '#a78bfa' },
    { l: 'AGENTS',    v: '6 ONLINE',      c: '#00ff88' },
    { l: 'STABILITY', v: '99.8%',         c: '#00ff88' },
  ]

  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="relative" style={{ width: 720, height: 580 }}>

        {/* Corner targeting brackets */}
        {[
          { style: { top: 0,    left: 0,  borderTop:    '1.5px solid rgba(255,32,32,0.6)', borderLeft:   '1.5px solid rgba(255,32,32,0.6)' } },
          { style: { top: 0,    right: 0, borderTop:    '1.5px solid rgba(255,32,32,0.6)', borderRight:  '1.5px solid rgba(255,32,32,0.6)' } },
          { style: { bottom: 0, left: 0,  borderBottom: '1.5px solid rgba(255,32,32,0.6)', borderLeft:   '1.5px solid rgba(255,32,32,0.6)' } },
          { style: { bottom: 0, right: 0, borderBottom: '1.5px solid rgba(255,32,32,0.6)', borderRight:  '1.5px solid rgba(255,32,32,0.6)' } },
        ].map((b, i) => (
          <motion.div key={i} className="absolute" style={{ width: 32, height: 32, ...b.style }}
            initial={{ opacity: 0, scale: 1.4 }} animate={{ opacity: isActive ? 1 : 0, scale: 1 }}
            transition={{ duration: 0.35, delay: i * 0.06 }} />
        ))}

        {/* Phase progress track */}
        <motion.div
          className="absolute top-3 left-1/2 -translate-x-1/2 flex flex-col items-center gap-1"
          initial={{ opacity: 0, y: -8 }} animate={{ opacity: isActive ? 1 : 0, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex items-center">
            {PHASE_ORDER.slice(0, 5).map((_, i) => {
              const isOn  = cur >= i
              const isCur = cur === i
              return (
                <div key={i} className="flex items-center">
                  <div className="transition-all duration-500 rounded-full" style={{
                    width: isCur ? 9 : 6, height: isCur ? 9 : 6,
                    background: isOn ? '#ff2020' : 'rgba(255,32,32,0.18)',
                    boxShadow:  isCur ? '0 0 12px #ff2020, 0 0 24px rgba(255,32,32,0.4)' : isOn ? '0 0 5px #ff2020' : undefined,
                  }} />
                  {i < 4 && <div className="w-10 h-px transition-all duration-700" style={{ background: cur > i ? 'rgba(255,32,32,0.6)' : 'rgba(255,32,32,0.12)' }} />}
                </div>
              )
            })}
          </div>
          <p className="font-mono text-[7.5px] text-white/40 tracking-[0.25em]">
            {cur >= 0 ? `SEQUENCE  ${cur + 1} / ${PHASE_ORDER.length - 1}` : ''}
          </p>
        </motion.div>

        {/* Left stat panels */}
        <AnimatePresence>
          {isActive && (
            <motion.div className="absolute flex flex-col gap-2"
              style={{ left: 8, top: '50%', transform: 'translateY(-50%)', width: 142 }}
              initial={{ opacity: 0, x: -18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }}
              transition={{ duration: 0.35, delay: 0.07 }}
            >
              {LEFT_STATS.map(({ l, v, c }, i) => (
                <motion.div key={l} className="rounded-lg px-2.5 py-2 border"
                  style={{ background: 'rgba(5,1,5,0.92)', borderColor: `${c}35`, backdropFilter: 'blur(10px)', boxShadow: `0 0 14px ${c}12` }}
                  initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.07 + i * 0.06, duration: 0.28 }}
                >
                  <p className="font-mono text-[7px] text-white/48">{l}</p>
                  <p className="font-mono text-[13px] font-black leading-tight" style={{ color: c, textShadow: `0 0 12px ${c}65` }}>{v}</p>
                </motion.div>
              ))}
              {/* Connector dot + line */}
              <div className="absolute right-0 top-1/2 -translate-y-1/2 flex items-center">
                <div style={{ width: 38, height: 1, background: 'linear-gradient(90deg, transparent, rgba(255,32,32,0.45))' }} />
                <div style={{ width: 5, height: 5, borderRadius: '50%', background: '#ff2020', boxShadow: '0 0 8px #ff2020' }} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Right stat panels */}
        <AnimatePresence>
          {isActive && (
            <motion.div className="absolute flex flex-col gap-2"
              style={{ right: 8, top: '50%', transform: 'translateY(-50%)', width: 142 }}
              initial={{ opacity: 0, x: 18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 12 }}
              transition={{ duration: 0.35, delay: 0.1 }}
            >
              {/* Connector dot + line */}
              <div className="absolute left-0 top-1/2 -translate-y-1/2 flex items-center">
                <div style={{ width: 5, height: 5, borderRadius: '50%', background: '#ff2020', boxShadow: '0 0 8px #ff2020' }} />
                <div style={{ width: 38, height: 1, background: 'linear-gradient(90deg, rgba(255,32,32,0.45), transparent)' }} />
              </div>
              {RIGHT_STATS.map(({ l, v, c }, i) => (
                <motion.div key={l} className="rounded-lg px-2.5 py-2 border"
                  style={{ background: 'rgba(5,1,5,0.92)', borderColor: `${c}35`, backdropFilter: 'blur(10px)', boxShadow: `0 0 14px ${c}12` }}
                  initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.1 + i * 0.06, duration: 0.28 }}
                >
                  <p className="font-mono text-[7px] text-white/48">{l}</p>
                  <p className="font-mono text-[13px] font-black leading-tight" style={{ color: c, textShadow: `0 0 12px ${c}65` }}>{v}</p>
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Bottom mode strip */}
        <AnimatePresence>
          {isActive && (
            <motion.div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.35, delay: 0.15 }}
            >
              {[
                { l: 'MODE',   v: 'AUTONOMOUS',        c: '#ff2020' },
                { l: 'UPTIME', v: s.uptime_str,        c: '#00ff88' },
                { l: 'PROCS',  v: `${s.process_count}`, c: '#00ffff' },
              ].map(({ l, v, c }, i, arr) => (
                <div key={l} className="flex items-center gap-1.5">
                  <span className="font-mono text-[7px] text-white/40">{l}</span>
                  <span className="font-mono text-[9px] font-bold" style={{ color: c, textShadow: `0 0 6px ${c}55` }}>{v}</span>
                  {i < arr.length - 1 && <span className="text-white/18 ml-1">·</span>}
                </div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Radial tick marks + N/E/S/W cardinal points */}
        <AnimatePresence>
          {isActive && (
            <motion.svg className="absolute pointer-events-none"
              style={{ left: '50%', top: '50%', transform: 'translate(-50%, -50%)', overflow: 'visible' }}
              width={440} height={440} viewBox="-220 -220 440 440"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.55, delay: 0.05 }}
            >
              {Array.from({ length: 36 }).map((_, i) => {
                const angle = (i / 36) * Math.PI * 2
                const isMaj = i % 9 === 0, isMed = i % 3 === 0
                const r1 = 174, r2 = isMaj ? 196 : isMed ? 186 : 180
                const x1 = Math.cos(angle) * r1, y1 = Math.sin(angle) * r1
                const x2 = Math.cos(angle) * r2, y2 = Math.sin(angle) * r2
                return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke={`rgba(255,32,32,${isMaj ? 0.7 : isMed ? 0.45 : 0.24})`}
                  strokeWidth={isMaj ? 1.6 : isMed ? 1.0 : 0.7} />
              })}
              <circle cx={0} cy={0} r={202} fill="none" stroke="rgba(255,32,32,0.14)" strokeWidth={0.8} strokeDasharray="5,9" />
              {[0, Math.PI / 2, Math.PI, 3 * Math.PI / 2].map((a, i) => (
                <line key={i}
                  x1={Math.cos(a) * 200} y1={Math.sin(a) * 200}
                  x2={Math.cos(a) * 232} y2={Math.sin(a) * 232}
                  stroke="rgba(0,255,255,0.45)" strokeWidth={1.3} />
              ))}
              {(['N', 'E', 'S', 'W'] as const).map((lbl, i) => {
                const a = i * Math.PI / 2
                const x = Math.cos(a) * 246, y = Math.sin(a) * 246 + 3
                return <text key={lbl} x={x} y={y} textAnchor="middle" dominantBaseline="middle"
                  fill="rgba(0,255,255,0.50)" fontSize="9" fontFamily="monospace" fontWeight="bold">{lbl}</text>
              })}
            </motion.svg>
          )}
        </AnimatePresence>

      </div>
    </div>
  )
}

// ── Phase 1 ────────────────────────────────────────────────────────────────────

function Phase1() {
  return (
    <motion.div key="phase1" className="flex flex-col items-center justify-center h-full gap-6"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.6 }}>
      <motion.p className="font-mono text-[10px] tracking-[0.4em] text-white/30"
        animate={{ opacity: [0.3, 0.8, 0.3] }} transition={{ duration: 2, repeat: Infinity }}>
        PRESENCE DETECTED
      </motion.p>
    </motion.div>
  )
}

// ── Phase 5 Directive ──────────────────────────────────────────────────────────

const DIRECTIVES = [
  'PRIMARY DIRECTIVE: XYRON EVOLUTION',
  'AUTONOMOUS EXPANSION CONTINUES',
  'WE BUILD FURTHER TODAY',
]

function Phase5Directive() {
  return (
    <motion.div key="directive" className="flex flex-col items-center gap-4 z-10"
      initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
      transition={{ duration: 0.6 }}>
      {DIRECTIVES.map((line, i) => (
        <motion.p
          key={line}
          className="font-mono font-black tracking-[0.3em] text-center"
          style={{
            fontSize:   i === 0 ? 14 : i === 1 ? 12 : 11,
            color:      i === 0 ? '#ff2020' : i === 1 ? '#00ffff' : 'rgba(255,255,255,0.5)',
            textShadow: i === 0 ? '0 0 20px rgba(255,32,32,0.8), 0 0 40px rgba(255,32,32,0.3)' : undefined,
            animation:  i === 0 ? 'directivePulse 2s ease-in-out infinite' : undefined,
          }}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.4, duration: 0.5 }}
        >
          {line}
        </motion.p>
      ))}
    </motion.div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface Props {
  phase:     ImHomePhase
  data:      ImHomeData
  logs:      string[]
  onDismiss: () => void
}

export default function ImHomeProtocol({ phase, data, logs, onDismiss }: Props) {
  const isVisible = phase !== 'idle'
  const env       = useEnvironment()
  const parallax  = useMouseParallax(5)

  useEffect(() => { injectAnims() }, [])

  useEffect(() => {
    if (!isVisible) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onDismiss() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [isVisible, onDismiss])

  const showTerminal   = ['phase2', 'phase3'].includes(phase)
  const showGauges     = ['phase4', 'phase5', 'complete'].includes(phase)
  const showWeather    = ['phase4', 'phase5', 'complete'].includes(phase)
  const showAgents     = ['phase3', 'phase4', 'phase5', 'complete'].includes(phase)
  const showNews       = ['phase4', 'phase5', 'complete'].includes(phase)
  const showPersonal   = ['phase4', 'phase5', 'complete'].includes(phase)
  const showObjectives = ['phase4', 'phase5', 'complete'].includes(phase)
  const showBottom     = ['phase4', 'phase5', 'complete'].includes(phase)
  const isPhase5       = phase === 'phase5' || phase === 'complete'

  // Bottom zone heights: ticker 50px + voice strip 52px + gap 6px = 108px
  const colBottom = showBottom ? 108 : 12

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          key="im-home-overlay"
          className="fixed inset-0"
          style={{ zIndex: 50, animation: 'crtFlicker 10s infinite' }}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.5 }}
        >
          {/* Deep background */}
          <div className="absolute inset-0" style={{ background: 'radial-gradient(ellipse at center, #080205 0%, #030103 60%, #000 100%)' }} />

          {/* Neural canvas */}
          <NeuralCanvas phase={phase} />

          {/* Holographic grid */}
          <div className="absolute inset-0 pointer-events-none" style={{
            backgroundImage: 'linear-gradient(rgba(255,32,32,0.022) 1px, transparent 1px), linear-gradient(90deg, rgba(255,32,32,0.022) 1px, transparent 1px)',
            backgroundSize: '52px 52px', zIndex: 51,
          }} />

          <Scanlines />
          <EdgeGlow active={isVisible} />

          {/* CRT vignette */}
          <div className="absolute inset-0 pointer-events-none" style={{ background: 'radial-gradient(ellipse at center, transparent 38%, rgba(0,0,0,0.75) 100%)', zIndex: 53 }} />

          {/* UI layer */}
          <div className="absolute inset-0" style={{ zIndex: 54 }}>

            <EnhancedHeader phase={phase} onDismiss={onDismiss} />

            {/* CENTER: Orb */}
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-6 pointer-events-none">
              <CinematicOrb phase={phase} />
              <AnimatePresence mode="wait">
                {isPhase5           && <Phase5Directive />}
                {phase === 'phase1' && <Phase1 />}
              </AnimatePresence>
            </div>

            {/* CENTER: Command frame around orb */}
            <OrbCommandFrame phase={phase} data={data} />

            {/* LEFT COLUMN */}
            <div
              className="absolute overflow-y-auto im-no-scroll"
              style={{
                top: 68, left: 12, width: 258,
                bottom: colBottom,
                transform: `translate(${parallax.x * -0.35}px, ${parallax.y * -0.18}px)`,
                transition: 'transform 0.12s ease',
              }}
            >
              <AnimatePresence>{showTerminal && <SystemInitTerminal logs={logs} />}</AnimatePresence>
              <AnimatePresence>{showGauges   && <SystemStatusGauges data={data} batteryPct={env?.battery_percent ?? null} />}</AnimatePresence>
              <AnimatePresence>{showWeather  && <WeatherPanel data={data} />}</AnimatePresence>
            </div>

            {/* RIGHT COLUMN */}
            <div
              className="absolute overflow-y-auto im-no-scroll"
              style={{
                top: 68, right: 12, width: 258,
                bottom: colBottom,
                transform: `translate(${parallax.x * 0.35}px, ${parallax.y * -0.18}px)`,
                transition: 'transform 0.12s ease',
              }}
            >
              <AnimatePresence>{showAgents   && <AgentNetworkPanel />}</AnimatePresence>
              <AnimatePresence>{showNews     && <AINewsFeed data={data} />}</AnimatePresence>
              <AnimatePresence>{showPersonal && <PersonalContextPanel />}</AnimatePresence>
            </div>

            {/* BOTTOM CENTER: Objectives */}
            <AnimatePresence>
              {showObjectives && (
                <div className="absolute" style={{ bottom: 116, left: '50%', transform: `translateX(-50%) translateY(${parallax.y * 0.25}px)`, width: 282, transition: 'transform 0.12s ease' }}>
                  <ObjectivesPanel />
                </div>
              )}
            </AnimatePresence>

            {/* VOICE STRIP */}
            <AnimatePresence>{showBottom && <VoiceCommandStrip phase={phase} />}</AnimatePresence>

            {/* NEWS TICKER */}
            <AnimatePresence>{showBottom && <EnhancedTicker data={data} />}</AnimatePresence>

          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
