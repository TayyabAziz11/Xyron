'use client'

import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import type { TakeoverPhase } from '../../hooks/useTakeoverMode'

// ── Live system stats (simulated) ────────────────────────────────────────────

function useStats(active: boolean) {
  const [cpu,  setCpu]  = useState(0)
  const [ram,  setRam]  = useState(0)
  const [temp, setTemp] = useState(0)
  const [net,  setNet]  = useState(0)
  const cpuBase  = useRef(36 + Math.random() * 22)
  const tempBase = useRef(52 + Math.random() * 14)

  useEffect(() => {
    if (!active) { setCpu(0); setRam(0); setTemp(0); setNet(0); return }
    setCpu(Math.round(cpuBase.current))
    setRam(Math.round(58 + Math.random() * 18))
    setTemp(Math.round(tempBase.current))
    setNet(Math.round(1.5 + Math.random() * 9))

    const id = setInterval(() => {
      cpuBase.current  = Math.max(18, Math.min(88, cpuBase.current  + (Math.random() - 0.47) * 10))
      tempBase.current = Math.max(46, Math.min(80, tempBase.current + (Math.random() - 0.5)  * 2.5))
      setCpu(Math.round(cpuBase.current))
      setRam(Math.round(58 + Math.random() * 18))
      setTemp(Math.round(tempBase.current))
      setNet(parseFloat((1.5 + Math.random() * 9).toFixed(1)))
    }, 1300)
    return () => clearInterval(id)
  }, [active])

  return { cpu, ram, temp, net }
}

function useClock(active: boolean) {
  const [t, setT] = useState('')
  useEffect(() => {
    if (!active) return
    const tick = () => setT(
      new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    )
    tick(); const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [active])
  return t
}

function useSessionTimer(active: boolean) {
  const [s, setS] = useState(0)
  const start = useRef(0)
  useEffect(() => {
    if (!active) { setS(0); return }
    start.current = Date.now()
    const id = setInterval(() => setS(Math.floor((Date.now() - start.current) / 1000)), 1000)
    return () => clearInterval(id)
  }, [active])
  return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`
}

// ── Scan-in typing effect ─────────────────────────────────────────────────────

function useScanIn(target: string, active: boolean, delayMs = 0): string {
  const [text, setText] = useState('')
  useEffect(() => {
    if (!active) { setText(''); return }
    let i = 0
    const t = setTimeout(() => {
      const id = setInterval(() => {
        setText(target.slice(0, ++i))
        if (i >= target.length) clearInterval(id)
      }, 26)
      return () => clearInterval(id)
    }, delayMs)
    return () => clearTimeout(t)
  }, [target, active, delayMs])
  return text
}

// ── Sub-components ───────────────────────────────────────────────────────────

interface StatRowProps {
  label:   string
  value:   string
  accent?: boolean
  bar?:    number
  delay?:  number
  active?: boolean
}

function StatRow({ label, value, accent, bar, delay = 0, active }: StatRowProps) {
  const text = useScanIn(value, !!active, delay)
  const fg   = accent ? 'rgba(0,230,255,0.95)' : 'rgba(160,210,220,0.8)'
  const dim  = accent ? 'rgba(0,170,200,0.45)' : 'rgba(80,130,150,0.4)'

  return (
    <div className="py-[7px] border-b" style={{ borderColor: 'rgba(0,200,255,0.06)' }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[7px] font-mono tracking-[3px] uppercase" style={{ color: dim }}>
          {label}
        </span>
        <span className="text-[11px] font-mono tabular-nums" style={{ color: fg }}>
          {text || <span className="opacity-0">_</span>}
        </span>
      </div>
      {bar !== undefined && (
        <div className="h-[2px] rounded-full overflow-hidden" style={{ background: 'rgba(0,200,255,0.07)' }}>
          <motion.div
            className="h-full rounded-full"
            style={{
              background: accent
                ? 'linear-gradient(90deg, rgba(0,180,255,0.9), rgba(0,240,255,0.75))'
                : 'linear-gradient(90deg, rgba(0,100,160,0.65), rgba(0,160,210,0.55))',
            }}
            animate={{ width: `${Math.min(bar, 100)}%` }}
            transition={{ duration: 0.9, ease: 'easeOut' }}
          />
        </div>
      )}
    </div>
  )
}

interface PanelProps {
  title:     string
  children:  React.ReactNode
  from:      'left' | 'right'
  delay?:    number
  active?:   boolean
}

function Panel({ title, children, from, delay = 0, active }: PanelProps) {
  const init = from === 'left' ? { x: -50, opacity: 0 } : { x: 50, opacity: 0 }
  return (
    <AnimatePresence>
      {active && (
        <motion.div
          initial={init}
          animate={{ x: 0, opacity: 1 }}
          exit={init}
          transition={{ delay, duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
          style={{
            background:    'rgba(0,6,14,0.78)',
            border:        '1px solid rgba(0,200,255,0.11)',
            backdropFilter: 'blur(18px)',
            borderRadius:  14,
            padding:       '14px 16px',
            boxShadow:     '0 0 36px rgba(0,200,255,0.04), inset 0 0 1px rgba(0,220,255,0.08)',
          }}
        >
          <div
            className="text-[7px] font-mono tracking-[4px] uppercase mb-3 pb-2 border-b"
            style={{ color: 'rgba(0,180,200,0.3)', borderColor: 'rgba(0,200,255,0.07)' }}
          >
            {title}
          </div>
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// ── Main HUD ─────────────────────────────────────────────────────────────────

interface Props {
  phase:      TakeoverPhase
  systemLine: string
  onClose:    () => void
}

export default function TakeoverHUD({ phase, systemLine, onClose }: Props) {
  const hudActive = phase === 'hud' || phase === 'active'
  const clock     = useClock(hudActive)
  const session   = useSessionTimer(hudActive)
  const { cpu, ram, temp, net } = useStats(hudActive)

  return (
    <>
      {/* Full-screen dark overlay */}
      <AnimatePresence>
        {hudActive && (
          <motion.div
            key="hud-overlay"
            className="fixed inset-0 z-[66] pointer-events-none"
            style={{ background: 'rgba(0,3,10,0.84)', backdropFilter: 'blur(22px) saturate(1.3)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
          />
        )}
      </AnimatePresence>

      {/* Close */}
      <AnimatePresence>
        {hudActive && (
          <motion.button
            key="close-btn"
            onClick={onClose}
            className="fixed top-5 right-5 z-[80] p-2 rounded-lg transition-all"
            style={{ color: 'rgba(0,200,255,0.35)', background: 'rgba(0,200,255,0.04)', border: '1px solid rgba(0,200,255,0.08)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ delay: 1.0 }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'rgba(0,220,255,0.8)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = 'rgba(0,200,255,0.35)' }}
          >
            <X size={13} />
          </motion.button>
        )}
      </AnimatePresence>

      {/* ── Top status bar ── */}
      <AnimatePresence>
        {hudActive && (
          <motion.div
            key="topbar"
            className="fixed top-0 left-0 right-0 z-[76] flex items-center justify-between px-8 py-4"
            style={{ borderBottom: '1px solid rgba(0,200,255,0.06)' }}
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ delay: 0.15, duration: 0.45 }}
          >
            <span className="text-[8px] font-mono tracking-[6px] uppercase" style={{ color: 'rgba(0,200,255,0.3)' }}>
              XYRON — TAKEOVER MODE
            </span>
            <div className="flex items-center gap-5">
              <span className="text-[10px] font-mono tabular-nums" style={{ color: 'rgba(0,200,255,0.5)' }}>
                {clock}
              </span>
              <div className="flex items-center gap-1.5">
                <motion.div
                  className="w-[6px] h-[6px] rounded-full"
                  style={{ background: 'rgba(0,255,110,0.85)', boxShadow: '0 0 6px rgba(0,255,110,0.7)' }}
                  animate={{ opacity: [1, 0.4, 1] }}
                  transition={{ duration: 1.8, repeat: Infinity }}
                />
                <span className="text-[7px] font-mono tracking-[3px] uppercase" style={{ color: 'rgba(0,230,110,0.45)' }}>
                  LIVE
                </span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Center headline ── */}
      <AnimatePresence>
        {hudActive && (
          <motion.div
            key="headline"
            className="fixed z-[76] select-none pointer-events-none"
            style={{ top: '50%', left: '50%', transform: 'translate(-50%, calc(-50% - 30px))', textAlign: 'center' }}
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ delay: 0.35, duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
          >
            <p className="text-[7px] font-mono tracking-[8px] uppercase mb-3" style={{ color: 'rgba(0,190,220,0.28)' }}>
              NEURAL CORE ONLINE
            </p>
            <h1 className="text-[28px] font-bold tracking-tight" style={{ color: 'rgba(210,240,255,0.95)' }}>
              {systemLine}
            </h1>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Left panel — System metrics ── */}
      <div className="fixed left-6 top-1/2 -translate-y-1/2 z-[76]" style={{ width: 210 }}>
        <Panel title="System Metrics" from="left" delay={0.2} active={hudActive}>
          <StatRow label="PROCESSOR" value={`${cpu}%`}     bar={cpu}  accent active={hudActive} delay={220} />
          <StatRow label="MEMORY"    value={`${ram}%`}     bar={ram}        active={hudActive} delay={360} />
          <StatRow label="TEMP"      value={`${temp}°C`}               active={hudActive} delay={500} />
          <StatRow label="NETWORK"   value={`${net} MB/s`}             active={hudActive} delay={640} />
        </Panel>
      </div>

      {/* ── Right panel — Session status ── */}
      <div className="fixed right-6 top-1/2 -translate-y-1/2 z-[76]" style={{ width: 210 }}>
        <Panel title="Session Status" from="right" delay={0.25} active={hudActive}>
          <StatRow label="MODE"         value="TAKEOVER"  accent active={hudActive} delay={280} />
          <StatRow label="SESSION TIME" value={session}          active={hudActive} delay={420} />
          <StatRow label="NOW PLAYING"  value="♪ lo-fi beats"   active={hudActive} delay={560} />
          <StatRow label="MODULES"      value="ALL ACTIVE"       active={hudActive} delay={700} />
        </Panel>
      </div>

      {/* ── Bottom bar ── */}
      <AnimatePresence>
        {hudActive && (
          <motion.div
            key="bottombar"
            className="fixed bottom-0 left-0 right-0 z-[76] flex items-center justify-between px-8 py-4"
            style={{ borderTop: '1px solid rgba(0,200,255,0.05)' }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ delay: 0.5, duration: 0.4 }}
          >
            <motion.span
              className="text-[7px] font-mono tracking-widest uppercase"
              style={{ color: 'rgba(0,180,210,0.22)' }}
              animate={{ opacity: [0.22, 0.55, 0.22] }}
              transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
            >
              WORKSPACE INITIALIZED — NEURAL LINK STABLE
            </motion.span>
            <button
              onClick={onClose}
              className="text-[7px] font-mono tracking-widest uppercase transition-colors"
              style={{ color: 'rgba(0,180,210,0.22)' }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = 'rgba(0,210,255,0.6)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = 'rgba(0,180,210,0.22)' }}
            >
              ESC TO EXIT
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Scanning sweep line ── */}
      <AnimatePresence>
        {hudActive && (
          <motion.div
            key="sweep"
            className="fixed left-0 right-0 z-[77] h-px pointer-events-none"
            style={{ background: 'linear-gradient(90deg, transparent, rgba(0,200,255,0.28), transparent)' }}
            animate={{ top: ['0%', '100%'] }}
            transition={{ duration: 7, repeat: Infinity, ease: 'linear', delay: 0.5 }}
          />
        )}
      </AnimatePresence>
    </>
  )
}
